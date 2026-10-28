from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from openai import OpenAI
from fastapi.testclient import TestClient

from autotuner import api, cli, config, llm
from autotuner.models import ExperimentRequest
from autotuner.service import TuningService


def mock_openai(monkeypatch, data=None, *, status="completed", http_status=200):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        if http_status != 200:
            return httpx.Response(http_status, json={"error": {"message": "secret-request-data", "type": "authentication_error"}})
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 1, "model": "gpt-6-luna",
            "status": status, "output": [{"id": "msg_test", "type": "message",
                "role": "assistant", "status": "completed", "content": [{"type": "output_text",
                    "text": json.dumps(data if data is not None else {"action_ids": ["instcombine"], "reason": "Combine redundant operations."}),
                    "annotations": []}]}]})

    def client(**kwargs):
        assert kwargs["api_key"] == "test-only-key"
        assert kwargs["timeout"] == 30
        assert kwargs["max_retries"] == 1
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr(llm, "OpenAI", client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setenv("AUTOTUNER_LLM_MODE", "auto")
    return calls


def test_shared_settings_modes_and_secret_redaction(monkeypatch):
    monkeypatch.setenv("AUTOTUNER_LLM_MODE", "auto")
    assert llm.OpenAISettings.from_env().resolve_mode() == "off"
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        llm.OpenAISettings.from_env().resolve_mode("openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    settings = llm.OpenAISettings.from_env()
    assert settings.resolve_mode() == "openai"
    assert settings.resolve_mode("off") == "off"
    assert "test-only-key" not in repr(settings)
    assert "test-only-key" not in json.dumps(settings.public_status())
    assert cli.main(["llm-status"]) == 0


@pytest.mark.parametrize("timeout", ["0", "-2", "nan", "inf", "wrong"])
def test_invalid_timeout(monkeypatch, timeout):
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", timeout)
    with pytest.raises(ValueError, match="OPENAI_TIMEOUT_SECONDS"):
        llm.OpenAISettings.from_env()


@pytest.mark.parametrize("name,value", [
    ("OPENAI_REASONING_EFFORT", "minimal"), ("OPENAI_REASONING_EFFORT", "ultra"),
    ("OPENAI_MAX_OUTPUT_TOKENS", "0"), ("OPENAI_MAX_OUTPUT_TOKENS", "NaN"),
    ("OPENAI_MAX_OUTPUT_TOKENS", "128001"),
])
def test_invalid_reasoning_settings(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        llm.OpenAISettings.from_env()


def test_legacy_model_can_omit_reasoning_parameter(monkeypatch):
    calls = mock_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "")
    policy = llm.OpenAIPolicy(("instcombine",), 1, 1, {}, llm.OpenAISettings.from_env())
    assert policy.propose(set(), []).proposal_type == "llm"
    assert "reasoning" not in calls[0]


def test_dotenv_loads_outside_checkout_without_overriding_environment(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("OPENAI_API_KEY=file-only-key\nOPENAI_MODEL=file-model\n", encoding="utf-8")
    env = dict(os.environ, AUTOTUNER_ENV_FILE=str(env_file), OPENAI_MODEL="process-model")
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONPATH"] = str(Path(config.__file__).resolve().parent.parent)
    result = subprocess.run([sys.executable, "-c",
        "from autotuner.llm import OpenAISettings; s=OpenAISettings.from_env(); "
        "assert s.api_key == 'file-only-key'; assert s.model == 'process-model'"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_structured_response_and_evidence_scope(monkeypatch):
    calls = mock_openai(monkeypatch)
    policy = llm.OpenAIPolicy(("instcombine", "dce"), 2, 1, {"loads": 3}, llm.OpenAISettings.from_env())
    proposal = policy.propose(set(), [{"id": "old", "action_ids": ["dce"], "status": "valid",
        "search_median_ns": 123, "source_path": "private-source", "holdout": "private-holdout"}])
    assert proposal.action_ids == ("instcombine",)
    assert proposal.proposal_type == "llm"
    assert proposal.evidence_trial_ids == ("old",)
    assert len(calls) == 1
    assert calls[0]["store"] is False
    assert calls[0]["model"] == "gpt-6-luna"
    assert calls[0]["reasoning"] == {"effort": "low"}
    assert calls[0]["max_output_tokens"] == 4096
    assert calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["text"]["format"]["schema"]["properties"]["action_ids"]["items"]["enum"] == ["instcombine", "dce"]
    assert all(secret not in json.dumps(calls) for secret in ("test-only-key", "private-source", "private-holdout"))


@pytest.mark.parametrize("data,status", [
    ({"action_ids": ["unknown"], "reason": "Bad action"}, "completed"),
    ({"action_ids": ["dce", "dce"], "reason": "Duplicate"}, "completed"),
    ({"action_ids": [], "reason": "Empty"}, "completed"),
    ({"action_ids": ["dce"], "reason": "Seen"}, "completed"),
    ({"action_ids": ["instcombine"], "reason": ""}, "completed"),
    ({"action_ids": ["instcombine"], "reason": "Truncated"}, "incomplete"),
])
def test_invalid_or_duplicate_proposals_use_labeled_local_fallback(monkeypatch, data, status):
    mock_openai(monkeypatch, data, status=status)
    policy = llm.OpenAIPolicy(("instcombine", "dce"), 1, 1, {}, llm.OpenAISettings.from_env())
    proposal = policy.propose({("dce",)}, [])
    assert proposal.action_ids == ("instcombine",)
    assert proposal.proposal_type == "llm_fallback"


def test_provider_errors_do_not_expose_credentials_or_request_data(monkeypatch):
    mock_openai(monkeypatch, http_status=401)
    policy = llm.OpenAIPolicy(("instcombine",), 1, 1, {}, llm.OpenAISettings.from_env())
    with pytest.raises(RuntimeError, match="OpenAI request failed") as exc:
        policy.propose(set(), [])
    assert "secret-request-data" not in str(exc.value)
    assert "test-only-key" not in str(exc.value)


@pytest.mark.parametrize("orchestrator", ["plain", "graph"])
def test_openai_runs_through_real_compiler_and_report(monkeypatch, llvm, orchestrator):
    calls = mock_openai(monkeypatch, {"action_ids": ["loop-unroll"], "reason": "Probe the loop feature with a measured unroll trial."})
    monkeypatch.setenv("AUTOTUNER_ORCHESTRATOR", orchestrator)
    svc = TuningService()
    svc._toolchain, svc._catalog = llvm
    run_id = svc.create_experiment({"benchmark_id": "dot", "mode": "full", "budget_per_strategy": 1,
        "tuning_input": {"size": 64, "seed": 1, "repeat": 1},
        "holdout_input": {"size": 65, "seed": 2, "repeat": 1}, "confirmation_rounds": 3})
    summary = svc.run_experiment(run_id)
    assert summary["status"] == "completed"
    assert len(calls) == 1  # Random comparator never calls the model.
    run = svc.get_experiment(run_id)
    assert run["request"]["llm_mode"] == "openai"
    assert run["environment"]["llm"]["model"] == "gpt-6-luna"
    assert run["environment"]["llm"]["reasoning_effort"] == "low"
    trial = svc.list_trials(run_id, strategy="guided")["items"][0]
    assert trial["status"] == "valid"
    assert svc.get_trial(run_id, trial["id"])["proposal_type"] == "llm"
    assert "test-only-key" not in json.dumps(run)


def test_api_and_cli_preserve_automatic_configuration(monkeypatch, llvm):
    mock_openai(monkeypatch)
    assert api.ExperimentCreateBody(benchmark_id="dot", mode="quick").model_dump(exclude_none=True) == {"benchmark_id": "dot", "mode": "quick"}
    assert ExperimentRequest.parse({"benchmark_id": "dot", "mode": "quick"}).llm_mode == "auto"
    captured = []
    class FakeService:
        def __init__(self, **kwargs): pass
        def create_experiment(self, request): captured.append(request); return "test-id"
        def run_experiment(self, run_id): return {}
    monkeypatch.setattr(cli, "TuningService", FakeService)
    assert cli.main(["tune", "--benchmark", "dot", "--llm-mode", "openai"]) == 0
    assert captured[0]["llm_mode"] == "openai"
    with TestClient(api.app) as client:
        health = client.get("/v1/health").json()
        assert health["llm"]["mode"] == "openai"
        assert "test-only-key" not in json.dumps(health)
