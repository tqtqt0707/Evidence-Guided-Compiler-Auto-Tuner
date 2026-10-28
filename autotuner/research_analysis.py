"""Deterministic analysis primitives for the research lab. No compiler is required."""
from __future__ import annotations
import itertools
import math
import random
import re
import sqlite3
import statistics
from pathlib import Path
from .config import stable_hash
from .actions import BY_ID

def sequence_plan(actions):
    actions=tuple(actions)
    if not actions or len(actions)>4 or len(set(actions))!=len(actions) or set(actions)-set(BY_ID):
        raise ValueError("Choose 1-4 distinct allowlisted passes")
    plan=[{"role":"empty_control","actions":[]},{"role":"full_sequence","actions":list(actions)}]
    for i,action in enumerate(actions):
        seq=actions[:i]+actions[i+1:]
        if seq: plan.append({"role":"remove:"+action,"actions":list(seq)})
    for seq in itertools.permutations(actions):
        if seq!=actions: plan.append({"role":"reorder","actions":list(seq)})
    seen=set()
    return [p for p in plan if not (tuple(p["actions"]) in seen or seen.add(tuple(p["actions"])))]

def compare_samples(candidate,baseline,seed=29):
    if len(candidate)!=len(baseline) or not candidate or any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in [*candidate,*baseline]):
        raise ValueError("Comparisons require matched, finite, positive paired timings")
    n=len(candidate);cm=statistics.median(candidate);bm=statistics.median(baseline)
    reduction=100*(1-cm/bm);wins=sum(c<b for c,b in zip(candidate,baseline));losses=sum(c>b for c,b in zip(candidate,baseline))
    rng=random.Random(seed)
    boot=[]
    for _ in range(500):
        ix=[rng.randrange(n) for _ in range(n)]
        boot.append(100*(1-statistics.median(candidate[i] for i in ix)/statistics.median(baseline[i] for i in ix)))
    boot.sort()
    label="demo_only" if n<10 else "win" if reduction>=2 and wins>=math.ceil(.8*n) else "loss" if reduction<=-2 and losses>=math.ceil(.8*n) else "inconclusive"
    return {"label":label,"candidate_median_ns":cm,"baseline_median_ns":bm,"candidate_mad_ns":statistics.median(abs(x-cm) for x in candidate),
            "baseline_mad_ns":statistics.median(abs(x-bm) for x in baseline),"runtime_reduction_percent":reduction,"paired_wins":wins,"paired_rounds":n,
            "bootstrap_interval_percent":[boot[12],boot[487]],"interval_note":"Exploratory paired bootstrap interval; not a multiple-comparison-adjusted significance test."}

def parse_remarks(text):
    import yaml
    if len(text)>4_000_000: raise ValueError("Remark output exceeds the 4 MB limit")
    class Loader(yaml.SafeLoader): pass
    def tagged(loader,suffix,node):
        data=loader.construct_mapping(node,deep=True);data["kind"]=suffix
        return data
    Loader.add_multi_constructor("!",tagged)
    rows=[]
    for data in yaml.load_all(text,Loader=Loader):
        if data is None: continue
        if not isinstance(data,dict): raise ValueError("Invalid compiler remark document")
        if len(rows)>=1000: break
        loc=data.get("DebugLoc") or {}
        args=data.get("Args") or []
        rows.append({"kind":str(data.get("kind","Analysis")),"pass":str(data.get("Pass","")),"name":str(data.get("Name","")),
                     "function":str(data.get("Function","")),"line":loc.get("Line"),"column":loc.get("Column"),
                     "message":"".join(str(v) for arg in args if isinstance(arg,dict) for k,v in arg.items() if k!="DebugLoc")[:4000]})
    return rows

def control_flow(ir):
    """Parse actual LLVM labels and terminators; never infer edges from prose."""
    locations={m.group(1):int(m.group(2)) for m in re.finditer(r'(?m)^!(\d+)\s*=\s*!DILocation\(line:\s*(\d+)',ir)}
    functions=[];current=None;block=None;pending=""
    name_re=r'(?:"([^"]+)"|([-\w.$]+))'
    def add_edges(text):
        if block is None: return
        # Terminators can span multiple lines (switch/invoke/callbr).
        for m in re.finditer(r'\blabel\s+%'+name_re,text):
            dest=m.group(1) or m.group(2)
            if dest not in block["successors"]:block["successors"].append(dest)
    for raw in ir.splitlines():
        m=re.match(r'^define\b.*?@'+name_re+r'\(',raw)
        if m:
            current={"name":m.group(1) or m.group(2),"blocks":[]};functions.append(current);block=None;pending="";continue
        if current is None:continue
        if raw.strip()=="}":current=None;block=None;pending="";continue
        m=re.match(r'^\s*'+name_re+r':(?:\s*;.*)?\s*$',raw)
        if m:
            block={"id":m.group(1) or m.group(2),"instructions":[],"source_lines":[],"successors":[]}
            current["blocks"].append(block);pending="";continue
        line=raw.strip()
        if not line or line.startswith(";"):continue
        if block is None:
            block={"id":"entry","instructions":[],"source_lines":[],"successors":[]};current["blocks"].append(block)
        block["instructions"].append(line)
        for dbg in re.findall(r'!dbg\s+!(\d+)',line):
            if dbg in locations and locations[dbg] not in block["source_lines"]:block["source_lines"].append(locations[dbg])
        if re.match(r'^(?:%[^=]+=\s*)?(br|switch|indirectbr|invoke|callbr|catchswitch|catchret|cleanupret)\b',line):
            pending=line;add_edges(line)
        elif pending:add_edges(line)
    for f in functions:
        ids={b["id"] for b in f["blocks"]};pred={i:set() for i in ids}
        for b in f["blocks"]:
            for dest in b["successors"]:
                if dest in ids:pred[dest].add(b["id"])
        if not ids:continue
        entry=f["blocks"][0]["id"];reachable={entry};stack=[entry]
        blocks={b["id"]:b for b in f["blocks"]}
        while stack:
            for dest in blocks[stack.pop()]["successors"]:
                if dest in ids and dest not in reachable:reachable.add(dest);stack.append(dest)
        dom={i:({i} if i==entry or i not in reachable else set(reachable)) for i in ids}
        changed=True
        while changed:
            changed=False
            for i in reachable-{entry}:
                parents=pred[i]&reachable
                d={i}|(set.intersection(*(dom[p] for p in parents)) if parents else set())
                if d!=dom[i]:dom[i]=d;changed=True
        f["edges"]=[{"from":b["id"],"to":dest,"back_edge":dest in dom[b["id"]]} for b in f["blocks"] for dest in b["successors"] if dest in ids]
        for b in f["blocks"]:b["instruction_count"]=len(b["instructions"]);b["reachable"]=b["id"] in reachable
    return {"functions":functions,"provenance":"Parsed LLVM IR labels, terminators and debug locations","source_mapping":"Only emitted !DILocation records are used"}

