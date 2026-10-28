const $ = (id) => document.getElementById(id);
const state = { benchmarks: [], activeId: null, poller: null, viewVersion: 0, detailVersion: 0, savedOffset: 0, toolchainReady: false, showcaseSubmitting: false };
const ms = (ns) => ns == null ? "—" : `${(ns / 1e6).toFixed(2)} ms`;

async function getJSON(url, options = {}) {
  const response = await fetch(url, options);
  // Preserve exact large integers in saved CLI/API runs, including uint64 seeds.
  const data = JSON.parse(await response.text(), (_key, value, context) => {
    if (typeof value === "number" && Number.isInteger(value) && !Number.isSafeInteger(value)) {
      if (context?.source) return context.source;
      throw new Error("This browser cannot display large saved integers exactly; use a current browser or download the raw JSON.");
    }
    return value;
  });
  if (!response.ok) {
    const diagnostics = data.error?.details?.diagnostics;
    throw new Error((data.error?.message || `HTTP ${response.status}`) + (diagnostics ? `\n\n${diagnostics}` : ""));
  }
  return data;
}

function showError(message) { $("form-error").textContent = message; $("form-error").hidden = false; }
function clearError() { $("form-error").hidden = true; }

function selectedBenchmark() { return state.benchmarks.find((item) => item.id === $("benchmark").value); }
function applyDefaults() {
  const item = selectedBenchmark();
  if (!item) return;
  const workload = $("mode").value === "quick" ? item.quick_input : item.tuning_input;
  $("size").value = workload.size;
  $("seed").value = workload.seed;
  $("repeat").value = workload.repeat;
  $("budget").value = $("mode").value === "quick" ? 5 : 20;
  $("search-repetitions").value = $("mode").value === "quick" ? 3 : 5;
  $("confirmation-rounds").value = $("mode").value === "quick" ? 3 : 10;
  $("holdout-fields").hidden = $("mode").value !== "full";
  for (const key of ["size", "seed", "repeat"]) $("holdout-" + key).value = item.holdout_input[key];
  $("holdout-size").max = item.size_max;
  $("size").max = item.size_max;
  $("benchmark-description").textContent = `${item.description} Supported size: ${item.size_min.toLocaleString()}–${item.size_max.toLocaleString()}.`;
}

async function loadSetup() {
  const [health, benchmarks] = await Promise.all([getJSON("/v1/health"), getJSON("/v1/benchmarks")]);
  $("health").textContent = health.toolchain_ready ? "Toolchain ready" : "Toolchain unavailable";
  $("health").className = `health ${health.toolchain_ready ? "ready" : "degraded"}`;
  $("health").title = health.toolchain_error || "LLVM actions, compilation, linking, and checksum validation passed";
  if (health.llm) {
    const llm = health.llm;
    $("health").textContent += llm.error ? " · OpenAI configuration error" : llm.mode === "openai" ? ` · OpenAI: ${llm.model}` : " · Local guided search";
    if (llm.error) $("health").title += `; ${llm.error}`;
  }
  $("run-button").disabled = !health.toolchain_ready;
  state.toolchainReady = health.toolchain_ready;
  $("inspect-button").disabled = !health.toolchain_ready;
  state.benchmarks = benchmarks.items;
  for (const item of benchmarks.items) {
    const option = document.createElement("option"); option.value = item.id; option.textContent = item.display_name;
    $("benchmark").append(option);
  }
  applyDefaults();
  await Promise.all([loadSaved().catch((error) => showError(error.message)), loadBundled(), loadShowcase()]);
}

