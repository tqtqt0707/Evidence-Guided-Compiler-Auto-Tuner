from __future__ import annotations

import json
import os
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autotuner import api, report, service, toolchain
from autotuner.models import ExperimentRequest
from autotuner.policies import Proposal, RandomPolicy
from autotuner.registry import benchmark
from autotuner.service import TrialFailure, TuningService
from autotuner.storage import Store, process_identity


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(service, "RUNS_DIR", runs)
    monkeypatch.setattr(report, "RUNS_DIR", runs)
    store = Store(tmp_path / "audit.db")
    request = ExperimentRequest.parse({"benchmark_id": "dot", "mode": "full", "budget_per_strategy": 1})
    run_id = store.create_experiment(request.as_dict(), benchmark("dot")["source_sha256"], {}, {"catalog_hash": "test"})
    return store, request, run_id, runs


@pytest.mark.parametrize("failure", ["preflight", "source_copy", "directory"])
def test_startup_failure_releases_reservation(experiment, monkeypatch, failure):
    store, request, run_id, runs = experiment
    tuning = TuningService(store)
    def fail(*args, **kwargs):
        raise OSError("simulated startup failure")
    monkeypatch.setattr(tuning, "preflight", fail if failure == "preflight" else lambda: {"catalog": {"items": [], "catalog_hash": "test"}})
    if failure == "source_copy":
        monkeypatch.setattr(service.shutil, "copy2", fail)
    if failure == "directory":
        runs.write_text("blocks directory creation")
    with pytest.raises(OSError):
        tuning.run_experiment(run_id)
    assert store.one("SELECT status FROM experiments WHERE id=?", (run_id,))["status"] == "failed"
    assert store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] is None
    assert store.create_experiment(request.as_dict(), "source", {}, {"catalog_hash": "test"})


def test_confirmed_failure_never_becomes_a_reported_winner(experiment, tmp_path, monkeypatch):
    store, request, run_id, runs = experiment
    tuning = TuningService(store)
    binary = tmp_path / "binary"
    binary.write_bytes(b"binary")
    trial_id = store.create_trial(run_id, "guided", 1, ("dce",), "function(dce)", Proposal(("dce",), "test", "test"))
    store.update_trial(trial_id, status="valid", search_median_ns=100, executable_bytes=6)
    store.select_winner(run_id, "guided", trial_id)
    variants = {kind: (store.create_variant(run_id, trial_id if kind == "guided" else None, kind, binary, tmp_path / "commands.json", "hash"), binary) for kind in ("o3", "empty", "guided")}
    guided_id = variants["guided"][0]
    calls = 0
    def validate(exp, variant, executable, workload, expected, phase, input_kind, logs, label, repetition, round_number=None, order=None):
        nonlocal calls
        if variant == guided_id and phase == "confirmation_holdout":
            calls += 1
            if calls == 2:
                raise TrialFailure("checksum_mismatch", "injected holdout failure")
        store.add_sample(exp, variant, phase, input_kind, workload, repetition, SimpleNamespace(duration_ns=90 if variant == guided_id else 100), "checksum", True, round_number, order)
    monkeypatch.setattr(tuning, "_validated_run", validate)
    winners = {"guided": tuning._trial_row(trial_id), "random": None}
    tuning._confirm(run_id, runs / run_id, request, variants, {"tuning": "checksum", "holdout": "checksum"}, winners)
    assert winners["guided"] is None
    assert store.one("SELECT trial_id FROM selections WHERE strategy='guided'")["trial_id"] is None
    summary = report.build_report(store, run_id, write_files=False)
    assert summary["winners"]["guided"] is None
    assert summary["comparisons"]["tuning"]["guided_vs_o3"]["label"] == "unavailable"
    # Also protect historical rows whose stale selection was never cleared.
    store.select_winner(run_id, "guided", trial_id)
    assert report.build_report(store, run_id, write_files=False)["winners"]["guided"] is None


def test_report_size_is_stable_after_binary_removal(experiment, tmp_path):
    store, request, run_id, runs = experiment
    binary = tmp_path / "binary"
    binary.write_bytes(b"123456")
    store.create_variant(run_id, None, "o3", binary, tmp_path / "commands.json", "hash")
    before = report.build_report(store, run_id, write_files=False)
    binary.unlink()
    after = report.build_report(store, run_id, write_files=False)
    assert before == after
    assert after["baseline_variants"]["o3"]["executable_bytes"] == 6


def test_failed_compile_evidence_is_available_without_variant(experiment):
    store, request, run_id, runs = experiment
    tuning = TuningService(store)
    trial_id = store.create_trial(run_id, "guided", 1, ("dce",), "function(dce)", Proposal(("dce",), "test", "test"))
    directory = runs / run_id / "trials" / trial_id
    directory.mkdir(parents=True)
    (directory / "attempt-commands.json").write_text(json.dumps([{"args": ["opt-21"], "returncode": 1}]))
    (directory / "opt.stderr").write_text("unknown pass")
    detail = tuning.get_trial(run_id, trial_id)
    assert detail["commands"][0]["returncode"] == 1
    assert detail["diagnostics"]["opt.stderr"] == "unknown pass"


