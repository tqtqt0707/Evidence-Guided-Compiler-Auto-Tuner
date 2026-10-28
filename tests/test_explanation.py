import json
from types import SimpleNamespace

import pytest

from autotuner.explanation import explain_inspection
from autotuner.llm import OpenAISettings
from autotuner import inspection


@pytest.mark.parametrize("kind", ["valid", "incomplete", "invalid", "wrong_stage_count"])
def test_explanation_evidence_and_failure_handling(monkeypatch, kind):
    stages = [{"title": "Compile", "pipeline": "clang", "features": {"instruction_count": 2}, "ir": "ret i32 1"}]
    data = {"original_behavior": "Returns one.", "changes": "No change.", "readable_code": "int f(void) { return 1; }",
            "caveats": "Illustrative.", "language": "c", "stage_explanations": ["Translation only."]}
    if kind == "wrong_stage_count":
        data["stage_explanations"] = []
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        @property
        def responses(self):
            return self
        def create(self, **kwargs):
            assert kwargs["model"] == "gpt-6-luna"
            assert kwargs["store"] is False
            payload = json.loads(kwargs["input"])
            assert payload["original_c"] == "int f(void){return 1;}"
            assert payload["stages"][0]["ir"] == "ret i32 1"
            assert "test-key" not in kwargs["input"]
            return SimpleNamespace(status="incomplete" if kind == "incomplete" else "completed",
                                   output_text="bad-json" if kind == "invalid" else json.dumps(data))
    monkeypatch.setattr(OpenAISettings, "client", lambda self: Client())
    result = explain_inspection("int f(void){return 1;}", stages, "ret", OpenAISettings("test-key"))
    assert result["status"] == ("completed" if kind == "valid" else "unavailable")


def test_offline_explanation_never_calls_provider(llvm, monkeypatch):
    def forbidden(*args):
        pytest.fail("Offline mode must not send source to OpenAI")
    monkeypatch.setattr(inspection, "explain_inspection", forbidden)
    result = inspection.inspect_source("int f(void){return 1;}", *llvm, action_ids=["dce"], explain=True, llm_mode="off")
    assert result["status"] == "completed"
    assert result["explanation"]["status"] == "unavailable"
