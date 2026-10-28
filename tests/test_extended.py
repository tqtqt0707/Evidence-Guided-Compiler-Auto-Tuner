from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autotuner import api, calibration, config
from autotuner.models import ExperimentRequest, Workload
from autotuner.oracle import expected_small
from autotuner.registry import benchmark
from autotuner.service import TuningService
from autotuner.storage import Store


@pytest.mark.parametrize("name", ["dot", "matmul", "histogram", "pipeline", "skew_histogram"])
@pytest.mark.parametrize("level", ["-O0", "-O3"])
def test_c_oracle_boundaries_and_invalid_arguments(name, level, tmp_path, llvm):
    tc, _ = llvm
    entry = benchmark(name)
    binary = tmp_path / name
    build = tc.compile_direct(Path(entry["source_path"]), level, binary, tmp_path, "build")
    assert build.ok, build.stderr_excerpt
    cases = [(1, 0, 1), (1, 2**64 - 1, 100), (2, 1, 9), (7, 11, 2), (9, 2**63, 4), (17, 2**64 - 1, 3)]
    for size, seed, repeat in cases:
        workload = Workload(size, seed, repeat)
        actual = subprocess.run([str(binary), *workload.args()], capture_output=True, text=True, timeout=5)
        assert actual.returncode == 0, actual.stderr
        assert actual.stdout == expected_small(name, workload)
    invalid = [[], ["--size", "1"], ["--size", "0", "--seed", "0", "--repeat", "1"],
               ["--size", str(entry["size_max"] + 1), "--seed", "0", "--repeat", "1"],
               ["--size", "1", "--seed", "-1", "--repeat", "1"],
               ["--size", "1", "--seed", str(2**64), "--repeat", "1"],
               ["--size", "1", "--seed", "0", "--repeat", "0"],
               ["--size", "1", "--seed", "0", "--repeat", "101"]]
    for args in invalid:
        result = subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=5)
        assert result.returncode != 0, args
        assert not result.stdout, args


@pytest.mark.parametrize("name", ["dot", "matmul", "histogram", "pipeline", "skew_histogram"])
@pytest.mark.parametrize("mode", ["quick", "full"])
@pytest.mark.parametrize("orchestrator", ["plain", "graph"])
def test_real_pipeline_matrix(name, mode, orchestrator, tmp_path, monkeypatch, llvm):
    tc, catalog = llvm
    monkeypatch.setenv("AUTOTUNER_ORCHESTRATOR", orchestrator)
    svc = TuningService()
    svc._toolchain, svc._catalog = tc, catalog
    size = 5 if name == "matmul" else 64
    request = {"benchmark_id": name, "mode": mode, "budget_per_strategy": 2,
               "tuning_input": {"size": size, "seed": 2**64 - 1, "repeat": 2}}
    if mode == "full":
        request["holdout_input"] = {"size": size + 1, "seed": 0, "repeat": 3}
    run_id = svc.create_experiment(request)
    summary = svc.run_experiment(run_id)
    assert summary["status"] == "completed"
    assert summary["trial_count"] == (2 if mode == "quick" else 4)
    assert summary == svc.report(run_id)
    run_dir = config.RUNS_DIR / run_id
    assert json.loads((run_dir / "summary.json").read_text()) == summary
    for artifact in summary["artifacts"]:
        assert (run_dir / artifact).is_file()
    for input_kind, comparisons in summary["comparisons"].items():
        assert len(comparisons["variants"]["o3"]["samples_ns"]) == (3 if mode == "quick" else 10)
        for name, comparison in comparisons.items():
            if name != "variants" and comparison["label"] != "unavailable":
                assert comparison["paired_rounds"] == (3 if mode == "quick" else 10)
    assert svc.store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] is None
    assert svc.store.all("SELECT seed FROM samples WHERE input_kind='tuning'")[0]["seed"] == str(2**64 - 1)
    if orchestrator == "graph":
        graph = json.loads((run_dir / "graph_state.json").read_text())
        assert len(graph["trial_ids"]) == summary["trial_count"]


