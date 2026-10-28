# Evidence-Guided LLVM Pass Tuner

A local, correctness-checked experiment for choosing short LLVM optimization-pass sequences for five C17 programs. It compares an IR-guided search (OpenAI-assisted when configured, otherwise rule-based) with an equal-budget random search, an empty-action control, and direct `clang -O3`. The tool records failures and raw timing samples, then remeasures finalists on tuning and held-out workloads. A speedup is an observation, not a guarantee.

## Requirements

- Ubuntu 26.04 WSL2 or a comparable Linux environment on x86-64
- Matching `clang-21`, `opt-21`, `llc-21`, and `llvm-dis-21`
- Python 3.11 or newer; the verified machine uses Python 3.14.4

On the verified Windows machine, the default WSL distribution is `docker-desktop`; explicitly use `wsl -d Ubuntu`. The compiler binaries and experiment database are created under Ubuntu's Linux filesystem. The source checkout may be on Windows, but measurements run from `~/.local/share/compiler-autotuner/runs/` inside Ubuntu.

```bash
sudo apt-get update
sudo apt-get install clang-21 llvm-21 python3-venv
python3 -m venv ~/.venvs/compiler-autotuner
~/.venvs/compiler-autotuner/bin/pip install -c constraints.txt -e '.[web,test,graph]'
```

The `graph` extra is optional. The default engine uses the same service loop without LangGraph. Local rule-based search works without an LLM key, cloud account, Docker container, or frontend build.

## OpenAI setup

The checkout includes an ignored `.env` file. Add your key there:

```dotenv
OPENAI_API_KEY=your-key-here
```

For a fresh clone, copy `.env.example` to `.env` first. Install/update dependencies using the install command above. The existing WSL virtual environment is already updated on this machine. Restart a running server after editing `.env`.

That single key enables OpenAI proposals in new experiments started through the CLI, API, webpage, and LangGraph. Configuration is loaded from the project directory even when launched elsewhere; process environment variables take precedence. Installed-wheel users can set `AUTOTUNER_ENV_FILE` to an absolute `.env` path or export the variables directly.

Optional settings: `OPENAI_MODEL` (default `gpt-6-luna`), `OPENAI_REASONING_EFFORT` (default `low` for GPT-6), `OPENAI_MAX_OUTPUT_TOKENS` (default `4096`, shared by reasoning and visible output), `OPENAI_TIMEOUT_SECONDS` (default `30`), and `AUTOTUNER_LLM_MODE` (`auto`, `off`, or `openai`; default `auto`). Low reasoning suits short, bounded pass proposals. Raise it to `medium` for a deliberate quality comparison if needed; also allow enough output tokens for reasoning. An empty reasoning setting omits the parameter for models that do not support it. `auto` uses OpenAI when a key is present and local rules otherwise. Use `--llm-mode off` for an offline CLI run, or send `"llm_mode": "off"` in an API request. Explicit `openai` mode requires a key. Inspect configuration without a network call or charge:

```bash
python -m autotuner llm-status
```

To verify the actual key/model and optionally run small live compiler experiments (uses API credits):

```bash
python scripts/check_openai.py
python scripts/check_openai.py --workflows
```

The live checker uses a disposable database and saves a safe summary in `test-results/openai-live.json`. Normal Python/browser regression tests stay offline. The exact model and reasoning level are saved with each experiment. GPT-6 Luna supports `none`, `low`, `medium`, `high`, `xhigh`, and `max` reasoning; see the [model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna).

The real-browser workflow can also explicitly use your key: set `AUTOTUNER_LIVE_OPENAI=1` when running `npm run test:live`. That opt-in uses credits for ten guided proposals across the five benchmarks; leave it unset for the usual offline regression run.