def choose_objective(rows,objective="fastest",max_text_bytes=None,max_compile_ms=None,max_slowdown_percent=100,baseline_ns=None):
    if objective not in {"fastest","smallest","balanced"}:raise ValueError("Unknown objective")
    for value in (max_text_bytes,max_compile_ms,max_slowdown_percent,baseline_ns):
        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):raise ValueError("Constraints must be finite and nonnegative")
    eligible=[]
    for r in rows:
        vals=[r.get(k) for k in ("median_ns","text_bytes","compile_ms")]
        if r.get("status")!="valid" or r.get("correctness_passed") is not True or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in vals) or vals[0]<=0:continue
        if max_text_bytes is not None and r["text_bytes"]>max_text_bytes:continue
        if max_compile_ms is not None and r["compile_ms"]>max_compile_ms:continue
        if baseline_ns and r["median_ns"]>baseline_ns*(1+max_slowdown_percent/100):continue
        eligible.append(r)
    if not eligible:return {"selected":None,"frontier":[],"feasible_count":0,"reason":"No correctness-checked candidate satisfies these constraints"}
    keys=("median_ns","text_bytes","compile_ms")
    frontier=[r for r in eligible if not any(all(o[k]<=r[k] for k in keys) and any(o[k]<r[k] for k in keys) for o in eligible)]
    minimum={k:max(1,min(r[k] for r in eligible)) for k in keys}
    if objective=="balanced":key=lambda r:sum(r[k]/minimum[k] for k in keys)
    else:key=lambda r:(r["median_ns"],r["text_bytes"]) if objective=="fastest" else (r["text_bytes"],r["median_ns"])
    return {"selected":min(eligible,key=key),"frontier":frontier,"feasible_count":len(eligible),"objective":objective,
            "score_note":"Balanced minimizes the sum of runtime, text size and build cost normalized to feasible minima."}

FEATURE_KEYS=("instruction_count","loads","stores","conditional_branches","phi_nodes")
class ExperienceMemory:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS experience (id TEXT PRIMARY KEY, context TEXT, source_hash TEXT, features TEXT, actions TEXT, reward REAL)")
    def connect(self):return sqlite3.connect(self.path,timeout=20)
    def record(self,*,context,source_hash,features,actions,reward,role,record_id):
        import json
        if role!="train":return False
        if not math.isfinite(reward):raise ValueError("Nonfinite reward")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO experience VALUES(?,?,?,?,?,?)",(record_id,context,source_hash,json.dumps(features),json.dumps(actions),max(-1,min(1,reward))))
        return True
    def neighbors(self,context,source_hash,features):
        import json
        with self.connect() as db:rows=db.execute("SELECT source_hash,features,actions,reward FROM experience WHERE context=? AND source_hash<>?",(context,source_hash)).fetchall()
        ranked=[]
        for source,raw,actions,reward in rows:
            old=json.loads(raw)
            distance=sum(abs(math.log1p(max(0,features.get(k,0)))-math.log1p(max(0,old.get(k,0)))) for k in FEATURE_KEYS)
            ranked.append({"source_hash":source,"actions":json.loads(actions),"reward":reward,"distance":distance})
        return sorted(ranked,key=lambda r:r["distance"])[:100]

class AdaptivePolicy:
    """Feature-neighbor reward prior plus an explicit exploration allocation."""
    def __init__(self,actions,features,memory,seed=29,max_length=3):
        self.actions=tuple(actions);self.features=features;self.memory=memory;self.rng=random.Random(seed);self.max_length=max_length
    def propose(self,seen,history):
        from .policies import unseen_random
        if self.rng.random()<.25:
            return unseen_random(self.actions,self.max_length,seen,self.rng)
        scored=[]
        for action in self.actions:
            evidence=[r for r in self.memory if action in r["actions"]]
            weight=sum(1/(1+r["distance"]) for r in evidence)
            reward=sum(r["reward"]/(1+r["distance"]) for r in evidence)/weight if weight else 0
            count=sum(action in r["actions"] for r in history)
            bonus=.15*math.sqrt(math.log(2+len(history))/(1+count))
            prior=.05 if self.features.get("loop_present") and action in ("licm","loop-vectorize","loop-fusion") else 0
            scored.append((reward+bonus+prior,action))
        ranked=[a for _,a in sorted(scored,key=lambda x:(-x[0],x[1]))]
        for length in range(1,min(self.max_length,len(ranked))+1):
            for seq in itertools.permutations(ranked[:6],length):
                if seq not in seen:return seq
        return unseen_random(self.actions,self.max_length,seen,self.rng)
