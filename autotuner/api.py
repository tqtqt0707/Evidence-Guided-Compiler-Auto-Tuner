from __future__ import annotations

import json
import queue
import threading
import asyncio
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import ROOT, RUNS_DIR, WEB
from .models import ExperimentRequest
from .llm import OpenAISettings
from .service import TuningService
from .inspection import InspectionError, inspect_source


class WorkloadBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    size: int = Field(ge=1)
    seed: int = Field(ge=0, le=2**64 - 1)
    repeat: int = Field(ge=1, le=100)


class ExperimentCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    benchmark_id: str
    mode: Literal["quick", "full"]
    tuning_input: WorkloadBody | None = None
    holdout_input: WorkloadBody | None = None
    strategies: list[Literal["guided", "random"]] | None = None
    budget_per_strategy: int | None = Field(default=None, ge=1, le=20)
    max_sequence_length: int | None = Field(default=None, ge=1, le=4)
    search_repetitions: int | None = Field(default=None, ge=3, le=10)
    confirmation_rounds: int | None = Field(default=None, ge=3, le=15)
    llm_mode: Literal["auto", "off", "openai"] | None = None


class InspectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(min_length=1, max_length=65536)
    explain: bool = False
    max_passes: int = Field(default=4, ge=1, le=4)
    action_ids: list[str] | None = Field(default=None, min_length=1, max_length=4)
    llm_mode: Literal["auto", "off", "openai"] | None = None


def error_response(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details or {}}})


def service(request: Request) -> TuningService:
    return request.app.state.service


def _worker(app: FastAPI) -> None:
    jobs = app.state.jobs
    while True:
        experiment_id = jobs.get()
        try:
            if experiment_id is None:
                return
            app.state.service.run_experiment(experiment_id)
        except Exception:
            # The service persists the terminal error and a traceback artifact.
            pass
        finally:
            jobs.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = TuningService(reconcile=True)
    app.state.jobs = queue.Queue(maxsize=1)
    app.state.inspection_lock = threading.Lock()
    worker = threading.Thread(target=_worker, args=(app,), name="autotuner-worker", daemon=True)
    worker.start()
    app.state.closing = False
    try:
        yield
    finally:
        app.state.closing = True
        # Finish accepted work before stopping the thread on a graceful shutdown.
        await asyncio.to_thread(app.state.jobs.join)
        app.state.jobs.put_nowait(None)
        await asyncio.to_thread(worker.join)


app = FastAPI(title="Evidence-Guided LLVM Pass Tuner", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB), name="static")
app.mount("/examples", StaticFiles(directory=ROOT / "examples", check_dir=False), name="examples")


@app.exception_handler(RequestValidationError)
async def request_error(_request: Request, exc: RequestValidationError):
    return error_response(422, "INVALID_REQUEST", "Request validation failed", {"issues": [{"field": ".".join(str(x) for x in item["loc"]), "message": item["msg"]} for item in exc.errors()]})


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return error_response(exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "INVALID_REQUEST", str(exc.detail))


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


@app.get("/v1/health")
def health(request: Request):
    current = service(request).store.one("SELECT experiment_id FROM worker_slot WHERE singleton_id=1")
    reason = None
    try:
        service(request).preflight()
        ready = True
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        ready, reason = False, str(exc)[:300]
    try:
        llm = OpenAISettings.from_env().public_status()
    except ValueError as exc:
        llm = {"mode": "unavailable", "error": str(exc)}
    return {"status": "ok" if ready else "degraded", "api_version": "v1", "worker": "busy" if current and current["experiment_id"] else "idle", "database": "ok", "toolchain_ready": ready, "toolchain_error": reason, "llm": llm}


@app.get("/v1/benchmarks")
def benchmarks(request: Request):
    items = []
    for entry in service(request).list_benchmarks():
        items.append({key: value for key, value in entry.items() if key != "source_path"})
    return {"items": items}


@app.get("/v1/actions")
def actions(request: Request, include_unavailable: bool = False):
    try:
        return service(request).list_actions(include_unavailable)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return error_response(503, "TOOLCHAIN_UNAVAILABLE", str(exc)[:300])


