"""Policy tests use synthetic timings; they make no compiler performance claims."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autotuner import config, deployment as d
from autotuner.research_analysis import compare_samples


def request(**extra):
    return dict(size=32, min_size=16, max_size=64, repeat=2, search_budget=8,
                envelope_budget=6, distributions=["uniform"], **extra)


@pytest.mark.parametrize("invalid", [
    {"compiler_versions":[21,21]}, {"compiler_versions":[True]}, {"rounds":True},
    {"min_size":1000001}, {"actions":["unknown"]}, {"actions":"licm"},
    {"distributions":["shell"]}, {"distributions":[[]]}, {"envelope_budget":1000},
    {"benchmark_id":"custom"}, {"benchmark_id":[]}, {"unknown":1},
])
def test_request_rejects_unbounded_and_unsupported_inputs(invalid):
    with pytest.raises(ValueError):
        d.validate_request(invalid)


def points(low=16, high=64, label="win"):
    candidate = 80 if label == "win" else 120
    return [dict(workload=asdict(d.DeploymentWorkload(n,s,2,"uniform")), status="valid",
                 comparison=compare_samples([candidate]*10,[100]*10))
            for n in {low,(low+high)//2,high} for s in (1009,1010)]


def test_envelope_requires_complete_multiseed_confirmation_and_falls_back():
    p = points()
    cell = d.cell_decision(p,16,64,"uniform",10)
    assert cell["decision"] == "optimized"
    assert d.cell_decision(p[:-1],16,64,"uniform",10)["decision"] == "insufficient_evidence"
    assert d.cell_decision(p,16,64,"uniform",3)["decision"] == "insufficient_evidence"
    assert d.cell_decision(points(label="loss"),16,64,"uniform",10)["decision"] == "baseline"
    envelope = dict(cells=[cell],repeat=2)
    assert d.choose_dispatch(envelope,d.DeploymentWorkload(30,999,2,"uniform"))[0] == "optimized"
    for w in [d.DeploymentWorkload(65,999,2,"uniform"),d.DeploymentWorkload(30,999,3,"uniform"),
              d.DeploymentWorkload(30,999,2,"skewed")]:
        assert d.choose_dispatch(envelope,w)[0] == "baseline"


def test_version_scoring_never_rewards_missing_or_incorrect_measurements():
    w = dict(size=32,seed=29,repeat=2,distribution="uniform")
    row = dict(candidate="c0",workload=w,compiler=21,status="valid",ratio=.8)
    assert d.worst_ratio([row],"c0",[w],[20,21]) == float("inf")
    assert d.worst_ratio([row,{**row,"compiler":20,"ratio":1.2}],"c0",[w],[20,21]) == 1.2
    assert d.worst_ratio([{**row,"status":"failed"}],"c0",[w],[21]) == float("inf")


def test_actual_remark_changes_prioritize_sequence_repairs():
    a = {"remarks":[{"pass":"licm","kind":"Missed","name":"Alias"}]}
    delta = d.evidence_delta([a,{"remarks":[]}])
    assert delta == {"licm":1}
    assert d.repair_sequences(["gvn","licm"],delta)[0] == ("gvn",)
    assert d.evidence_delta([a]) == {}


def fake_environment():
    return dict(versions={"opt":"LLVM 21.1.0"},target_triple="x86_64-linux",cpu_model="test",
                clang_flags=[],llc_flags=[],link_flags=[])


class FakeCompiler:
    """Model a first sequence that fails on large inputs, and a robust replacement."""
    calls = []
    all_invalid = False

    def __init__(self, req, directory, major, progress, deadline):
        self.req,self.directory,self.major = req,Path(directory),major
        self.directory.mkdir(parents=True)
        self.environment = fake_environment()
        self.environment["versions"] = {"opt":f"LLVM {major}.1.0"}
        self.commands=[]
        self.o3=self.directory/"o3";self.o3.write_bytes(b"baseline")
        self.source=self.directory/"source.c";self.source.write_text("test source")
        self.initial_ir="define i32 @f() { ret i32 0 }"

    def prepare(self):
        pass

    def build(self, sequence):
        p=self.directory/"candidate";p.write_bytes(b"optimized")
        return {"id":"candidate"}

    def binary(self, row):
        return self.directory/row["id"]

    def diagnostic(self, sequence):
        return dict(compiler=self.major, remarks=[])

    def measure(self, sequence, w, rounds, label, candidate="candidate"):
        self.calls.append((label,w.seed,self.major))
        value = 130 if tuple(sequence)==tuple(self.req["actions"]) and w.size>32 else 80
        row=dict(candidate=candidate,compiler=self.major,workload=asdict(w),actions=list(sequence),status="valid")
        if self.all_invalid:
            return {**row,"status":"failed","error":"wrong checksum"}
        return {**row,"ratio":value/100,"samples_ns":{"candidate":[value]*rounds,"o3":[100]*rounds},
                "comparison":compare_samples([value]*rounds,[100]*rounds)}

    def paired(self, variants, w, rounds, label):
        self.calls.append((label,w.seed,self.major))
        return {key:[110 if key=="dispatcher" else 100]*rounds for key in variants}


def test_joint_search_migration_envelope_and_holdout_are_separated(monkeypatch,tmp_path):
    monkeypatch.setattr(d,"DeploymentCompiler",FakeCompiler)
    FakeCompiler.calls=[]
    result=d.DeploymentRunner(request(compiler_versions=[20,21]),tmp_path/"run").execute()
    assert result["search"]["spent"] <= result["search"]["budget"]
    assert result["budgets"]["envelope_spent"] <= result["budgets"]["envelope_limit"]
    assert {r["compiler"] for r in result["search"]["records"]}=={20,21}
    assert result["migration"]["status"]=="compared"
    assert any(e["operation"]=="challenge_workload" for e in result["search"]["events"])
    assert any(e["operation"]=="repair_sequence" for e in result["search"]["events"])
    assert result["counterexamples"]
    assert result["envelope"]["cells"][0]["decision"]=="optimized"
    assert result["dispatch_confirmation"][0]["comparison"]["label"]=="loss"
    training={s for label,s,_ in FakeCompiler.calls if label.startswith("search-")}
    calibration={s for label,s,_ in FakeCompiler.calls if label.startswith("envelope-")}
    holdout={s for label,s,_ in FakeCompiler.calls if label.startswith("holdout-")}
    assert training.isdisjoint(calibration|holdout) and calibration.isdisjoint(holdout)
    assert json.loads((tmp_path/"run"/"report.json").read_text())["kind"]=="deployment"


def test_all_invalid_candidates_produce_baseline_policy_without_a_speed_claim(monkeypatch,tmp_path):
    monkeypatch.setattr(d,"DeploymentCompiler",FakeCompiler)
    monkeypatch.setattr(FakeCompiler,"all_invalid",True)
    result=d.DeploymentRunner(request(),tmp_path/"run").execute()
    assert result["search"]["selected_actions"] is None
    assert result["artifacts"]["optimized"] is None
    assert all(c["decision"]!="optimized" for c in result["envelope"]["cells"])
    assert all(c["worst_ratio"] is None for c in result["migration"]["candidate_scores"])


def test_dispatch_verifies_context_and_artifacts_and_falls_back(monkeypatch,tmp_path):
    monkeypatch.setattr(d,"DeploymentCompiler",FakeCompiler)
    report=d.DeploymentRunner(request(),tmp_path/"run").execute()
    monkeypatch.setattr(d,"Toolchain",lambda _:SimpleNamespace(environment=lambda:fake_environment()))
    args=["--size","30","--seed","555","--repeat","2","--distribution","uniform"]
    path=tmp_path/"run"/"report.json"
    assert d.dispatch(path,args,replace=False)["decision"]=="optimized"
    (tmp_path/"run"/report["artifacts"]["optimized"]["path"]).write_bytes(b"tampered")
    assert d.dispatch(path,args,replace=False)["decision"]=="baseline"
    (tmp_path/"run"/report["artifacts"]["baseline"]["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError,match="Baseline artifact"):
        d.dispatch(path,args,replace=False)
    monkeypatch.setattr(d,"Toolchain",lambda _:SimpleNamespace(environment=lambda:{**fake_environment(),"cpu_model":"other"}))
    with pytest.raises(ValueError,match="context changed"):
        d.dispatch(path,args,replace=False)


def test_deployment_api_strict_validation_and_unavailable_tools(monkeypatch):
    from fastapi.testclient import TestClient
    from autotuner.api import app
    monkeypatch.setattr(d,"available_compilers",lambda:[])
    with TestClient(app) as client:
        assert client.post("/v1/research/deployment/jobs",json={}).status_code==503
        for body in [{"rounds":"10"},{"compiler_versions":[21,21]},{"distributions":["bad"]}]:
            assert client.post("/v1/research/deployment/jobs",json=body).status_code==422

