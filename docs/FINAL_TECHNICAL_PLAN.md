# Evidence-Guided LLVM Pass Tuner — Final Technical Plan

**Prepared:** 2026-10-07  
**Status:** historical implementation specification. Core implementation and three full experiments are complete. See `PROJECT_STATUS.md` for current functionality, verification, and remaining extensions. References below to the environment "at plan time" describe historical conditions.
**Project type:** local Compiler Design course project, one Linux machine, three built-in C17 benchmarks

## 1. Decision and document precedence

Build a local tool that inspects LLVM IR, proposes bounded pass sequences, compiles them, rejects incorrect binaries, measures the valid ones, and explains the observed result. The scientific question is whether an IR-guided search finds a better runtime than an equally budgeted random search on these workloads. Direct `clang -O3` is a separate practical reference; the empty-action control isolates the effect of added passes after the common `-O1` IR seed.

This plan reconciles the two 2026-10-05 documents with the older Review 1 presentation. The six-day experiment design governs implementation. The presentation's promised **single webpage** remains a delivery requirement, implemented as a small static page served by FastAPI. Its **5–10 second live optimization** claim is withdrawn: five candidates with correctness checks and repeated 0.3–1.0 second runs cannot reliably finish in that time. A saved report can open immediately; a live quick run targets approximately 1–2 minutes after calibration. The presentation's three-week schedule and four-to-six-candidate research scope are superseded by this six-day plan and its explicitly bounded modes. Revise the slides and speech before the next review so they agree with measured demo latency and scope.

### Success criteria

1. One command runs a candidate through Clang → `opt` → `llc` → link → correctness → timing → persistent report.
2. Three distinct built-in benchmarks run with deterministic, defined C behavior.
3. Guided and random policies get the same proposal budget, action catalog, validation, and timing protocol. Neither policy sees the other's trial outcomes.
4. Every final performance statement uses fresh, interleaved confirmation samples and names its comparator and input.
5. CLI, local API, and webpage read the same stored experiment records. A saved full experiment remains inspectable without rerunning the compiler.

No speedup over `-O3` is guaranteed. A valid negative result is a successful experiment.

## 2. Scope and priority

| Priority | Required behavior |
|---|---|
| P0, working engine | Matching LLVM toolchain; benchmark registry; deterministic oracle; validated action catalog; compiler runner; correctness and timeout gates; measurement; SQLite records; guided and random policies; CLI; empty control and `-O3` baseline; report generation. |
| P1, complete course deliverable | All three benchmarks; held-out confirmation; local FastAPI API; one-page browser interface; evidence trail; charts; documented methodology; demo script. Add LangGraph only after the plain service loop works and its dependency passes an import/smoke test. |
| P2, optional research extensions | LLM proposer and explanation, more benchmarks, custom C input, hardware counters, `.text` size, multi-seed policy comparison, cancellation, and broader deployment. These require their own acceptance checks and must not delay P0/P1. |

The required system makes **zero LLM requests**. “Agent” names denote bounded software decision roles; the initial policies are deterministic Python rules. No training, cloud service, user account, uploaded source, arbitrary shell command, or production deployment is in P0/P1.

## 3. Current workspace and environment gate

At plan time, this Windows folder contains a PowerPoint deck and two speech PDFs. It is not yet a Git repository and contains no tuner source, tests, benchmark results, or preinstalled LLVM tools. Ubuntu 26.04 LTS is present in WSL, with Python 3.14.4 and package candidates `clang-21` and `llvm-21` at `21.1.8-6ubuntu1`; those packages are not installed. The default WSL distribution is `docker-desktop`, so **all WSL commands must select `Ubuntu` explicitly** until the default is changed. For example:

```powershell
wsl -d Ubuntu -e bash -lc 'cat /etc/os-release'
```

Develop and run Linux binaries inside Ubuntu's Linux filesystem, then copy final source and exported evidence into the project workspace or make this project a WSL-backed checkout. Avoid timing executables on `/mnt/c` or a OneDrive path. The exact location and Git commit must be recorded. Day 1 preflight installs and probes the packages; package availability is not proof that a pass pipeline works.

```bash
sudo apt-get update
sudo apt-get install clang-21 llvm-21 python3-venv
clang-21 --version
opt-21 --version
llc-21 --version
opt-21 --print-passes
```

Use one committed dependency lock/constraints file after installing the chosen Python packages. Python standard library is sufficient for the engine (`argparse`, `subprocess`, `sqlite3`, `hashlib`, `statistics`, `json`, `csv`, `threading`, `queue`, `pathlib`). P1 adds `fastapi`, `pydantic`, and `uvicorn`; `langgraph` is conditional on a Python 3.14 import and one graph smoke test. If incompatible, use a supported Python interpreter for the optional graph or keep the plain loop. Do not make graph installation a prerequisite for P0.

