"""Real compiler and launcher execution. Short workloads test plumbing, not speed."""
from pathlib import Path
import shutil
import sys

import pytest

from autotuner.deployment import DeploymentCompiler, DeploymentRunner, DeploymentWorkload, histogram_oracle

pytestmark=pytest.mark.skipif(sys.platform!="linux",reason="Requires x86-64 Linux and LLVM")


def small(**extra):
    return dict(size=32,min_size=16,max_size=64,repeat=2,rounds=3,search_budget=4,
                envelope_budget=6,actions=["instcombine","simplifycfg"],
                distributions=["uniform"],compiler_versions=[21],**extra)


def test_histogram_distributions_match_independent_oracle(tmp_path):
    from autotuner.deployment import validate_request
    c=DeploymentCompiler(validate_request(small()),tmp_path/"compiler",21,lambda _:None,float("inf"))
    c.prepare()
    for distribution in ("uniform","skewed","alternating"):
        for seed in (0,11,2**53-1):
            w=DeploymentWorkload(13,seed,3,distribution)
            assert c.run(c.o3,w,f"oracle-{distribution}-{seed}")[0]==histogram_oracle(w)


def test_real_search_and_dispatcher_run_with_conservative_short_run_policy(tmp_path):
    result=DeploymentRunner(small(),tmp_path/"run").execute()
    assert result["migration"]["status"]=="requires_second_compiler"
    assert any(r["status"]=="valid" for r in result["search"]["records"])
    assert all(c["decision"]!="optimized" for c in result["envelope"]["cells"])
    assert result["dispatch_confirmation"]
    assert all(c["comparison"]["label"]=="demo_only" for c in result["dispatch_confirmation"])
    assert (tmp_path/"run"/"dispatch").is_file()


def test_two_real_compiler_versions_are_measured_separately(tmp_path):
    if not shutil.which("clang-20"):
        pytest.skip("LLVM 20 is optional locally; required in the compiler CI job")
    req=small();req["compiler_versions"]=[20,21]
    result=DeploymentRunner(req,tmp_path/"migration").execute()
    assert result["migration"]["status"]=="compared"
    assert {r["compiler"] for r in result["search"]["records"]}=={20,21}
    assert (tmp_path/"migration"/"llvm-20"/"source.c").read_bytes()==(tmp_path/"migration"/"llvm-21"/"source.c").read_bytes()

