# Added performance cases (25 October 2026)

The new `pipeline` benchmark contains two adjacent loops: one writes an intermediate array and one reads it for a reduction. LLVM 21's `loop-fusion` pass fused these loops in the `clang -O1` seed IR. The `skew_histogram` benchmark uses the same binning logic as `histogram` but makes the first branch common. `loop-interchange` is also available as an action for nested-loop experiments.

## Focused measurements

`scripts/verify_added_cases.py` compiles each source at `-O0` and direct `clang -O3`, builds an empty-action control and a hand-selected action candidate, checks all four binaries against independent Python oracles on small inputs, and requires exact checksums on tuning and held-out inputs. It then alternates execution order over 10 paired rounds on each input. Time is whole-process wall time. The machine was a 13th Gen Intel Core i7-13650HX under Ubuntu 26.04 WSL2 with LLVM 21.1.8. Raw paired samples, compiler environment, source hashes, and binary hashes are in the ignored local file `test-results/added-cases.json`.

| Case and actions | Input | Direct `clang -O3` median | Candidate median | Candidate faster pairs | Median speedup |
|---|---|---:|---:|---:|---:|
| `pipeline`: `loop-fusion` | tuning, 12M × 12 | 315.15 ms | 214.99 ms | 10/10 | 31.78% |
| `pipeline`: `loop-fusion` | holdout, 14M × 12 | 340.34 ms | 264.86 ms | 10/10 | 22.18% |
| `skew_histogram`: `simplifycfg → licm → dce` | tuning, 8M × 10 | 164.61 ms | 164.62 ms | 5/10 | −0.01% |
| `skew_histogram`: `simplifycfg → licm → dce` | holdout, 10M × 10 | 164.64 ms | 164.66 ms | 4/10 | −0.01% |

The pipeline numbers show a concrete pass sequence beating the direct compiler reference in this configuration. The focused probe specified the action beforehand. The skewed histogram is a coverage case and produced no measured improvement with the selected sequence.

## Full tuner experiment

A separate full experiment (`2ad8f7cc-56f5-44b9-a096-a2a9db79e37e`) ran the normal rule-based guided search and equal-budget random search with 20 proposals each, five search repetitions, and ten interleaved confirmation rounds. The guided finalist was `simplifycfg → slp-vectorizer → instcombine → loop-fusion`; the random finalist also contained `loop-fusion`. On the holdout input, the guided finalist took a 265.46 ms median versus 365.86 ms for direct `clang -O3`: a 27.44% runtime reduction with 10/10 faster paired rounds. It also beat the empty-action control. The guided and random finalists were statistically inconclusive against one another (265.46 ms versus 265.31 ms median), so this supports the auto-tuner finding a compiler-beating sequence, not a guided-search advantage over random search. The exported `summary.json`, `trials.csv`, `samples.csv`, and charts are in the ignored local directory `test-results/full-pipeline-2026-10-25/`.

Rerun on the presentation machine before making a live performance claim; the sources, toolchain, and hardware affect the result.

Reproduce the focused probe from the repository root inside the documented Linux environment:

```bash
python scripts/verify_added_cases.py --rounds 10
python -m autotuner tune --benchmark pipeline --mode full --budget 20 --llm-mode off
```
