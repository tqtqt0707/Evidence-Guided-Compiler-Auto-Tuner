"use strict";
const $=id=>document.getElementById(id);
let report=null,pollTimer=null,reportURL=null,compilerReady=false;
const time=ns=>typeof ns==="number"?(ns/1e6).toFixed(3)+" ms":"unavailable";
const pct=n=>typeof n==="number"?(n>=0?"+":"")+n.toFixed(2)+"%":"unavailable";
const text=(id,value)=>$(id).textContent=value;
const seq=actions=>actions?.length?actions.join(" → "):"Empty control";
function error(message){text("error",message);$("error").hidden=false;}
async function json(url,options){const r=await fetch(url,options);const data=await r.json();if(!r.ok)throw Error(data.error?.message||"Request failed");return data;}
function table(id,head,rows){
 const wrap=document.createElement("div");wrap.className="table-scroll";
 const t=document.createElement("table"),thead=t.createTHead(),tr=thead.insertRow();
 head.forEach(h=>{const th=document.createElement("th");th.textContent=h;tr.append(th);});
 const body=t.createTBody();rows.forEach(values=>{const row=body.insertRow();values.forEach(value=>{const cell=row.insertCell();if(value instanceof Node)cell.append(value);else cell.textContent=String(value??"—");});});wrap.append(t);$(id).replaceChildren(wrap);
}
function selectTab(name){
 document.querySelectorAll(".pane").forEach(p=>p.hidden=p.id!=="pane-"+name);
 document.querySelectorAll("[data-tab]").forEach(b=>b.setAttribute("aria-selected",String(b.dataset.tab===name)));
}
document.querySelectorAll("[data-tab]").forEach(b=>b.addEventListener("click",()=>selectTab(b.dataset.tab)));
$("toggle-config").onclick=()=>$("configuration").hidden=!$("configuration").hidden;
function comparisons(phase){
 const rows=report.confirmation.comparisons[phase];
 return Object.entries(rows).map(([k,v])=>[k,time(v.candidate_median_ns),time(v.baseline_median_ns),pct(v.runtime_reduction_percent),v.paired_wins+"/"+v.paired_rounds,v.label]);
}
function loadReport(data,kind="Saved compiler evidence"){
 if(data?.schema_version!==1||data.status!=="completed"||!data.provenance||!data.search||!data.cfg)throw Error("Not a completed Research Lab report");
 report=data;$("report").hidden=false;$("configuration").hidden=true;$("error").hidden=true;
 text("report-title",(data.request.benchmark_id==="custom"?"Custom C kernel":data.request.benchmark_id)+" · research evidence");
 text("evidence-badge",kind);
 const env=data.provenance.environment;
 text("provenance","Recorded "+new Date(data.created_at).toLocaleString()+" · "+env.cpu_model+" · "+env.versions.opt+" · "+env.target_triple+". "+data.provenance.measurement);
 $("overview-metrics").replaceChildren();
 for(const [label,value] of [["Analyses",8],["Pass interventions",data.ablation.length],["Proposals per strategy",data.request.budget],["Paired confirmation rounds",data.request.rounds]]){
  const div=document.createElement("div");div.className="metric";const small=document.createElement("small"),strong=document.createElement("strong");small.textContent=label;strong.textContent=value;div.append(small,strong);$("overview-metrics").append(div);
 }
 if(reportURL)URL.revokeObjectURL(reportURL);reportURL=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));$("download-report").href=reportURL;$("download-report").download="compiler-research-"+data.id+".json";$("download-report").hidden=false;
 table("contribution-table",["Intervention","Pass sequence","Status","Runtime median","Change vs full","Faster pairs","Outcome"],data.ablation.map(r=>[r.role,seq(r.actions),r.status,time(r.median_ns),pct(r.versus_full?.runtime_reduction_percent),r.versus_full?r.versus_full.paired_wins+"/"+r.versus_full.paired_rounds:"—",r.versus_full?.label||"unavailable"]));
 text("contribution-summary","All listed variants were compiled from the same seed. No-op transformations retain their measured control behavior. Fewer than ten pairs are demonstration evidence only.");
 table("remarks-table",["Kind","Pass / function","Source location","Actual compiler message"],data.remarks.items.map(r=>{
  const button=document.createElement("button");button.className="secondary";button.textContent=r.line?"Line "+r.line+(r.column?":"+r.column:""):"Not emitted";
  button.disabled=!r.line;button.onclick=()=>{const lines=(data.request.source||data.source||"Source is stored with the compiler job.").split("\n");text("remark-source",lines.map((x,i)=>(i+1===r.line?"> ":"  ")+(i+1)+": "+x).join("\n"));$("remark-source").hidden=false;};
  return [r.kind,r.pass+" / "+r.function,button,r.message||r.name];
 }));
 if(!data.remarks.items.length)text("remarks-table",data.remarks.parse_error||"LLVM emitted no remarks for this selected sequence. This does not mean that no optimization opportunities exist.");
 text("robustness-note",data.robustness.selection);
 $("robustness-grid").replaceChildren();
 for(const cell of data.robustness.cells){const box=document.createElement("div");box.className="cell "+cell.comparison.label;const small=document.createElement("small"),strong=document.createElement("strong"),p=document.createElement("p");small.textContent="Size "+cell.workload.size.toLocaleString()+" · seed "+cell.workload.seed;strong.textContent=pct(cell.comparison.runtime_reduction_percent);p.textContent=cell.comparison.label+" · "+cell.comparison.paired_wins+"/"+cell.comparison.paired_rounds+" faster pairs";box.append(small,strong,p);$("robustness-grid").append(box);}
 table("holdout-table",["Strategy","Candidate","-O3","Reduction","Faster pairs","Label"],comparisons("holdout"));
 text("memory-summary",data.learning.method+". Role: "+data.learning.role+". Eligible prior records: "+data.learning.eligible_records+" from "+data.learning.source_programs+" other source programs. New training records: "+data.learning.new_training_records+". "+data.learning.context_rule+". No learning advantage is assumed.");
 table("learning-table",["Strategy","Proposals","Valid","Chosen sequence","Held-out reduction vs -O3","Label"],Object.entries(data.search).map(([k,rows])=>[k,rows.length,rows.filter(r=>r.status==="valid").length,seq(data.winners[k]?.actions),pct(data.confirmation.comparisons.holdout[k]?.runtime_reduction_percent),data.confirmation.comparisons.holdout[k]?.label]));
 drawSearch(data.search);
 text("custom-summary",data.request.benchmark_id==="custom"?"This record executed user-supplied C against its independent reference. Correctness is limited to the tested inputs.":"This record uses a built-in program. The Custom C workflow is available through Configure your own C kernel.");
 text("custom-source",data.request.source||data.source||"Built-in source is preserved in the compiler job and repository.");
 text("custom-reference",data.request.reference_source||"Built-in runs use independent Python small-input oracles and an -O0 checksum crosscheck for larger workloads.\nCustom contract:\nuint64_t kernel(uint64_t size, uint64_t seed);\nuint64_t reference(uint64_t size, uint64_t seed);");
 renderObjective(data.objective);
 const names=[...new Set([...data.cfg.before.functions,...data.cfg.after.functions].map(f=>f.name))];
 $("cfg-function").replaceChildren(...names.map(n=>{const o=document.createElement("option");o.value=n;o.textContent=n;return o;}));
 if(names.includes("main"))$("cfg-function").value="main";renderCFG();
 const val=data.validation;
 table("validation-table",["Check","Status","Scope"],[
 ...Object.entries(val.differential).map(([k,v])=>[k+" differential tests",v.status,(v.cases??0)+" cases · "+v.oracle]),
 ["ASan / UBSan source checks",val.sanitizers.status,val.sanitizers.scope||val.sanitizers.diagnostic],
 ["Alive2 translation validation",val.alive2.status,val.alive2.scope]]);
 text("formal-note",val.alive2.status==="not_installed"?"Alive2 is not installed on the recorded host. The adapter is available, but this run has no formal validation claim.":val.alive2.scope);
 $("limits").replaceChildren(...data.limits.map(s=>{const li=document.createElement("li");li.textContent=s;return li;}));
 selectTab("contribution");$("report").scrollIntoView({behavior:"smooth"});
}
function svgElement(name,attrs={}){const e=document.createElementNS("http://www.w3.org/2000/svg",name);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);return e;}
function drawSearch(search){
 const svg=svgElement("svg",{viewBox:"0 0 1000 245",role:"img","aria-label":"Best search runtime by proposal"});
 const all=Object.values(search).flat().filter(r=>Number.isFinite(r.median_ns));if(!all.length){text("learning-chart","No valid search timings");return;}
 const max=Math.max(...all.map(r=>r.median_ns)),min=Math.min(...all.map(r=>r.median_ns)),span=max-min||1;
 const colors={guided:"#41dbb7",random:"#f1b76e",adaptive:"#86b8ff"};
 svg.append(svgElement("path",{d:"M55 15V200H960",stroke:"#52747d",fill:"none"}));
 Object.entries(search).forEach(([name,rows],index)=>{let best=Infinity;const pts=[];rows.forEach((r,i)=>{if(r.status==="valid"&&Number.isFinite(r.median_ns))best=Math.min(best,r.median_ns);if(Number.isFinite(best))pts.push((55+i*890/Math.max(1,rows.length-1))+","+(190-(best-min)/span*160));});svg.append(svgElement("polyline",{points:pts.join(" "),fill:"none",stroke:colors[name],"stroke-width":3}));const label=svgElement("text",{x:65+index*275,y:230,fill:colors[name],"font-size":14});label.textContent=name+" · best search runtime";svg.append(label);});
 const hi=svgElement("text",{x:6,y:25,fill:"#a0b8bc","font-size":11});hi.textContent=(max/1e6).toFixed(2)+" ms";svg.append(hi);$("learning-chart").className="chart";$("learning-chart").replaceChildren(svg);
}
function renderObjective(result){
 text("objective-summary",result.selected?"Selected "+result.selected.strategy+" · "+seq(result.selected.actions)+" · "+time(result.selected.median_ns)+" · "+result.selected.text_bytes+" .text bytes. "+result.feasible_count+" feasible candidates.":result.reason);
 table("objective-table",["Pareto candidate","Pass sequence","Runtime","Code .text bytes","Build cost"],result.frontier.map(r=>[r.strategy,seq(r.actions),time(r.median_ns),r.text_bytes,r.compile_ms?.toFixed(2)+" ms"]));
}
$("objective-form").onsubmit=async e=>{e.preventDefault();try{
 const rows=Object.values(report.search).flat();
 const data={rows,objective:$("objective").value,max_slowdown_percent:Number($("max-slowdown").value),baseline_ns:report.confirmation.tuning_samples_ns.o3.slice().sort((a,b)=>a-b)[Math.floor(report.request.rounds/2)]};
 if($("max-text").value!=="")data.max_text_bytes=Number($("max-text").value);
 if($("max-build").value!=="")data.max_compile_ms=Number($("max-build").value);
 renderObjective(await json("/v1/research/objectives",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)}));
}catch(ex){error(ex.message);}};
function renderCFG(){
 for(const phase of ["before","after"]){
  const container=$("cfg-"+phase),f=report.cfg[phase].functions.find(f=>f.name===$("cfg-function").value);
  if(!f){container.textContent="Function absent after this transformation";continue;}
  const blocks=f.blocks.slice(0,30),width=780,height=Math.max(140,Math.ceil(blocks.length/3)*115);
  const svg=svgElement("svg",{viewBox:"0 0 "+width+" "+height,role:"img","aria-label":phase+" control-flow graph"});
  const positions=new Map(blocks.map((b,i)=>[b.id,{x:20+(i%3)*255,y:20+Math.floor(i/3)*115}]));
  const defs=svgElement("defs"),marker=svgElement("marker",{id:"arrow-"+phase,viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:6,markerHeight:6,orient:"auto-start-reverse"});marker.append(svgElement("path",{d:"M0 0L10 5L0 10z",fill:"#7cabb2"}));defs.append(marker);svg.append(defs);
  for(const edge of f.edges){const a=positions.get(edge.from),b=positions.get(edge.to);if(!a||!b)continue;svg.append(svgElement("path",{d:"M"+(a.x+110)+" "+(a.y+66)+" Q"+(a.x+120)+" "+(b.y-16)+" "+(b.x+110)+" "+b.y,stroke:edge.back_edge?"#f1b76e":"#729ca8","stroke-dasharray":edge.back_edge?"6 4":"",fill:"none","marker-end":"url(#arrow-"+phase+")"}));}
  for(const block of blocks){const p=positions.get(block.id),g=svgElement("g",{tabindex:0,role:"button","aria-label":"Inspect "+phase+" block "+block.id});g.append(svgElement("rect",{x:p.x,y:p.y,width:225,height:67,rx:9,fill:"#153742",stroke:"#477985"}));const a=svgElement("text",{x:p.x+12,y:p.y+25,fill:"#e7f3f2","font-size":14});a.textContent=block.id;const b=svgElement("text",{x:p.x+12,y:p.y+48,fill:"#a0b8bc","font-size":11});b.textContent=block.instruction_count+" instructions · "+(block.source_lines.length?"source "+block.source_lines.join(","):"no source location");g.append(a,b);const show=()=>text("cfg-instructions",phase+" / "+f.name+" / "+block.id+"\nSource lines: "+(block.source_lines.join(", ")||"not emitted")+"\n\n"+block.instructions.join("\n"));g.onclick=show;g.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();show();}};svg.append(g);}
  container.replaceChildren(svg);if(f.blocks.length>30){const p=document.createElement("p");p.textContent="Showing 30 blocks. Download the report for the complete graph.";container.append(p);}
 }
}
$("cfg-function").onchange=()=>report&&renderCFG();
$("report-file").onchange=async()=>{try{const f=$("report-file").files[0];if(f.size>8_000_000)throw Error("Report exceeds 8 MB");loadReport(JSON.parse(await f.text()),"Imported compiler evidence");}catch(ex){error(ex.message);}};
$("open-evidence").onclick=async()=>{try{loadReport(await json("/examples/research/review-evidence.json"),"Bundled Linux compiler run");}catch(ex){error("Bundled research evidence is unavailable: "+ex.message);}};
$("configure-custom").onclick=()=>{$("configuration").hidden=false;$("lab-benchmark").value="custom";$("custom-editors").hidden=false;$("configuration").scrollIntoView({behavior:"smooth"});};
$("lab-benchmark").onchange=()=>{$("custom-editors").hidden=$("lab-benchmark").value!=="custom";if($("lab-benchmark").value==="matmul"){$("lab-size").value=64;$("grid-sizes").value="32, 96";}else{$("lab-size").value=250000;$("grid-sizes").value="125000, 500000";}};
$("research-form").onsubmit=async e=>{
 e.preventDefault();$("error").hidden=true;const numbers=id=>$(id).value.split(",").map(x=>Number(x.trim()));
 const body={benchmark_id:$("lab-benchmark").value,actions:$("lab-actions").value.split(",").map(s=>s.trim()),size:Number($("lab-size").value),seed:Number($("lab-seed").value),repeat:Number($("lab-repeat").value),rounds:Number($("lab-rounds").value),budget:Number($("lab-budget").value),memory_role:$("memory-role").value,grid_sizes:numbers("grid-sizes"),grid_seeds:numbers("grid-seeds")};
 if(body.benchmark_id==="custom"){body.source=$("kernel-source").value;body.reference_source=$("reference-source").value;body.trusted_local_code=$("trusted-code").checked;}
 $("run-lab").disabled=true;
 try{const job=await json("/v1/research/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const poll=async()=>{try{const state=await json("/v1/research/jobs/"+job.id);$("job-progress").hidden=false;text("job-progress",state.status+" · "+state.stage+" · "+job.id);
   if(state.status==="completed"){loadReport(await json("/v1/research/jobs/"+job.id+"/report"),"Fresh compiler execution");$("run-lab").disabled=!compilerReady;return;}
   if(state.status==="failed")throw Error(state.error||"Compiler experiment failed");
   pollTimer=setTimeout(poll,2000);
  }catch(ex){error(ex.message);$("run-lab").disabled=!compilerReady;}};await poll();
 }catch(ex){error(ex.message);$("run-lab").disabled=!compilerReady;}
};
(async()=>{try{const data=await json("/v1/research/capabilities");compilerReady=data.compiler_available;text("capability",compilerReady?"LLVM available · local execution":"Viewer mode · Linux LLVM required for new runs");$("run-lab").disabled=!compilerReady;$("kernel-source").value=data.custom_example;$("reference-source").value=data.reference_example;}catch(ex){error(ex.message);$("run-lab").disabled=true;}})();

$("open-custom-evidence").onclick=async()=>{try{loadReport(await json("/examples/research/custom-evidence.json"),"Bundled custom C compiler run");}catch(ex){error(ex.message);}};
