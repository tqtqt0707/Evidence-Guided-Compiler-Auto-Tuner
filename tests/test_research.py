from pathlib import Path
import math
import pytest
from autotuner.research_analysis import (sequence_plan,compare_samples,parse_remarks,control_flow,choose_objective,ExperienceMemory,AdaptivePolicy)
from autotuner.research import validate_request,ResearchRunner,CUSTOM_EXAMPLE,REFERENCE_EXAMPLE
from autotuner import config

def test_plan_contains_matched_controls_ablation_and_reordering():
    plan=sequence_plan(["licm","gvn","dce"])
    assert plan[0]["actions"]==[]
    assert any(p["role"]=="remove:gvn" and p["actions"]==["licm","dce"] for p in plan)
    assert len({tuple(p["actions"]) for p in plan})==len(plan)
    assert sum(p["role"]=="reorder" for p in plan)==5
    with pytest.raises(ValueError):sequence_plan(["dce","dce"])
    with pytest.raises(ValueError):sequence_plan(["shell-command"])

def test_paired_comparison_uses_all_rounds_and_no_claim_for_small_sample():
    assert compare_samples([80]*10,[100]*10)["label"]=="win"
    assert compare_samples([120]*10,[100]*10)["label"]=="loss"
    assert compare_samples([80]*3,[100]*3)["label"]=="demo_only"
    assert compare_samples([80]*6+[120]*4,[100]*10)["label"]=="inconclusive"
    for a,b in [([0],[1]),([float("nan")],[1]),([1,2],[1])]:
        with pytest.raises(ValueError):compare_samples(a,b)
    assert compare_samples([80]*10,[100]*10)["bootstrap_interval_percent"]==[pytest.approx(20),pytest.approx(20)]

def test_real_llvm_remark_tags_and_source_locations_are_preserved():
    rows=parse_remarks("--- !Missed\nPass: loop-vectorize\nName: CantVectorize\nFunction: kernel\nDebugLoc: { File: source.c, Line: 7, Column: 3 }\nArgs:\n  - String: 'loop not vectorized'\n")
    assert rows[0]["kind"]=="Missed" and rows[0]["line"]==7
    assert rows[0]["message"]=="loop not vectorized"
    assert parse_remarks("")==[]
    with pytest.raises(Exception):parse_remarks("!!python/object/apply:os.system ['echo unsafe']")

IR="""define i32 @f(i32 %x) {
entry:
  br label %loop
loop:
  %a = phi i32 [0, %entry], [1, %loop]
  %b = icmp eq i32 %x, 0
  br i1 %b, label %exit, label %loop, !dbg !3
exit:
  ret i32 %a
}
!3 = !DILocation(line: 9, column: 2, scope: !4)
"""
def test_cfg_extracts_terminators_not_phi_references_and_finds_back_edges():
    f=control_flow(IR)["functions"][0]
    assert len(f["blocks"])==3 and len(f["edges"])==3
    assert next(e for e in f["edges"] if e["from"]=="loop" and e["to"]=="loop")["back_edge"]
    assert f["blocks"][1]["source_lines"]==[9]
    assert not any(e["from"]=="loop" and e["to"]=="entry" for e in f["edges"])

def test_cfg_handles_multiline_switch_and_quoted_names():
    text='define void @"my func"() {\nentry:\n switch i32 0, label %exit [\n i32 1, label %"case one"\n ]\n"case one":\n br label %exit\nexit:\n ret void\n}\n'
    f=control_flow(text)["functions"][0]
    assert f["name"]=="my func"
    assert {e["to"] for e in f["edges"]}=={"exit","case one"}

def rows():
    return [{"id":"fast","status":"valid","correctness_passed":True,"median_ns":10,"text_bytes":200,"compile_ms":10},
            {"id":"small","status":"valid","correctness_passed":True,"median_ns":12,"text_bytes":100,"compile_ms":8},
            {"id":"wrong","status":"valid","correctness_passed":False,"median_ns":1,"text_bytes":1,"compile_ms":1}]
def test_constraints_change_winner_and_reject_incorrect_and_infeasible_candidates():
    assert choose_objective(rows())["selected"]["id"]=="fast"
    assert choose_objective(rows(),"smallest")["selected"]["id"]=="small"
    assert choose_objective(rows(),max_text_bytes=150)["selected"]["id"]=="small"
    assert choose_objective(rows(),max_text_bytes=50)["selected"] is None
    assert choose_objective(rows(),baseline_ns=10,max_slowdown_percent=0)["selected"]["id"]=="fast"
    with pytest.raises(ValueError):choose_objective(rows(),max_compile_ms=math.inf)

def test_memory_never_trains_evaluation_or_crosses_source_and_context(tmp_path):
    m=ExperienceMemory(tmp_path/"memory.db")
    args=dict(context="llvm21",source_hash="programA",features={"loads":8},actions=["gvn"],reward=.3,record_id="a")
    assert not m.record(**args,role="evaluate")
    assert m.neighbors("llvm21","programB",{})==[]
    assert m.record(**args,role="train")
    assert m.neighbors("llvm21","programA",{})==[]
    assert m.neighbors("llvm22","programB",{})==[]
    assert len(m.neighbors("llvm21","programB",{}))==1

def test_adaptive_proposals_are_allowlisted_unseen_and_reproducible():
    a=AdaptivePolicy(["gvn","dce","licm"],{"loop_present":True},[],seed=9)
    b=AdaptivePolicy(["gvn","dce","licm"],{"loop_present":True},[],seed=9)
    seen=set()
    for _ in range(6):
        x=a.propose(seen,[]);y=b.propose(seen,[])
        assert x==y and x not in seen and set(x)<={"gvn","dce","licm"}
        seen.add(x)

def test_custom_contract_requires_explicit_trust_reference_and_bounded_inputs():
    with pytest.raises(ValueError):validate_request({"benchmark_id":"custom","source":CUSTOM_EXAMPLE})
    d=validate_request({"benchmark_id":"custom","source":CUSTOM_EXAMPLE,"reference_source":REFERENCE_EXAMPLE,"trusted_local_code":True})
    assert d["rounds"]==10
    for req in [{"rounds":True},{"grid_sizes":[1,1]},{"budget":100},{"benchmark_id":"matmul","size":999}]:
        with pytest.raises(ValueError):validate_request(req)

def test_research_api_can_view_and_validate_without_a_compiler(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from autotuner.api import app
    from autotuner import research_api
    monkeypatch.setattr(research_api,"capabilities",lambda:{"compiler_available":False})
    with TestClient(app) as client:
        assert client.get("/research").status_code==200
        assert client.get("/v1/research/capabilities").status_code==200
        assert client.get("/v1/research/jobs").json()=={"items":[]}
        assert client.post("/v1/research/jobs",json={}).status_code==503
        result=client.post("/v1/research/objectives",json={"rows":rows(),"objective":"smallest"})
        assert result.status_code==200 and result.json()["selected"]["id"]=="small"
        assert client.get("/v1/research/jobs/00000000-0000-0000-0000-000000000000").status_code==404
        assert client.post("/v1/research/jobs",json={"rounds":"10"}).status_code==422
