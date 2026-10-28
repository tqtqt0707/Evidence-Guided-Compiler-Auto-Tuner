"""Real LLVM integration checks; run in the Linux compiler CI job."""
import sys
import pytest
from autotuner import config
from autotuner.research import ResearchRunner,CUSTOM_EXAMPLE,REFERENCE_EXAMPLE

pytestmark=pytest.mark.skipif(sys.platform!="linux",reason="Real LLVM 21 execution requires Linux")
def small(**extra):
    return {"size":64,"repeat":2,"rounds":3,"budget":2,"fuzz_cases":4,"actions":["licm","loop-fusion"],"grid_sizes":[32,96],"grid_seeds":[101],**extra}

def test_all_eight_research_analyses_with_actual_compiler(llvm,tmp_path,monkeypatch):
    monkeypatch.setattr(config,"DATA_DIR",tmp_path/"data")
    result=ResearchRunner(small(),tmp_path/"suite").execute()
    assert result["status"]=="completed"
    assert any(row["role"]=="remove:licm" for row in result["ablation"])
    assert result["cfg"]["before"]["functions"]
    assert result["cfg"]["after"]["functions"]
    assert len(result["robustness"]["cells"])==2
    assert {key:len(rows) for key,rows in result["search"].items()}=={"guided":2,"random":2,"adaptive":2}
    assert all(v["label"]=="demo_only" for v in result["confirmation"]["comparisons"]["holdout"].values())
    assert all(v["status"]=="passed" for v in result["validation"]["differential"].values())
    assert result["validation"]["sanitizers"]["status"]=="passed"
    assert result["objective"]["selected"] is not None
    assert result["learning"]["new_training_records"]==0

def test_custom_kernel_and_reference_run_and_mismatch_is_rejected(llvm,tmp_path,monkeypatch):
    monkeypatch.setattr(config,"DATA_DIR",tmp_path/"data")
    req=small(benchmark_id="custom",source=CUSTOM_EXAMPLE,reference_source=REFERENCE_EXAMPLE,trusted_local_code=True,actions=["instcombine","gvn"])
    result=ResearchRunner(req,tmp_path/"custom").execute()
    assert all(v["status"]=="passed" for v in result["validation"]["differential"].values())
    req["reference_source"]="#include <stdint.h>\nuint64_t reference(uint64_t n,uint64_t s){return 123;}"
    with pytest.raises(RuntimeError,match="disagreement"):
        ResearchRunner(req,tmp_path/"wrong").execute()
