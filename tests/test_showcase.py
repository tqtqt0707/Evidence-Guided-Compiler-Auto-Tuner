"""Check that presentation selection cannot turn missing/poor evidence into a win."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import statistics

import pytest

from autotuner.models import ExperimentRequest
from autotuner.config import ROOT, sha256_file
from autotuner.registry import benchmark
from autotuner.showcase import eligible, publish, screening_cases


def summary(reduction=0):
    return {"status": "completed", "comparisons": {
        phase: {reference: {"paired_rounds": 10, "runtime_reduction_percent": reduction}
                for reference in ("guided_vs_o3", "guided_vs_random")}
        for phase in ("tuning", "holdout")}}


@pytest.mark.parametrize("reduction,expected", [(35, True), (0, True), (-1.99, True), (-2, True), (-2.01, False)])
def test_selection_boundary(reduction, expected):
    assert eligible(summary(reduction)) is expected


@pytest.mark.parametrize("phase", ["tuning", "holdout"])
@pytest.mark.parametrize("reference", ["guided_vs_o3", "guided_vs_random"])
def test_each_reference_and_phase_must_pass(phase, reference):
    measured = summary(30)
    measured["comparisons"][phase][reference]["runtime_reduction_percent"] = -12
    assert not eligible(measured)
    measured["comparisons"][phase][reference] = {"paired_rounds": 10}
    assert not eligible(measured)
    measured["comparisons"][phase][reference] = {"paired_rounds": 3, "runtime_reduction_percent": 30}
    assert not eligible(measured)


def test_failed_and_missing_reports_are_not_selected():
    measured = summary(30)
    measured["status"] = "failed"
    assert not eligible(measured)
    assert not eligible({"status": "completed"})


@pytest.mark.parametrize("tuning,holdout,expected", [("win", "win", True), ("win", "inconclusive", False), ("inconclusive", "win", False), (None, None, False)])
def test_follow_up_cases_require_real_wins_on_both_inputs(tuning, holdout, expected):
    measured = summary(25)
    measured["comparisons"]["tuning"]["guided_vs_o3"]["label"] = tuning
    measured["comparisons"]["holdout"]["guided_vs_o3"]["label"] = holdout
    assert eligible(measured, require_win=True) is expected


def test_workloads_are_valid_and_differ_from_holdout():
    cases = screening_cases()
    assert len(cases) == len({case["id"] for case in cases}) == 14
    for case in cases:
        request = ExperimentRequest.parse(case["request"])
        assert request.llm_mode == "off"
        assert request.tuning_input.size != request.holdout_input.size
        assert request.tuning_input.seed != request.holdout_input.seed
        assert request.strategies == ("guided", "random")


def test_publish_retains_excluded_evidence_in_audit(tmp_path):
    records = [{"id": "good", "selected": True}, {"id": "poor", "selected": False}]
    original = deepcopy(records)
    publish(records, tmp_path)
    catalog = json.loads((tmp_path / "catalog.json").read_text())
    assert [row["id"] for row in catalog["items"]] == ["good"]
    assert catalog["screened"] == 2 and catalog["excluded"] == 1
    assert json.loads((tmp_path / "screening.json").read_text()) == original
    assert records == original


def test_bundled_showcase_statistics_match_raw_samples():
    folder = ROOT / "examples" / "showcase"
    catalog = json.loads((folder / "catalog.json").read_text())
    audit = json.loads((folder / "screening.json").read_text())
    assert catalog["screened"] == len(audit) == len(screening_cases())
    assert catalog["items"] == [row for row in audit if row["selected"]]
    assert catalog["excluded"] == sum(not row["selected"] for row in audit)
    assert len(catalog["items"]) >= 3
    with (folder / "statistics.csv").open(newline="") as stream:
        statistics_rows = list(csv.DictReader(stream))
    assert len(statistics_rows) == 2 * len(catalog["items"])
    for row in statistics_rows:
        case = next(case for case in catalog["items"] if case["id"] == row["case_id"])
        assert float(row["reduction_vs_o3_percent"]) == case["comparisons"][row["input"]]["guided_vs_o3"]["runtime_reduction_percent"]
    for case in audit:
        if "error" in case:
            assert not case["selected"]
            continue
        measured = json.loads((folder / case["id"] / "summary.json").read_text())
        assert case["selected"] == eligible(measured, require_win=case.get("require_o3_win", False))
        assert case["comparisons"] == measured["comparisons"]
        assert case["source_sha256"] == sha256_file(Path(benchmark(case["request"]["benchmark_id"])["source_path"]))
        assert all(measured["request"][key] == value for key, value in case["request"].items())
        with (folder / case["id"] / "samples.csv").open(newline="") as stream:
            samples = list(csv.DictReader(stream))
        for phase in ("tuning", "holdout"):
            variants = measured["comparisons"][phase]["variants"]
            for variant, stats in variants.items():
                assert len(stats["samples_ns"]) == 10
                assert stats["median_ns"] == int(statistics.median(stats["samples_ns"]))
                # Reports store the integer-nanosecond median before computing MAD.
                assert stats["mad_ns"] == int(statistics.median(abs(value - stats["median_ns"]) for value in stats["samples_ns"]))
                expected = stats["samples_ns"]
                # All three controls/finalists remain represented in raw data.
                matching = [int(row["duration_ns"]) for row in samples
                            if row["phase"] == f"confirmation_{phase}" and row["valid"] == "1"]
                assert all(value in matching for value in expected), variant
