# Deployment experiments

The Research Lab includes three experimental extensions: active challenging-workload
search, compiler-version migration, and a measured performance envelope with a real
baseline-fallback launcher. These are implemented methods, not established claims
of research novelty or superiority over existing autotuners.

## Run

On x86-64 Linux, install a complete, matching LLVM toolset for each requested major:

```bash
sudo apt-get install clang-20 llvm-20 clang-21 llvm-21
python -m autotuner serve --host 127.0.0.1 --port 8000
```

Open `/research` and use **Challenge an optimization before deployment**. Select one
compiler to study workload robustness, or two to measure migration. Missing toolsets
cause an explicit error; a one-version run is marked `requires_second_compiler` and
never pretends to compare versions. With two versions, list the current compiler
first and the proposed deployment compiler last: `[20, 21]` calibrates and dispatches
LLVM 21 artifacts. Classic experiments still require LLVM 21.

The standalone entry point accepts the same JSON request as the API:

```json
{
  "benchmark_id": "histogram",
  "actions": ["simplifycfg", "loop-vectorize"],
  "compiler_versions": [20, 21],
  "distributions": ["uniform", "skewed", "alternating"],
  "size": 100000,
  "min_size": 1000,
  "max_size": 1000000,
  "seed": 29,
  "repeat": 8,
  "rounds": 10,
  "search_budget": 12,
  "envelope_budget": 18
}
```

```bash
python -m autotuner.deployment tune --request deployment.json --output-dir ./results
python -m autotuner.deployment dispatch ./results/deployment-UUID/report.json \
  --size 50000 --seed 777 --repeat 8 --distribution uniform
```

The job directory also contains an executable `dispatch` with the same workload
arguments. For dot, pipeline and matmul, use distribution `native` and omit the
distribution argument when invoking the launcher. Matrix sizes must be <=512.
There are no cloud or LLM requests. Source uploads are not part of this workflow.

## 1. Joint workload and pass-sequence search

The initial candidate is measured on the tuning workload. The controller alternates
between challenging it with changed sizes/distributions and trying local sequence
repairs. Challengers are mutated around the worst measured workload, with global
exploration. Observed regressions prioritize affordable sequence repairs. Candidate
and workload pools are capped at eight each. Every new candidate is evaluated over
all active workloads; every new workload evaluates all current candidates. Thus a
candidate cannot win merely because a difficult measurement is missing.

Selection minimizes the worst candidate / direct-O3 runtime ratio across the
active workload set and requested compilers. Each compiler has its own -O3 control;
source is independently compiled per major and bitcode is never shared across
versions. Invalid or missing comparisons disqualify a candidate. Direct -O3 is
explicitly preferred when no candidate improves the worst observed search ratio.
The best valid candidate can still be calibrated for narrower winning regions.

One search-budget unit is one candidate/workload comparison across **all** requested
versions, with the requested paired-round count per version. Budgets therefore
bound comparison count, not constant elapsed time. An operation that cannot fit is
not started; unused budget is reported. Up to eight candidate/workload regression
hypotheses receive separate fresh confirmation. Only actual `loss` labels are
confirmed counterexamples; `demo_only`/`inconclusive` rows remain hypotheses.

## 2. Compiler migration

Actual passed/missed/analysis remark counts are compared by pass, kind and name.
Cross-version count differences prioritize which positions to remove or replace
in the original sequence. Reorderings and unseen guided proposals provide fallback
exploration. No remark delta means no claim that evidence guided the edit order.
Remark differences are heuristic evidence, not proof of a compiler bug or causality.
Both full toolchain environments, command logs, failures, actual remarks, and
per-candidate worst ratios are saved.

## 3. Performance envelope and fallback

After search freezes the candidate, calibration uses **two new seeds** at each size
interval's endpoints and midpoint. A region permits the optimized binary only when
all required points have valid `win` labels and at least ten paired rounds. A mixed
win/loss interval is split when budget permits; incomplete children remain
`insufficient_evidence`. Missing or failed points never authorize optimized dispatch.

The guard uses size, distribution and repeat count. It interpolates between sampled
sizes; it does not prove performance for every seed or point in the region. Unknown
regions and changed repeat counts use direct -O3. The exact compiler versions,
target, CPU model and build flags must match; changed host context requires a rerun.
Executable hashes are checked before dispatch. A missing/changed optimized artifact
falls back to an intact baseline; a changed baseline or path outside the job
directory is rejected. Only run reports/artifacts you trust; hashes do not authenticate
a report supplied by someone else.

Two further reserved seeds, disjoint from search and calibration, evaluate random
held-out sizes per distribution after all choices are frozen. They never repair the
sequence or change guard boundaries. The actual Python launcher is measured in
separate randomized paired rounds, including interpreter startup, context/hash
checks and exec overhead. This overhead can outweigh a kernel improvement and is
shown separately. The launcher is an auditable local prototype, not a zero-cost
in-process production dispatcher.

## Evidence, limits and evaluation

`POST /v1/research/deployment/jobs` creates a job under the same exclusive compiler
lock as classic tuning/inspection/research. Existing `/v1/research/jobs/{id}`,
`/report` and `/download` endpoints expose its state and report. CLI jobs should be
run on an idle machine and must not run alongside the web worker. Jobs retain a
15-minute wall-clock limit and subprocess timeouts. Source snapshots, seed IR,
candidate IR, remarks, binaries and individual command manifests remain local.
Imported JSON can be viewed/downloaded, but cannot execute without local artifacts.

Timing is whole-process wall time, including generation and checksum work. Controls
and candidates are checked against an independent Python oracle on small inputs;
every measured execution must agree with an O0 control. These checks are not a
universal equivalence proof. Failed measurements stay in the evidence. Short runs
test plumbing only. Ten-round practical labels follow the existing exploratory 2%
and 80%-paired-agreement rule, not a multiple-testing-adjusted significance claim.

The three budgets are separate: bounded search, bounded envelope calibration, and
bounded regression/holdout/launcher confirmation. Raw paired samples and requested
round counts are recorded. They must all be included when comparing total tuning
cost against another method. No claim of equal total cost versus classic search is
made by the UI.

For a novelty/performance study, compare against fixed-workload guided search,
random-workload expansion and ordinary warm-start repair, matching total executions
or wall-clock cost. Remove challenger feedback and remark-prioritized repair in
separate ablations; use multiple independent seeds and external C workloads. The
current implementation provides mechanisms and evidence, not completed evidence
of general industry-scale performance or research priority.

## Verification

```bash
python -m pytest tests/test_deployment.py tests/test_deployment_integration.py -q
npm test
```

Policy tests use explicitly synthetic timings to check budgets, invalid candidates,
seed separation, envelope incompleteness, artifact tampering and context changes.
Linux integration tests compile all three histogram distributions, compare with the
independent oracle, execute the real launcher, and compare LLVM 20 and 21 when both
are installed. Compiler CI installs both. Short CI runs establish execution and
evidence handling, not performance gains.
