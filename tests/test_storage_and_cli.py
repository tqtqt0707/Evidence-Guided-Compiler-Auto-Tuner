from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from autotuner import api, cli, config, report, service, storage
from autotuner.models import ExperimentRequest
from autotuner.service import TuningService
from autotuner.storage import Store, SCHEMA


def request():
    return ExperimentRequest.parse({"benchmark_id": "dot", "mode": "quick"}).as_dict()


def test_only_one_concurrent_creator_can_reserve_worker(tmp_path):
    db = tmp_path / "exclusive.db"
    Store(db)
    barrier = threading.Barrier(6)
    def create(_):
        store = Store(db)
        barrier.wait(timeout=10)
        try:
            return store.create_experiment(request(), "source", {}, {"catalog_hash": "test"})
        except RuntimeError as exc:
            assert str(exc).startswith("RUN_IN_PROGRESS:")
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(create, range(6)))
    assert len([value for value in results if value]) == 1
    assert Store(db).one("SELECT count(*) AS count FROM experiments")["count"] == 1


def test_legacy_migration_preserves_rows_and_binary_sizes(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as con:
        con.executescript(SCHEMA)
        con.execute("INSERT INTO experiments(id,status,stage,mode,benchmark_id,request_json,source_sha256,environment_json,catalog_json,catalog_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("old", "completed", "completed", "quick", "dot", json.dumps(request()), "hash", "{}", "{}", "catalog", "2026-10-01"))
        binary = tmp_path / "old-binary"
        binary.write_bytes(b"original binary")
        con.execute("INSERT INTO variants(id,experiment_id,kind,executable_path,executable_sha256,command_manifest_path,created_at) VALUES(?,?,?,?,?,?,?)",
                    ("variant", "old", "o3", str(binary), "hash", "commands.json", "2026-10-01"))
    migrated = Store(db)
    assert migrated.one("SELECT cancellation_requested FROM experiments WHERE id='old'")["cancellation_requested"] == 0
    assert migrated.one("SELECT executable_bytes FROM variants WHERE id='variant'")["executable_bytes"] == len(b"original binary")
    binary.unlink()
    assert Store(db).one("SELECT executable_bytes FROM variants WHERE id='variant'")["executable_bytes"] == len(b"original binary")


def test_concurrent_legacy_database_startup(tmp_path):
    db = tmp_path / "startup.db"
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
    barrier = threading.Barrier(8)
    def start(_):
        barrier.wait(timeout=10)
        return Store(db).one("SELECT count(*) AS count FROM worker_slot")["count"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(start, range(8))) == [1] * 8


def test_queued_experiment_is_claimed_before_slow_preflight(monkeypatch):
    store = Store()
    run_id = store.create_experiment(request(), "source", {}, {"catalog_hash": "test"})
    first, second = TuningService(store), TuningService(store)
    entered, release = threading.Event(), threading.Event()
    errors = []
    def slow_preflight():
        entered.set()
        assert release.wait(timeout=10)
        raise RuntimeError("simulated preflight failure")
    def duplicate_preflight():
        raise AssertionError("duplicate worker reached preflight")
    monkeypatch.setattr(first, "preflight", slow_preflight)
    monkeypatch.setattr(second, "preflight", duplicate_preflight)
    def run():
        try: first.run_experiment(run_id)
        except RuntimeError as exc: errors.append(str(exc))
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(timeout=10)
        with pytest.raises(RuntimeError, match="queued|owned"):
            second.run_experiment(run_id)
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert errors == ["simulated preflight failure"]
    assert store.one("SELECT status FROM experiments WHERE id=?", (run_id,))["status"] == "failed"
    assert store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] is None


@pytest.mark.parametrize("command", ["benchmarks", "status", "trials", "report"])
def test_read_only_cli_does_not_compile(command, monkeypatch, capsys):
    store = Store()
    run_id = store.create_experiment(request(), "source", {}, {"catalog_hash": "test"})
    store.update_experiment(run_id, status="completed", stage="completed")
    store.release(run_id)
    config.RUNS_DIR.joinpath(run_id).mkdir(parents=True)
    report.build_report(store, run_id)
    def no_compile(*args, **kwargs): pytest.fail("saved record access must not invoke LLVM")
    monkeypatch.setattr(service, "Toolchain", no_compile)
    assert cli.main([command] + ([] if command == "benchmarks" else [run_id])) == 0
    assert capsys.readouterr().out.strip()


def test_report_and_example_export_keep_matching_summary(tmp_path, monkeypatch):
    from autotuner import examples
    store = Store()
    run_id = store.create_experiment(request(), "source", {}, {"catalog_hash": "test"})
    store.update_experiment(run_id, status="completed", stage="completed")
    store.release(run_id)
    expected = report.build_report(store, run_id)
    monkeypatch.setattr(examples, "RUNS_DIR", config.RUNS_DIR)
    monkeypatch.setattr(examples, "TuningService", lambda: TuningService(store))
    target = examples.export_example(run_id, "audit-dot", tmp_path / "export")
    assert json.loads((target / "bundle.json").read_text())["summary"] == expected
    assert json.loads((target / "summary.json").read_text()) == expected
    assert json.loads((target.parent / "index.json").read_text())["items"][0]["experiment_id"] == run_id


def test_saved_example_sources_and_raw_sample_counts_match():
    from autotuner.registry import benchmark
    from autotuner.config import sha256_file
    import csv
    for directory in (config.ROOT / "examples").glob("full-*"):
        summary = json.loads((directory / "summary.json").read_text())
        bundle = json.loads((directory / "bundle.json").read_text())
        assert summary == bundle["summary"]
        assert summary["source_sha256"] == benchmark(summary["benchmark_id"])["source_sha256"]
        with (directory / "samples.csv").open(newline="") as stream:
            assert len(list(csv.DictReader(stream))) == summary["sample_count"]
        assert len(bundle["trials"]) == summary["trial_count"] == 40


def test_restart_recovers_unreserved_active_rows():
    from autotuner.policies import Proposal
    store = Store()
    run_id = store.create_experiment(request(), "source", {}, {"catalog_hash": "test"})
    trial_id = store.create_trial(run_id, "guided", 1, ("dce",), "dce", Proposal(("dce",), "test", "test"))
    store.update_experiment(run_id, status="running", stage="search")
    store.update_trial(trial_id, status="compiling")
    store.release(run_id)  # Simulate the interrupted-state bug from an older version.
    store.reconcile_interrupted()
    assert store.one("SELECT status FROM experiments WHERE id=?", (run_id,))["status"] == "interrupted"
    assert store.one("SELECT status FROM trials WHERE id=?", (trial_id,))["status"] == "interrupted"