@app.get("/v1/experiments")
def experiments(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), status: Literal["queued", "running", "completed", "failed", "interrupted", "cancelled"] | None = None):
    return service(request).list_experiments(limit=limit, offset=offset, status=status)


@app.post("/v1/experiments", status_code=202)
def create_experiment(body: ExperimentCreateBody, request: Request, response: Response):
    if request.app.state.closing:
        return error_response(503, "SERVICE_STOPPING", "The service is shutting down")
    data = body.model_dump(exclude_none=True)
    try:
        ExperimentRequest.parse(data)
        if not request.app.state.inspection_lock.acquire(blocking=False):
            return error_response(409, "INSPECTION_IN_PROGRESS", "Wait for the code inspection to finish")
        try:
            experiment_id = service(request).create_experiment(data)
        finally:
            request.app.state.inspection_lock.release()
    except ValueError as exc:
        return error_response(422, "INVALID_REQUEST", str(exc))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc)
        if message.startswith("RUN_IN_PROGRESS:"):
            return error_response(409, "RUN_IN_PROGRESS", "An experiment is already active", {"experiment_id": message.split(":", 1)[1]})
        return error_response(503, "TOOLCHAIN_UNAVAILABLE", message[:300])
    return enqueue_experiment(experiment_id, request, response)


def enqueue_experiment(experiment_id: str, request: Request, response: Response):
    try:
        request.app.state.jobs.put_nowait(experiment_id)
    except queue.Full:
        service(request).store.update_experiment(experiment_id, status="failed", stage="failed", error_code="QUEUE_UNAVAILABLE", error_message="Worker queue is unavailable")
        service(request).store.release(experiment_id)
        return error_response(503, "QUEUE_UNAVAILABLE", "Worker queue is unavailable")
    location = f"/v1/experiments/{experiment_id}"
    response.headers["Location"] = location
    row = service(request).get_experiment(experiment_id)
    return {"id": experiment_id, "status": "queued", "mode": row["mode"], "benchmark_id": row["benchmark_id"],
            "created_at": row["created_at"], "progress": row["progress"],
            "links": {"self": location, "trials": location + "/trials", "report": location + "/report"}}


@app.post("/v1/experiments/{experiment_id}/cancel", status_code=202)
def cancel_experiment(experiment_id: UUID, request: Request):
    try:
        row = service(request).cancel_experiment(str(experiment_id))
    except KeyError:
        return error_response(404, "NOT_FOUND", "Experiment not found")
    except RuntimeError:
        return error_response(409, "EXPERIMENT_NOT_ACTIVE", "Only queued or running experiments can be cancelled")
    return {"id": row["id"], "status": row["status"], "cancellation_requested": True}


@app.post("/v1/experiments/{experiment_id}/retry", status_code=202)
def retry_experiment(experiment_id: UUID, request: Request, response: Response):
    if request.app.state.closing:
        return error_response(503, "SERVICE_STOPPING", "The service is shutting down")
    try:
        if not request.app.state.inspection_lock.acquire(blocking=False):
            return error_response(409, "INSPECTION_IN_PROGRESS", "Wait for the code inspection to finish")
        try:
            new_id = service(request).retry_experiment(str(experiment_id))
        finally:
            request.app.state.inspection_lock.release()
    except KeyError:
        return error_response(404, "NOT_FOUND", "Experiment not found")
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc)
        if message == "EXPERIMENT_STILL_ACTIVE" or message.startswith("RUN_IN_PROGRESS:"):
            return error_response(409, "RUN_IN_PROGRESS", "Wait for the active experiment to finish")
        return error_response(503, "TOOLCHAIN_UNAVAILABLE", message[:300])
    return enqueue_experiment(new_id, request, response)