const percent = (value) => `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;

async function loadShowcase() {
  const container = $("showcase-cases"); container.replaceChildren();
  try {
    const catalog = await getJSON("/examples/showcase/catalog.json");
    $("showcase-selection").textContent = `${catalog.selection} Catalog generated ${new Date(catalog.generated_at).toLocaleString()}. Screened ${catalog.screened}; selected ${catalog.items.length}; excluded ${catalog.excluded}.`;
    const reductions = catalog.items.map(item => item.comparisons.holdout.guided_vs_o3.runtime_reduction_percent);
    const families = new Set(catalog.items.map(item => item.request.benchmark_id)).size;
    const wins = catalog.items.filter(item => item.comparisons.holdout.guided_vs_o3.label === "win").length;
    $("showcase-win-count").textContent = `${wins} confirmed held-out wins versus Clang -O3. A win requires ≥2% runtime reduction and faster execution in at least 80% of ten or more paired rounds. This is the engine's evidence rule, not a statistical significance test.`;
    const stats = [["Selected workload cases", catalog.items.length, `${families} source programs; size and seed variants`],
      ["Held-out reductions ≥ 2%", reductions.filter(value => value >= 2).length, "Descriptive count versus Clang -O3"],
      ["Near equal / slightly worse", reductions.filter(value => value < 2).length, "Within the selected 2% regression limit"]];
    $("showcase-stats").replaceChildren(...stats.map(([label, value, note]) => {
      const box = document.createElement("div"); box.className = "metric";
      const small = document.createElement("small"); small.textContent = label;
      const strong = document.createElement("strong"); strong.textContent = value;
      const span = document.createElement("span"); span.textContent = note;
      box.append(small, strong, span); return box;
    }));
    for (const item of catalog.items) {
      const card = document.createElement("article"); card.className = "showcase-case"; card.dataset.caseId = item.id;
      card.dataset.o3Win = String(item.comparisons.holdout.guided_vs_o3.label === "win");
      const title = document.createElement("h3"); title.textContent = item.title;
      const reduction = item.comparisons.holdout.guided_vs_o3.runtime_reduction_percent;
      const observation = document.createElement("p"); observation.className = "note";
      observation.textContent = `Held-out evidence vs O3: ${item.comparisons.holdout.guided_vs_o3.label.toUpperCase()}. Median: ${reduction >= 10 ? "much faster (≥10% reduction)" : reduction >= 2 ? "faster (≥2% reduction)" : reduction > 0 ? "near equal, slightly faster" : reduction === 0 ? "equal" : "near equal, slightly slower"}.`;
      const purpose = document.createElement("p"); purpose.className = "note"; purpose.textContent = item.purpose;
      const tableWrap = document.createElement("div"); tableWrap.className = "table-wrap";
      const table = document.createElement("table");
      const header = document.createElement("thead"); const headerRow = document.createElement("tr");
      for (const label of ["Input", "Clang -O3 median ± MAD", "Guided median ± MAD", "Reduction vs O3", "Faster pairs", "Evidence vs O3", "Reduction vs random"]) {
        const cell = document.createElement("th"); cell.textContent = label; headerRow.append(cell);
      }
      header.append(headerRow); table.append(header);
      const body = document.createElement("tbody");
      for (const phase of ["tuning", "holdout"]) {
        const comparison = item.comparisons[phase], result = comparison.guided_vs_o3;
        const workload = item.request[`${phase}_input`];
        const row = document.createElement("tr");
        for (const value of [`${phase === "holdout" ? "Held-out" : "Tuning"}: ${workload.size.toLocaleString()} × ${workload.repeat}, seed ${workload.seed}`,
          `${ms(result.comparator_median_ns)} ± ${ms(result.comparator_mad_ns)}`,
          `${ms(result.candidate_median_ns)} ± ${ms(result.candidate_mad_ns)}`,
          percent(result.runtime_reduction_percent), `${result.paired_wins}/${result.paired_rounds}`, result.label,
          percent(comparison.guided_vs_random.runtime_reduction_percent)]) {
          const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
        }
        body.append(row);
      }
      table.append(body); tableWrap.append(table);
      const buttons = document.createElement("div"); buttons.className = "showcase-actions";
      const evidence = document.createElement("button"); evidence.type = "button"; evidence.className = "text-button"; evidence.textContent = "Open measured evidence";
      evidence.addEventListener("click", async () => {
        const version = ++state.viewVersion; clearTimeout(state.poller); state.activeId = null;
        $("results").hidden = true; $("cancel-run").hidden = true; $("retry-run").hidden = true;
        try {
          const base = `/examples/showcase/${item.id}`;
          const bundle = await getJSON(`${base}/bundle.json`);
          if (version !== state.viewVersion) return;
          $("empty-state").hidden = true; $("run-status").hidden = false;
          $("status-badge").textContent = "selected saved full"; $("status-badge").className = "badge";
          $("run-id").textContent = item.experiment_id; $("progress-fill").style.width = "100%";
          $("progress-text").textContent = `${bundle.summary.trial_count} previously measured candidate proposals`;
          $("run-message").textContent = "Selected showcase evidence. No fresh benchmark process was started.";
          renderResults(bundle.summary, bundle.trials, base, bundle.details);
          $("results").scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) { if (version === state.viewVersion) showShowcaseError(error.message); }
      });
      const run = document.createElement("button"); run.type = "button"; run.className = "showcase-run"; run.textContent = "Run this test →";
      run.disabled = !state.toolchainReady; run.title = state.toolchainReady ? "Fresh full search with these exact settings; uses local rules, no API credits" : "LLVM toolchain is unavailable";
      run.addEventListener("click", async () => {
        if (state.showcaseSubmitting) return;
        state.showcaseSubmitting = true;
        container.querySelectorAll(".showcase-run").forEach(button => { button.disabled = true; });
        $("showcase-error").hidden = true;
        try {
          const data = await getJSON("/v1/experiments", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(item.request) });
          await watchRun(data.id);
          $("run-status").scrollIntoView({ behavior: "smooth", block: "center" });
        } catch (error) { showShowcaseError(error.message); }
        finally {
          state.showcaseSubmitting = false;
          container.querySelectorAll(".showcase-run").forEach(button => { button.disabled = !state.toolchainReady; });
        }
      });
      buttons.append(evidence, run); card.append(title, purpose, observation, tableWrap, buttons); container.append(card);
    }
    if (!catalog.items.length) container.textContent = "No measured cases currently meet the selection rule.";
    applyShowcaseFilter();
  } catch (error) {
    $("showcase-selection").textContent = "Measured showcase is unavailable.";
    showShowcaseError(error.message);
  }
}