## 4. Repository layout and module contracts

```text
compiler-autotuner/
  README.md
  pyproject.toml                 # package, CLI entry point, pinned dependency groups
  constraints.txt               # resolved versions used for final evaluation
  autotuner/
    __main__.py, cli.py          # CLI parsing and terminal progress
    models.py                    # typed request/response and persisted enums
    config.py                    # paths, flags, limits, benchmark manifest
    registry.py                  # built-in source and workload contracts
    toolchain.py                 # subprocess stages, preflight, action composition
    actions.py                   # action IDs and validated pass templates
    oracle.py                    # reference checksum rules and comparison
    features.py                  # seed IR features and explicit uncertainty
    policies.py                  # guided and random proposals
    evaluator.py                 # ranking, tie rule, noise policy
    measure.py                   # warmups, samples, calibration, confirmation
    storage.py                   # SQLite transactions and artifact manifest
    service.py                   # common CLI/API use cases and worker reservation
    report.py                    # summaries, CSV, JSON, SVG charts
    api.py                       # FastAPI adapter only
    graph.py                     # optional LangGraph adapter
  benchmarks/
    dot.c, matmul.c, histogram.c
    manifest.json
  web/
    index.html, app.js, style.css
  tests/
    test_pipeline.py, test_correctness.py, test_budget.py,
    test_api.py, test_report.py
  docs/
    methodology.md, limitations.md, demo_script.md
  runs/                         # generated, ignored except a labeled sample export
```

`TuningService` owns `preflight`, `list_benchmarks`, `list_actions`, `create_experiment`, `run_experiment`, `get_experiment`, `list_trials`, `get_trial`, and `build_report`. CLI and HTTP call these methods. The policy returns **action IDs**, never a command string. The toolchain alone constructs subprocess argument arrays. The evaluator cannot bypass correctness. The report builder reads persisted records and does not compile or remeasure.

## 5. Benchmark contract and independent correctness evidence

Every built-in C17 source accepts `--size N --seed S --repeat R`. `N` is positive, `S` is a nonnegative 64-bit integer, and `R` is a positive integer. It writes exactly one stdout line, `checksum:<16 lowercase hexadecimal digits>\n`; diagnostics use stderr. Invalid arguments exit nonzero. The manifest fixes accepted ranges, correctness edge cases, tuning input, holdout input, quick input, and a calibrated repeat count. `size`, `seed`, and `repeat` are all workload identity fields and must be identical for compared binaries.

Use a documented `splitmix64` generator with unsigned 64-bit arithmetic to derive inputs from `seed`; convert generated values with explicit masks. The C sources use fixed-width unsigned integer types, bounds-checked allocation, and deterministic loop order. No signed overflow, uninitialized reads, data races, undefined shifts, or floating-point tolerance questions are allowed. The resulting checksum must depend on every computed result. For array results, use specified FNV-1a over the little-endian bytes of each `uint64_t` output element so the independent Python reference can reproduce it exactly.

| Benchmark | Kernel and expected optimization opportunity | Correctness and workload notes |
|---|---|---|
| `dot` | Two runtime-filled `uint32_t` arrays; `uint64_t` accumulation of products in source order; loop/vectorization probe. | Check arithmetic modulo 2^64; calibrate `repeat` so measured time is not dominated by allocation and input generation. Vary one deterministic element between repeats and fold each repeat result into checksum so repeat work cannot be eliminated. |
| `matmul` | Row-major integer matrix multiply with fixed `i,j,k` loop order and `uint64_t` output cells; nested-loop and memory opportunity. | Size range selected against memory and ten-second execution cap. Do not change algorithm between variants. |
| `histogram` | Runtime-filled values routed through a documented branch transform into 256 `uint64_t` bins; branch/memory-write opportunity. | Sum bins and hash all bins; check that total count equals processed elements across repeats. |

The **trusted small-case oracle** is a separate Python implementation of each algorithm, generator, and checksum, with hand-computed edge fixtures (`N=1`, minimum valid seed, and two ordinary small inputs). This detects benchmark mistakes shared by `-O0` and `-O3`. For larger tuning and holdout workloads, run `-O0`, direct `-O3`, and empty-action binaries once and require exact agreement with each other; save that output as the experiment's expected checksum. A mismatch fails the experiment before any search. On every timed execution, require exit code zero, one correctly formatted checksum line, and equality to the saved expected checksum; the timing sample is discarded and the variant invalidated otherwise.

The manifest's initial numeric sizes and repeats are **provisional**. Calibration measures the actual binaries on the presentation machine and freezes per-benchmark values yielding roughly 0.3–1.0 seconds per execution, with the chosen kernel doing most of the work. Record calibration observations and the final values. A new teacher-selected size/seed/repeat is a new workload: run the oracle crosscheck first and retune in quick mode. Do not advertise arbitrary C programs as supported.