def test_restart_detects_reused_pid_and_preserves_trials(experiment):
    store, request, run_id, runs = experiment
    trial_id = store.create_trial(run_id, "guided", 1, ("dce",), "function(dce)", Proposal(("dce",), "test", "test"))
    with store.connect() as con:
        con.execute("UPDATE worker_slot SET owner_id=?", (f"{os.getpid()}:different-boot:0",))
    store.reconcile_interrupted()
    assert store.one("SELECT status FROM experiments WHERE id=?", (run_id,))["status"] == "interrupted"
    assert store.one("SELECT id FROM trials WHERE id=?", (trial_id,))
    assert store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] is None


def test_live_owner_is_not_reconciled(experiment):
    store, _, run_id, _ = experiment
    assert store.one("SELECT owner_id FROM worker_slot")["owner_id"] == process_identity(os.getpid())
    store.reconcile_interrupted()
    assert store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] == run_id


def test_live_best_and_final_selection_use_same_tie_rule(experiment):
    store, _, run_id, _ = experiment
    tuning = TuningService(store)
    for ordinal, action, duration, size in [(1, "dce", 1000, 200), (2, "gvn", 1005, 100)]:
        trial_id = store.create_trial(run_id, "guided", ordinal, (action,), action, Proposal((action,), "test", "test"))
        store.update_trial(trial_id, status="valid", search_median_ns=duration, executable_bytes=size)
    assert tuning.get_experiment(run_id)["best_trial_ids"]["guided"] == trial_id
    store.select_winner(run_id, "guided", None)
    assert tuning.get_experiment(run_id)["best_trial_ids"]["guided"] is None


def test_outcome_uses_only_paired_valid_measurements():
    a = [{"round": r, "duration_ns": 100, "valid": True} for r in range(10)]
    b = [{"round": r, "duration_ns": 90, "valid": True} for r in range(10)]
    a += [{"round": 20, "duration_ns": 100000, "valid": True}]
    b += [{"round": 21, "duration_ns": 1, "valid": False}]
    result = report.outcome(a, b, "full")
    assert result["comparator_median_ns"] == 100
    assert result["paired_rounds"] == 10
    assert result["label"] == "win"
    assert report.outcome(a, b, "quick")["label"] == "demo_only"
    assert report.outcome(a[:1], [{"round": 22, "duration_ns": 90}], "full")["label"] == "unavailable"


def test_fifteen_round_rule_requires_eighty_percent_agreement():
    a = [{"round": r, "duration_ns": 100} for r in range(15)]
    b = [{"round": r, "duration_ns": 90 if r < 8 else 101} for r in range(15)]
    assert report.outcome(a, b, "full")["label"] == "inconclusive"


@pytest.mark.parametrize("field,value", [("size", True), ("seed", 1.0), ("repeat", "2"), ("seed", 2**64)])
def test_api_rejects_coerced_workload_values(field, value):
    from pydantic import ValidationError
    workload = {"size": 7, "seed": 11, "repeat": 2, field: value}
    with pytest.raises(ValidationError):
        api.ExperimentCreateBody.model_validate({"benchmark_id": "dot", "mode": "quick", "tuning_input": workload})


def test_ir_disassembly_failure_is_not_empty_ir(tmp_path, monkeypatch):
    tc = object.__new__(toolchain.Toolchain)
    tc.paths = {"llvm_dis": "llvm-dis-21"}
    monkeypatch.setattr(toolchain, "execute", lambda *args, **kwargs: SimpleNamespace(ok=False, stderr_excerpt="bad bitcode", returncode=1))
    with pytest.raises(RuntimeError, match="IR disassembly failed"):
        tc.textual_ir(tmp_path / "bad.bc", tmp_path, "bad")


def test_random_policy_exhausts_finite_space_without_duplicates():
    policy = RandomPolicy(("dce", "gvn"), 2, 17)
    seen = set()
    while proposal := policy.propose(seen, []):
        assert proposal.action_ids not in seen
        seen.add(proposal.action_ids)
    assert len(seen) == 4


def test_graph_records_budget_and_exhaustion():
    pytest.importorskip("langgraph.graph")
    from autotuner.graph import run_search_graph
    policies = {key: RandomPolicy(("dce",), 1, 17) for key in ("guided", "random")}
    histories, state = run_search_graph(("guided", "random"), 3, policies, {},
        lambda strategy, ordinal, proposal: {"id": f"{strategy}-{ordinal}", "status": "valid"})
    assert {key: len(rows) for key, rows in histories.items()} == {"guided": 1, "random": 1}
    assert len(state["trial_ids"]) == 2
    assert state["stop_reason"] == "budget_exhausted"


