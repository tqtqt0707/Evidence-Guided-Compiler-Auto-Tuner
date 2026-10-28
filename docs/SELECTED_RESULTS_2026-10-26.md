# Selected test-set measurements: 26 October 2026

This is the initial ten-case screening batch. See the [four additional winning
workloads](ADDITIONAL_WINS_2026-10-26.md) for the follow-up measurements and updated totals.

Screened 10 full experiments; selected 8; excluded 2. The runs recorded 400 candidate proposals and 3939 executions (including correctness checks, warmups, and search/confirmation samples).

Selected showcase, not a representative evaluation. At most 2% measured runtime regression versus both Clang -O3 and equal-budget random search, on tuning and holdout inputs. Workload variants reuse two C programs; they are not independent algorithms. Results are exploratory single-machine observations.

Measured on a 13th Gen Intel Core i7-13650HX, Ubuntu WSL2, LLVM 21.1.8, x86-64. Local rule-based guided search, 20 proposals per strategy, five search repetitions, ten confirmation rounds per input. Each execution repeats the kernel 12 times. Full methodology: [selected test set](SELECTED_TEST_SET.md).

Positive reduction means lower runtime. Median and MAD are in milliseconds. The comparison label uses the existing full-report evidence rule; a positive median difference alone does not establish a win.

| Test | Input: size / seed | O3 median / MAD (ms) | Guided median / MAD (ms) | Reduction vs O3 | Faster pairs | Reduction vs random | O3 label |
|---|---|---:|---:|---:|---:|---:|---|
| `pipeline-4m` | tuning: 4,000,000 / 17 | 114.77 / 0.15 | 64.72 / 0.12 | +43.61% | 9/10 | +0.04% | win |
| `pipeline-4m` | holdout: 5,000,000 / 1017 | 140.08 / 25.05 | 114.94 / 0.05 | +17.95% | 7/10 | -0.14% | inconclusive |
| `pipeline-8m` | tuning: 8,000,000 / 29 | 215.37 / 0.15 | 140.11 / 25.22 | +34.94% | 10/10 | +15.25% | win |
| `pipeline-8m` | holdout: 9,000,000 / 1029 | 215.46 / 0.09 | 165.05 / 0.06 | +23.40% | 10/10 | +0.17% | win |
| `pipeline-16m` | tuning: 16,000,000 / 79 | 365.69 / 0.11 | 265.28 / 0.17 | +27.46% | 10/10 | -0.01% | win |
| `pipeline-16m` | holdout: 15,000,000 / 1079 | 365.96 / 0.19 | 265.31 / 0.27 | +27.50% | 10/10 | -0.05% | win |
| `pipeline-6m` | tuning: 6,000,000 / 101 | 165.00 / 0.10 | 114.87 / 0.08 | +30.38% | 10/10 | +0.20% | win |
| `pipeline-6m` | holdout: 7,000,000 / 1101 | 190.28 / 25.13 | 114.99 / 0.33 | +39.57% | 9/10 | +0.36% | win |
| `skew-histogram-4m` | tuning: 4,000,000 / 17 | 115.22 / 0.35 | 115.01 / 0.16 | +0.18% | 4/10 | +0.04% | inconclusive |
| `skew-histogram-4m` | holdout: 5,000,000 / 1017 | 114.73 / 0.04 | 114.55 / 0.03 | +0.16% | 7/10 | -0.00% | inconclusive |
| `skew-histogram-8m` | tuning: 8,000,000 / 29 | 165.04 / 0.13 | 164.96 / 0.08 | +0.05% | 5/10 | +0.08% | inconclusive |
| `skew-histogram-8m` | holdout: 9,000,000 / 1029 | 165.04 / 0.12 | 165.04 / 0.21 | +0.00% | 5/10 | +0.16% | inconclusive |
| `skew-histogram-12m` | tuning: 12,000,000 / 43 | 215.22 / 0.15 | 215.21 / 0.32 | +0.01% | 5/10 | -0.04% | inconclusive |
| `skew-histogram-12m` | holdout: 13,000,000 / 1043 | 265.29 / 0.08 | 265.39 / 0.09 | -0.04% | 4/10 | -0.04% | inconclusive |
| `skew-histogram-16m` | tuning: 16,000,000 / 61 | 315.34 / 0.18 | 315.53 / 0.17 | -0.06% | 2/10 | -0.03% | inconclusive |
| `skew-histogram-16m` | holdout: 15,000,000 / 1061 | 315.30 / 0.27 | 290.44 / 25.13 | +7.89% | 7/10 | +7.93% | inconclusive |

## Selected pass sequences

| Test | Guided finalist | Experiment |
|---|---|---|
| `pipeline-4m` | `loop-unroll -> dce -> loop-fusion` | `c46d5e9c-5fb5-42d6-985a-46f6967dac64` |
| `pipeline-8m` | `licm -> loop-fusion -> slp-vectorizer` | `b3785df0-7b7b-4b3e-b3d8-c5d231bd56d2` |
| `pipeline-16m` | `gvn -> loop-fusion -> licm` | `86990a88-a4e5-4d7a-b422-4ad5e6ecce7c` |
| `pipeline-6m` | `loop-interchange -> loop-fusion` | `6fde4f49-d35a-4687-aa61-1760631d8548` |
| `skew-histogram-4m` | `loop-interchange -> loop-vectorize` | `476dad47-1ac2-4d85-81fe-db5377df2605` |
| `skew-histogram-8m` | `licm -> loop-interchange` | `015fc9fc-f7d9-4d8d-853d-87cbfff411a8` |
| `skew-histogram-12m` | `dce -> loop-unroll` | `5b3f9acb-8d9c-4504-a4a9-8b000d5425c4` |
| `skew-histogram-16m` | `loop-unroll -> slp-vectorizer -> loop-fusion` | `d757961a-a705-4558-81e2-1fe649d61ee2` |

## Reusable evidence

- [Selected statistics CSV](../examples/showcase/statistics.csv)
- [Selected catalog and exact runnable requests](../examples/showcase/catalog.json)
- [Complete screening audit](../examples/showcase/screening.json)

The UI exposes only qualifying showcase cards. Each card opens its complete original report or starts a fresh, unfiltered full experiment. No result is guaranteed on rerun. The screening audit preserves all attempted results; each completed experiment has its original summary, raw samples, trial records, commands, environment, and charts under `examples/showcase/<case-id>/`.