## 6. Compiler pipeline, flags, and action validation

Use Clang/LLVM major version 21 throughout. Resolve absolute executable paths, full version strings, target triple, CPU feature choice, relocation/PIE choice, linker choice, standard library path, optimization and debug flags, and environment in `environment.json`. Freeze one target policy for all variants. Do not rely on `llc` auto-detecting a possibly different CPU from Clang; choose a target/CPU flag pair that both tools accept, verify generated IR triple/data layout, and record both command arrays. If an explicit target choice is impractical, verify and document the effective defaults with a smoke run before the experiment. Disable LTO in all paths.

Illustrative stages, with resolved flags inserted from one immutable config object:

```text
oracle:     clang-21 -std=c17 -O0 -fno-lto <target/link flags> source.c -o oracle
reference:  clang-21 -std=c17 -O3 -fno-lto <target/link flags> source.c -o baseline_o3
seed:       clang-21 -std=c17 -O1 -fno-lto -emit-llvm -c <target flags> source.c -o base.bc
control:    llc-21 -O3 -filetype=obj <target/relocation flags> base.bc -o empty.o
            clang-21 <link flags> empty.o -o empty
candidate:  opt-21 -verify-each -passes=<validated pipeline> base.bc -o candidate.bc
            llc-21 -O3 -filetype=obj <target/relocation flags> candidate.bc -o candidate.o
            clang-21 <link flags> candidate.o -o candidate
```

`clang -O3` is a **different complete compiler pipeline**, so comparisons against it are practical reference comparisons, not an isolated test of one added pass. The empty-action control uses the same `-O1` bitcode and `llc -O3` backend as candidates. A direct `-O1` executable may be recorded as diagnostic context but is not substituted for the empty control.

The provisional action IDs are `instcombine`, `simplifycfg`, `gvn`, `dce`, `loop-unroll`, and `loop-vectorize`; `licm` and `slp-vectorizer` may be added if validated. Each catalog row contains `{id, description, ir_unit, template, prerequisites, hypothesis, available, reason}`. LLVM's new pass manager distinguishes module, function, and loop nesting, so do not concatenate raw pass names. Preflight must:

1. Check `opt-21 --print-passes` and test each **complete template** on small and loop-containing IR.
2. Test each permitted *ordered combination pattern* as composed by the builder, including mixed function/loop actions. At minimum, enumerate all candidate pairs; generated longer strings are parsed in a preflight `opt` invocation before they can consume search budget.
3. Apply `-verify-each`, link, execute, and compare checksum in smoke tests.
4. Record the exact effective pipeline, prerequisite passes, IR hash before and after, diagnostics, and catalog hash. Exclude invalid actions with a reason; a no-op action is flagged and removed when it is a no-op on all three seeds.

Sequences contain 1–4 distinct action IDs in significant order. Empty sequence is a control outside the proposal budget. A failed generated candidate consumes its policy's proposal budget. Duplicates within one policy are resampled before consuming it. A pass pipeline whose normalized textual IR is identical to the seed is a recorded `no_ir_change` trial, not silently counted as a speedup claim; it still consumes a proposal once compiled. Raw bitcode byte hashes alone are insufficient for this decision because serialization details can differ. No action is silently replaced.

## 7. Features, policies, and bounded search

`IRFeatureExtractor` runs once on each common seed and records instruction count, basic-block count, conditional branch count, PHI count, load/store counts, and loop presence. It also counts instructions in each successfully produced candidate IR using the same method, storing before/after counts for explanation. Prefer an LLVM analysis command when available; any textual IR heuristic is labeled approximate. The feature schema records extractor version and missing values. These features only weight hypotheses; a lower IR instruction count is not evidence of lower runtime.

**Guided policy:** start with at least four unique one-action probes selected by feature priors. Afterwards retain the top three valid sequences by search median, then propose a neighbor by append, delete, replace, or swap. Use a seeded PRNG for weighted choices; reserve approximately 25% of proposals for uninformed exploration. Record `proposal_type`, parent trial ID if mutated, feature weights, own-history trial IDs used, and RNG seed. If all neighbors are exhausted, sample an unseen sequence from the finite catalog. Stop at the exact proposal budget or explicit search-space exhaustion.

**Random policy:** sample unique lengths 1–4 and ordered action IDs from the same catalog with its own fixed seed; it gets no IR features and no guided history. Failed parses, compilation, correctness, and timeouts consume a proposal. Both strategies use identical compiler flags, correctness cases, search repetitions, ranking, and candidate budget. Alternate one guided and one random trial where possible to reduce thermal drift. An equal number of candidate proposals is the primary fairness unit; report total compile and wall-clock effort separately.