Keys stay on the Python server. Only IR feature counts, allowed passes, previously attempted sequences, and the guided strategy's measured history are sent to OpenAI. Tuning never sends source code or holdout results. The optional inspection explanation sends the submitted source and compiler output to OpenAI. OpenAI uses [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs); proposals still pass local allowlist, uniqueness, compilation, checksum, and timing checks. Incomplete/refused/invalid/repeated proposals fall back to local rules and are labeled `llm_fallback`. Provider failures stop the experiment with a safe error instead of silently treating local search as an LLM result. Each guided candidate makes at most one logical API request (with one transient retry); random search never calls OpenAI. The resolved mode and model are saved with each experiment; model outputs are not deterministic.

Set `AUTOTUNER_ORCHESTRATOR=graph` before starting the CLI or service to run the proposal/compile/evaluate loop through LangGraph nodes. The compiler, policies, budgets, and measurements are unchanged; the run stores `graph_state.json` for inspection.

## Start in two minutes

```bash
python -m autotuner doctor
python -m autotuner benchmarks
python -m autotuner tune --benchmark dot --mode quick
python -m autotuner serve --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) for the local page or [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for the API. A quick run proposes five guided candidates, measures each three times, and performs three tuning-input confirmation rounds. Its report is labeled `demo_only`. A full run proposes 20 guided and 20 random candidates per benchmark, measures each valid candidate five times, and confirms finalists in ten interleaved rounds on tuning and held-out inputs.

```bash
python -m autotuner tune --benchmark matmul --mode full
python -m autotuner status <experiment-id>
python -m autotuner trials <experiment-id>
python -m autotuner report <experiment-id>
python -m autotuner report <experiment-id> --output-dir ./exported-report
python -m autotuner cancel <experiment-id>
python -m autotuner retry <experiment-id>
```

Before a formal run, inspect the workload calibration:

```bash
python -m autotuner calibrate
```

The default full workloads were calibrated on a 13th Gen Intel Core i7-13650HX under WSL2. Different hardware should repeat calibration and adjust `benchmarks/manifest.json` before collecting final results.

## What is recorded

The default data directory is `~/.local/share/compiler-autotuner`; set `AUTOTUNER_DATA_DIR` to change it. SQLite stores experiment status, trial decisions, variants, all executions, and artifact metadata. Each run directory stores a source snapshot, compiler command logs, IR features, expected checksums, `summary.json`, `trials.csv`, `samples.csv`, `environment.json`, and two SVG charts. `report` reads saved records without compiling again. Compiled candidate bitcode/binaries may be reused only under a full build-key match; timing samples are always fresh.

The webpage also offers three **bundled full experiments** under `examples/`. They open without LLVM or a local database and are clearly marked as previously measured. To refresh a bundle after a new full run, use `python -m autotuner.examples <experiment-id> full-dot` (or `full-matmul` / `full-histogram`).

The **Selected performance test set** panel adds measured workload variants with
per-case tuning/held-out statistics, complete portable evidence, and a **Run this
test** button for a fresh full experiment. It explicitly selects cases within a
2% regression limit against both Clang `-O3` and random search. It is a showcase,
not a representative evaluation; the full screening audit remains downloadable.
Run `python -m autotuner.showcase` on an idle LLVM machine to refresh the fourteen-case
screening pool, or add `--resume` to reuse existing measurements and measure new
cases only. Four follow-up workloads require wins versus O3 on both inputs.
Use **Show only confirmed held-out wins** to focus the UI on actual win labels.
See [test-set details and methodology](docs/SELECTED_TEST_SET.md)
and the [initial measured results](docs/SELECTED_RESULTS_2026-10-26.md), followed by
[four additional winning workloads](docs/ADDITIONAL_WINS_2026-10-26.md).

The candidate path is:

```text
C17 source → clang -O1 bitcode → validated opt pass pipeline
           → llc -O3 object → clang link → correctness gate → timing