function showShowcaseError(message) {
  $("showcase-error").textContent = message; $("showcase-error").hidden = false;
}

function applyShowcaseFilter() {
  for (const card of $("showcase-cases").querySelectorAll(".showcase-case")) {
    card.hidden = $("showcase-wins-only").checked && card.dataset.o3Win !== "true";
  }
}
$("showcase-wins-only").addEventListener("change", applyShowcaseFilter);

async function loadBundled() {
  const container = $("bundled-runs"); container.replaceChildren();
  try {
    const index = await getJSON("/examples/index.json");
    for (const item of index.items) {
      const button = document.createElement("button"); button.className = "saved-run"; button.type = "button";
      const title = document.createElement("span"); title.textContent = `${item.benchmark_id} · saved full`;
      const detail = document.createElement("small"); detail.textContent = "Portable evidence";
      button.append(title, detail);
      button.addEventListener("click", async () => {
        clearTimeout(state.poller); state.activeId = null;
        const version = ++state.viewVersion;
        $("results").hidden = true;
        $("cancel-run").hidden = true; $("retry-run").hidden = true;
        try {
          const bundle = await getJSON(`/examples/${item.name}/bundle.json`);
          if (version !== state.viewVersion) return;
          $("empty-state").hidden = true; $("run-status").hidden = false;
          $("status-badge").textContent = "saved full"; $("status-badge").className = "badge";
          $("run-id").textContent = item.experiment_id;
          $("progress-fill").style.width = "100%";
          $("progress-text").textContent = `${bundle.summary.trial_count} previously measured candidate proposals`;
          $("run-message").textContent = "Bundled evidence from an earlier run. No compiler or benchmark process was started.";
          renderResults(bundle.summary, bundle.trials, `/examples/${item.name}`, bundle.details);
        } catch (error) { if (version === state.viewVersion) showError(error.message); }
      });
      container.append(button);
    }
    if (!index.items.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "No bundled examples."; container.append(p); }
  } catch (_) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "Bundled examples are unavailable."; container.append(p); }
}

async function loadSaved(append = false) {
  const offset = append ? state.savedOffset : 0;
  const data = await getJSON(`/v1/experiments?limit=20&offset=${offset}`);
  const container = $("saved-runs"); if (!append) container.replaceChildren();
  state.savedOffset = offset + data.items.length;
  $("load-more-saved").hidden = state.savedOffset >= data.total;
  if (!data.items.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "No saved experiments yet."; container.append(p); return; }
  for (const item of data.items) {
    const button = document.createElement("button"); button.className = "saved-run"; button.type = "button";
    const title = document.createElement("span"); title.textContent = `${item.benchmark_id} · ${item.mode}`;
    const detail = document.createElement("small"); detail.textContent = `${item.status} · ${new Date(item.created_at).toLocaleString()}`;
    button.append(title, detail); button.addEventListener("click", () => { clearError(); watchRun(item.id); }); container.append(button);
  }
}