@pytest.mark.parametrize("change", [
    {"mode": "other"}, {"benchmark_id": "missing"}, {"budget_per_strategy": 0},
    {"budget_per_strategy": True}, {"max_sequence_length": 5}, {"search_repetitions": 2},
    {"confirmation_rounds": 16}, {"llm_mode": "on"}, {"strategies": []},
    {"strategies": ["guided", "guided"]}, {"strategies": ["other"]}, {"unexpected": 1},
    {"tuning_input": {"size": 0, "seed": 0, "repeat": 1}},
    {"tuning_input": {"size": 1, "seed": -1, "repeat": 1}},
    {"tuning_input": {"size": 1, "seed": 2**64, "repeat": 1}},
])
def test_invalid_requests_never_reserve_a_worker(change):
    svc = TuningService()
    with pytest.raises(ValueError):
        svc.create_experiment({"benchmark_id": "dot", "mode": "quick", **change})
    assert svc.store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] is None


@pytest.mark.parametrize("operation", ["actions", "create", "retry"])
@pytest.mark.parametrize("error", [FileNotFoundError("tool disappeared"), subprocess.TimeoutExpired("clang-21 --version", 10)])
def test_api_tool_failures_are_service_unavailable(operation, error, monkeypatch):
    svc = TuningService()
    old = svc.store.create_experiment(ExperimentRequest.parse({"benchmark_id": "dot", "mode": "quick"}).as_dict(), "hash", {}, {"catalog_hash": "test"})
    svc.store.update_experiment(old, status="failed", stage="failed")
    svc.store.release(old)
    def fail():
        raise error
    monkeypatch.setattr(svc, "preflight", fail)
    monkeypatch.setattr(api, "TuningService", lambda reconcile=False: svc)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        if operation == "actions":
            response = client.get("/v1/actions")
        elif operation == "create":
            response = client.post("/v1/experiments", json={"benchmark_id": "dot", "mode": "quick"})
        else:
            response = client.post(f"/v1/experiments/{old}/retry")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "TOOLCHAIN_UNAVAILABLE"
    assert svc.store.one("SELECT experiment_id FROM worker_slot")["experiment_id"] is None


def test_unknown_calibration_benchmark_is_rejected_before_any_build(monkeypatch):
    monkeypatch.setattr(calibration, "Toolchain", lambda: pytest.fail("invalid benchmark must not invoke LLVM"))
    with pytest.raises(ValueError, match="Unknown benchmark"):
        calibration.calibrate("misspelled-benchmark")


@pytest.mark.parametrize("name", ["dot", "matmul", "histogram", "pipeline", "skew_histogram"])
def test_real_calibration_records_each_workload(name, llvm, monkeypatch):
    tc, _ = llvm
    entry = dict(benchmark(name))
    for key, size in [("quick_input", 3), ("tuning_input", 5), ("holdout_input", 7)]:
        entry[key] = {"size": size, "seed": 11, "repeat": 2}
    monkeypatch.setattr(calibration, "registry", lambda: {name: entry})
    monkeypatch.setattr(calibration, "Toolchain", lambda: tc)
    result = calibration.calibrate(name)
    for key, case in result["benchmarks"][name]["cases"].items():
        assert len(case["samples_ns"]) == 3
        assert all(value > 0 for value in case["samples_ns"])
        assert case["workload"] == entry[key]
    saved = json.loads((config.DATA_DIR / "calibration/calibration.json").read_text())
    assert saved == result


def test_twenty_proposals_exercise_measured_guided_mutations(llvm):
    tc, catalog = llvm
    svc = TuningService()
    svc._toolchain, svc._catalog = tc, catalog
    run_id = svc.create_experiment({"benchmark_id": "dot", "mode": "full", "budget_per_strategy": 20,
        "tuning_input": {"size": 64, "seed": 17, "repeat": 2},
        "holdout_input": {"size": 65, "seed": 91, "repeat": 2}})
    summary = svc.run_experiment(run_id)
    trials = svc.store.all("SELECT * FROM trials WHERE experiment_id=? ORDER BY strategy,ordinal", (run_id,))
    assert summary["trial_count"] == 40
    assert len({(row["strategy"], row["action_ids_json"]) for row in trials}) == 40
    assert any(row["proposal_type"] == "mutation" for row in trials)
    for row in trials:
        if row["parent_trial_id"]:
            parent = next(item for item in trials if item["id"] == row["parent_trial_id"])
            assert parent["strategy"] == row["strategy"]
            assert parent["ordinal"] < row["ordinal"]
            assert parent["status"] == "valid"
    assert summary == svc.report(run_id)
