# Selected performance test set

The **Selected performance test set** panel is a presentation showcase selected
after measurement. It is not an unbiased evaluation or evidence that the tuner
usually beats Clang. The screening pool is defined in `autotuner/showcase.py`.
The initial ten-case pool was:

| Program | Tuning sizes (elements) | Tuning seeds | Repetitions inside each execution |
|---|---|---|---|
| Two-stage array pipeline | 4M, 8M, 12M, 14M, 16M, 6M | 17, 29, 43, 61, 79, 101 | 12 |
| Skewed branch histogram | 4M, 8M, 12M, 16M | 17, 29, 43, 61 | 12 |

Each size pairs with the seed in the same position. Held-out sizes are 1M larger
except at the maximum size (16M), where they are 1M smaller. Held-out seeds are
tuning seed + 1000. These are ten workload cases from two C source programs,
not ten independent algorithms. Both programs have independent small-input
Python correctness oracles. The pipeline tests opportunities for fusing adjacent
producer/consumer loops. The histogram tests a biased branch distribution with
scattered memory updates.

## Follow-up pool focused on wins

After reviewing the initial results, four additional pipeline workloads were
chosen using sizes and search seeds that had previously found loop fusion:

| Tuning size | Seed | Held-out size | Held-out seed | Kernel repetitions |
|---:|---:|---:|---:|---:|
| 8,000,000 | 79 | 8,500,000 | 2079 | 24 |
| 10,000,000 | 101 | 10,500,000 | 2101 | 24 |
| 12,000,000 | 79 | 12,500,000 | 2079 | 24 |
| 16,000,000 | 101 | 15,500,000 | 2101 | 24 |

This is adaptive showcase screening informed by prior results, not a fresh
unbiased evaluation. These are additional workloads of the same pipeline source.
Longer executions reduce the relative impact of startup and timing granularity.
The follow-up cases must satisfy the original comparison limits **and** receive
the engine's `win` label versus Clang `-O3` on **both** tuning and held-out inputs.
That requires at least 2% runtime reduction and faster execution in at least
80% of ten or more paired rounds. The rule is unchanged; it is a practical
evidence rule, not a statistical significance test. No measured run is retried
to obtain a more favorable result. The combined screening pool has fourteen
cases. Each follow-up record carries `require_o3_win: true` in the audit.

Every case runs the ordinary full tuning service with local rule-based guided
search (`llm_mode=off`), 20 proposals for each of guided and random search, a
maximum of four passes, five search measurements per valid candidate, and ten
interleaved confirmation rounds on each of tuning and held-out inputs. No
hand-picked pass sequence replaces the guided search. Small cases are checked
against Python oracles; workload checksums must match the `-O0` reference, and
every measured binary execution passes the checksum gate. The original engine
selects finalists using tuning measurements without seeing holdout timings.

Selection is a separate presentation step after the full run. The guided finalist
must have no more than 2% median runtime regression against **both direct Clang
`-O3` and the equal-budget random finalist**, on **both workloads**, with at least
ten matched confirmation rounds per comparison. Missing, failed, or insufficient
measurements never qualify. Cases outside this limit do not get showcase cards;
their actual results remain in the complete screening audit and exported reports.
There are no retries to obtain a better measurement during one screening run.

The UI's **Show only confirmed held-out wins versus Clang -O3** filter uses
the actual report label. Cards also show each input's evidence label, so a lower
median with insufficient paired wins remains visibly inconclusive.

The cards display median wall-clock time, median absolute deviation (MAD),
runtime reduction, and faster paired rounds. Runtime reduction is
`100 * (reference median - guided median) / reference median`; a positive value
means less runtime. The summary counts describe this selected subset only.
They do not estimate an overall success rate. The full report retains the
engine's existing win/loss/inconclusive labels and all five comparisons,
including empty-action controls. A result within 2% is a descriptive close
comparison, not a statistical equivalence test. These measurements include
process startup and polling overhead and reflect a single WSL machine.

## Reproduce and inspect

From an idle machine in the documented LLVM 21 Linux environment:

```bash
python -m autotuner.showcase
```

The full pool can take tens of minutes and writes `examples/showcase/catalog.json` (selected
cases), `statistics.csv` (one statistics row per selected workload/input),
`screening.json` (every attempted case), and one normal portable report
directory per completed case. It uses no API credits. `--resume` reuses completed
records whose source hash and request match; do not use it for a new independent
measurement session. `--output DIRECTORY` writes a separate screening session.

The page loads the selected catalog as a static resource, so historical evidence
also opens without a working LLVM toolchain. **Open measured evidence** opens the
complete original report, raw CSV downloads, pass actions, commands, and candidate
details. **Run this test** posts the case's exact full-experiment settings to the
normal API and displays live progress and the complete fresh result. It never
filters the new result or replaces the historical measurements. Only one
experiment can run at a time. Fresh runs may regress or select different passes.

To validate the selection rule and UI behavior:

```bash
python -m pytest -q tests/test_showcase.py
npm test
```
