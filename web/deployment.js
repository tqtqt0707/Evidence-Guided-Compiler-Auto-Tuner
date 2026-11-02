"use strict";
let deploymentURL=null,deploymentReady=false;
function loadDeploymentReport(data){
 if(data?.schema_version!==1||data.kind!=="deployment"||data.status!=="completed"||!data.envelope||!data.search||!Array.isArray(data.holdout))throw Error("Not a completed deployment report");
 $("deployment-report").hidden=false;$("report").hidden=true;$("error").hidden=true;
 const env=data.provenance.environment;
 text("deployment-provenance",data.created_at+" · "+env.cpu_model+" · "+env.target_triple+" · "+data.provenance.measurement);
 text("deployment-summary","Search used "+data.search.spent+" / "+data.search.budget+" comparisons. Selected sequence: "+(data.search.selected_actions?seq(data.search.selected_actions):"none valid")+". "+(data.search.baseline_preferred?"Direct -O3 is preferred on worst observed search runtime.":"Candidate improves worst observed search runtime; final holdout is reported separately.")+" "+(data.deployment_gate?.reason||"No final release authorization in this saved report."));
 table("deployment-events",["Operation","Pass sequence / workload","Budget spent"],data.search.events.map(e=>[e.operation,e.actions?seq(e.actions):JSON.stringify(e.workload),e.spent]));
 const comparisonRow=r=>[r.compiler,r.candidate??"—",r.workload.size,r.workload.distribution,pct(r.comparison?.runtime_reduction_percent),r.status==="valid"?r.comparison.label:r.error];
 table("deployment-counterexamples",["LLVM","Candidate","Size","Distribution","Reduction vs -O3","Fresh confirmation"],data.counterexamples.map(comparisonRow));
 if(!data.counterexamples.length)text("deployment-counterexamples","No regression hypotheses were selected for fresh confirmation. This does not establish absence of regressions.");
 text("deployment-migration-status",data.migration.status==="compared"?"Compared actual LLVM versions "+data.migration.versions.join(" and ")+". Scores below use search workloads only.":"Compiler migration requires a second installed LLVM version. This run has one compiler; no cross-version claim is made.");
 table("deployment-migration",["Candidate","Sequence","Worst search runtime / its -O3 baseline"],data.migration.candidate_scores.map(c=>[c.candidate,seq(c.actions),c.worst_ratio===null?"Unavailable / invalid":c.worst_ratio.toFixed(3)+"×"]));
 text("deployment-diagnostics",JSON.stringify({remark_delta:data.migration.remark_delta,diagnostics:data.migration.diagnostics},null,2));
 table("deployment-envelope",["Distribution","Size interval","Decision","Calibration points"],data.envelope.cells.map(c=>[c.distribution,c.min_size+"–"+c.max_size,c.decision,c.measured_points.length]));
 table("deployment-holdout",["LLVM","Candidate","Size","Distribution","Reduction vs -O3","Held-out result"],data.holdout.flatMap(c=>c.comparisons.map(comparisonRow)));
 table("deployment-dispatch",["Size","Distribution","Launcher reduction vs -O3","Result"],(data.dispatch_confirmation||[]).map(c=>[c.workload.size,c.workload.distribution,pct(c.comparison.runtime_reduction_percent),c.comparison.label]));
 $("deployment-limits").replaceChildren(...data.limits.map(s=>{const li=document.createElement("li");li.textContent=s;return li;}));
 if(deploymentURL)URL.revokeObjectURL(deploymentURL);
 deploymentURL=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));
 $("deployment-download").href=deploymentURL;$("deployment-download").download="deployment-"+data.id+".json";
 $("deployment-report").scrollIntoView({behavior:"smooth"});
}
$("deployment-benchmark").onchange=()=>{
 const histogram=$("deployment-benchmark").value==="histogram",matrix=$("deployment-benchmark").value==="matmul";
 $("deployment-distributions").value=histogram?"uniform, skewed, alternating":"native";
 $("deployment-min").value=matrix?16:1000;$("deployment-size").value=matrix?64:100000;$("deployment-max").value=matrix?128:1000000;
};
$("deployment-form").onsubmit=async e=>{
 e.preventDefault();$("error").hidden=true;$("run-deployment").disabled=true;
 const split=id=>$(id).value.split(",").map(s=>s.trim());
 const body={benchmark_id:$("deployment-benchmark").value,actions:split("deployment-actions"),compiler_versions:split("deployment-versions").map(Number),distributions:split("deployment-distributions")};
 for(const [field,id]of Object.entries({size:"size",min_size:"min",max_size:"max",repeat:"repeat",rounds:"rounds",search_budget:"budget",envelope_budget:"envelope-budget",seed:"seed"}))body[field]=Number($("deployment-"+id).value);
 try{
  const job=await json("/v1/research/deployment/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const poll=async()=>{try{
   const state=await json("/v1/research/jobs/"+job.id);$("deployment-progress").hidden=false;text("deployment-progress",state.status+" · "+state.stage);
   if(state.status==="completed"){loadDeploymentReport(await json("/v1/research/jobs/"+job.id+"/report"));$("run-deployment").disabled=!deploymentReady;return;}
   if(state.status==="failed")throw Error(state.error||"Deployment experiment failed");
   setTimeout(poll,2000);
  }catch(ex){error(ex.message);$("run-deployment").disabled=!deploymentReady;}};
  await poll();
 }catch(ex){error(ex.message);$("run-deployment").disabled=!deploymentReady;}
};
(async()=>{try{
 const capabilities=await json("/v1/research/capabilities"),versions=capabilities.deployment_compilers||[];
 deploymentReady=versions.length>0;$("run-deployment").disabled=!deploymentReady;
 text("deployment-compilers",versions.length?"Installed LLVM toolsets: "+versions.join(", ")+". Select two to test migration.":"No complete LLVM toolsets detected. Saved deployment JSON can still be opened above.");
 if(versions.length)$("deployment-versions").value=versions.slice(-2).join(", ");
}catch(ex){error(ex.message);}})();
