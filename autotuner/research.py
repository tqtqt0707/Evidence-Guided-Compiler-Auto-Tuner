"""Bounded Linux/LLVM research experiments, persisted separately from legacy runs."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import random
import re
import shutil
import statistics
import threading
import time
from uuid import uuid4

from . import config
from .actions import BY_ID, compose
from .features import summarize_ir
from .models import Workload
from .oracle import expected_small, OUTPUT_RE
from .policies import GuidedPolicy, RandomPolicy
from .registry import benchmark
from .research_analysis import (AdaptivePolicy, ExperienceMemory, choose_objective,
    compare_samples, control_flow, parse_remarks, sequence_plan)
from .toolchain import Toolchain, execute

CUSTOM_EXAMPLE = """#include <stdint.h>
uint64_t kernel(uint64_t size, uint64_t seed) {
    uint64_t total=0;
    for (uint64_t i=0;i<size;i++) total += ((i ^ seed) & 1023u);
    return total;
}
"""
REFERENCE_EXAMPLE = """#include <stdint.h>
uint64_t reference(uint64_t size, uint64_t seed) {
    uint64_t total=0, i=size;
    while (i) { --i; total += ((i ^ seed) & 1023u); }
    return total;
}
"""
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern uint64_t FUNCTION(uint64_t,uint64_t);
int main(int argc,char **argv) {
    uint64_t n=1,seed=0,repeat=1;
    for(int i=1;i+1<argc;i+=2) {
      uint64_t x=strtoull(argv[i+1],0,10);
      if(!strcmp(argv[i],"--size"))n=x;
      else if(!strcmp(argv[i],"--seed"))seed=x;
      else if(!strcmp(argv[i],"--repeat"))repeat=x;
      else return 2;
    }
    if(!n || n>16000000 || !repeat || repeat>100)return 2;
    uint64_t checksum=14695981039346656037ULL;
    for(uint64_t r=0;r<repeat;r++){
      uint64_t v=FUNCTION(n,seed+r);
      for(unsigned s=0;s<64;s+=8){checksum^=(v>>s)&255;checksum*=1099511628211ULL;}
    }
    printf("checksum:%016llx\n",(unsigned long long)checksum);
    return 0;
}
"""

def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp");tmp.write_text(json.dumps(data,indent=2,allow_nan=False),encoding="utf-8");tmp.replace(path)