Search medians rank correct trials. If two medians differ by at most 1%, smaller executable size wins the pragmatic tie. Record every trial, including regressions and failures. A high-noise trial may receive **at most one** additional five-sample search set, using a fixed predeclared trigger (`MAD / median > 0.03`); label both sample sets. The extra set does not consume a proposal but its cost is reported. Do not adaptively remeasure only the desired winner after seeing its apparent speedup.

One guided/random pair on each of three benchmarks is the required descriptive comparison. Do not claim general superiority of guidance from three workloads or one random seed. If time allows, run three independent, matched policy seeds per benchmark and report the distribution of confirmed outcomes as P2. The one-seed core still directly answers what happened in this bounded experiment.

## 8. Measurement and confirmation rules

Run one candidate at a time on AC power and record CPU model, WSL kernel, OS, date, and any known background-load issue. Each binary gets one warmup and five fresh search executions; `time.perf_counter_ns()` wraps the entire child process, including its deterministic input generation and checksum output. `stdout` is bounded to one short line and `stderr` to a capped log file. Each execution has a ten-second timeout after calibration; compiler/link stages have 30-second timeouts. On timeout, kill the process group and classify the trial. Save raw nanoseconds, not rounded seconds.

After a full search, choose the best valid trial per strategy on tuning data. Confirm direct `-O3`, empty control, guided winner, and random winner in **ten randomized interleaved rounds** on both tuning and held-out workloads, with a fresh warmup per binary/input. Save the permutation for each round. Recheck checksum on every run. Never use held-out performance to propose or select candidates; it is only a final generalization check. If a winner fails holdout correctness, invalidate it and report failure rather than selecting another based on holdout speed. Quick mode uses three tuning-only confirmation rounds and labels the result `demo_only`; it cannot use the full-run win/loss rule or support a held-out claim.

For each variant/input, report median, minimum, maximum, MAD, all samples, and executable bytes. For each comparison, report `speedup = median(comparator) / median(candidate)` and `runtime_reduction_percent = 100 × (median(comparator) − median(candidate)) / median(comparator)`. Show comparisons with both empty control and `-O3`; guided versus random is a separate comparison. Show a runtime/size Pareto plot and best-so-far search curve. IR instruction count is explanatory only.

Predeclare an **exploratory full-run** outcome label: `win` requires at least 2% lower confirmed median and improvement in at least 8 of 10 paired rounds; `loss` uses the symmetric rule; otherwise `inconclusive`. Report exact paired round differences so readers can judge noise. This is a practical course-demo rule, not a p-value or proof of significance. Quick mode always uses `demo_only` instead. A tuning win that fails on holdout must be described as workload-specific. No selective benchmark changes after seeing final data; a changed source, flags, catalog, or input starts a new experiment.

## 9. Persistence, cache, failure states, and worker lifecycle

SQLite is the canonical store (`PRAGMA foreign_keys=ON`, WAL mode). Use UUIDs for public IDs, UTC ISO 8601 timestamps, explicit schema version, and migrations only through checked-in SQL. Proposed tables:

```text
experiments(id PK, parent_experiment_id FK nullable, status, stage, mode,
  benchmark_id, request_json, source_sha256, environment_json,
  catalog_json, catalog_hash, created_at, started_at, finished_at,
  error_code nullable, schema_version)
trials(id PK, experiment_id FK, strategy, ordinal, action_ids_json,
  effective_pipeline, proposal_type, proposal_reason, parent_trial_id nullable,
  status, error_code nullable, ir_sha256 nullable,
  ir_instructions_before nullable, ir_instructions_after nullable,
  executable_bytes nullable,
  search_median_ns nullable, search_mad_ns nullable,
  noise_flag boolean, created_at,
  UNIQUE(experiment_id, strategy, ordinal), UNIQUE(experiment_id, strategy, action_ids_json))
variants(id PK, experiment_id FK, trial_id FK nullable, kind,
  executable_path, executable_sha256, command_manifest_path, created_at)
samples(id PK, experiment_id FK, variant_id FK, phase, input_kind, size,
  seed, repeat, round, order_in_round, repetition, duration_ns,
  checksum, valid, created_at)
artifacts(experiment_id FK, name, relative_path, sha256, media_type,
  created_at, PRIMARY KEY(experiment_id, name))
worker_slot(singleton_id PK, experiment_id FK nullable, owner_id nullable)
```

`phase` distinguishes calibration, baseline correctness, search warmup, search, confirmation tuning, and confirmation holdout. Warmups and failed executions never enter runtime medians. The manifest also records complete subprocess argument arrays, working directories, return codes, bounded stdout/stderr paths, timeouts, hashes of source/IR/binary, target and linker flags, random seeds, library versions, and Git commit when one exists. Export immutable `summary.json`, `trials.csv`, `samples.csv`, `environment.json`, and SVG/PNG charts. A report regenerated from SQLite must match the exported summary numerically.