```

An empty-action binary goes through the same bitcode/backend path. Direct `clang -O3` is a separate practical reference. All tools are version-pinned to LLVM 21, use x86-64 and PIE/PIC flags consistently, and are probed by `doctor`. Pass names are action IDs; the actual `-passes=` string appears in trial records.

## Benchmarks and interface

### Inspect your own C code

Use **Inspect your own C code** at the top of the webpage to paste a single C17 translation unit or load a `.c` file (up to 64 KiB). Functions without `main` are supported. No runtime inputs or expected outputs are needed.

The trace shows Clang's initial IR at `-O0` with `optnone` disabled, explicit `mem2reg` preparation into SSA form, the guided proposal and rationale, each verified pass's IR diff and instruction counts, before/after IR, and final x86-64 assembly generated by `llc -O3`. Unchanged steps are labeled as such. Download the trace as JSON to keep a copy; inspections are temporary and are not saved as benchmark experiments. Pass selection sends only extracted feature counts. The optional GPT-6 Luna explanation sends the submitted C, each IR stage, and final assembly to OpenAI (store=false), using an additional API request. Uncheck the explanation option to keep source local. Local rule-based inspection works with LLM mode off.

Inspection does not link or execute your program and does not establish output correctness or a runtime speedup. It displays real LLVM IR/assembly plus an optional plain-language walkthrough and illustrative C reconstruction (or annotated assembly when C is unsuitable). Reconstructed code is not verified equivalent and is never compiled or executed. Explanation failures leave the compiler results available. Only single-file C and headers available in the local toolchain are supported; custom build flags, project folders, extra source files, and custom header uploads are not part of this flow. Compiler errors appear inline. Inspection and web tuning jobs cannot run at the same time, so inspection does not interfere with timing measurements.

The API is `POST /v1/inspections`, with `source`, optional `max_passes` (1–4), optional `llm_mode`, optional `explain` (default false for API clients), and optionally `action_ids` for explicitly selected allowlisted passes. For example:

```json
{"source": "int twice(int x) { return (x + 0) * 2; }", "max_passes": 4}
```

`dot`, `matmul`, `histogram`, `pipeline`, and `skew_histogram` accept `--size N --seed S --repeat R` and print exactly one `checksum:<16 lowercase hex digits>` line. The allowed size interval and quick/tuning/holdout defaults are listed by `benchmarks` and `/v1/benchmarks`. Small cases are checked against separate Python reference implementations. Larger workloads require exact agreement between `-O0`, direct `-O3`, and the empty-action binary before search. Every candidate and every timed execution must return the same checksum.

This version accepts only the built-in benchmarks. The HTTP service listens on `127.0.0.1` and has one worker, so concurrent experiments receive `409 RUN_IN_PROGRESS`. Do not expose the service on a public interface.

The `pipeline` workload has adjacent producer and consumer loops over an intermediate array. It gives the `loop-fusion` action a concrete memory-traffic hypothesis. `skew_histogram` keeps the original histogram computation but generates inputs that favor the first branch, giving branch and control-flow passes a second distribution to evaluate. The catalog also includes `loop-interchange` for legal nested-loop reorderings. These are hypotheses to measure against direct `clang -O3`, not pre-established wins. The new workloads use different tuning and holdout seeds and sizes, and both have independent small-input Python oracles.

To reproduce a focused comparison of hand-selected pass sequences against direct `clang -O3`, including ten alternating paired rounds on both inputs, run `python scripts/verify_added_cases.py`. It saves raw samples and build details in `test-results/added-cases.json`. This probe is separate from the normal guided/random search; its pass sequences are specified in the script.

The [25 October added-case measurements](docs/ADDED_CASES_2026-10-25.md) include a `loop-fusion` pipeline speedup on tuning and held-out inputs. A full 20+20 candidate search also found a held-out win over direct `clang -O3`; guided versus random was inconclusive. The skewed-histogram result was effectively tied.

The results page shows all five comparisons on tuning and held-out inputs, including median/MAD and faster paired rounds. Measurement settings and held-out inputs are editable under **Measurement settings**. The browser limits seeds to `9007199254740991` to preserve integer precision; the CLI/API accept the full unsigned 64-bit range. Failed, interrupted, and cancelled runs retain inspectable partial trials without a final performance claim.

Cancel requests stop work at the next stage/execution boundary. An already running subprocess finishes or reaches its normal timeout before cancellation takes effect. **Run again with the same settings** creates a fresh experiment linked to its parent, recompares current controls, and collects fresh timings. It preserves the original evidence rather than resuming or overwriting it. Graceful API shutdown drains accepted work; forced termination is reconciled on restart using the process boot/start identity.

## API

Twelve business route operations are under `/v1`: health, benchmarks, actions, experiment list/create/detail, trial list/detail, report, whitelisted artifact retrieval, and `POST /v1/experiments/{id}/cancel` and `/retry`. FastAPI generates `/openapi.json` and `/docs`. A `POST /v1/experiments` or `/retry` returns `202` with a run ID and `Location`; poll the experiment route every 2–5 seconds. Workload JSON values must be integers, without string/boolean/float coercion. Health reports toolchain readiness only after version, pass, compile/link, and checksum checks succeed.

## Development and tests

```bash
python -m pytest -q
python -m autotuner doctor
```

Tests cover the independent C/Python oracle, malformed output, timeout cleanup, guided/random pipelines, confirmation filtering, startup cleanup, cancelled/retried work, restart identity, failed-command evidence, binary-independent reports, corrupt cache recovery, strict validation, and graph exhaustion.

The extended suite tests all five benchmarks in quick/full mode with both plain and graph orchestration, unsigned seed boundaries, a 40-proposal guided/random search, injected compile/runtime/checksum failures, concurrent ownership, legacy migrations, CLI/export behavior, and unavailable tools. Every Python test uses a disposable data directory.

To measure Python statement/branch coverage:

```bash
python -m coverage run -m pytest -q
python -m coverage report
```

Optional browser regression checks use intercepted API responses and require no listening server:

```bash
npm ci
npx playwright install chromium
npm test
npm run test:live
```

`npm test` checks UI edge cases with API fixtures. `npm run test:live` drives Chromium against the actual FastAPI worker and LLVM through a stdio ASGI adapter, including all five benchmarks, downloads, cancellation/retry, and saved runs. It uses no listening port. On Linux set `AUTOTUNER_TEST_PYTHON` to the project virtual environment's Python. On Windows the test defaults to the documented Ubuntu WSL environment; set `AUTOTUNER_TEST_PYTHON` and `AUTOTUNER_TEST_WSL_DISTRO` to override it. `BROWSER_EXECUTABLE` optionally selects an already installed Chromium executable.

The separate clean-install check builds an sdist, builds a wheel from it, and tests the installed CLI/resources/exports in a fresh virtual environment:

```bash
python scripts/check_package.py
```

This build check downloads declared build requirements through pip. The [test plan](docs/TEST_PLAN.md) and [20 October results](docs/TEST_RESULTS_2026-10-20.md) distinguish real compiler checks, injected failures, browser transport, and remaining verification limits.

Node/Playwright are development-only dependencies; the app still needs no frontend build. CI runs compiler integration, wheel installation, and browser checks. To build a distributable wheel, run `python -m pip wheel . --no-deps`; it includes the benchmark sources, page assets, and bundled evidence. See [current status](docs/PROJECT_STATUS.md), [methodology](docs/methodology.md), [limitations](docs/limitations.md), and the [demo script](docs/demo_script.md).

## Research context

AutoPass and Compiler-R1 motivate evidence-guided pass tuning, but this project does not reproduce their models or reported results. The local contribution is a small, auditable search with an equal-budget comparator and explicit correctness and measurement gates. LLVM's [new pass manager](https://llvm.org/docs/NewPassManager.html), [`opt` guide](https://llvm.org/docs/CommandGuide/opt.html), and [benchmarking guidance](https://llvm.org/docs/Benchmarking.html) define the relevant tool behavior.

## Research Lab

Open /research for eight compiler-backed extensions: pass ablation/order experiments, actual LLVM remarks, workload robustness maps, adaptive cross-program experience, trusted custom C benchmarking, constrained objectives, interactive control flow, and stronger correctness validation. See [Research Lab documentation](docs/RESEARCH_LAB.md) for contracts, API, reproducibility and verification limits. The optional Alive2 adapter clearly reports unavailable or unsupported cases.
