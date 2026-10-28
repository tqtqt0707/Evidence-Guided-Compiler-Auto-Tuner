"""Generate fresh, portable research evidence on the CI runner."""
from pathlib import Path
import os
import json
from autotuner import config
from autotuner.research import ResearchRunner,atomic_json,CUSTOM_EXAMPLE,REFERENCE_EXAMPLE
def main():
    output=Path("test-results");output.mkdir(exist_ok=True)
    config.DATA_DIR=Path(os.environ.get("AUTOTUNER_DATA_DIR","/tmp/research-review-data"))
    common={"rounds":10,"budget":3,"fuzz_cases":12,"grid_seeds":[101,211]}
    runs=[
      ("research-review",{"size":250000,"repeat":8,"grid_sizes":[125000,500000],"memory_role":"train",**common}),
      ("custom-review",{"benchmark_id":"custom","source":CUSTOM_EXAMPLE,"reference_source":REFERENCE_EXAMPLE,
          "trusted_local_code":True,"actions":["instcombine","gvn"],"size":200000,"repeat":6,
          "grid_sizes":[100000,300000],"memory_role":"evaluate",**common}),
    ]
    for name,request in runs:
        result=ResearchRunner(request,output/(name+"-work"),lambda x:print(name,x,flush=True)).execute()
        result["provenance"]["ci"]={"run_id":os.environ.get("GITHUB_RUN_ID"),"commit":os.environ.get("GITHUB_SHA"),"runner":"GitHub Actions Ubuntu 26.04 container"}
        atomic_json(output/(name+".json"),result)
        print(name,json.dumps({"id":result["id"],"validation":result["validation"],"prior_records":result["learning"]["eligible_records"]}),flush=True)
if __name__=="__main__":main()