The compile cache key includes source hash, compiler/LLVM paths and full versions, target/CPU/relocation/link flags, action catalog version, exact pipeline, and build environment. It caches **compiled binaries only**. Runtime samples are never reused for a new experiment. Use atomic temp-file-to-final-file replacement and SHA-256 checks before using cached artifacts. Do not include input size/seed in the compile key unless these are compile-time defines; they are required in timing identity and expected-checksum records.

The API runs with one Uvicorn process, `--workers 1`, no reload. A dedicated worker thread drains a one-item local queue. `POST` transactionally reserves `worker_slot`, inserts a `queued` experiment, then enqueues it; a second POST or CLI tune receives `RUN_IN_PROGRESS`. The CLI uses the same database reservation to avoid parallel measurements with the API. On startup, mark stale `queued` or `running` experiments `interrupted`, clear their slot, and preserve all completed trial records. A retry creates a new experiment with `parent_experiment_id`; it does not mutate old results. This is a local single-process design, not a durable distributed queue. FastAPI's `BackgroundTasks` is not the experiment worker: the official guidance positions it for small in-process work and notes heavier jobs may need a task queue.

Trial states: `proposed`, `compiling`, `valid`, `pass_parse_failed`, `compile_failed`, `ir_verification_failed`, `execution_failed`, `timeout`, `checksum_mismatch`, and `no_ir_change`. `no_ir_change` consumes a proposal but skips timing and cannot win; a noisy valid trial retains status `valid` with a separate diagnostic flag. Experiment states: `queued`, `running`, `completed`, `failed`, `interrupted`. Every terminal failure records a stable error code and a safe summary; full diagnostics remain in local logs.

## 10. CLI contract

```text
python -m autotuner doctor
python -m autotuner benchmarks
python -m autotuner actions
python -m autotuner tune --benchmark matmul --mode quick [--size N --seed S --repeat R]
python -m autotuner tune --benchmark matmul --mode full [--budget 20]
python -m autotuner status <experiment-id>
python -m autotuner trials <experiment-id> [--strategy guided|random]
python -m autotuner report <experiment-id> [--output-dir PATH]
python -m autotuner serve --host 127.0.0.1 --port 8000
```

`doctor` prints environment, catalog exclusions, and one full smoke compile/run. `tune` waits and prints progress and terminal status. `report` is read-only and works after compiler tools are removed, provided stored records exist. Quick mode defaults to guided, budget five, three search repetitions, and three confirmation rounds; label these quick results as a demonstration rather than comparable to a full guided/random experiment. Full mode defaults to guided and random, budget 20 **per strategy**, five search repetitions, and ten confirmation rounds. CLI and API validate through the same request model.

## 11. HTTP API, schemas, and webpage

Bind only to `127.0.0.1:8000`. All custom API routes begin `/v1`; JSON uses UTF-8, UUID IDs, ISO 8601 UTC timestamps, integer nanosecond durations, and bytes for executable size. FastAPI generates `/docs` and `/openapi.json`. One static webpage at `/` uses same-origin `fetch`; no Node build or JS framework is needed. It provides benchmark and workload selection, quick/full start, 2–5 second status polling, visible progress, trial table, one trial's complete evidence trail, comparison cards, and SVG charts. It labels saved reports versus live measurements and displays `inconclusive` without green “gain” styling.

### Request model: `ExperimentCreateV1`

```json
{
  "benchmark_id": "matmul", "mode": "full",
  "tuning_input": {"size": 256, "seed": 17, "repeat": 1},
  "holdout_input": {"size": 320, "seed": 91, "repeat": 1},
  "strategies": ["guided", "random"],
  "budget_per_strategy": 20, "max_sequence_length": 4,
  "search_repetitions": 5, "confirmation_rounds": 10,
  "llm_mode": "off"
}
```

Only `benchmark_id` and `mode` are mandatory; defaults are resolved from the calibrated manifest and persisted in the immutable request snapshot. Full mode requires different tuning and holdout workload tuples and both strategies for the primary comparison. Supported limits: budget 1–20 per strategy; sequence length 1–4; search repetitions 3–10; confirmation rounds 3–15; input values and repeat within each benchmark's manifest. `llm_mode` accepts only `off` in P0/P1. Invalid requests receive `422` with a field-specific error.