async function startRun(event) {
  event.preventDefault(); clearError();
  const button = $("run-button"); button.disabled = true;
  const body = { benchmark_id: $("benchmark").value, mode: $("mode").value,
    tuning_input: { size: Number($("size").value), seed: Number($("seed").value), repeat: Number($("repeat").value) },
    budget_per_strategy: Number($("budget").value), max_sequence_length: Number($("max-length").value),
    search_repetitions: Number($("search-repetitions").value), confirmation_rounds: Number($("confirmation-rounds").value) };
  if (body.mode === "full") body.holdout_input = Object.fromEntries(["size", "seed", "repeat"].map((key) => [key, Number($("holdout-" + key).value)]));
  const numbers = [...Object.values(body.tuning_input), ...Object.values(body.holdout_input || {})];
  if (!numbers.every(Number.isSafeInteger)) { showError("Workload values must be exact integers; browser seeds are limited to 9007199254740991."); button.disabled = false; return; }
  try {
    const data = await getJSON("/v1/experiments", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await watchRun(data.id);
  } catch (error) { showError(error.message); }
  finally { button.disabled = false; }
}

async function watchRun(id) {
  state.activeId = id; clearTimeout(state.poller); $("results").hidden = true;
  const version = ++state.viewVersion;
  $("cancel-run").hidden = true; $("retry-run").hidden = true;
  $("empty-state").hidden = true; $("run-status").hidden = false;
  await updateRun(id, version);
}

async function updateRun(id, version) {
  if (version !== state.viewVersion || state.activeId !== id) return;
  try {
    const run = await getJSON(`/v1/experiments/${id}`);
    if (version !== state.viewVersion) return;
    $("status-badge").textContent = run.status; $("status-badge").className = `badge ${run.status === "failed" ? "failed" : ""}`;
    $("run-id").textContent = run.id;
    const p = run.progress; const fraction = p.planned_candidates ? Math.min(100, Math.round(100 * p.attempted_candidates / p.planned_candidates)) : 0;
    $("progress-fill").style.width = `${fraction}%`;
    $("progress-text").textContent = `${p.attempted_candidates}/${p.planned_candidates} candidate proposals · ${p.valid_candidates} valid · ${run.stage}`;
    $("run-message").textContent = run.error_message || (run.cancellation_requested ? "Cancellation requested. Waiting for the current compiler stage or execution to finish." : run.status === "completed" ? "Experiment finished. Fresh confirmation measurements are shown below." : "Measurements are saved after each trial.");
    const terminal = ["completed", "failed", "interrupted", "cancelled"].includes(run.status);
    $("cancel-run").hidden = terminal; $("cancel-run").disabled = Boolean(run.cancellation_requested);
    $("retry-run").hidden = !terminal;
    if (terminal) {
      state.poller = null;
      if (run.status === "completed") await loadResults(run.id, version);
      else await loadPartialResults(run, version);
      await loadSaved();
    } else {
      state.poller = setTimeout(() => updateRun(id, version), 2500);
    }
  } catch (error) { if (version === state.viewVersion) { showError(`${error.message}. Select the saved run to retry.`); clearTimeout(state.poller); } }
}

async function loadPartialResults(run, version) {
  const trials = await getJSON(`/v1/experiments/${run.id}/trials?limit=100`);
  if (version !== state.viewVersion) return;
  $("results").hidden = false;
  for (const id of ["metrics", "comparison-panel", "charts", "downloads"]) $(id).hidden = true;
  $("result-label").textContent = run.status; $("result-label").className = "badge failed";
  $("result-note").textContent = "Partial evidence only. This experiment did not complete confirmation and has no final performance claim.";
  renderTrials(trials.items, null, run.id);
}

function addMetric(container, label, value, detail, kind = "") {
  const card = document.createElement("div"); card.className = `metric ${kind}`;
  const title = document.createElement("small"); title.textContent = label;
  const number = document.createElement("strong"); number.textContent = value;
  const caption = document.createElement("span"); caption.textContent = detail;
  card.append(title, number, caption); container.append(card);
}

async function loadResults(id, version) {
  const [report, trials] = await Promise.all([getJSON(`/v1/experiments/${id}/report`), getJSON(`/v1/experiments/${id}/trials?limit=100`)]);
  if (version === state.viewVersion) renderResults(report, trials.items, `/v1/experiments/${id}/artifacts`, null, id);
}

function renderResults(report, trials, artifactBase, bundledDetails = null, liveId = null) {
  $("results").hidden = false;
  for (const id of ["metrics", "comparison-panel", "charts", "downloads"]) $(id).hidden = false;
  const strategy = report.request.strategies.includes("guided") ? "guided" : "random";
  const comparison = report.comparisons.tuning[`${strategy}_vs_o3`];
  const label = comparison.label || "unavailable";
  $("result-label").textContent = label.replaceAll("_", " ");
  $("result-label").className = `badge ${label === "demo_only" ? "demo" : label === "loss" ? "failed" : ""}`;
  $("result-note").textContent = `${report.mode === "quick" ? "Quick demo" : "Full run"}: ${report.request.confirmation_rounds} confirmation rounds, ${report.request.search_repetitions} search repetitions per initial sample set. ${report.mode === "quick" ? "One input; demo only." : "Tuning and held-out inputs; observations on this machine."}`;
  const metrics = $("metrics"); metrics.replaceChildren();
  addMetric(metrics, "Clang -O3 confirmed", ms(comparison.comparator_median_ns), "Median runtime on tuning input");
  addMetric(metrics, `${strategy === "guided" ? "Guided" : "Random"} winner confirmed`, ms(comparison.candidate_median_ns), (report.winners[strategy]?.action_ids || []).join(" → ") || "No valid confirmed trial");
  addMetric(metrics, "Versus -O3", comparison.runtime_reduction_percent == null ? "—" : `${comparison.runtime_reduction_percent.toFixed(1)}%`, "Positive means faster", comparison.label === "win" ? "good" : comparison.label === "loss" ? "bad" : "");
  renderComparisons(report);
  $("search-chart").src = `${artifactBase}/search.svg`;
  $("pareto-chart").src = `${artifactBase}/pareto.svg`;
  for (const [link, name] of [["summary-link", "summary.json"], ["trials-link", "trials.csv"], ["samples-link", "samples.csv"], ["environment-link", "environment.json"]]) $(link).href = `${artifactBase}/${name}`;
  renderTrials(trials, bundledDetails, liveId);
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

// Presentation help is local and instant; it does not change stored evidence.
const passHelp = {
  "instcombine": ["Simplify calculations", "Instruction combining", "Replaces unnecessarily complicated calculations with simpler ones.", "Before: result = x + 0;\nAfter:  result = x;"],
  "simplifycfg": ["Simplify branches", "Control-flow simplification", "Cleans up unnecessary decisions and paths through the program.", "Before: if (ready) return x; else return x;\nAfter:  return x;"],
  "gvn": ["Reuse repeated calculations", "Global value numbering", "Reuses an earlier result when the same calculation is still valid.", "Before: a = x + y; b = x + y;\nAfter:  a = x + y; b = a;"],
  "dce": ["Remove unused calculations", "Dead code elimination", "Removes calculations whose results are never used, when they have no required side effects.", "Before: unused = x + 1; return x;\nAfter:  return x;"],
  "licm": ["Move repeated work outside loops", "Loop-invariant code motion", "Moves a calculation outside a loop if its value stays the same and moving it is safe.", "Before: each loop iteration calculates a * b.\nAfter:  calculate a * b once, then reuse it in the loop."],
  "loop-unroll": ["Do several loop steps together", "Loop unrolling", "Repeats the loop body within one iteration, reducing how often the loop must check and update its counter.", "Before: process one item, then check the loop.\nAfter:  process four items, then check the loop.\nAny leftover items still need handling."],
  "loop-vectorize": ["Process loop items in parallel", "Loop vectorization", "Uses CPU vector instructions to process several independent loop items at once, when safe and worthwhile.", "Before: add array elements one pair at a time.\nAfter:  one vector operation adds several pairs at once."],
  "slp-vectorizer": ["Group similar calculations", "Superword-level parallelism (SLP) vectorization", "Packs nearby independent scalar calculations into a vector operation. It does not require a loop.", "Before: four separate, similar additions.\nAfter:  one vector addition processes those four pairs."],
  "loop-fusion": ["Combine neighboring loops", "Loop fusion", "Joins adjacent loops when their dependencies allow it. This can reuse data sooner and reduce loop overhead.", "Before: one loop fills a temporary array; the next reads it.\nAfter:  one combined loop produces and uses each item, if safe."],
  "loop-interchange": ["Swap nested loop order", "Loop interchange", "Switches which nested loop runs inside the other, when legal. This can improve how the program accesses memory.", "Before: visit a table column by column.\nAfter:  visit it row by row, if dependencies allow it."]
};
const trialResultLabels = {
  valid: "Passed checks", no_ir_change: "No code change", compiling: "Compiling",
  proposed: "Waiting", cancelled: "Cancelled", interrupted: "Interrupted",
  timeout: "Time limit reached", checksum_mismatch: "Wrong output — rejected",
  execution_failed: "Program failed", compile_failed: "Compilation failed",
  pass_parse_failed: "Pass could not run", ir_verification_failed: "Invalid compiler code — rejected"
};
function understandTrial(trial) {
  const content = $("trial-understand-content"); content.replaceChildren();
  $("trial-understand-title").textContent = `Understand attempt ${trial.ordinal} · ${trial.strategy === "guided" ? "Guided search" : "Random search"}`;
  const add = (parent, tag, text) => { const node = document.createElement(tag); node.textContent = text; parent.append(node); };
  add(content, "p", trial.strategy === "guided" ? "Guided search chooses sequences using features of the compiler code and earlier results. Depending on this run's settings, selection uses local rules or GPT." : "Random search chooses sequences randomly within the same allowed pass set and candidate budget. It is the comparison method.");
  add(content, "p", "The compiler tries the steps below in order. These examples explain each idea; they are not a claim that this exact change happened in your program.");
  const list = document.createElement("ol");
  for (const id of trial.action_ids) {
    const item = document.createElement("li"), help = passHelp[id];
    add(item, "h3", help?.[0] || id);
    add(item, "p", help?.[2] || "A compiler optimization step. See the technical evidence for its details.");
    if (help) {
      add(item, "pre", help[3]);
      add(item, "small", `Compiler term: ${help[1]} (${id})`);
    }
    list.append(item);
  }
  content.append(list);
  add(content, "h3", "What this result means");
  add(content, "p", trial.status === "valid" ? "This sequence passed the program's output checks and was timed. Passing checks does not mean it ran faster than another version." : trial.status === "no_ir_change" ? "The sequence produced no change in the compiler's intermediate code, so it was not timed as a new version. This is not an incorrect-output failure." : `${trialResultLabels[trial.status] || trial.status.replaceAll("_", " ")}. See the technical evidence for the recorded details.`);
  add(content, "p", `Typical runtime is the median (middle timing sample): ${ms(trial.search_median_ns)}. A dash means no timing is recorded. Program size is the compiled executable's size, not the number of source lines or instructions.`);
  add(content, "p", "An optimization may make no change or may make the program slower. Use the measured comparison results to judge speed.");
  $("trial-understand").showModal();
}

function renderTrials(trials, bundledDetails, liveId) {
  const body = $("trial-rows"); body.replaceChildren(); $("trial-detail").hidden = true;
  for (const trial of trials) {
    const row = document.createElement("tr");
    row.tabIndex = 0; row.setAttribute("aria-label", `Inspect ${trial.strategy} trial ${trial.ordinal}`);
    row.addEventListener("keydown", (event) => { if (event.target === row && ["Enter", " "].includes(event.key)) { event.preventDefault(); row.click(); } });
    for (const value of [trial.ordinal, trial.strategy === "guided" ? "Guided search" : trial.strategy === "random" ? "Random search" : trial.strategy, trial.action_ids.map(id => passHelp[id]?.[0] || id).join(" → "), trialResultLabels[trial.status] || trial.status.replaceAll("_", " "), ms(trial.search_median_ns), trial.executable_bytes == null ? "—" : `${trial.executable_bytes.toLocaleString()} B`]) {
      const cell = document.createElement("td"); cell.textContent = String(value); row.append(cell);
    }
    const helpCell = document.createElement("td"), helpButton = document.createElement("button");
    helpButton.type = "button"; helpButton.className = "understand-button"; helpButton.textContent = "Understand";
    helpButton.setAttribute("aria-label", `Understand ${trial.strategy} attempt ${trial.ordinal}`);
    helpButton.addEventListener("click", event => { event.stopPropagation(); understandTrial(trial); });
    helpCell.append(helpButton); row.append(helpCell);
    row.addEventListener("click", async () => {
      const version = state.viewVersion, detailVersion = ++state.detailVersion;
      try { const detail = bundledDetails ? bundledDetails[trial.id] : await getJSON(`/v1/experiments/${liveId}/trials/${trial.id}`); if (version !== state.viewVersion || detailVersion !== state.detailVersion) return; $("trial-detail").hidden = false; $("trial-detail").textContent = JSON.stringify(detail, null, 2); }
      catch (error) { if (version === state.viewVersion && detailVersion === state.detailVersion) showError(error.message); }
    });
    body.append(row);
  }
}

function renderComparisons(report) {
  const container = $("comparisons"); container.replaceChildren();
  const readable = (name) => name.replaceAll("_vs_", " vs ").replaceAll("o3", "Clang -O3").replaceAll("empty", "empty control");
  for (const [input, comparisons] of Object.entries(report.comparisons)) {
    const heading = document.createElement("h3"); heading.textContent = `${input === "holdout" ? "Held-out" : "Tuning"} confirmation`;
    const workload = report.request[input === "holdout" ? "holdout_input" : "tuning_input"];
    const note = document.createElement("p"); note.className = "muted"; note.textContent = `Size ${workload.size}, seed ${workload.seed}, repeat ${workload.repeat}. Positive reduction means the first strategy is faster.`;
    const wrapper = document.createElement("div"); wrapper.className = "table-wrap";
    const table = document.createElement("table"), head = document.createElement("thead"), body = document.createElement("tbody");
    const header = document.createElement("tr");
    for (const label of ["Comparison", "Candidate median ± MAD", "Comparator median ± MAD", "Reduction", "Faster pairs", "Outcome"]) { const cell = document.createElement("th"); cell.textContent = label; header.append(cell); }
    head.append(header);
    for (const [name, value] of Object.entries(comparisons)) {
      if (name === "variants") continue;
      const row = document.createElement("tr");
      for (const text of [readable(name), `${ms(value.candidate_median_ns)} ± ${ms(value.candidate_mad_ns)}`, `${ms(value.comparator_median_ns)} ± ${ms(value.comparator_mad_ns)}`, value.runtime_reduction_percent == null ? "—" : `${value.runtime_reduction_percent.toFixed(2)}%`, value.paired_rounds == null ? "—" : `${value.paired_wins}/${value.paired_rounds}`, value.label.replaceAll("_", " ")]) { const cell = document.createElement("td"); cell.textContent = text; row.append(cell); }
      body.append(row);
    }
    table.append(head, body); wrapper.append(table); container.append(heading, note, wrapper);
  }
  const explanation = $("result-explanation"); explanation.replaceChildren();
  const notes = report.explanations || [report.mode === "quick" ? "Demo only; no held-out performance claim." : "Win/loss requires at least ten paired rounds, a 2% median difference, and agreement in at least 80% of pairs. Otherwise the outcome is inconclusive."];
  for (const text of notes) { const p = document.createElement("p"); p.textContent = text; explanation.append(p); }
  const failures = document.createElement("p"); failures.textContent = `Non-valid proposals: ${Object.entries(report.failure_counts).map(([key, count]) => `${key}: ${count}`).join(", ") || "none"}. No-IR-change proposals consume budget and are not correctness failures.`; explanation.append(failures);
  const tuning = report.comparisons.tuning, holdout = report.comparisons.holdout;
  if (holdout && Object.keys(tuning).some((key) => tuning[key]?.label === "win" && holdout[key]?.label !== "win")) { const p = document.createElement("p"); p.textContent = "A tuning win did not reproduce on holdout; treat it as workload-specific."; explanation.append(p); }
}

$("benchmark").addEventListener("change", applyDefaults);
$("mode").addEventListener("change", applyDefaults);
$("run-form").addEventListener("submit", startRun);
$("refresh-saved").addEventListener("click", () => loadSaved().catch((error) => showError(error.message)));
$("load-more-saved").addEventListener("click", async () => { $("load-more-saved").disabled = true; try { await loadSaved(true); } catch (error) { showError(error.message); } finally { $("load-more-saved").disabled = false; } });
$("cancel-run").addEventListener("click", async () => {
  const id = state.activeId;
  if (!id) return;
  $("cancel-run").disabled = true;
  try { await getJSON(`/v1/experiments/${id}/cancel`, { method: "POST" }); if (state.activeId === id) await watchRun(id); }
  catch (error) { showError(error.message); $("cancel-run").disabled = false; }
});
$("retry-run").addEventListener("click", async () => {
  const id = state.activeId;
  if (!id) return;
  $("retry-run").disabled = true;
  try { const run = await getJSON(`/v1/experiments/${id}/retry`, { method: "POST" }); await watchRun(run.id); }
  catch (error) { showError(error.message); }
  finally { $("retry-run").disabled = false; }
});
const inspectionExample = `int sum_positive(const int *values, int count) {
    int total = 0;
    for (int i = 0; i < count; ++i) {
        int value = values[i];
        int doubled = value * 2;
        if (value > 0) {
            total += doubled + 0;
        }
    }
    return total;
}
`;
let inspectionDownloadURL = null;
function inspectionError(message) {
  $("inspection-error").textContent = message;
  $("inspection-error").hidden = false;
}
function clearInspection() {
  $("inspection-results").hidden = true;
  $("inspection-error").hidden = true;
  if (inspectionDownloadURL) { URL.revokeObjectURL(inspectionDownloadURL); inspectionDownloadURL = null; }
}
$("source-code").addEventListener("input", clearInspection);
$("load-code-example").addEventListener("click", () => {
  clearInspection(); $("source-code").value = inspectionExample; $("source-file").value = "";
});
$("source-file").addEventListener("change", async () => {
  clearInspection();
  const file = $("source-file").files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".c") || file.size > 65536) { inspectionError("Choose a single .c file no larger than 64 KiB."); return; }
  try { $("source-code").value = await file.text(); }
  catch (_error) { inspectionError("Could not read the selected file."); }
});
$("inspection-form").addEventListener("submit", async (event) => {
  event.preventDefault(); clearInspection();
  const quick = event.submitter?.value === "quick";
  const source = $("source-code").value;
  if (!source.trim() || new TextEncoder().encode(source).length > 65536) { inspectionError("Paste non-empty C source no larger than 64 KiB."); return; }
  const controls = ["inspect-button", "inspect-quick", "source-code", "source-file", "load-code-example", "inspection-max-passes", "inspection-explain"];
  controls.forEach((id) => { $(id).disabled = true; });
  $("inspection-progress").hidden = false;
  $("inspection-progress").textContent = quick ? "Quick: compiling → one cleanup pass → assembly…" : "Compiling → selecting passes → verifying → preparing readable explanation…";
  try {
    const data = await getJSON("/v1/inspections", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(quick ? { source, max_passes: 1, action_ids: ["instcombine"], llm_mode: "off", explain: false } :
        { source, max_passes: Number($("inspection-max-passes").value), explain: $("inspection-explain").checked }) });
    renderInspection(data);
    $("inspection-readable").hidden = quick;
    $("inspection-raw").open = quick;
  } catch (error) { inspectionError(error.message); }
  finally { controls.forEach((id) => { $(id).disabled = false; }); $("inspection-progress").hidden = true; }
});
function renderInspection(data) {
  const first = data.stages[0], last = data.stages[data.stages.length - 1];
  $("inspection-notice").textContent = data.notice;
  const selection = data.llm.mode === "openai" ? `${data.llm.model} · ${data.proposal.proposal_type}` : data.llm.mode === "manual" ? "Fixed local passes · no GPT selection" : "Local feature-guided selection";
  $("inspection-proposal").textContent = `${selection}: ${data.proposal.reason}`;
  $("inspection-proposal").hidden = data.llm.mode === "manual";
  $("inspection-pipeline").textContent = `C → LLVM IR → mem2reg → ${data.proposal.action_ids.join(" → ")} → assembly`;
  const metrics = $("inspection-metrics"); metrics.replaceChildren();
  addMetric(metrics, "IR instructions before", first.features.instruction_count, "Approximate textual count");
  addMetric(metrics, "IR instructions after", last.features.instruction_count, "A count change does not establish a speedup");
  addMetric(metrics, "Suggested passes applied", data.proposal.action_ids.length, "LLVM verifier checked every transformation");
  $("inspection-before").textContent = first.ir;
  $("inspection-after").textContent = last.ir;
  const readable = $("inspection-readable"); readable.replaceChildren();
  const explanation = data.explanation;
  function paragraph(parent, text, tag = "p") {
    const element = document.createElement(tag); element.textContent = text; parent.append(element);
  }
  if (explanation?.status === "completed") {
    paragraph(readable, "Your code, explained", "h3");
    paragraph(readable, explanation.original_behavior);
    const comparison = document.createElement("div"); comparison.className = "inspection-ir-grid";
    for (const [label, code] of [["Original C", data.source], [explanation.language === "c" ? "Simpler C representation" : "Assembly with explanatory comments", explanation.readable_code]]) {
      const column = document.createElement("div"); paragraph(column, label, "h3"); paragraph(column, code, "pre"); comparison.append(column);
    }
    readable.append(comparison);
    paragraph(readable, "What changed", "h3"); paragraph(readable, explanation.changes);
    paragraph(readable, `GPT-6 Luna explanation · Illustrative reconstruction; equivalence has not been verified. ${explanation.caveats}`);
  } else {
    paragraph(readable, explanation?.message || "Readable explanation is not included in this inspection.");
  }
  const stageNames = {
    mem2reg: ["Remove temporary variable storage", "Promotes eligible local variables from stack loads and stores to direct values in LLVM IR."],
    instcombine: ["Simplify calculations", "Combines and simplifies arithmetic and other instructions where LLVM's rules allow it."],
    simplifycfg: ["Simplify branches and paths", "Cleans up redundant branches and control-flow blocks."],
    dce: ["Remove unused calculations", "Removes instructions whose results are unused and which have no required side effects."]
  };
  const stages = $("inspection-stages"); stages.replaceChildren();
  data.stages.forEach((stage, index) => {
    const details = document.createElement("details"), summary = document.createElement("summary");
    const name = index === 0 ? "Translate C into compiler instructions" : stageNames[stage.action_id]?.[0] || stage.title;
    const count = stage.features.instruction_count;
    const change = index === 0 ? `${count} IR instructions` : `${data.stages[index - 1].features.instruction_count} → ${count} IR instructions${stage.changed ? "" : " · nothing changed"}`;
    summary.textContent = `${index + 1}. ${name} · ${change}`;
    const description = document.createElement("p");
    description.textContent = explanation?.status === "completed" ? explanation.stage_explanations[index] :
      (index === 0 ? "Clang translates your C into LLVM's intermediate representation. This is compiler code, not CPU instructions." :
        `${stageNames[stage.action_id]?.[1] || "Applies this LLVM transformation when possible."} ${stage.changed ? "The compiler representation changed in this run." : "This step made no change in this run."}`);
    const pipeline = document.createElement("p"); pipeline.className = "muted"; pipeline.textContent = stage.pipeline;
    const counts = document.createElement("p"); counts.className = "note";
    counts.textContent = `Loads: ${stage.features.loads} · Stores: ${stage.features.stores} · Conditional branches: ${stage.features.conditional_branches} · Phi nodes: ${stage.features.phi_nodes}`;
    const code = document.createElement("pre"); code.textContent = index === 0 ? stage.ir : stage.diff || "No textual IR change. This pass found no applicable transformation at this stage.";
    details.append(summary, pipeline, counts, code);
    const card = document.createElement("div"); card.append(description, details); stages.append(card);
  });
  $("inspection-assembly").textContent = data.assembly;
  $("inspection-commands").textContent = JSON.stringify(data.commands, null, 2);
  inspectionDownloadURL = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  $("inspection-download").href = inspectionDownloadURL;
  $("inspection-results").hidden = false;
  $("inspection-results").scrollIntoView({ behavior: "smooth", block: "start" });
}
loadSetup().catch((error) => showError(error.message));
