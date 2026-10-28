# Additional winning workloads: 26 October 2026

Screened 4 follow-up workloads; 4 qualified. The combined showcase now contains 12 selected cases from 14 screened cases, including 7 held-out wins versus Clang `-O3` (previously three).

These are workload variants of the existing two-stage array pipeline, chosen after reviewing the initial screening results. They use 24 kernel repetitions rather than 12, new workload combinations, and fresh held-out seeds/sizes. This is adaptive, selected showcase evidence, not a representative evaluation or additional independent algorithms. Original measurements remain intact.

Every added case must win against Clang `-O3` on both tuning and held-out inputs: at least 2% runtime reduction and faster execution in at least 80% of ten or more paired rounds. It must also remain within 2% of the equal-budget random finalist on both inputs. The win rule and search budget are unchanged.

Each full experiment uses local rule-based guided search, 20 guided and 20 random proposals, at most four passes, five search repetitions, and ten confirmation rounds per workload. Independent small-input oracles and exact checksum gates are unchanged. Measurements are whole-process wall time on the same Intel i7-13650HX / WSL2 / LLVM 21.1.8 machine, including process startup and polling overhead.

| Case | Input: elements / seed | Clang -O3 median / MAD (ms) | Guided median / MAD (ms) | Reduction vs O3 | Faster pairs | O3 evidence | Reduction vs random |
|---|---|---:|---:|---:|---:|---|---:|
| `pipeline-8m-r24-s79` | tuning: 8,000,000 / 79 | 315.53 / 0.19 | 215.30 / 0.23 | +31.76% | 10/10 | win | -0.06% |
| `pipeline-8m-r24-s79` | holdout: 8,500,000 / 2079 | 365.40 / 0.12 | 214.86 / 0.18 | +41.20% | 10/10 | win | +0.09% |
| `pipeline-10m-r24-s101` | tuning: 10,000,000 / 101 | 415.96 / 0.12 | 265.46 / 0.24 | +36.18% | 10/10 | win | +0.01% |
| `pipeline-10m-r24-s101` | holdout: 10,500,000 / 2101 | 416.26 / 0.17 | 265.86 / 0.39 | +36.13% | 10/10 | win | -0.07% |
| `pipeline-12m-r24-s79` | tuning: 12,000,000 / 79 | 516.72 / 0.20 | 341.06 / 25.02 | +34.00% | 10/10 | win | +0.07% |
| `pipeline-12m-r24-s79` | holdout: 12,500,000 / 2079 | 517.19 / 0.35 | 366.15 / 0.12 | +29.20% | 10/10 | win | +0.05% |
| `pipeline-16m-r24-s101` | tuning: 16,000,000 / 101 | 668.03 / 0.93 | 416.83 / 0.45 | +37.60% | 10/10 | win | -0.04% |
| `pipeline-16m-r24-s101` | holdout: 15,500,000 / 2101 | 617.20 / 0.09 | 416.74 / 0.13 | +32.48% | 10/10 | win | -0.02% |

The added cases are wins against Clang `-O3`. Their guided-versus-random comparisons are inconclusive; this does not establish a guided-search advantage over random search. The win label is the existing practical evidence rule, not a statistical significance test.

## Finalists and portable evidence

| Case | Guided pass actions | Original report |
|---|---|---|
| `pipeline-8m-r24-s79` | `loop-fusion -> slp-vectorizer` | [Summary](../examples/showcase/pipeline-8m-r24-s79/summary.json) |
| `pipeline-10m-r24-s101` | `loop-fusion -> slp-vectorizer` | [Summary](../examples/showcase/pipeline-10m-r24-s101/summary.json) |
| `pipeline-12m-r24-s79` | `dce -> loop-fusion -> gvn` | [Summary](../examples/showcase/pipeline-12m-r24-s79/summary.json) |
| `pipeline-16m-r24-s101` | `loop-unroll -> loop-fusion` | [Summary](../examples/showcase/pipeline-16m-r24-s101/summary.json) |

## Run from the UI

Refresh the page to load the updated showcase. **Show only confirmed held-out wins versus Clang -O3** filters using the actual report label. Every visible case has **Run this test** for a fresh full experiment and **Open measured evidence** for the original complete report. Each tuning/held-out row also shows its evidence label. A fresh run is always shown in full, including any regressions.

- [Combined statistics CSV](../examples/showcase/statistics.csv)
- [Combined selected catalog](../examples/showcase/catalog.json)
- [Complete screening audit, including excluded cases](../examples/showcase/screening.json)
- [Methodology and exact follow-up inputs](SELECTED_TEST_SET.md)
- [Initial screening results](SELECTED_RESULTS_2026-10-26.md)

Use `python -m autotuner.showcase --resume` to retain completed records while measuring newly added workloads. Use the UI buttons for fresh measurements of an individual case.