| Endpoint | Request | Success response | Main error cases |
|---|---|---|---|
| `GET /v1/health` | None | `200 {status, api_version, worker, database, toolchain_ready}`; read-only, no compiler invocation | `500` only if health handler itself fails |
| `GET /v1/benchmarks` | None | `200 {items:[BenchmarkSummary]}` with ranges, defaults, source hash, calibrated-duration estimate | `500` storage/registry defect |
| `GET /v1/actions` | `include_unavailable=false` optional | `200 {llvm_version,catalog_hash,items:[ActionSummary]}` | `422` malformed query |
| `GET /v1/experiments` | `limit=1..100`, `offset>=0`, optional `status` | `200 {items:[ExperimentSummary],total,limit,offset}` newest first; powers saved-report selection in the webpage | `422` invalid filters |
| `POST /v1/experiments` | `ExperimentCreateV1` JSON | `202 {id,status,mode,benchmark_id,progress,created_at,links}` and `Location` header | `409 RUN_IN_PROGRESS`; `422 INVALID_REQUEST`; `503` preflight/storage unavailable |
| `GET /v1/experiments/{id}` | UUID | `200 {request,status,stage,progress,best_trial_ids,timestamps,error_code}` | `404 NOT_FOUND`; `422` bad UUID |
| `GET /v1/experiments/{id}/trials` | `strategy`, `status`, `limit=1..100`, `offset>=0`, `sort=ordinal|runtime_asc` | `200 {items:[TrialSummary],total,limit,offset}`; null runtime last | `404`, `422` |
| `GET /v1/experiments/{id}/trials/{trial_id}` | Two UUIDs | `200 TrialDetail` with action IDs, effective pipeline, proposal evidence, commands, IR hashes, correctness cases, size, all sample IDs/raw times, diagnostic excerpt | `404` if run/trial pair absent; `422` bad UUID |
| `GET /v1/experiments/{id}/report` | UUID | `200 ReportV1` after completion; `202 {status,progress}` plus `Retry-After: 3` while running | `404`; `409 REPORT_UNAVAILABLE` for failed/interrupted |
| `GET /v1/experiments/{id}/artifacts/{name}` | Whitelisted `summary.json`, `trials.csv`, `samples.csv`, `environment.json`, `search.svg`, `pareto.svg` | `200` with correct media type, content length, and stored SHA-256/ETag | `404`; reject any path separator or unknown name |

This is **ten** business endpoints, plus `/` and framework docs. The original seven-route API could not fulfill its own demo promise to show a trial's raw samples/checksum, open saved full experiments from the webpage, or make report artifact links usable there. Experiment listing, trial detail, and artifact retrieval are therefore required P1 routes. No arbitrary file paths, source uploads, or action edits enter the API.

`TrialSummary` fields: `id`, `strategy`, `ordinal`, `action_ids`, `status`, `proposal_reason`, `search_median_ns`, `search_mad_ns`, `executable_bytes`, and `correctness`. `TrialDetail` adds exact commands and return codes, expected/observed checksums for each correctness case, IR hashes, warmup/search samples, parent/evidence trial IDs, and capped diagnostics. `ReportV1` includes environment and workload snapshots, baseline and finalist summaries, each comparator's formulas and numbers, tuning and holdout labels, failure counts, methodology, limitations, and artifact links. Numeric results are computed by code only.

Use one error envelope for all endpoints:

```json
{"error":{"code":"RUN_IN_PROGRESS","message":"An experiment is already active","details":{"experiment_id":"<uuid>"}}}
```

Error codes include `NOT_FOUND`, `INVALID_REQUEST`, `RUN_IN_PROGRESS`, `TOOLCHAIN_UNAVAILABLE`, `INSUFFICIENT_ACTIONS`, `REFERENCE_MISMATCH`, `REPORT_UNAVAILABLE`, and `INTERNAL_ERROR`. Full compiler stderr is never embedded in normal API responses. The API has no authentication because it is local-only; changing the bind address requires a separate security design.

## 12. Experiment workflow and graph contract

```text
validate request → reserve worker → snapshot environment/catalog/source
→ independent small-case oracle + -O0/-O3/empty crosscheck
→ create common -O1 bitcode → extract IR features
→ alternate guided/random proposals → parse/compile/verify
→ correctness gate → warmup/timing → persist each trial
→ pick tuning winners → interleaved tuning + holdout confirmation
→ atomic report exports → completed
```

The core is an ordinary Python loop with explicit budget counters and terminal states. If LangGraph passes installation, wrap **the same service methods** in nodes `preflight`, `baseline`, `inspect_ir`, `propose`, `compile`, `verify`, `measure`, `evaluate`, `confirm`, `report`. Graph state contains IDs, small feature summaries, counters, current strategy/trial ID, best IDs, and stop reason; SQLite/files hold verbose logs and samples. A conditional edge must decrement/inspect the finite budget and never depend on an LLM response to terminate. LangGraph changes inspectability, not the experiment's algorithm or numbers.