def validate_request(data):
    allowed={"benchmark_id","source","reference_source","trusted_local_code","actions","size","seed","repeat","rounds","budget","fuzz_cases","memory_role","grid_sizes","grid_seeds"}
    if set(data)-allowed:raise ValueError("Unknown research request fields")
    defaults={"benchmark_id":"pipeline","actions":["licm","loop-fusion","slp-vectorizer"],"size":250000,"seed":29,"repeat":8,
              "rounds":10,"budget":4,"fuzz_cases":12,"memory_role":"evaluate","grid_seeds":[101,211]}
    req={**defaults,**data}
    sequence_plan(req["actions"])
    for name,lo,hi in [("size",1,16000000),("seed",0,2**53-1),("repeat",1,100),("rounds",3,15),("budget",1,8),("fuzz_cases",1,64)]:
        if type(req[name]) is not int or not lo<=req[name]<=hi:raise ValueError(f"{name} must be an integer from {lo} to {hi}")
    if req["memory_role"] not in {"train","evaluate"}:raise ValueError("memory_role must be train or evaluate")
    if req["benchmark_id"]=="custom":
        if req.get("trusted_local_code") is not True:raise ValueError("Confirm trusted local C code; this runner is not a security sandbox")
        for key,symbol in [("source","kernel"),("reference_source","reference")]:
            text=req.get(key,"")
            if not isinstance(text,str) or not 1<=len(text.encode())<=16384 or "\0" in text or not re.search(r"\b"+symbol+r"\s*\(",text):
                raise ValueError(f"{key} must define {symbol}(uint64_t size, uint64_t seed), at most 16 KiB")
    else:
        entry=benchmark(req["benchmark_id"])
        Workload.parse({"size":req["size"],"seed":req["seed"],"repeat":req["repeat"]},entry)
    maxsize=512 if req["benchmark_id"]=="matmul" else 16000000
    req.setdefault("grid_sizes",list(dict.fromkeys([max(1,req["size"]//2),min(maxsize,req["size"]*2)])))
    for key,lo,hi,maxlen in [("grid_sizes",1,maxsize,4),("grid_seeds",0,2**53-1,3)]:
        vals=req[key]
        if not isinstance(vals,list) or not 1<=len(vals)<=maxlen or len(set(vals))!=len(vals) or any(type(x) is not int or not lo<=x<=hi for x in vals):
            raise ValueError(f"Invalid {key}")
    return req

def capabilities():
    paths={name:shutil.which(exe) for name,exe in config.TOOL_NAMES.items()}
    return {"compiler_available":all(paths.values()) and os.name!="nt","required_platform":"x86-64 Linux / LLVM 21",
            "paths":paths,"alive2_available":bool(shutil.which("alive-tv")),"custom_execution":"Trusted local code only; CPU/address-space/file limits are not a security sandbox",
            "features":["pass-contribution","optimization-remarks","robustness","adaptive-memory","custom-C","constrained-objectives","CFG","differential-validation"]}

class ResearchRunner:
    def __init__(self,request,directory,progress=None):
        self.req=validate_request(request);self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.progress=progress or (lambda stage:None);self.tc=None;self.built={};self.deadline=time.monotonic()+900
        self.rng=random.Random(self.req["seed"]);self.commands=[]
    def stage(self,name):
        if time.monotonic()>self.deadline:raise RuntimeError("Research job exceeded its 15-minute wall-clock limit")
        self.progress(name)
    def checked(self,result):
        self.commands.append({"args":result.args,"returncode":result.returncode,"duration_ns":result.duration_ns,"timed_out":result.timed_out})
        if not result.ok:raise RuntimeError(("Command timed out: " if result.timed_out else "Compiler command failed: ")+result.stderr_excerpt[-1500:])
        return result
    def compile_source(self,source,output,level="-O0",extra=()):
        return self.checked(execute([self.tc.paths["clang"],*self.tc.clang_flags,level,*extra,str(source),"-o",str(output),*self.tc.link_flags],output.parent,output.name+"-compile"))
    def run(self,binary,workload,label,sanitized=False):
        self.stage("measure")
        args=[str(binary),*workload.args()]
        if self.req["benchmark_id"]=="custom":
            limiter=shutil.which("prlimit")
            if not limiter:raise RuntimeError("Trusted custom execution requires Linux prlimit")
            limits=["--cpu=3","--fsize=1048576","--nofile=64"]
            if not sanitized:limits.append("--as=536870912")
            args=[limiter,*limits,"--",*args]
        result=execute(args,self.directory/"execution",label,timeout=5)
        if not result.ok or not OUTPUT_RE.fullmatch(result.stdout):
            raise RuntimeError("Execution/checksum protocol failed: "+result.stderr_excerpt[-500:])
        return result.stdout,result.duration_ns
    def text_bytes(self,binary):
        size=shutil.which("llvm-size-21")
        if not size:return None
        result=execute([size,"--format=sysv",str(binary)],self.directory/"sizes",binary.name)
        if not result.ok:return None
        sections=re.findall(r"(?m)^\s*\.text\S*\s+(\d+)\s+",result.stdout)
        return sum(map(int,sections)) if sections else None
    def build(self,actions):
        key=tuple(actions)
        if key in self.built:return self.built[key]
        folder=self.directory/("variant-"+str(len(self.built)));folder.mkdir()
        bitcode=folder/"candidate.bc";binary=folder/"program"
        row={"id":folder.name,"actions":list(actions),"status":"failed","correctness_passed":False,"text_bytes":None,"median_ns":None,"compile_ms":None}
        self.built[key]=row
        try:
            elapsed=self.seed_ns
            if actions:
                remarks=folder/"remarks.yaml"
                result=self.checked(execute([self.tc.paths["opt"],"-verify-each",f"-passes={compose(actions)}",
                    "-pass-remarks-output="+str(remarks),"-pass-remarks=.*","-pass-remarks-missed=.*","-pass-remarks-analysis=.*",
                    str(self.seed),"-o",str(bitcode)],folder,"opt"))
                elapsed+=result.duration_ns
            else:shutil.copy2(self.seed,bitcode)
            for result in self.tc.object_and_link(bitcode,binary,folder,"build"):elapsed+=self.checked(result).duration_ns
            row.update(status="built",compile_ms=elapsed/1e6,text_bytes=self.text_bytes(binary),executable_bytes=binary.stat().st_size)
            ir=self.tc.textual_ir(bitcode,folder,"candidate-ir")
            (folder/"candidate.ll").write_text(ir,encoding="utf-8")
            row.update(ir_changed=config.stable_hash(ir)!=config.stable_hash(self.initial_ir),ir_instructions=summarize_ir(ir)["instruction_count"])
        except (RuntimeError,OSError) as exc:row["error"]=str(exc)
        return row
    def binary(self,row):return self.directory/row["id"]/"program"
    def expected(self,w,label):
        if self.req["benchmark_id"]=="custom":return self.run(self.reference,w,label+"-reference")[0]
        return self.run(self.o0,w,label+"-o0")[0]
    def paired(self,variants,w,rounds,label):
        expected=self.expected(w,label);samples={k:[] for k in variants}
        # One warmup per variant, excluded from the samples.
        for key,binary in variants.items():
            if self.run(binary,w,label+"-warm-"+key)[0]!=expected:raise RuntimeError("Warmup checksum mismatch: "+key)
        for r in range(rounds):
            names=list(variants);self.rng.shuffle(names)
            for key in names:
                output,ns=self.run(variants[key],w,f"{label}-{r}-{key}")
                if output!=expected:raise RuntimeError("Measured execution checksum mismatch: "+key)
                samples[key].append(ns)
        return samples
    def small_fixtures(self,row,cases):
        for i,w in enumerate(cases):
            expected=expected_small(self.req["benchmark_id"],w) if self.req["benchmark_id"]!="custom" else self.run(self.reference,w,f"fuzz-ref-{i}")[0]
            actual=self.run(self.binary(row),w,f"fuzz-{row['id']}-{i}")[0]
            if actual!=expected:return {"status":"failed","case":asdict(w),"expected":expected,"actual":actual}
        return {"status":"passed","cases":len(cases),"oracle":"Independent Python oracle" if self.req["benchmark_id"]!="custom" else "User-supplied reference compiled at -O0",
                "scope":"Checks only the generated inputs; not a proof of universal equivalence"}
    def source_checks(self,cases):
        sanitized=self.directory/"sanitized"
        try:
            self.compile_source(self.source,sanitized,"-O1",("-fsanitize=address,undefined","-fno-sanitize-recover=all","-fno-omit-frame-pointer"))
            for i,w in enumerate(cases[:3]):self.run(sanitized,w,f"sanitized-{i}",sanitized=True)
            return {"status":"passed","cases":min(3,len(cases)),"scope":"ASan/UBSan execution of the source build, separate from performance measurements"}
        except (RuntimeError,OSError) as exc:return {"status":"failed","diagnostic":str(exc)}
    def alive2(self,row):
        tool=shutil.which("alive-tv")
        if not tool:return {"status":"not_installed","scope":"Optional Alive2 integration; no formal validation claimed"}
        output=execute([tool,str(self.directory/"initial.ll"),str(self.directory/row["id"]/"candidate.ll")],self.directory/"alive2","validation",timeout=30)
        text=Path(output.stdout_path).read_text(errors="replace")+output.stderr_excerpt
        if output.timed_out:status="timeout"
        elif "Transformation doesn't verify" in text or "ERROR:" in text:status="counterexample_or_unsupported"
        elif output.ok and "Transformation seems to be correct!" in text and not re.search(r"(failed|unsupported|couldn't prove)",text,re.I):status="validated_supported_case"
        else:status="unknown"
        return {"status":status,"diagnostic":text[-6000:],"scope":"Translation validation only within this tool's supported semantics and bounds; no interprocedural guarantee"}
    def execute(self):
        self.stage("preflight")
        self.tc=Toolchain();environment=self.tc.environment()
        catalog=self.tc.validate_actions(Path(benchmark("dot")["source_path"]),self.directory/"preflight")
        available=tuple(x["id"] for x in catalog["items"] if x["available"])
        if set(self.req["actions"])-set(available):raise ValueError("One or more requested passes are unavailable")
        if self.req["benchmark_id"]=="custom":
            self.source=self.directory/"source.c";self.source.write_text(self.req["source"]+DRIVER.replace("FUNCTION","kernel"),encoding="utf-8")
            refsource=self.directory/"reference.c";refsource.write_text(self.req["reference_source"]+DRIVER.replace("FUNCTION","reference"),encoding="utf-8")
            self.reference=self.directory/"reference"
            self.compile_source(refsource,self.reference)
        else:
            self.source=self.directory/"source.c";shutil.copy2(benchmark(self.req["benchmark_id"])["source_path"],self.source)
        self.o0=self.directory/"o0";self.o3=self.directory/"o3"
        self.compile_source(self.source,self.o0);self.compile_source(self.source,self.o3,"-O3")
        self.seed=self.directory/"initial.bc"
        seedresult=self.checked(execute([self.tc.paths["clang"],*self.tc.clang_flags,"-O1","-gline-tables-only","-emit-llvm","-c",str(self.source),"-o",str(self.seed)],self.directory,"seed"))
        self.seed_ns=seedresult.duration_ns;self.initial_ir=self.tc.textual_ir(self.seed,self.directory,"seed-ir")
        (self.directory/"initial.ll").write_text(self.initial_ir,encoding="utf-8")
        features=summarize_ir(self.initial_ir)
        source_hash=config.sha256_file(self.source)
        context=config.stable_hash({"versions":environment["versions"],"target":environment["target_triple"],"flags":self.tc.clang_flags,"catalog":catalog["catalog_hash"]})
        memory=ExperienceMemory(config.DATA_DIR/"research"/"experience.db")
        neighbors=memory.neighbors(context,source_hash,features)
        w=Workload(self.req["size"],self.req["seed"],self.req["repeat"])
        expected=self.expected(w,"control")
        if self.run(self.o3,w,"check-o3")[0]!=expected or self.run(self.o0,w,"check-o0")[0]!=expected:raise RuntimeError("Reference/-O0/-O3 output disagreement")
        rng=random.Random(self.req["seed"]+913)
        cases=[Workload(1,0,1),Workload(4 if self.req["benchmark_id"]=="matmul" else 7,11,2)]
        cases += [Workload(rng.randint(1,32 if self.req["benchmark_id"]=="matmul" else 64),rng.randrange(2**32),rng.randint(1,3)) for _ in range(max(0,self.req["fuzz_cases"]-2))]
        self.stage("pass_contribution")
        plan=sequence_plan(self.req["actions"]);ablation=[]
        for spec in plan:
            row=self.build(spec["actions"]);row["role"]=spec["role"]
            if row["status"]=="built":
                check=self.small_fixtures(row,cases[:2])
                if check["status"]=="passed":row.update(status="valid",correctness_passed=True)
                else:row.update(status="checksum_mismatch",failure=check)
            ablation.append(row.copy())
        full=next((r for r in ablation if r["role"]=="full_sequence" and r["status"]=="valid"),None)
        if not full:raise RuntimeError("The requested full sequence failed compilation or correctness checks")
        valid={r["id"]:self.binary(r) for r in ablation if r["status"]=="valid"}
        paired=self.paired(valid,w,self.req["rounds"],"ablation")
        for r in ablation:
            if r["id"] in paired:
                r["samples_ns"]=paired[r["id"]];r["median_ns"]=statistics.median(r["samples_ns"])
                r["versus_full"]=compare_samples(r["samples_ns"],paired[full["id"]])
        self.stage("equal_budget_search")
        policies={"guided":GuidedPolicy(available,3,self.req["seed"],features),"random":RandomPolicy(available,3,self.req["seed"]),
                  "adaptive":AdaptivePolicy(available,features,neighbors,self.req["seed"])}
        seen={k:set() for k in policies};history={k:[] for k in policies};winners={}
        for ordinal in range(self.req["budget"]):
            for strategy,policy in policies.items():
                proposal=policy.propose(seen[strategy],history[strategy])
                seq=tuple(proposal if strategy=="adaptive" else proposal.action_ids) if proposal else ()
                if not seq:continue
                seen[strategy].add(seq)
                row=self.build(seq).copy();row.update(strategy=strategy,ordinal=ordinal+1,action_ids=list(seq))
                if row["status"] in {"built","valid"}:
                    check=self.small_fixtures(row,cases[:2])
                    if check["status"]=="passed":
                        samples=self.paired({"candidate":self.binary(row)},w,3,f"search-{strategy}-{ordinal}")["candidate"]
                        row.update(status="valid",correctness_passed=True,median_ns=statistics.median(samples),search_median_ns=statistics.median(samples),samples_ns=samples)
                    else:row.update(status="checksum_mismatch",failure=check)
                history[strategy].append(row)
        for strategy,rows in history.items():
            candidates=[r for r in rows if r["status"]=="valid"]
            if candidates:winners[strategy]=min(candidates,key=lambda r:r["median_ns"])
        self.stage("fresh_confirmation")
        variants={"o3":self.o3,**{k:self.binary(v) for k,v in winners.items()}}
        tuning=self.paired(variants,w,self.req["rounds"],"confirmation-tuning")
        maximum=512 if self.req["benchmark_id"]=="matmul" else 16000000
        # Holdout is fixed independently, never used to rank sequences or train memory.
        reserved_seeds=set(self.req["grid_seeds"]+[w.seed]);hold_seed=1009
        while hold_seed in reserved_seeds:hold_seed+=1
        hold=Workload(min(maximum,max(1,w.size+w.size//4)),hold_seed,w.repeat)
        holdout=self.paired(variants,hold,self.req["rounds"],"confirmation-holdout")
        comparisons={phase:{k:compare_samples(samples[k],samples["o3"]) for k in winners} for phase,samples in [("tuning",tuning),("holdout",holdout)]}
        self.stage("robustness_grid")
        grid=[]
        for size,seed in itertools.product(self.req["grid_sizes"],self.req["grid_seeds"]):
            cell=Workload(size,seed,w.repeat)
            if cell==hold:raise RuntimeError("Exploratory grid overlaps the reserved holdout")
            samples=self.paired({"guided":self.binary(winners.get("guided",full)),"o3":self.o3},cell,self.req["rounds"],f"grid-{size}-{seed}")
            grid.append({"workload":asdict(cell),"comparison":compare_samples(samples["guided"],samples["o3"]),"samples_ns":samples})
        self.stage("validation")
        fuzz={key:self.small_fixtures(row,cases) for key,row in winners.items()}
        sanitizer=self.source_checks(cases)
        for key,result in fuzz.items():
            if result["status"]!="passed":winners[key]["correctness_passed"]=False
        formal=self.alive2(winners.get("guided",full))
        self.stage("compiler_diagnostics")
        best=winners.get("guided",full);remark_file=self.directory/best["id"]/"remarks.yaml"
        try:remarks=parse_remarks(remark_file.read_text(encoding="utf-8")) if remark_file.exists() else []
        except ValueError as exc:remarks=[];remark_error=str(exc)
        final_ir=(self.directory/best["id"]/"candidate.ll").read_text()
        graph={"before":control_flow(self.initial_ir),"after":control_flow(final_ir)}
        candidates=[r for rows in history.values() for r in rows if r.get("median_ns")]
        for r in candidates:
            if r["strategy"] in fuzz and fuzz[r["strategy"]]["status"]!="passed":r["correctness_passed"]=False
        objectives=choose_objective(candidates,baseline_ns=statistics.median(tuning["o3"]))
        learned=0
        if sanitizer["status"]=="passed" and all(v["status"]=="passed" for v in fuzz.values()):
            for strategy,rows in history.items():
                for i,row in enumerate(rows):
                    if row.get("correctness_passed") and row.get("median_ns"):
                        learned+=memory.record(context=context,source_hash=source_hash,features=features,actions=row["actions"],
                            reward=1-row["median_ns"]/statistics.median(tuning["o3"]),role=self.req["memory_role"],record_id=self.directory.name+f"-{strategy}-{i}")
        result={"schema_version":1,"status":"completed","id":self.directory.name,"created_at":datetime.now(timezone.utc).isoformat(),
                "provenance":{"kind":"fresh_compiler_execution","environment":environment,"source_sha256":source_hash,"context_hash":context,
                              "measurement":"Whole-process wall time; randomized paired order; separate warmups; custom runs include prlimit launcher cost"},
                "request":self.req,"source":self.source.read_text(),"features":features,"ablation":ablation,"search":history,"winners":winners,
                "confirmation":{"tuning_input":asdict(w),"holdout_input":asdict(hold),"comparisons":comparisons,"tuning_samples_ns":tuning,"holdout_samples_ns":holdout},
                "robustness":{"cells":grid,"selection":"Winner frozen on tuning input; grid exploratory; reserved holdout not used for search, objective selection or training"},
                "learning":{"role":self.req["memory_role"],"eligible_records":len(neighbors),"source_programs":len({r["source_hash"] for r in neighbors}),
                            "new_training_records":learned,"method":"Feature-neighbor reward prior with exploration; equal proposal budgets",
                            "context_rule":"Exact compiler/target/flags/catalog match; same source hash excluded"},
                "remarks":{"items":remarks,"note":"Only remarks emitted by LLVM are shown; empty means no remark was emitted","parse_error":locals().get("remark_error")},
                "cfg":graph,"objective":objectives,"validation":{"differential":fuzz,"sanitizers":sanitizer,"alive2":formal},
                "limits":["Single-host exploratory measurements","Checks do not prove universal C equivalence","Alive2 is optional and may be unavailable",
                          "Trusted local custom execution is resource-limited, not a security sandbox"],
                "commands":self.commands}
        atomic_json(self.directory/"report.json",result);self.stage("completed")
        return result

class ResearchManager:
    def __init__(self,base=None):
        from concurrent.futures import ThreadPoolExecutor
        self.base=Path(base or config.DATA_DIR/"research");self.base.mkdir(parents=True,exist_ok=True)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="research");self.guard=threading.Lock()
    def submit(self,request,release):
        request=validate_request(request);job_id=str(uuid4());directory=self.base/job_id
        atomic_json(directory/"state.json",{"id":job_id,"status":"queued","stage":"queued"})
        def progress(stage):atomic_json(directory/"state.json",{"id":job_id,"status":"completed" if stage=="completed" else "running","stage":stage})
        def work():
            try:ResearchRunner(request,directory,progress).execute()
            except Exception as exc:atomic_json(directory/"state.json",{"id":job_id,"status":"failed","stage":"failed","error":str(exc)[:2000]})
            finally:release()
        self.pool.submit(work);return {"id":job_id,"status":"queued"}
    def state(self,job_id):
        path=self.base/job_id/"state.json"
        if not path.is_file():raise KeyError(job_id)
        return json.loads(path.read_text())
    def report(self,job_id):
        path=self.base/job_id/"report.json"
        if not path.is_file():raise KeyError(job_id)
        return json.loads(path.read_text())
    def close(self):self.pool.shutdown(wait=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True);parser.add_argument("--benchmark",default="pipeline")
    parser.add_argument("--size",type=int,default=250000);parser.add_argument("--repeat",type=int,default=8)
    parser.add_argument("--rounds",type=int,default=10);parser.add_argument("--budget",type=int,default=4)
    parser.add_argument("--memory-role",choices=["train","evaluate"],default="evaluate")
    args=parser.parse_args();out=Path(args.output)
    request={"benchmark_id":args.benchmark,"size":args.size,"repeat":args.repeat,"rounds":args.rounds,"budget":args.budget,"memory_role":args.memory_role}
    result=ResearchRunner(request,out.parent/("research-"+str(uuid4())),lambda x:print(x,flush=True)).execute()
    atomic_json(out,result);print(str(out))
if __name__=="__main__":main()
