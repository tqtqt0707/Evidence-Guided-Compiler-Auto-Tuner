# Current review script

Use this script instead of the original proposal speeches. The original Review 1 deck is historical and its 5–10 second latency target is superseded.

## 1. Project and scope

This project tests short LLVM optimization sequences for three deterministic C17 programs. The implementation uses Python rules and measured feedback. It does not use an LLM or a trained model.

## 2. Compiler pipeline

Clang 21 emits a common -O1 bitcode seed. Validated opt pipelines transform that IR. llc -O3 emits an object and Clang links it. Every executable must preserve the expected checksum before its measurements can contribute to a result.

## 3. Correctness evidence

Independent Python implementations check small inputs. Larger tuning and held-out workloads must agree across -O0, direct -O3, and the empty-action path. Every timed candidate execution checks its output again. This is evidence on tested inputs rather than a proof for every possible input.

## 4. Guided and random search

The guided policy uses approximate IR features and its own previous trial timings. Random search uses the same catalog and budget without guided evidence. Default full experiments allocate 20 proposals to each strategy. A failed or no-change proposal consumes budget.

## 5. Measurement

Search medians choose finalists. Fresh confirmation measurements compare the finalists and controls in randomized interleaved rounds. Held-out data does not affect selection. Default full mode uses ten rounds per input, and the page displays the actual settings.

## 6. Outcome rule

A full win requires at least 2% lower median runtime and agreement in at least 80% of ten or more paired rounds. Loss is symmetric. Other results are inconclusive. Quick runs are demo-only. A tuning win that fails to reproduce on holdout is workload-specific.

## 7. Results

The three saved full experiments contain 120 proposals in total. The guided histogram finalist beats random on holdout, but no guided result establishes a win over direct -O3. Guided matrix multiplication loses on holdout. Dot comparisons are inconclusive. These mixed results answer the experimental question honestly.

## 8. Live demonstration

Open a bundled full report first and show all comparisons and held-out outcomes. Run quick mode to demonstrate the live pipeline. State its measured duration from the verification record for this machine; do not promise a universal duration. If the live run exceeds the presentation slot, switch to explicitly labelled saved evidence. Show trial commands, checksums, and raw samples. Cancellation preserves partial evidence, and retry starts a new linked experiment.

## 9. Limitations

Three programs and one WSL laptop cannot establish a general tuning advantage. Scheduling and process overhead affect timing. IR counts are approximate and do not prove speedup. Multiple seeds and more machines are future experiments. Custom C uploads and public deployment remain outside the supported local tool.

## 10. Contribution

The contribution is a reproducible compiler experiment platform with correctness gates, fair controls, saved raw evidence, and transparent negative results.