Decision roles:

| Role | Inputs | Allowed decision | Output |
|---|---|---|---|
| IR analyst | Common seed IR and extractor version | Classify coarse features and confidence | `FeatureSummary` |
| Search agent | Allowed catalog, own history, features only if guided, remaining budget | Pick one unseen action sequence or stop | `Proposal` with reason/evidence IDs |
| Evaluator | Correctness state, samples, size, noise threshold | Reject, rank, or request one bounded remeasure | `EvaluationDecision` |
| Compiler/correctness/measurement tools | Validated request and action IDs | Execute fixed procedures only | Immutable artifacts and measurements |
| Explanation writer | Verified report data | Deterministic text template in P1 | Cited trial IDs and cautious narrative |

## 13. Verification and acceptance gates

Tests should target failure modes, not duplicate implementation formulas:

1. **Toolchain integration:** tiny C input survives bitcode, mixed-action `opt` pipeline, `llc`, link, execution, and checksum comparison.
2. **Oracle:** intentionally wrong-output fixture and malformed/duplicate stdout are rejected before timing or winner selection; edge fixtures match independent Python calculation.
3. **Timeout/process cleanup:** sleeping child is killed and classified; the next candidate still runs.
4. **Budget/fairness:** each full strategy consumes exactly 20 unique proposals or records exhaustion, cannot read the other's history, and uses identical workload and flags.
5. **Storage/restart:** interrupted run preserves committed trials; second simultaneous experiment gets `409`; report reconstructs from SQLite without compilation; compiled cache validates its key/hash while timings are fresh.
6. **API/UI:** POST a quick run, poll to terminal, list saved experiments, fetch trial detail and artifacts; `/openapi.json` lists ten business routes; webpage displays a failed or inconclusive run accurately.
7. **Measurement:** saved final comparisons use only confirmation samples; warmups and failed executions are excluded; interleaving order is stored.
8. **Demo:** on the presentation machine, live quick mode fits the measured time budget and a saved full report opens immediately. Record actual seconds, not a guessed promise.

P0 exit gate: one benchmark, guided and random, full correctness and persistent report work with one command. P1 exit gate: all three benchmarks, saved raw evidence, held-out confirmation, ten API routes, webpage, and reproducible demo. If a phase is late, remove LangGraph and P2 features first; never remove correctness, controls, raw samples, or transparent failure reporting.

## 14. Six-day execution order

| Day | Work | End-of-day evidence |
|---|---|---|
| 1 | Create Git repo in Ubuntu filesystem; install matched LLVM tools; freeze target/link choices; implement one benchmark and independent small oracle; validate action templates. | `doctor` and one candidate compile/run log. |
| 2 | Finish three C benchmarks; add toolchain, correctness, timeouts, calibration, SQLite, immutable manifests, and empty/O3 controls. | One five-candidate quick run with all raw records. |
| 3 | Guided/random policies, alternating scheduler, ranking, CLI, cache, and P0 tests. | One full benchmark run with equal budgets and a regenerated report. Freeze experiment semantics. |
| 4 | Add IR feature extractor and FastAPI ten-route adapter; implement static webpage and trial evidence view. Add LangGraph only if the adapter and Python version are stable. | API smoke test and browser view of one completed run. |
| 5 | Run calibrated full experiments on three benchmarks, confirm finalists on tuning/holdout, inspect noisy/failing cases, generate charts. | Frozen `summary.json`, CSVs, environment manifests, and comparison tables. |
| 6 | Finish README/methodology/limitations, revise slides to match real latency and scope, rehearse live quick and saved full reports twice. | Five-minute script, measured demo runtime, complete deliverable package. |

The previously estimated 37–50 active hours remain plausible only if P2 is excluded and the frontend stays small. If Day 3 is late, complete P0 and reduce the number of final benchmarks only with an explicit limitation in the report. Never present a partial run as a three-benchmark result.

## 15. Delivered report and communication rules

Deliver `README.md`, `docs/methodology.md`, `docs/limitations.md`, `docs/demo_script.md`, source, tests, `runs/final-example` labeled as previously measured, and a five-to-eight-page course report. Tables must show benchmark/workload, empty control, direct `-O3`, guided and random winners, pass sequence, confirmed tuning and holdout median/MAD, paired-round outcome, executable size, checksum status, and failure count. Figures show best-so-far search runtime and size-versus-runtime Pareto points. Include raw observations and machine/toolchain manifest. Reference AutoPass and Compiler-R1 only as related work, with exact claims verified from the papers before final submission.

Use precise language: “observed on this machine and workload,” “inconclusive under the predeclared rule,” and “the `-O1`-seeded sequence beat/did not beat the empty control or direct `-O3`.” The dashboard and slides must never turn an IR-count reduction into a runtime claim or imply a rule-based role used an LLM.