def test_cancelled_queued_run_releases_worker_without_compiling(experiment, monkeypatch):
    store, request, run_id, _ = experiment
    tuning = TuningService(store)
    monkeypatch.setattr(tuning, "preflight", lambda: pytest.fail("cancelled queue must not compile"))
    assert tuning.cancel_experiment(run_id)["cancellation_requested"]
    with pytest.raises(service.ExperimentCancelled):
        tuning.run_experiment(run_id)
    assert tuning.get_experiment(run_id)["status"] == "cancelled"
    assert store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] is None


def test_retry_preserves_original_and_links_fresh_experiment(experiment, monkeypatch):
    store, request, run_id, _ = experiment
    tuning = TuningService(store)
    store.update_experiment(run_id, status="failed", stage="failed")
    store.release(run_id)
    monkeypatch.setattr(tuning, "preflight", lambda: {"environment": {}, "catalog": {"catalog_hash": "test", "items": []}})
    new_id = tuning.retry_experiment(run_id)
    new = tuning.get_experiment(new_id)
    assert new_id != run_id
    assert new["parent_experiment_id"] == run_id
    assert new["request"] == request.as_dict()
    assert tuning.get_experiment(run_id)["status"] == "failed"
    assert new["status"] == "queued"


def test_cancel_and_retry_api_contracts(experiment, monkeypatch):
    store, request, run_id, _ = experiment
    tuning = TuningService(store)
    monkeypatch.setattr(api, "TuningService", lambda reconcile=False: tuning)
    with TestClient(api.app) as client:
        assert client.post(f"/v1/experiments/{run_id}/cancel").status_code == 202
        assert client.post(f"/v1/experiments/{run_id}/retry").status_code == 409
        assert client.post("/v1/experiments/00000000-0000-0000-0000-000000000000/cancel").status_code == 404
    # The pending fixture belongs to this process, so explicitly finish it.
    with pytest.raises(service.ExperimentCancelled):
        tuning.run_experiment(run_id)


def test_corrupt_build_cache_is_rebuilt(experiment, tmp_path, monkeypatch):
    from autotuner.config import stable_hash
    from autotuner.actions import compose
    store, request, run_id, runs = experiment
    data = tmp_path / "data"
    monkeypatch.setattr(service, "DATA_DIR", data)
    cache_key = stable_hash({"build": "fingerprint", "pipeline": compose(("dce",))})
    directory = data / "cache" / cache_key
    directory.mkdir(parents=True)
    (directory / "metadata.json").write_text("{broken json")
    class FakeToolchain:
        calls = 0
        def optimize(self, seed, actions, output, logs):
            self.calls += 1
            output.write_bytes(b"bitcode")
            return SimpleNamespace(ok=True, timed_out=False, as_dict=lambda: {"args": ["opt-21"]})
        def textual_ir(self, *args):
            return "unchanged"
    tc = FakeToolchain()
    tuning = TuningService(store)
    tuning._toolchain = tc
    trial = tuning._trial(run_id, runs / run_id, tmp_path / "seed.bc", "unchanged", {"instruction_count": 0}, request, benchmark("dot"), {}, "guided", 1, Proposal(("dce",), "test", "test"), "fingerprint")
    assert tc.calls == 1
    assert trial["status"] == "no_ir_change"


def test_toolchain_rejects_mixed_versions(monkeypatch):
    tc = object.__new__(toolchain.Toolchain)
    tc.paths = {"clang": "clang", "opt": "opt"}
    tc._environment = None
    monkeypatch.setattr(toolchain.subprocess, "run", lambda args, **kwargs: SimpleNamespace(returncode=0, stdout="LLVM version 21.1.8" if args[0] == "clang" else "LLVM version 21.1.7", stderr=""))
    with pytest.raises(RuntimeError, match="matching versions"):
        tc.environment()


def test_api_graceful_shutdown_drains_accepted_work(experiment, monkeypatch):
    import time
    import threading
    store, _, run_id, _ = experiment
    tuning = TuningService(store)
    finished = threading.Event()
    def run(identifier):
        time.sleep(0.05)
        store.update_experiment(identifier, status="completed", stage="completed")
        store.release(identifier)
        finished.set()
    monkeypatch.setattr(tuning, "run_experiment", run)
    monkeypatch.setattr(api, "TuningService", lambda reconcile=False: tuning)
    with TestClient(api.app):
        api.app.state.jobs.put_nowait(run_id)
    assert finished.is_set()
    assert tuning.get_experiment(run_id)["status"] == "completed"


def test_toolchain_change_after_creation_releases_worker(experiment, monkeypatch):
    store, _, run_id, _ = experiment
    with store.connect() as con:
        con.execute("UPDATE experiments SET environment_json=? WHERE id=?", (json.dumps({"versions": {"clang": "21.1.8"}}), run_id))
    tuning = TuningService(store)
    monkeypatch.setattr(tuning, "preflight", lambda: {"catalog": {"catalog_hash": "test", "items": []}, "environment": {"versions": {"clang": "21.1.7"}}})
    with pytest.raises(TrialFailure, match="ENVIRONMENT_CHANGED"):
        tuning.run_experiment(run_id)
    assert store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] is None
