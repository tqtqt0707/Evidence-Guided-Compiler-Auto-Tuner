"""Research-lab HTTP routes. Uses the existing compiler ownership lock."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from .research import capabilities, validate_request, CUSTOM_EXAMPLE, REFERENCE_EXAMPLE
from .research_analysis import choose_objective

router=APIRouter(prefix="/v1/research",tags=["Research lab"])
class ResearchBody(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    benchmark_id:str="pipeline"
    source:str|None=Field(default=None,max_length=16384)
    reference_source:str|None=Field(default=None,max_length=16384)
    trusted_local_code:bool=False
    actions:list[str]=Field(default_factory=lambda:["licm","loop-fusion","slp-vectorizer"],min_length=1,max_length=4)
    size:int=Field(default=250000,ge=1,le=16000000)
    seed:int=Field(default=29,ge=0,le=9007199254740991)
    repeat:int=Field(default=8,ge=1,le=100)
    rounds:int=Field(default=10,ge=3,le=15)
    budget:int=Field(default=4,ge=1,le=8)
    fuzz_cases:int=Field(default=12,ge=1,le=64)
    memory_role:Literal["train","evaluate"]="evaluate"
    grid_sizes:list[int]|None=Field(default=None,min_length=1,max_length=4)
    grid_seeds:list[int]=Field(default_factory=lambda:[101,211],min_length=1,max_length=3)

class ObjectiveBody(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    rows:list[dict]=Field(max_length=200)
    objective:Literal["fastest","smallest","balanced"]="fastest"
    max_text_bytes:int|None=Field(default=None,ge=0)
    max_compile_ms:float|None=Field(default=None,ge=0)
    max_slowdown_percent:float=Field(default=100,ge=0)
    baseline_ns:float|None=Field(default=None,gt=0)

def error(status,code,message):
    return JSONResponse(status_code=status,content={"error":{"code":code,"message":message}})

@router.get("/capabilities")
def status():
    return {**capabilities(),"custom_example":CUSTOM_EXAMPLE,"reference_example":REFERENCE_EXAMPLE}

@router.get("/jobs")
def jobs(request:Request):
    base=request.app.state.research.base
    files=sorted(base.glob("*/state.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:50]
    import json
    return {"items":[json.loads(p.read_text()) for p in files]}

@router.post("/jobs",status_code=202)
def create(body:ResearchBody,request:Request):
    if request.app.state.closing:return error(503,"SERVICE_STOPPING","The service is shutting down")
    try:data=validate_request(body.model_dump(exclude_none=True))
    except ValueError as exc:return error(422,"INVALID_REQUEST",str(exc))
    if not capabilities()["compiler_available"]:return error(503,"TOOLCHAIN_UNAVAILABLE","Run the Research Lab inside x86-64 Ubuntu with LLVM 21")
    lock=request.app.state.inspection_lock
    if not lock.acquire(blocking=False):return error(409,"COMPILER_BUSY","Wait for the active inspection or research job")
    try:
        current=request.app.state.service.store.one("SELECT experiment_id FROM worker_slot WHERE singleton_id=1")
        if current and current["experiment_id"]:
            lock.release();return error(409,"COMPILER_BUSY","Wait for the active tuning experiment")
        return request.app.state.research.submit(data,lock.release)
    except Exception:
        lock.release();raise

@router.get("/jobs/{job_id}")
def job(job_id:UUID,request:Request):
    try:return request.app.state.research.state(str(job_id))
    except KeyError:return error(404,"NOT_FOUND","Research job not found")

@router.get("/jobs/{job_id}/report")
def report(job_id:UUID,request:Request):
    try:return request.app.state.research.report(str(job_id))
    except KeyError:return error(404,"REPORT_UNAVAILABLE","This job has no completed report")

@router.get("/jobs/{job_id}/download")
def download(job_id:UUID,request:Request):
    path=request.app.state.research.base/str(job_id)/"report.json"
    if not path.is_file():return error(404,"NOT_FOUND","Research report not found")
    return FileResponse(path,media_type="application/json",filename="research-"+str(job_id)+".json")

@router.post("/objectives")
def objectives(body:ObjectiveBody):
    try:return choose_objective(**body.model_dump())
    except (ValueError,TypeError) as exc:return error(422,"INVALID_OBJECTIVE",str(exc))
