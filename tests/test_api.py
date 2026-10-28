from __future__ import annotations

import time

from fastapi.testclient import TestClient

from autotuner import api as api_module
from autotuner import report as report_module
from autotuner import service as service_module
from autotuner.api import app
from autotuner.service import TuningService
from autotuner.storage import Store


def test_api_contract_and_saved_experiment_access():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/v1/health").json()["database"] == "ok"
        benchmarks = client.get("/v1/benchmarks")
        assert benchmarks.status_code == 200
        assert {item["id"] for item in benchmarks.json()["items"]} == {"dot", "matmul", "histogram", "pipeline", "skew_histogram"}
        assert client.get("/v1/actions").status_code == 200
        paths = {path: methods for path, item in client.get("/openapi.json").json()["paths"].items() for methods in [set(item)] if path.startswith("/v1")}
        assert sum(len(methods) for methods in paths.values()) == 13
        assert client.get("/v1/experiments").status_code == 200
        assert client.get("/v1/experiments/00000000-0000-0000-0000-000000000000").status_code == 404
        invalid = client.post("/v1/experiments", json={"benchmark_id": "dot", "mode": "quick", "budget_per_strategy": 21})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_REQUEST"


def test_api_worker_trial_detail_and_artifact(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(service_module, "RUNS_DIR", runs)
    monkeypatch.setattr(report_module, "RUNS_DIR", runs)
    monkeypatch.setattr(api_module, "RUNS_DIR", runs)
    tuning = TuningService(Store(tmp_path / "experiments.db"))
    monkeypatch.setattr(api_module, "TuningService", lambda reconcile=False: tuning)
    with TestClient(app) as client:
        created = client.post("/v1/experiments", json={"benchmark_id": "dot", "mode": "quick", "budget_per_strategy": 1,
                                                       "tuning_input": {"size": 500, "seed": 17, "repeat": 2}})
        assert created.status_code == 202
        experiment_id = created.json()["id"]
        assert created.headers["Location"].endswith(experiment_id)
        second = client.post("/v1/experiments", json={"benchmark_id": "dot", "mode": "quick"})
        assert second.status_code == 409
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            status = client.get(f"/v1/experiments/{experiment_id}").json()["status"]
            if status in {"completed", "failed"}:
                break
            time.sleep(0.2)
        assert status == "completed"
        trials = client.get(f"/v1/experiments/{experiment_id}/trials").json()["items"]
        assert len(trials) == 1
        detail = client.get(f"/v1/experiments/{experiment_id}/trials/{trials[0]['id']}")
        assert detail.status_code == 200
        assert detail.json()["samples"]
        assert client.get(f"/v1/experiments/{experiment_id}/report").status_code == 200
        assert client.get(f"/v1/experiments/{experiment_id}/artifacts/summary.json").status_code == 200
        assert client.get(f"/v1/experiments/{experiment_id}/artifacts/not-a-file").status_code == 404
        retry = client.post(f"/v1/experiments/{experiment_id}/retry")
        assert retry.status_code == 202
        retry_id = retry.json()["id"]
        assert retry_id != experiment_id
        assert client.post(f"/v1/experiments/{retry_id}/cancel").status_code == 202
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            retried = client.get(f"/v1/experiments/{retry_id}").json()
            if retried["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        assert retried["status"] == "cancelled"
        assert retried["parent_experiment_id"] == experiment_id
        assert client.get(f"/v1/experiments/{retry_id}/report").status_code == 409
        assert client.get(f"/v1/experiments/{experiment_id}/report").status_code == 200