## 16. Deferred extensions and interface changes

### 16.1 Custom benchmark input

P2 custom C support needs a source contract, safe compile/run isolation, header/library allowlist, memory/CPU/disk limits, deterministic input protocol, independent oracle strategy, and a new API schema. The current built-in registry never accepts a source path from HTTP. A future implementation must avoid treating agreement between two optimized builds as proof that arbitrary user code is correct. It must also define how a custom benchmark's expected checksum is established before timing.

### 16.2 Optional LLM proposer and explanation

P0/P1 use **no LLM prompts**. An optional P2 LLM strategy replaces only proposal selection; compiler execution, correctness, measurement, and numeric evaluation remain code. Run it as a separately named strategy with the same action set, sequence length, candidate budget, inputs, and measurements. Configure provider, pinned model ID, model/API version, temperature, prompt version, response-schema version, request latency, token usage, and cost in the experiment manifest. No API key appears in logs, prompts, or exports. Reject `llm_mode=proposer` before queuing if configuration is absent. The local FastAPI service does not expose the model provider's HTTP endpoint.

The provider adapter submits one structured request per proposal with a bounded IR feature summary, allowed action catalog, and **only that strategy's** prior trial records. It never sends source code, full IR, compiler stderr, or arbitrary run files. The system prompt `PROPOSER_SYSTEM_V1` is:

```text
Propose one LLVM pass-action sequence for this bounded experiment.
Use only the supplied action IDs. Return one JSON object matching ProposalV1.
Choose 1 to max_sequence_length distinct action IDs in significant order.
Do not invent passes, flags, commands, source edits, inputs, or measurements.
Use features as hypotheses and prior trial IDs as measured evidence.
State a hypothesis in at most 40 words. Do not claim an unmeasured gain.
```

The user template `PROPOSER_USER_V1` is:

```text
Benchmark: {benchmark_id}
Workload: {size, seed, repeat}
LLVM version and target: {llvm_version, target}
Allowed actions and exact hypotheses: {action_catalog_json}
Maximum sequence length: {max_sequence_length}
Remaining proposal budget: {remaining_budget}
IR features with extractor caveats: {feature_summary_json}
Valid prior trials from this strategy: {compact_valid_history_json}
Failed prior trials from this strategy: {compact_failed_history_json}
Forbidden duplicate sequences: {seen_sequences_json}
Return ProposalV1 only.
```

`ProposalV1` is `{action_ids: string[1..4], hypothesis: string<=40 words, evidence_trial_ids: UUID[], proposal_type: "feature_prior"|"mutation"|"exploration"}`. Validate allowed IDs, uniqueness, length, trial references, and absence of duplicate sequences in Python. On malformed output, make **one** retry with `REPAIR_SYSTEM_V1`: `Your prior response did not match ProposalV1 or used an invalid action. Return only one valid ProposalV1 JSON object using supplied action IDs.` The repair user message contains the original bounded request and a sanitized validation error, never raw compiler output. A second invalid answer becomes `invalid_llm_proposal` and consumes one proposal budget unit.

An optional explanation request receives verified report metrics and pass descriptions only. `EXPLAIN_SYSTEM_V1`: `Write a concise technical explanation using only the supplied verified numbers and trial IDs. Distinguish tuning from held-out results and guided from random. Describe inconclusive results as inconclusive. Return JSON fields summary, evidence_trial_ids, limitations, next_test. Do not invent causal proof or statistical significance.` The user template supplies `{research_question, benchmark_and_inputs, baseline_metrics, winner_metrics, failure_counts, used_actions, confirmation}`. Its output is a clearly labeled narrative section; computed tables, outcome labels, and charts never depend on model text.

### 16.3 Additional service behavior

P2 cancellation adds `POST /v1/experiments/{id}/cancel` with an empty body and `202 {status:"cancel_requested"}`. The worker observes the flag between stages, terminates an active subprocess group if necessary, commits `cancelled`, and releases the slot. Repeated cancel requests are idempotent. This requires adding `cancel_requested` and `cancelled` to experiment states and restart handling. Multi-user or remote deployment needs authentication, job isolation, a durable worker queue, and stricter sandboxing. None of these are implied by the local ten-route API.

## 17. Technical references checked for this revision

- [LLVM new pass manager and IR-unit nesting](https://llvm.org/docs/NewPassManager.html)
- [LLVM `opt` pipeline and verification options](https://llvm.org/docs/CommandGuide/opt.html)
- [LLVM `llc` target, CPU, and object options](https://llvm.org/docs/CommandGuide/llc.html)
- [LLVM benchmarking guidance](https://llvm.org/docs/Benchmarking.html)
- [FastAPI background task guidance](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
