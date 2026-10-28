from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH, ensure_data_dirs


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def process_identity(pid: int) -> str:
    """Linux boot and start time distinguish PID reuse; old PID-only rows still work."""
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        start = stat.read_text().rsplit(")", 1)[1].split()[19]
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return f"{pid}:{boot}:{start}"
    return str(pid)


SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY, parent_experiment_id TEXT REFERENCES experiments(id),
    status TEXT NOT NULL, stage TEXT NOT NULL, mode TEXT NOT NULL,
    benchmark_id TEXT NOT NULL, request_json TEXT NOT NULL,
    source_sha256 TEXT NOT NULL, environment_json TEXT NOT NULL,
    catalog_json TEXT NOT NULL, catalog_hash TEXT NOT NULL,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    error_code TEXT, error_message TEXT, schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS trials (
    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
    strategy TEXT NOT NULL, ordinal INTEGER NOT NULL,
    action_ids_json TEXT NOT NULL, effective_pipeline TEXT NOT NULL,
    proposal_type TEXT NOT NULL, proposal_reason TEXT NOT NULL,
    parent_trial_id TEXT, evidence_trial_ids_json TEXT NOT NULL,
    status TEXT NOT NULL, error_code TEXT, error_message TEXT,
    ir_sha256 TEXT, ir_instructions_before INTEGER,
    ir_instructions_after INTEGER, executable_bytes INTEGER,
    search_median_ns INTEGER, search_mad_ns INTEGER, noise_flag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(experiment_id,strategy,ordinal), UNIQUE(experiment_id,strategy,action_ids_json)
);
CREATE TABLE IF NOT EXISTS variants (
    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
    trial_id TEXT REFERENCES trials(id), kind TEXT NOT NULL,
    executable_path TEXT NOT NULL, executable_sha256 TEXT NOT NULL,
    command_manifest_path TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selections (
    experiment_id TEXT NOT NULL REFERENCES experiments(id), strategy TEXT NOT NULL,
    trial_id TEXT REFERENCES trials(id), selection_basis TEXT NOT NULL,
    PRIMARY KEY(experiment_id,strategy)
);
CREATE TABLE IF NOT EXISTS samples (
    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
    variant_id TEXT NOT NULL REFERENCES variants(id), phase TEXT NOT NULL,
    input_kind TEXT NOT NULL, size INTEGER NOT NULL, seed TEXT NOT NULL,
    repeat INTEGER NOT NULL, round INTEGER, order_in_round INTEGER,
    repetition INTEGER NOT NULL, duration_ns INTEGER NOT NULL,
    checksum TEXT NOT NULL, valid INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    experiment_id TEXT NOT NULL REFERENCES experiments(id), name TEXT NOT NULL,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, media_type TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY(experiment_id,name)
);
CREATE TABLE IF NOT EXISTS worker_slot (
    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
    experiment_id TEXT REFERENCES experiments(id), owner_id TEXT
);
INSERT OR IGNORE INTO worker_slot(singleton_id) VALUES(1);
CREATE INDEX IF NOT EXISTS idx_trials_experiment ON trials(experiment_id,strategy,ordinal);
CREATE INDEX IF NOT EXISTS idx_samples_experiment ON samples(experiment_id,phase,variant_id);
"""


class Store:
    def __init__(self, path: Path | None = None):
        ensure_data_dirs()
        self.path = path if path is not None else DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)
            # Serialize schema inspection and ALTERs across simultaneous startups.
            con.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in con.execute("PRAGMA table_info(variants)")}
            if "executable_bytes" not in columns:
                con.execute("ALTER TABLE variants ADD COLUMN executable_bytes INTEGER")
            experiment_columns = {row["name"] for row in con.execute("PRAGMA table_info(experiments)")}
            if "cancellation_requested" not in experiment_columns:
                con.execute("ALTER TABLE experiments ADD COLUMN cancellation_requested INTEGER NOT NULL DEFAULT 0")
            for row in con.execute("SELECT id,executable_path FROM variants WHERE executable_bytes IS NULL").fetchall():
                try:
                    size = Path(row["executable_path"]).stat().st_size
                except OSError:
                    continue
                con.execute("UPDATE variants SET executable_bytes=? WHERE id=?", (size, row["id"]))

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def reconcile_interrupted(self) -> None:
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            slot = con.execute("SELECT experiment_id,owner_id FROM worker_slot WHERE singleton_id=1").fetchone()
            if slot and slot["experiment_id"]:
                try:
                    identity = slot["owner_id"] or ""
                    pid = int(identity.split(":", 1)[0])
                    if pid <= 0:
                        raise ValueError("Invalid worker PID")
                    os.kill(pid, 0)
                    if ":" in identity and process_identity(pid) != identity:
                        raise ProcessLookupError("Worker PID was reused")
                    state = con.execute("SELECT status FROM experiments WHERE id=?", (slot["experiment_id"],)).fetchone()
                    if state and state["status"] not in {"queued", "running"}:
                        con.execute("UPDATE worker_slot SET experiment_id=NULL,owner_id=NULL WHERE singleton_id=1")
                except (ProcessLookupError, ValueError, TypeError):
                    con.execute("UPDATE experiments SET status='interrupted',stage='interrupted',finished_at=?,error_code='PROCESS_RESTARTED' WHERE id=? AND status IN ('queued','running')", (utc_now(), slot["experiment_id"]))
                    con.execute("UPDATE worker_slot SET experiment_id=NULL,owner_id=NULL WHERE singleton_id=1")
                except PermissionError:
                    pass
            con.execute("""UPDATE experiments SET status='interrupted',stage='interrupted',finished_at=?,error_code='PROCESS_RESTARTED'
                WHERE status IN ('queued','running') AND id NOT IN (
                    SELECT experiment_id FROM worker_slot WHERE experiment_id IS NOT NULL)""", (utc_now(),))
            con.execute("""UPDATE trials SET status='interrupted',error_code='PROCESS_RESTARTED',error_message='Experiment interrupted before trial completion'
                WHERE status IN ('proposed','compiling') AND experiment_id IN (
                    SELECT id FROM experiments WHERE status='interrupted')""")

    def create_experiment(self, request: dict, source_sha256: str, environment: dict, catalog: dict) -> str:
        experiment_id = str(uuid.uuid4())
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            active = con.execute("SELECT experiment_id FROM worker_slot WHERE singleton_id=1").fetchone()[0]
            if active:
                raise RuntimeError(f"RUN_IN_PROGRESS:{active}")
            con.execute("INSERT INTO experiments(id,status,stage,mode,benchmark_id,request_json,source_sha256,environment_json,catalog_json,catalog_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (experiment_id, "queued", "queued", request["mode"], request["benchmark_id"], json.dumps(request), source_sha256, json.dumps(environment), json.dumps(catalog), catalog["catalog_hash"], utc_now()))
            con.execute("UPDATE worker_slot SET experiment_id=?,owner_id=? WHERE singleton_id=1", (experiment_id, process_identity(os.getpid())))
        return experiment_id

    def release(self, experiment_id: str) -> None:
        with self.connect() as con:
            con.execute("UPDATE worker_slot SET experiment_id=NULL,owner_id=NULL WHERE singleton_id=1 AND experiment_id=?", (experiment_id,))

    def claim_experiment(self, experiment_id: str) -> bool:
        """A reservation may be executed once, by its owning process only."""
        with self.connect() as con:
            result = con.execute("""UPDATE experiments SET status='running',stage='preflight',started_at=?
                WHERE id=? AND status='queued' AND EXISTS (
                    SELECT 1 FROM worker_slot WHERE singleton_id=1 AND experiment_id=? AND owner_id=?)""",
                (utc_now(), experiment_id, experiment_id, process_identity(os.getpid())))
            return bool(result.rowcount)

    def request_cancel(self, experiment_id: str) -> bool:
        with self.connect() as con:
            result = con.execute("UPDATE experiments SET cancellation_requested=1 WHERE id=? AND status IN ('queued','running')", (experiment_id,))
            return bool(result.rowcount)

    def interrupt_pending_trials(self, experiment_id: str, error_code: str) -> None:
        with self.connect() as con:
            con.execute("""UPDATE trials SET status='interrupted',error_code=?,error_message='Experiment ended before trial completion'
                WHERE experiment_id=? AND status IN ('proposed','compiling')""", (error_code, experiment_id))

    def update_experiment(self, experiment_id: str, **fields: object) -> None:
        if not fields:
            return
        allowed = {"status", "stage", "started_at", "finished_at", "error_code", "error_message"}
        if set(fields) - allowed:
            raise ValueError("Unexpected experiment update field")
        with self.connect() as con:
            con.execute(f"UPDATE experiments SET {', '.join(f'{name}=?' for name in fields)} WHERE id=?", (*fields.values(), experiment_id))

    def create_trial(self, experiment_id: str, strategy: str, ordinal: int, action_ids: tuple[str, ...], pipeline: str, proposal: object) -> str:
        trial_id = str(uuid.uuid4())
        with self.connect() as con:
            con.execute("INSERT INTO trials(id,experiment_id,strategy,ordinal,action_ids_json,effective_pipeline,proposal_type,proposal_reason,parent_trial_id,evidence_trial_ids_json,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (trial_id, experiment_id, strategy, ordinal, json.dumps(action_ids), pipeline, proposal.proposal_type, proposal.reason, proposal.parent_trial_id, json.dumps(proposal.evidence_trial_ids), "proposed", utc_now()))
        return trial_id

    def update_trial(self, trial_id: str, **fields: object) -> None:
        if not fields:
            return
        allowed = {"status", "error_code", "error_message", "ir_sha256", "ir_instructions_before", "ir_instructions_after", "executable_bytes", "search_median_ns", "search_mad_ns", "noise_flag"}
        if set(fields) - allowed:
            raise ValueError("Unexpected trial update field")
        with self.connect() as con:
            con.execute(f"UPDATE trials SET {', '.join(f'{name}=?' for name in fields)} WHERE id=?", (*fields.values(), trial_id))

    def create_variant(self, experiment_id: str, trial_id: str | None, kind: str, executable: Path, command_manifest: Path, digest: str) -> str:
        variant_id = str(uuid.uuid4())
        with self.connect() as con:
            con.execute("INSERT INTO variants(id,experiment_id,trial_id,kind,executable_path,executable_sha256,command_manifest_path,created_at,executable_bytes) VALUES(?,?,?,?,?,?,?,?,?)",
                        (variant_id, experiment_id, trial_id, kind, str(executable), digest, str(command_manifest), utc_now(), executable.stat().st_size))
        return variant_id

    def select_winner(self, experiment_id: str, strategy: str, trial_id: str | None) -> None:
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO selections(experiment_id,strategy,trial_id,selection_basis) VALUES(?,?,?,?)",
                        (experiment_id, strategy, trial_id, "lowest search median with 1% size tie band"))

    def add_sample(self, experiment_id: str, variant_id: str, phase: str, input_kind: str, workload: object, repetition: int, result: object, checksum: str, valid: bool, round_number: int | None = None, order: int | None = None) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO samples(id,experiment_id,variant_id,phase,input_kind,size,seed,repeat,round,order_in_round,repetition,duration_ns,checksum,valid,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), experiment_id, variant_id, phase, input_kind, workload.size, str(workload.seed), workload.repeat, round_number, order, repetition, result.duration_ns, checksum, int(valid), utc_now()))

    def add_artifact(self, experiment_id: str, name: str, relative_path: str, digest: str, media_type: str) -> None:
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO artifacts(experiment_id,name,relative_path,sha256,media_type,created_at) VALUES(?,?,?,?,?,?)", (experiment_id, name, relative_path, digest, media_type, utc_now()))

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.connect() as con:
            row = con.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, args).fetchall()]