@app.post("/v1/inspections")
def inspect_code(body: InspectionBody, request: Request):
    if request.app.state.closing:
        return error_response(503, "SERVICE_STOPPING", "The service is shutting down")
    if not request.app.state.inspection_lock.acquire(blocking=False):
        return error_response(409, "INSPECTION_IN_PROGRESS", "Wait for the current code inspection to finish")
    try:
        current = service(request).store.one("SELECT experiment_id FROM worker_slot WHERE singleton_id=1")
        if current and current["experiment_id"]:
            return error_response(409, "RUN_IN_PROGRESS", "Wait for the tuning experiment to finish before inspecting code")
        readiness = service(request).preflight()
        return inspect_source(body.source, service(request)._toolchain, readiness["catalog"],
                              max_passes=body.max_passes, action_ids=body.action_ids, llm_mode=body.llm_mode,
                              explain=body.explain)
    except InspectionError as exc:
        return error_response(exc.status_code, exc.code, str(exc), {"diagnostics": exc.diagnostics})
    except ValueError as exc:
        return error_response(422, "INVALID_REQUEST", str(exc))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return error_response(503, "INSPECTION_UNAVAILABLE", str(exc)[:500])
    finally:
        request.app.state.inspection_lock.release()


@app.get("/v1/experiments/{experiment_id}")
def experiment(experiment_id: UUID, request: Request):
    row = service(request).get_experiment(str(experiment_id))
    if not row:
        raise HTTPException(404, "Experiment not found")
    return {key: value for key, value in row.items() if key != "catalog"}


@app.get("/v1/experiments/{experiment_id}/trials")
def trials(experiment_id: UUID, request: Request, strategy: Literal["guided", "random"] | None = None,
           status: str | None = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
           sort: Literal["ordinal", "runtime_asc"] = "ordinal"):
    if not service(request).get_experiment(str(experiment_id)):
        raise HTTPException(404, "Experiment not found")
    allowed = {"proposed", "compiling", "valid", "pass_parse_failed", "compile_failed", "ir_verification_failed", "execution_failed", "timeout", "checksum_mismatch", "no_ir_change", "cancelled", "interrupted"}
    if status and status not in allowed:
        return error_response(422, "INVALID_REQUEST", "Unknown trial status")
    return service(request).list_trials(str(experiment_id), strategy=strategy, status=status, limit=limit, offset=offset, sort=sort)


@app.get("/v1/experiments/{experiment_id}/trials/{trial_id}")
def trial(experiment_id: UUID, trial_id: UUID, request: Request):
    row = service(request).get_trial(str(experiment_id), str(trial_id))
    if not row:
        raise HTTPException(404, "Trial not found in experiment")
    return row


@app.get("/v1/experiments/{experiment_id}/report")
def report(experiment_id: UUID, request: Request, response: Response):
    row = service(request).get_experiment(str(experiment_id))
    if not row:
        raise HTTPException(404, "Experiment not found")
    if row["status"] in {"queued", "running"}:
        response.status_code = 202
        response.headers["Retry-After"] = "3"
        return {"status": row["status"], "progress": row["progress"]}
    if row["status"] != "completed":
        return error_response(409, "REPORT_UNAVAILABLE", row["error_message"] or "Experiment did not complete", {"error_code": row["error_code"], "partial_trials": row["progress"]["attempted_candidates"]})
    return service(request).report(str(experiment_id))


@app.get("/v1/experiments/{experiment_id}/artifacts/{name}")
def artifact(experiment_id: UUID, name: str, request: Request):
    allowed = {"summary.json", "trials.csv", "samples.csv", "environment.json", "search.svg", "pareto.svg"}
    if name not in allowed or "/" in name or "\\" in name:
        raise HTTPException(404, "Artifact not found")
    row = service(request).store.one("SELECT * FROM artifacts WHERE experiment_id=? AND name=?", (str(experiment_id), name))
    if not row:
        raise HTTPException(404, "Artifact not found")
    target = RUNS_DIR / str(experiment_id) / row["relative_path"]
    if not target.is_file() or target.resolve().parent != (RUNS_DIR / str(experiment_id)).resolve():
        raise HTTPException(404, "Artifact not found")
    return FileResponse(target, media_type=row["media_type"], headers={"ETag": row["sha256"]})
