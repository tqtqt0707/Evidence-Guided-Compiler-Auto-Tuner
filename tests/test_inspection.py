from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from autotuner import api, inspection
from autotuner.inspection import InspectionError, inspect_source
from autotuner.models import ExperimentRequest
from autotuner.policies import Proposal
from autotuner.toolchain import CommandResult

SOURCE = "int twice(int x) { int redundant = x + 0; return redundant * 2; }"


def test_inspection_shows_real_ir_diffs_and_assembly_without_main(llvm):
    tc, catalog = llvm
    result = inspect_source(SOURCE, tc, catalog, action_ids=["instcombine", "simplifycfg"])
    assert result["status"] == "completed"
    assert result["executed"] is False
    assert result["correctness"] == "not_tested"
    assert result["performance"] == "not_measured"
    assert [stage["action_id"] for stage in result["stages"]] == [None, "mem2reg", "instcombine", "simplifycfg"]
    assert result["stages"][0]["features"]["instruction_count"] > result["stages"][-1]["features"]["instruction_count"]
    assert "define" in result["stages"][-1]["ir"]
    assert "twice:" in result["assembly"]
    assert any("--- before.ll" in stage["diff"] for stage in result["stages"])
    assert all("-verify-each" in command["args"] for command in result["commands"] if "opt" in Path(command["args"][0]).name)
    assert not list(inspection.DATA_DIR.glob("inspect-*"))


def test_submitted_program_is_never_executed(llvm, tmp_path, monkeypatch):
    tc, catalog = llvm
    def forbidden(*args, **kwargs):
        pytest.fail("Inspection must never link or execute the submitted program")
    monkeypatch.setattr(tc, "run_binary", forbidden)
    monkeypatch.setattr(tc, "object_and_link", forbidden)
    marker = tmp_path / "should-not-exist"
    source = '#include <stdlib.h>\nint main(void) { system("touch ' + str(marker) + '"); for (;;) {} }'
    result = inspect_source(source, tc, catalog, action_ids=["instcombine"])
    assert result["status"] == "completed"
    assert not marker.exists()


@pytest.mark.parametrize("source", [" ", "\x00", "é" * 40000])
def test_invalid_source_rejected_before_compilation(source, llvm):
    with pytest.raises(ValueError):
        inspect_source(source, *llvm, action_ids=["instcombine"])


@pytest.mark.parametrize("actions", [[], ["unknown"], ["dce", "dce"], ["dce", "gvn", "licm", "loop-unroll", "instcombine"]])
def test_invalid_manual_passes_rejected(actions, llvm):
    with pytest.raises(ValueError):
        inspect_source(SOURCE, *llvm, action_ids=actions)


def test_compile_error_reports_diagnostics_and_cleans_up(llvm):
    with pytest.raises(InspectionError) as exc:
        inspect_source("int invalid( {", *llvm, action_ids=["instcombine"])
    assert exc.value.code == "SOURCE_COMPILE_FAILED"
    assert "error:" in exc.value.diagnostics
    assert not list(inspection.DATA_DIR.glob("inspect-*"))


def test_large_ir_rejected_without_incorrect_truncated_counts(llvm, monkeypatch):
    monkeypatch.setattr(inspection, "MAX_IR_BYTES", 10)
    with pytest.raises(InspectionError, match="too large"):
        inspect_source(SOURCE, *llvm, action_ids=["instcombine"])


def test_inspection_uses_shared_openai_settings_but_does_not_send_source(llvm, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setenv("AUTOTUNER_LLM_MODE", "auto")
    calls = []
    class FakePolicy:
        def __init__(self, actions, max_length, seed, features, settings):
            calls.append({"features": features, "model": settings.model, "effort": settings.reasoning_effort})
        def propose(self, seen, history):
            assert not seen and not history
            return Proposal(("instcombine",), "llm", "Inspect redundant arithmetic")
    monkeypatch.setattr(inspection, "OpenAIPolicy", FakePolicy)
    result = inspect_source(SOURCE, *llvm)
    assert result["llm"]["model"] == "gpt-6-luna"
    assert result["proposal"]["proposal_type"] == "llm"
    assert "twice" not in json.dumps(calls)
    assert "test-only-key" not in json.dumps(result)


def test_compiler_timeout_is_classified(llvm, monkeypatch):
    monkeypatch.setattr(inspection, "execute", lambda *a, **k: CommandResult(["clang-21"], -9, 1, True, "", "", "", ""))
    with pytest.raises(InspectionError) as exc:
        inspect_source(SOURCE, *llvm, action_ids=["instcombine"])
    assert exc.value.code == "COMPILER_TIMEOUT"


def test_inspection_api_validation_and_failure_recovery(llvm):
    with TestClient(api.app) as client:
        api.app.state.service._toolchain, api.app.state.service._catalog = llvm
        assert client.post("/v1/inspections", json={"source": "x", "unexpected": True}).status_code == 422
        invalid = client.post("/v1/inspections", json={"source": "int bad( {"})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "SOURCE_COMPILE_FAILED"
        assert "error:" in invalid.json()["error"]["details"]["diagnostics"]
        valid = client.post("/v1/inspections", json={"source": SOURCE, "action_ids": ["instcombine"]})
        assert valid.status_code == 200
        assert valid.json()["executed"] is False
        assert client.get("/v1/experiments").json()["total"] == 0


def test_inspections_and_tuning_cannot_overlap(llvm):
    with TestClient(api.app) as client:
        svc = api.app.state.service
        svc._toolchain, svc._catalog = llvm
        api.app.state.inspection_lock.acquire()
        try:
            assert client.post("/v1/inspections", json={"source": SOURCE}).status_code == 409
            assert client.post("/v1/experiments", json={"benchmark_id": "dot", "mode": "quick"}).status_code == 409
        finally:
            api.app.state.inspection_lock.release()
        request = ExperimentRequest.parse({"benchmark_id": "dot", "mode": "quick"})
        svc.store.create_experiment(request.as_dict(), "source", {}, llvm[1])
        busy = client.post("/v1/inspections", json={"source": SOURCE})
        assert busy.status_code == 409
        assert busy.json()["error"]["code"] == "RUN_IN_PROGRESS"
