# Local testing walkthrough

The webpage and API share a single server:

- App: http://127.0.0.1:8000/
- Interactive API documentation: http://127.0.0.1:8000/docs
- Health and selected model: http://127.0.0.1:8000/v1/health

The runtime is Ubuntu WSL, LLVM 21, and the existing compiler-autotuner virtual environment. OpenAI proposals use GPT-6 Luna with low reasoning. No separate frontend development server is needed.

## First quick test

To explore your own source, use **Inspect your own C code** above the benchmark form. Paste a function or load a `.c` file, then click **Inspect code**. Expand the individual transformation steps to see IR diffs, compare before/after IR and counts, inspect the generated assembly, or download the JSON trace. Try **Load example code** first. No `main`, runtime input, or expected output is required. Compiler diagnostics are shown for invalid C. This flow does not execute your code or measure performance.

1. Open the app. The header should say `Toolchain ready · OpenAI: gpt-6-luna`.
2. Choose **Integer dot product**, **Quick demo**, and budget **3**. Keep the benchmark's default size, seed, and repeat values.
3. Click **Start experiment**. Progress should advance and finish as **completed**. The result is labeled `demo_only` because quick mode is a functional demonstration.
4. Inspect **Candidate evidence trail**. Open a valid trial to see compiler commands, checksums, and timing samples. A `no_ir_change` trial is an expected rejection; `llm_fallback` means an unusable or repeated model proposal was replaced by local rules.
5. Download **Summary JSON**, **Trials CSV**, **Samples CSV**, and **Environment JSON**. The environment records the selected model and reasoning level.
6. Reload and reopen the run under **Saved experiments**. Results should still be available without recompiling.

## Meaningful comparison

1. Choose **Full comparison** first, then lower **Budget per strategy** from 20 to **5** for an initial comparison.
2. Keep default workload settings. Under **Measurement settings**, retain **5** search repetitions and **10** confirmation rounds. Keep the held-out workload distinct from the tuning workload.
3. Run one benchmark at a time. Repeat for matrix multiplication and histogram.
4. Inspect guided versus random, guided versus Clang `-O3`, and guided versus the empty-action control on both tuning and held-out inputs.
5. A promising performance result is a confirmed `win` that also holds on the held-out workload. `inconclusive` and `loss` are legitimate measured results. A single fast search sample or a reduction in IR instructions does not establish a performance gain.

For timing comparisons, keep other heavy CPU workloads idle and use a consistent power configuration. Quick tests establish functionality; the small verification runs do not establish speedups.

## Other things to try

- **Bundled full evidence:** inspect the three previously measured experiments immediately, without compiling or calling OpenAI. They do not test the current key/model.
- **Cancel and retry:** cancel a running experiment, inspect the partial evidence, then use **Run again with the same settings**. The retry creates a new linked experiment.
- **Validation:** submit an invalid value (for example budget 0), or identical tuning and holdout workloads in full mode. The app should reject it without starting an experiment.
- **Parameters:** vary size, seed, repetitions, confirmation rounds, or maximum pass count (1–4) to see how the measured search changes.
- **API:** explore benchmark/action lists, create experiments, read status and trials, fetch reports, and cancel/retry through the interactive docs.
- **CLI:** use `python -m autotuner` in the configured WSL environment. `llm-status`, `doctor`, `benchmarks`, `actions`, `tune`, `status`, `trials`, `report`, `cancel`, `retry`, and `calibrate` are available. Avoid starting another tuning job while the web worker is busy.

## Success criteria

A healthy functional test completes, accepts only correctness-checked candidates for timing, preserves evidence and saved runs, and loads charts/downloads. Invalid candidates should be rejected. A performance win is desirable, but is not required for the tool to be functioning correctly.

New guided experiments and OpenAI-guided code inspections make paid OpenAI calls. Random candidates and viewing saved/bundled results do not. Custom `.c` files are supported for compile-only inspection; timed comparisons still use the three built-in benchmarks.

## Starting again later

Run from the repository root in Ubuntu WSL:

```bash
~/.venvs/compiler-autotuner/bin/python -m autotuner serve --host 127.0.0.1 --port 8000
```

The server reads `.env` on startup. Restart it after changing model or key settings.
