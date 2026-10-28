# Methodology

## Research question and comparators

For each built-in C17 workload, ask whether a bounded rule-based search using coarse IR features and previous timings selects a faster valid sequence than an equal-budget seeded random search. The empty-action control answers whether added `opt` passes help relative to the same `-O1` bitcode and `llc -O3` backend. Direct `clang -O3` answers a different practical question: whether the resulting binary is competitive with Clang's full standard pipeline. Never combine these into one undifferentiated “speedup.”

## Fixed compiler pipeline

Each run snapshots the benchmark source and hashes it. Clang 21 creates `-O0` and direct `-O3` binaries and a common `-O1` bitcode seed. `opt-21` applies one validated pass-action sequence with `-verify-each`. `llc-21 -O3 -filetype=obj -mcpu=x86-64 -relocation-model=pic` emits an object, and Clang links it with `-pie`. The same source, target, LTO policy, workload tuple, and link policy are used across variants. Full compiler argument arrays and diagnostics are saved for every build. LLVM action templates are checked against the installed version, including ordered action pairs.

The final selectable catalog contains function-pass templates for `instcombine`, `simplifycfg`, `gvn`, `dce`, `loop-unroll`, `loop-vectorize`, and `slp-vectorizer`, plus `function(loop-mssa(licm))`. A catalog item is only selectable after its smoke parse succeeds. Sequences contain one to four distinct IDs. A duplicate proposal is resampled; a compile or correctness failure consumes a proposal budget unit.

## Correctness

The C programs use fixed-width unsigned arithmetic, deterministic SplitMix64 input generation, and FNV-1a checksum output. An independent Python implementation checks two small fixtures for each benchmark. For tuning and held-out workloads, `-O0`, direct `-O3`, and the empty-action binary must agree before any search. Every candidate is checked on small fixtures and the tuning workload, and every measured execution checks its output again. A mismatch, malformed stdout, timeout, or nonzero exit cannot become a winner. Held-out performance does not influence proposals or selection.

## Search and measurement

Guided search begins with one-action probes weighted by branch, load, and loop evidence. It then mutates up to three best valid sequences with append/delete/replace/swap and reserves approximately 25% of proposals for exploration. Random search samples unseen ordered sequences without IR features or guided history. The two strategies alternate trials and each receives 20 proposals in full mode. Both use the same catalog, compiler flags, inputs, correctness checks, and five search timings per valid candidate.

Search ranking uses the median runtime, with executable size as a tie-breaker within a 1% runtime band. A high-noise candidate (`MAD / median > 0.03`) gets at most one additional search sample set, recorded separately. After selecting one finalist per strategy on tuning data, the system freshly measures `-O3`, empty control, and finalists in ten randomized interleaved rounds on tuning and held-out workloads. One warmup per binary/input precedes confirmation. The report keeps nanosecond observations, median, MAD, paired-round differences, size, IR counts, pass sequence, and failures.

The exploratory full-run `win` label requires at least ten matched valid confirmation rounds, a 2% lower confirmed median, and improvement in at least 80% of paired rounds (8/10 by default); `loss` is symmetric, otherwise the label is `inconclusive`. Comparisons use only matched rounds. Finalists that fail confirmation are excluded from both tuning and held-out final claims. These thresholds are a predeclared practical rule, not a statistical-significance test. Quick mode defaults to five guided proposals, three search repetitions, and three tuning-only confirmation rounds, and returns `demo_only` when confirmation is available. Missing confirmed finalists produce `unavailable`.

## Reproducibility

Every experiment stores exact compiler paths/versions, target triple, CPU/WSL details, source and catalog hashes, source snapshot, requested workload, strategy seeds, commands, stderr/stdout files, trial states, and raw timing samples. Binaries can be cached by source/toolchain/catalog/flags/pipeline identity, but timings are never cached across experiments. Reports are regenerated from SQLite without recompilation. The `summary.json`, `trials.csv`, `samples.csv`, and SVG figures are exported artifacts, not manually edited results.

New experiments additionally record dependency versions, Git revision/dirty state when available, and command working directories. Variant byte sizes are persisted so report numbers survive executable cleanup. SQLite migrations preserve existing experiments; source evidence bundles remain historical measurements. Cancelled/failed runs preserve partial records but cannot return a completed report. Retrying creates a linked new record with fresh measurements.
