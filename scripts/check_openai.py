"""Opt-in live OpenAI verification. Uses API credits and disposable experiment data.

Run explicitly: python scripts/check_openai.py [--workflows]
Never invoked by pytest, CI, or the browser regression suite.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CheckFailed(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CheckFailed(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows", action="store_true", help="Also run compiler, graph, CLI, and API checks with live proposals")
    args = parser.parse_args()
    result = {"status": "running", "checks": []}
    output = ROOT / "test-results" / "openai-live.json"
    output.parent.mkdir(exist_ok=True)

    def record(name, **details):
        item = {"check": name, **details}
        result["checks"].append(item)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(item), flush=True)

    try:
        with tempfile.TemporaryDirectory(prefix="autotuner-openai-") as directory:
            os.environ["AUTOTUNER_DATA_DIR"] = directory
            from autotuner.llm import OpenAISettings
            from openai import APIStatusError, OpenAIError
            settings = OpenAISettings.from_env()
            result["configuration"] = settings.public_status()
            require(settings.resolve_mode("openai") == "openai", "OpenAI key is required")
            started = time.monotonic()
            try:
                with settings.client() as client:
                    response = client.responses.create(
                        model=settings.model, store=False, reasoning={"effort": settings.reasoning_effort},
                        max_output_tokens=settings.max_output_tokens, input="Reply with exactly READY.")
            except APIStatusError as exc:
                record("authentication_and_generation", status="failed", http_status=exc.status_code,
                       error_category=type(exc).__name__)
                raise CheckFailed("OpenAI rejected the live request; see HTTP status and error category above") from None
            except OpenAIError as exc:
                record("authentication_and_generation", status="failed", error_category=type(exc).__name__)
                raise CheckFailed("OpenAI connection or response failed") from None
            require(response.status == "completed" and response.output_text.strip() == "READY", "Live model response did not complete as expected")
            record("authentication_and_generation", status="passed", requested_model=settings.model,
                   returned_model=response.model, reasoning_effort=settings.reasoning_effort,
                   elapsed_seconds=round(time.monotonic() - started, 2),
                   usage=response.usage.model_dump() if response.usage else None)

            if args.workflows:
                from autotuner.service import TuningService
                from autotuner import config
                svc = TuningService()
                readiness = svc.preflight()
                record("llvm_preflight", status="passed", available_actions=sum(item["available"] for item in readiness["catalog"]["items"]))
                for orchestrator in ("plain", "graph"):
                    os.environ["AUTOTUNER_ORCHESTRATOR"] = orchestrator
                    for benchmark in ("dot", "matmul", "histogram"):
                        size = 5 if benchmark == "matmul" else 64
                        run_id = svc.create_experiment({"benchmark_id": benchmark, "mode": "full",
                            "llm_mode": "openai", "budget_per_strategy": 2, "confirmation_rounds": 3,
                            "tuning_input": {"size": size, "seed": 17, "repeat": 2},
                            "holdout_input": {"size": size + 1, "seed": 91, "repeat": 2}})
                        report = svc.run_experiment(run_id)
                        trials = svc.list_trials(run_id, limit=100)["items"]
                        details = [svc.get_trial(run_id, trial["id"]) for trial in trials]
                        guided = [trial for trial in details if trial["strategy"] == "guided"]
                        require(report["status"] == "completed" and len(trials) == 4, "Compiler workflow did not complete its candidate budget")
                        require(any(trial["proposal_type"] == "llm" for trial in guided), "No live model proposal was accepted")
                        require(all(trial["proposal_type"] in {"llm", "llm_fallback"} for trial in guided), "Unexpected guided proposal type")
                        require(any(trial["status"] == "valid" for trial in guided), "No live guided proposal reached correctness and timing validation")
                        require(svc.report(run_id) == report, "Saved report differs from completed report")
                        for artifact in report["artifacts"]:
                            require((config.RUNS_DIR / run_id / artifact).is_file(), "Missing report artifact")
                        record(f"compiler_{orchestrator}_{benchmark}", status="passed", trial_count=len(trials),
                               guided_proposals=[{"actions": trial["action_ids"], "status": trial["status"],
                                                  "proposal_type": trial["proposal_type"]} for trial in guided],
                               artifacts=len(report["artifacts"]))

                os.environ["AUTOTUNER_ORCHESTRATOR"] = "plain"
                cli_run = subprocess.run([sys.executable, "-m", "autotuner", "tune", "--benchmark", "dot",
                    "--budget", "1", "--size", "64", "--repeat", "1", "--llm-mode", "openai"],
                    capture_output=True, text=True, timeout=180, cwd=ROOT)
                require(cli_run.returncode == 0 and '"status": "completed"' in cli_run.stdout, "Live CLI experiment failed")
                record("live_cli", status="passed")

                from fastapi.testclient import TestClient
                from autotuner.api import app
                with TestClient(app) as client:
                    health = client.get("/v1/health").json()
                    require(health["toolchain_ready"] and health["llm"]["mode"] == "openai", "API health did not load OpenAI configuration")
                    created = client.post("/v1/experiments", json={"benchmark_id": "dot", "mode": "quick",
                        "budget_per_strategy": 1, "tuning_input": {"size": 64, "seed": 1, "repeat": 1}})
                    require(created.status_code == 202, "API did not accept a live experiment")
                    run_id = created.json()["id"]
                    deadline = time.monotonic() + 180
                    while time.monotonic() < deadline:
                        run = client.get(f"/v1/experiments/{run_id}").json()
                        if run["status"] in {"completed", "failed", "cancelled"}:
                            break
                        time.sleep(0.2)
                    require(run["status"] == "completed", "API worker failed the live experiment")
                    trials = client.get(f"/v1/experiments/{run_id}/trials").json()["items"]
                    trial = client.get(f"/v1/experiments/{run_id}/trials/{trials[0]['id']}").json()
                    require(trial["proposal_type"] in {"llm", "llm_fallback"}, "Unexpected API proposal type")
                    for artifact in ("summary.json", "trials.csv", "samples.csv", "environment.json"):
                        require(client.get(f"/v1/experiments/{run_id}/artifacts/{artifact}").status_code == 200, "API artifact download failed")
                    record("live_api_worker_and_downloads", status="passed", model=run["environment"]["llm"]["model"],
                           proposal_type=trial["proposal_type"])
            result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        # Print our own safe checks only; SDK/transport tracebacks can include request data.
        result["failure"] = str(exc) if isinstance(exc, CheckFailed) else type(exc).__name__
        print(json.dumps({"status": "failed", "reason": result["failure"]}), flush=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
