from __future__ import annotations

import json
import shutil
import sys

import pytest

from autotuner import report as report_module
from autotuner import service as service_module
from autotuner.service import TuningService
from autotuner.storage import Store
from autotuner.toolchain import execute


@pytest.mark.skipif(not shutil.which("opt-21"), reason="LLVM 21 is required")
def test_full_experiment_has_equal_budgets_and_confirmed_report(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(service_module, "RUNS_DIR", runs)
    monkeypatch.setattr(report_module, "RUNS_DIR", runs)
    tuning = TuningService(Store(tmp_path / "experiments.db"))
    experiment_id = tuning.create_experiment({
        "benchmark_id": "dot", "mode": "full",
        "tuning_input": {"size": 300, "seed": 17, "repeat": 2},
        "holdout_input": {"size": 400, "seed": 91, "repeat": 2},
        "budget_per_strategy": 2,
    })
    summary = tuning.run_experiment(experiment_id)
    assert summary["status"] == "completed"
    assert summary["trial_count"] == 4
    trials = tuning.list_trials(experiment_id, limit=100)["items"]
    assert [row["strategy"] for row in trials].count("guided") == 2
    assert [row["strategy"] for row in trials].count("random") == 2
    assert len({(row["strategy"], tuple(row["action_ids"])) for row in trials}) == 4
    assert tuning.get_experiment(experiment_id)["status"] == "completed"
    assert (runs / experiment_id / "samples.csv").exists()
    regenerated = tuning.report(experiment_id)
    assert regenerated["comparisons"] == summary["comparisons"]
    assert json.loads((runs / experiment_id / "summary.json").read_text())["status"] == "completed"
    assert summary["comparisons"]["holdout"]["guided_vs_o3"]["paired_rounds"] == 10


def test_timeout_kills_child_and_returns_a_classified_result(tmp_path):
    result = execute([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, "sleep", timeout=1)
    assert result.timed_out
    assert result.returncode != 0
