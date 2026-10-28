from __future__ import annotations

import json
import os
import random
import shutil
import statistics
import traceback
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from .actions import compose
from .config import DATA_DIR, RUNS_DIR, ensure_data_dirs, sha256_file, stable_hash
from .features import summarize_ir
from .models import ExperimentRequest, Workload
from .oracle import expected_small, parse_checksum
from .policies import GuidedPolicy, RandomPolicy
from .llm import OpenAISettings, OpenAIPolicy
from .registry import benchmark, registry
from .report import build_report, median_mad
from .storage import Store, utc_now
from .toolchain import CommandResult, Toolchain, command_manifest


class TrialFailure(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


class ExperimentCancelled(TrialFailure):
    def __init__(self):
        super().__init__("CANCELLED", "Experiment cancelled by user; partial evidence is preserved")


def _check_command(result: CommandResult, code: str) -> None:
    if result.timed_out:
        raise TrialFailure("timeout", f"Timed out: {' '.join(result.args[:2])}")
    if not result.ok:
        raise TrialFailure(code, result.stderr_excerpt.strip()[-1000:] or f"Exit {result.returncode}")


class TuningService:
    def __init__(self, store: Store | None = None, *, reconcile: bool = False):
        self.store = store or Store()
        if reconcile:
            self.store.reconcile_interrupted()
        self._toolchain: Toolchain | None = None
        self._catalog: dict | None = None
        self._preflight_lock = threading.RLock()

    def preflight(self) -> dict:
        with self._preflight_lock:
            if self._toolchain is None:
                self._toolchain = Toolchain()
            environment = self._toolchain.environment()
            if self._catalog is None:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="doctor-", dir=DATA_DIR) as directory:
                    self._catalog = self._toolchain.validate_actions(Path(benchmark("dot")["source_path"]), Path(directory))
            return {"environment": environment, "catalog": self._catalog}

    def list_benchmarks(self) -> list[dict]:
        return list(registry().values())

    def list_actions(self, include_unavailable: bool = False) -> dict:
        result = self.preflight()["catalog"].copy()
        if not include_unavailable:
            result["items"] = [item for item in result["items"] if item["available"]]
        return result

    def create_experiment(self, request_data: dict) -> str:
        request = ExperimentRequest.parse(request_data)
        llm_settings = OpenAISettings.from_env()
        request = replace(request, llm_mode=llm_settings.resolve_mode(request.llm_mode))
        readiness = self.preflight()
        entry = benchmark(request.benchmark_id)
        environment = dict(readiness["environment"])
        environment["llm"] = {"mode": request.llm_mode, "provider": "openai" if request.llm_mode == "openai" else None,
                              "model": llm_settings.model if request.llm_mode == "openai" else None,
                              "reasoning_effort": llm_settings.reasoning_effort if request.llm_mode == "openai" else None,
                              "max_output_tokens": llm_settings.max_output_tokens}
        environment["orchestrator"] = os.environ.get("AUTOTUNER_ORCHESTRATOR", "plain")
        environment["strategy_seeds"] = {strategy: request.tuning_input.seed + (103 if strategy == "guided" else 509) for strategy in request.strategies}
        if environment["orchestrator"] not in {"plain", "graph"}:
            raise ValueError("AUTOTUNER_ORCHESTRATOR must be plain or graph")
        if environment["orchestrator"] == "graph":
            try:
                import langgraph.graph
            except ImportError as exc:
                raise RuntimeError("Install the graph extra before creating a graph experiment") from exc
        return self.store.create_experiment(request.as_dict(), entry["source_sha256"], environment, readiness["catalog"])

    def get_experiment(self, experiment_id: str) -> dict | None:
        row = self.store.one("SELECT * FROM experiments WHERE id=?", (experiment_id,))
        if not row:
            return None
        row["request"] = json.loads(row.pop("request_json"))
        row["environment"] = json.loads(row.pop("environment_json"))
        row["catalog"] = json.loads(row.pop("catalog_json"))
        counts = self.store.one("SELECT COUNT(*) AS attempted, SUM(CASE WHEN status='valid' THEN 1 ELSE 0 END) AS valid FROM trials WHERE experiment_id=?", (experiment_id,)) or {}
        row["progress"] = {"planned_candidates": row["request"]["budget_per_strategy"] * len(row["request"]["strategies"]),
                           "attempted_candidates": counts.get("attempted", 0), "valid_candidates": counts.get("valid") or 0}
        best = {}
        for strategy in row["request"]["strategies"]:
            history = self.store.all("SELECT * FROM trials WHERE experiment_id=? AND strategy=?", (experiment_id, strategy))
            selection = self.store.one("SELECT trial_id FROM selections WHERE experiment_id=? AND strategy=?", (experiment_id, strategy))
            winner = (next((trial for trial in history if trial["id"] == selection["trial_id"] and trial["status"] == "valid"), None)
                      if selection else self._select_winner(history))
            best[strategy] = winner["id"] if winner else None
        row["best_trial_ids"] = best
        return row

    def list_experiments(self, *, limit: int = 50, offset: int = 0, status: str | None = None) -> dict:
        where, params = (" WHERE status=?", (status,)) if status else ("", ())
        total = self.store.one("SELECT COUNT(*) AS count FROM experiments" + where, params)["count"]
        rows = self.store.all("SELECT id,benchmark_id,mode,status,stage,created_at,finished_at,error_code FROM experiments" + where + " ORDER BY created_at DESC LIMIT ? OFFSET ?", (*params, limit, offset))
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def cancel_experiment(self, experiment_id: str) -> dict:
        if not self.store.request_cancel(experiment_id):
            if not self.get_experiment(experiment_id):
                raise KeyError(experiment_id)
            raise RuntimeError("EXPERIMENT_NOT_ACTIVE")
        return self.get_experiment(experiment_id)

    def retry_experiment(self, experiment_id: str) -> str:
        original = self.get_experiment(experiment_id)
        if not original:
            raise KeyError(experiment_id)
        if original["status"] in {"queued", "running"}:
            raise RuntimeError("EXPERIMENT_STILL_ACTIVE")
        new_id = self.create_experiment(original["request"])
        with self.store.connect() as con:
            con.execute("UPDATE experiments SET parent_experiment_id=? WHERE id=?", (experiment_id, new_id))
        return new_id

    def _check_cancelled(self, experiment_id: str) -> None:
        row = self.store.one("SELECT cancellation_requested FROM experiments WHERE id=?", (experiment_id,))
        if row and row["cancellation_requested"]:
            raise ExperimentCancelled()

    def list_trials(self, experiment_id: str, *, strategy: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0, sort: str = "ordinal") -> dict:
        filters = ["experiment_id=?"]
        params: list = [experiment_id]
        if strategy:
            filters.append("strategy=?")
            params.append(strategy)
        if status:
            filters.append("status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(filters)
        total = self.store.one("SELECT COUNT(*) AS count FROM trials" + where, tuple(params))["count"]
        order = "(search_median_ns IS NULL),search_median_ns,strategy,ordinal" if sort == "runtime_asc" else "strategy,ordinal"
        rows = self.store.all("SELECT id,strategy,ordinal,action_ids_json,effective_pipeline,proposal_reason,status,error_code,search_median_ns,search_mad_ns,executable_bytes,ir_instructions_before,ir_instructions_after FROM trials" + where + f" ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit, offset))
        for row in rows:
            row["action_ids"] = json.loads(row.pop("action_ids_json"))
            row["correctness"] = "passed" if row["status"] == "valid" else "rejected"
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def get_trial(self, experiment_id: str, trial_id: str) -> dict | None:
        row = self.store.one("SELECT * FROM trials WHERE experiment_id=? AND id=?", (experiment_id, trial_id))
        if not row:
            return None
        row["action_ids"] = json.loads(row.pop("action_ids_json"))
        row["evidence_trial_ids"] = json.loads(row.pop("evidence_trial_ids_json"))
        variants = self.store.all("SELECT * FROM variants WHERE trial_id=?", (trial_id,))
        row["variants"] = variants
        row["samples"] = self.store.all("SELECT samples.* FROM samples JOIN variants ON samples.variant_id=variants.id WHERE variants.trial_id=? ORDER BY samples.created_at,samples.id", (trial_id,))
        expected_path = RUNS_DIR / experiment_id / "expected_checksums.json"
        row["expected_checksums"] = json.loads(expected_path.read_text(encoding="utf-8")) if expected_path.exists() else {}
        row["commands"] = []
        trial_dir = RUNS_DIR / experiment_id / "trials" / trial_id
        attempt = trial_dir / "attempt-commands.json"
        command_logs = sorted(trial_dir.glob("*.command.json"))
        if command_logs:
            for path in command_logs:
                row["commands"].extend(json.loads(path.read_text(encoding="utf-8")))
        elif attempt.exists():
            row["commands"] = json.loads(attempt.read_text(encoding="utf-8"))
        else:
            for variant in variants:
                path = Path(variant["command_manifest_path"])
                if path.exists():
                    row["commands"].extend(json.loads(path.read_text(encoding="utf-8")))
        cache_manifest = trial_dir / "commands.json"
        if cache_manifest.exists():
            row["cache"] = [item for item in json.loads(cache_manifest.read_text(encoding="utf-8")) if item.get("cache_hit")]
        row["diagnostics"] = {path.name: path.read_bytes()[-65536:].decode("utf-8", "replace")
                              for path in trial_dir.glob("*.stderr") if path.stat().st_size}
        return row

    def report(self, experiment_id: str) -> dict:
        row = self.store.one("SELECT status FROM experiments WHERE id=?", (experiment_id,))
        if not row:
            raise KeyError(experiment_id)
        if row["status"] != "completed":
            raise RuntimeError("REPORT_UNAVAILABLE")
        return build_report(self.store, experiment_id, write_files=False)

    def _validated_run(self, experiment_id: str, variant_id: str, binary: Path, workload: Workload, expected: str,
                       phase: str, input_kind: str, log_dir: Path, label: str, repetition: int,
                       round_number: int | None = None, order: int | None = None) -> CommandResult:
        assert self._toolchain is not None
        self._check_cancelled(experiment_id)
        result = self._toolchain.run_binary(binary, workload, log_dir, label)
        valid = result.ok
        try:
            parsed = parse_checksum(result.stdout) if valid else ""
            valid = parsed == expected
        except ValueError:
            parsed, valid = "", False
        self.store.add_sample(experiment_id, variant_id, phase, input_kind, workload, repetition, result, parsed, valid, round_number, order)
        if result.timed_out:
            raise TrialFailure("timeout", f"Execution exceeded timeout for {label}")
        if result.returncode != 0:
            raise TrialFailure("execution_failed", f"Exit {result.returncode}: {result.stderr_excerpt[-500:]}")
        if not valid:
            raise TrialFailure("checksum_mismatch", f"Expected {expected.strip()}, observed {result.stdout.strip()[:100]}")
        return result

    def _baseline(self, experiment_id: str, run_dir: Path, source: Path, request: ExperimentRequest, entry: dict) -> tuple[dict, dict, Path, dict]:
        assert self._toolchain is not None
        tc = self._toolchain
        baseline_dir = run_dir / "baseline"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        binaries: dict[str, tuple[str, Path]] = {}
        for kind, level in (("o0", "-O0"), ("o3", "-O3")):
            self._check_cancelled(experiment_id)
            binary = baseline_dir / kind
            result = tc.compile_direct(source, level, binary, baseline_dir, f"build-{kind}")
            _check_command(result, "compile_failed")
            manifest = baseline_dir / f"{kind}-commands.json"
            command_manifest(manifest, [result])
            binaries[kind] = (self.store.create_variant(experiment_id, None, kind, binary, manifest, sha256_file(binary)), binary)
        seed = baseline_dir / "base.bc"
        seed_result = tc.emit_seed(source, seed, baseline_dir)
        _check_command(seed_result, "compile_failed")
        empty = baseline_dir / "empty"
        empty_results = tc.object_and_link(seed, empty, baseline_dir, "empty")
        for result in empty_results:
            _check_command(result, "compile_failed")
        empty_manifest = baseline_dir / "empty-commands.json"
        command_manifest(empty_manifest, [seed_result, *empty_results])
        binaries["empty"] = (self.store.create_variant(experiment_id, None, "empty", empty, empty_manifest, sha256_file(empty)), empty)
        cases: list[tuple[str, Workload]] = [(f"small_{index}", Workload.parse(item, entry)) for index, item in enumerate(entry["small_cases"])]
        cases.append(("tuning", request.tuning_input))
        if request.holdout_input:
            cases.append(("holdout", request.holdout_input))
        expected: dict[str, str] = {}
        for case_name, workload in cases:
            self._check_cancelled(experiment_id)
            oracle_text = expected_small(request.benchmark_id, workload) if case_name.startswith("small") else None
            reference_id, reference_binary = binaries["o0"]
            reference = tc.run_binary(reference_binary, workload, baseline_dir, f"{case_name}-o0")
            _check_command(reference, "REFERENCE_MISMATCH")
            try:
                expected_text = parse_checksum(reference.stdout)
            except ValueError as exc:
                raise TrialFailure("REFERENCE_MISMATCH", str(exc)) from exc
            if oracle_text and expected_text != oracle_text:
                raise TrialFailure("REFERENCE_MISMATCH", f"Independent oracle disagreed on {case_name}")
            self.store.add_sample(experiment_id, reference_id, "baseline_correctness", case_name, workload, 0, reference, expected_text, True)
            expected[case_name] = expected_text
            for kind in ("o3", "empty"):
                variant_id, binary = binaries[kind]
                self._validated_run(experiment_id, variant_id, binary, workload, expected_text, "baseline_correctness", case_name, baseline_dir, f"{case_name}-{kind}", 0)
        (run_dir / "expected_checksums.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
        base_text = tc.textual_ir(seed, baseline_dir, "base-ir")
        features = summarize_ir(base_text)
        (run_dir / "ir_features.json").write_text(json.dumps(features, indent=2), encoding="utf-8")
        return binaries, expected, seed, features

    def _trial(self, experiment_id: str, run_dir: Path, seed: Path, base_text: str, features: dict,
               request: ExperimentRequest, entry: dict, expected: dict[str, str], strategy: str,
               ordinal: int, proposal: object, build_fingerprint: str) -> dict:
        assert self._toolchain is not None
        self._check_cancelled(experiment_id)
        tc = self._toolchain
        action_ids = proposal.action_ids
        pipeline = compose(action_ids)
        trial_id = self.store.create_trial(experiment_id, strategy, ordinal, action_ids, pipeline, proposal)
        trial_dir = run_dir / "trials" / trial_id
        trial_dir.mkdir(parents=True, exist_ok=True)
        commands: list[CommandResult] = []
        cache_record: dict | None = None
        try:
            self.store.update_trial(trial_id, status="compiling")
            bitcode = trial_dir / "candidate.bc"
            binary = trial_dir / "candidate"
            cache_key = stable_hash({"build": build_fingerprint, "pipeline": pipeline})
            cache_dir = DATA_DIR / "cache" / cache_key
            metadata_path = cache_dir / "metadata.json"
            cache_hit = False
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if not isinstance(metadata, dict):
                        metadata = {}
                except (OSError, ValueError):
                    metadata = {}
                cached_bc, cached_binary = cache_dir / "candidate.bc", cache_dir / "candidate"
                if metadata.get("cache_key") == cache_key and cached_bc.is_file() and cached_binary.is_file() and metadata.get("bitcode_sha256") == sha256_file(cached_bc) and metadata.get("binary_sha256") == sha256_file(cached_binary):
                    shutil.copy2(cached_bc, bitcode)
                    shutil.copy2(cached_binary, binary)
                    cache_hit = True
                    cache_record = {"cache_hit": True, "cache_key": cache_key, "validated_hashes": True,
                                    "original_build_commands": metadata.get("build_commands", [])}
            if not cache_hit:
                opt_result = tc.optimize(seed, action_ids, bitcode, trial_dir)
                commands.append(opt_result)
                if not opt_result.ok:
                    code = "ir_verification_failed" if "Broken module" in opt_result.stderr_excerpt else "pass_parse_failed" if "unknown" in opt_result.stderr_excerpt.lower() else "compile_failed"
                    _check_command(opt_result, code)
            try:
                candidate_text = tc.textual_ir(bitcode, trial_dir, "candidate-ir")
            except RuntimeError as exc:
                raise TrialFailure("ir_verification_failed", str(exc)) from exc
            counts = summarize_ir(candidate_text)
            self.store.update_trial(trial_id, ir_sha256=sha256_file(bitcode),
                                    ir_instructions_before=features["instruction_count"],
                                    ir_instructions_after=counts["instruction_count"])
            if candidate_text == base_text:
                self.store.update_trial(trial_id, status="no_ir_change")
                return self._trial_row(trial_id)
            if not cache_hit:
                self._check_cancelled(experiment_id)
                results = tc.object_and_link(bitcode, binary, trial_dir, "candidate")
                commands.extend(results)
                for result in results:
                    _check_command(result, "compile_failed")
                cache_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bitcode, cache_dir / "candidate.bc.tmp")
                shutil.copy2(binary, cache_dir / "candidate.tmp")
                (cache_dir / "candidate.bc.tmp").replace(cache_dir / "candidate.bc")
                (cache_dir / "candidate.tmp").replace(cache_dir / "candidate")
                temporary_metadata = cache_dir / "metadata.json.tmp"
                temporary_metadata.write_text(json.dumps({"cache_key": cache_key, "bitcode_sha256": sha256_file(bitcode),
                                                          "binary_sha256": sha256_file(binary),
                                                          "build_commands": [result.as_dict() for result in commands]}, indent=2), encoding="utf-8")
                temporary_metadata.replace(metadata_path)
            manifest = trial_dir / "commands.json"
            if cache_record:
                manifest.write_text(json.dumps([cache_record], indent=2), encoding="utf-8")
            else:
                command_manifest(manifest, commands)
            variant_id = self.store.create_variant(experiment_id, trial_id, "candidate", binary, manifest, sha256_file(binary))
            self.store.update_trial(trial_id, executable_bytes=binary.stat().st_size)
            for index, small in enumerate(entry["small_cases"]):
                workload = Workload.parse(small, entry)
                case = f"small_{index}"
                self._validated_run(experiment_id, variant_id, binary, workload, expected[case], "correctness", case, trial_dir, f"correct-{case}", 0)
            self._validated_run(experiment_id, variant_id, binary, request.tuning_input, expected["tuning"], "correctness", "tuning", trial_dir, "correct-tuning", 0)
            samples = []
            self._validated_run(experiment_id, variant_id, binary, request.tuning_input, expected["tuning"], "search_warmup", "tuning", trial_dir, "warmup", 0)
            for index in range(request.search_repetitions):
                result = self._validated_run(experiment_id, variant_id, binary, request.tuning_input, expected["tuning"], "search", "tuning", trial_dir, f"search-{index}", index)
                samples.append(result.duration_ns)
            med, mad = median_mad(samples)
            noise = int(bool(med and mad / med > 0.03))
            if noise:
                for index in range(request.search_repetitions):
                    result = self._validated_run(experiment_id, variant_id, binary, request.tuning_input, expected["tuning"], "search", "tuning", trial_dir, f"remeasure-{index}", request.search_repetitions + index)
                    samples.append(result.duration_ns)
                med, mad = median_mad(samples)
            self.store.update_trial(trial_id, status="valid", search_median_ns=med, search_mad_ns=mad, noise_flag=noise)
        except ExperimentCancelled:
            self.store.update_trial(trial_id, status="cancelled", error_code="CANCELLED", error_message="Experiment cancelled")
            raise
        except TrialFailure as exc:
            self.store.update_trial(trial_id, status=exc.code if exc.code in {"timeout", "checksum_mismatch", "execution_failed", "compile_failed", "pass_parse_failed", "ir_verification_failed"} else "compile_failed", error_code=exc.code, error_message=exc.message[:1000])
        finally:
            command_manifest(trial_dir / "attempt-commands.json", commands)
        return self._trial_row(trial_id)

    def _trial_row(self, trial_id: str) -> dict:
        row = self.store.one("SELECT * FROM trials WHERE id=?", (trial_id,))
        assert row is not None
        row["action_ids"] = json.loads(row["action_ids_json"])
        return row

    @staticmethod
    def _select_winner(history: list[dict]) -> dict | None:
        valid = [item for item in history if item["status"] == "valid" and item["search_median_ns"] is not None]
        if not valid:
            return None
        fastest = min(item["search_median_ns"] for item in valid)
        tied = [item for item in valid if item["search_median_ns"] <= fastest * 1.01]
        return min(tied, key=lambda item: (item["executable_bytes"] or 0, item["search_median_ns"]))

    def _confirm(self, experiment_id: str, run_dir: Path, request: ExperimentRequest, binaries: dict, expected: dict, winners: dict) -> None:
        variants = self.store.all("SELECT * FROM variants WHERE experiment_id=?", (experiment_id,))
        active = {kind: (variant_id, binary) for kind, (variant_id, binary) in binaries.items() if kind in {"o3", "empty"}}
        for strategy, trial in winners.items():
            if trial:
                variant = next((row for row in variants if row["trial_id"] == trial["id"]), None)
                if variant:
                    active[strategy] = (variant["id"], Path(variant["executable_path"]))
        inputs = [("tuning", request.tuning_input, "confirmation_tuning")]
        if request.holdout_input:
            inputs.append(("holdout", request.holdout_input, "confirmation_holdout"))
        for input_kind, workload, phase in inputs:
            confirm_dir = run_dir / "confirmation" / input_kind
            for kind, (variant_id, binary) in list(active.items()):
                try:
                    self._validated_run(experiment_id, variant_id, binary, workload, expected[input_kind], "confirmation_warmup", input_kind, confirm_dir, f"warmup-{kind}", 0)
                except ExperimentCancelled:
                    raise
                except TrialFailure as exc:
                    if kind in winners and winners[kind]:
                        self.store.update_trial(winners[kind]["id"], status=exc.code, error_code=exc.code, error_message=exc.message)
                        self.store.select_winner(experiment_id, kind, None)
                        winners[kind] = None
                        active.pop(kind)
                    else:
                        raise
            for round_number in range(request.confirmation_rounds):
                order = list(active)
                random.Random(10007 + round_number).shuffle(order)
                for position, kind in enumerate(order):
                    variant_id, binary = active[kind]
                    try:
                        self._validated_run(experiment_id, variant_id, binary, workload, expected[input_kind], phase, input_kind, confirm_dir, f"round-{round_number}-{kind}", round_number, round_number, position)
                    except ExperimentCancelled:
                        raise
                    except TrialFailure as exc:
                        if kind in winners and winners[kind]:
                            self.store.update_trial(winners[kind]["id"], status=exc.code, error_code=exc.code, error_message=exc.message)
                            self.store.select_winner(experiment_id, kind, None)
                            winners[kind] = None
                            active.pop(kind)
                        else:
                            raise

    def run_experiment(self, experiment_id: str) -> dict:
        row = self.store.one("SELECT * FROM experiments WHERE id=?", (experiment_id,))
        if not row:
            raise KeyError(experiment_id)
        if not self.store.claim_experiment(experiment_id):
            raise RuntimeError("Only queued experiments owned by this worker can be executed; create a new experiment to retry")
        run_dir = RUNS_DIR / experiment_id
        try:
            self._check_cancelled(experiment_id)
            request = ExperimentRequest.parse(json.loads(row["request_json"]))
            entry = benchmark(request.benchmark_id)
            readiness = self.preflight()
            catalog = readiness["catalog"]
            environment = json.loads(row["environment_json"])
            if environment.get("versions") and any(environment.get(key) != readiness["environment"].get(key)
                                                   for key in ("paths", "versions", "target_triple", "clang_flags", "llc_flags", "link_flags")):
                raise TrialFailure("ENVIRONMENT_CHANGED", "Toolchain changed after experiment creation; create a fresh experiment")
            if row["catalog_hash"] != catalog.get("catalog_hash"):
                raise TrialFailure("CATALOG_CHANGED", "Action catalog changed after experiment creation; create a fresh experiment")
            available = tuple(item["id"] for item in catalog["items"] if item["available"])
            run_dir.mkdir(parents=True, exist_ok=True)
            source = run_dir / entry["source"]
            shutil.copy2(entry["source_path"], source)
            self.store.update_experiment(experiment_id, stage="baselines")
            if sha256_file(source) != row["source_sha256"]:
                raise TrialFailure("SOURCE_CHANGED", "Source changed after experiment creation")
            binaries, expected, seed, features = self._baseline(experiment_id, run_dir, source, request, entry)
            assert self._toolchain is not None
            base_text = self._toolchain.textual_ir(seed, run_dir / "baseline", "base-ir-for-search")
            build_fingerprint = stable_hash({"source": row["source_sha256"], "catalog": row["catalog_hash"], "environment": json.loads(row["environment_json"])})
            self.store.update_experiment(experiment_id, stage="search")
            policies = {}
            for strategy in request.strategies:
                policy_seed = request.tuning_input.seed + (103 if strategy == "guided" else 509)
                if strategy == "guided" and request.llm_mode == "openai":
                    settings = OpenAISettings.from_env()
                    settings = replace(settings, model=environment["llm"]["model"],
                                       reasoning_effort=environment["llm"].get("reasoning_effort"),
                                       max_output_tokens=environment["llm"].get("max_output_tokens", 4096))
                    policies[strategy] = OpenAIPolicy(available, request.max_sequence_length, policy_seed, features, settings)
                else:
                    policies[strategy] = GuidedPolicy(available, request.max_sequence_length, policy_seed, features) if strategy == "guided" else RandomPolicy(available, request.max_sequence_length, policy_seed)
            if json.loads(row["environment_json"]).get("orchestrator") == "graph":
                from .graph import run_search_graph
                histories, graph_state = run_search_graph(
                    request.strategies, request.budget_per_strategy, policies, features,
                    lambda strategy, ordinal, proposal: self._trial(experiment_id, run_dir, seed, base_text, features,
                                                                     request, entry, expected, strategy, ordinal,
                                                                     proposal, build_fingerprint),
                )
                (run_dir / "graph_state.json").write_text(json.dumps({key: graph_state[key] for key in ("turn", "total_turns", "trial_ids", "seen_sequences", "stop_reason")}, indent=2), encoding="utf-8")
            else:
                histories = {strategy: [] for strategy in request.strategies}
                seen: dict[str, set[tuple[str, ...]]] = {strategy: set() for strategy in request.strategies}
                exhausted: set[str] = set()
                for ordinal in range(1, request.budget_per_strategy + 1):
                    for strategy in request.strategies:
                        if strategy in exhausted:
                            continue
                        proposal = policies[strategy].propose(seen[strategy], histories[strategy])
                        if proposal is None:
                            exhausted.add(strategy)
                            continue
                        seen[strategy].add(proposal.action_ids)
                        trial = self._trial(experiment_id, run_dir, seed, base_text, features, request, entry, expected, strategy, ordinal, proposal, build_fingerprint)
                        histories[strategy].append(trial)
            winners = {}
            for strategy, history in histories.items():
                winners[strategy] = self._select_winner(history)
                self.store.select_winner(experiment_id, strategy, winners[strategy]["id"] if winners[strategy] else None)
            self.store.update_experiment(experiment_id, stage="confirmation")
            self._confirm(experiment_id, run_dir, request, binaries, expected, winners)
            self._check_cancelled(experiment_id)
            self.store.update_experiment(experiment_id, stage="report")
            summary = build_report(self.store, experiment_id)
            self.store.update_experiment(experiment_id, status="completed", stage="completed", finished_at=utc_now())
            return summary
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
            code = ("USER_INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "PROCESS_EXITED" if interrupted
                    else exc.code if isinstance(exc, TrialFailure) else "INTERNAL_ERROR")
            status = "interrupted" if interrupted else "cancelled" if isinstance(exc, ExperimentCancelled) else "failed"
            self.store.update_experiment(experiment_id, status=status, stage=status, finished_at=utc_now(), error_code=code, error_message=str(exc)[:1000])
            self.store.interrupt_pending_trials(experiment_id, code)
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
            except OSError:
                pass  # A full/unwritable disk must not prevent worker release.
            raise
        finally:
            self.store.release(experiment_id)
