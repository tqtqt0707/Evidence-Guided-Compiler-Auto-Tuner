# Current project status

Updated 20 October 2026.

The local correctness-checked LLVM tuner is implemented. It supports dot product, integer matrix multiplication, and histogram benchmarks; rule-based guided and seeded random policies; empty-action and direct Clang -O3 controls; tuning/held-out confirmation; SQLite records; CLI; localhost API; a browser evidence page; and optional LangGraph orchestration. It makes zero LLM calls.

## Reliability work completed

- Startup failures mark the run failed and release its worker, including source-copy and directory failures.
- Rejected finalists cannot retain a winning selection or contribute to final performance claims.
- Comparisons use matched valid confirmation rounds and scale the 80% agreement rule to the requested round count.
- Reports persist executable sizes and reconstruct without surviving binaries.
- Failed compiler commands and bounded stderr diagnostics remain available without a successful variant.
- Invalid IR disassembly fails explicitly; malformed or hash-mismatched cache entries rebuild.
- Readiness validates matching LLVM 21 versions, action pipelines, linking, and checksum execution.
- Worker recovery distinguishes process boot/start identity from a reused PID. Graceful shutdown drains accepted work.
- Cancel and retry are available through CLI/API/UI. Retry preserves the original and links a fresh experiment.
- Browser results show all comparisons on tuning/held-out inputs, actual settings, raw evidence, and measured-result explanations.
- Polling ignores stale responses. Seeds are restricted to exact browser integers. Saved runs have pagination and trial rows support the keyboard.
- The Python wheel includes sources, web assets, and three portable evidence bundles.
- Compiler/browser regression tests and CI configuration are included.
- A corrected ten-slide implementation-review deck is available under the workspace's `output/presentations/`, with the original proposal preserved.

## Saved research outcome

Three bundled full experiments each contain 20 guided and 20 random proposals. Source hashes match the current benchmark files. No guided comparison establishes a win over direct -O3 under the declared rule. Histogram guidance beats random on holdout, matrix multiplication guidance loses on holdout, and dot comparisons remain inconclusive. These are valid exploratory outcomes, not pending bugs.

The bundles retain their original observations. Regression checks and new demo timing checks are not replacements for a multi-seed performance study.

## Remaining extensions and delivery decisions

- Independent search seeds, more benchmarks/machines, native Linux comparisons, kernel-only timing, hardware counters, and structured LLVM analysis require further research and new measurements.
- Arbitrary uploaded C requires a separate sandbox and oracle design. It is intentionally unsupported.
- LLM/learned proposals require model configuration and evaluation against the same controls. Current explanations are deterministic descriptions of measured outcomes.
- Remote/multi-user deployment needs authentication, isolation, quotas, and a durable worker queue. The supported service remains localhost-only.
- Cancellation takes effect between stages/executions. Retry starts over rather than resuming a partially completed search.
- The repository has no initial commit. No commit, remote publication, or hosted CI run has been performed as part of these local fixes.
- The original Review 1 deck and speech PDFs describe the proposal. Use the implementation-review deck and `presentation_notes.md` as the current review script and retain old artifacts as historical references.
- The existing PDF report records an earlier implementation snapshot. Current verification and added controls are documented here rather than retroactively changing its experiment record.

## Verification on 16 October

Default five-proposal quick runs completed on all three benchmarks after preflight readiness: dot 2.357 s, matmul 2.380 s, histogram 2.994 s. Small-input full integration runs completed with both strategies and ten held-out rounds on all three programs. A real graph-orchestrated compile/execute run also completed. See `verification-2026-10-16.json` for the machine/toolchain record. Small-input full checks validate plumbing and are not performance evidence.

The final local checks passed: 31 Python tests and six Chromium browser tests. Browser tests use intercepted API fixtures; real worker/API/compiler behavior is checked separately in Python integration tests. A rebuilt wheel was installed outside the source checkout and completed a compiler experiment with all eight actions, three evidence bundles, and twelve API operations available. Hosted CI is configured but has not been run.

## Verification on 20 October

The expanded suite passes **104 Python tests and eight Chromium tests**. The browser suite now includes a complete workflow against the actual FastAPI worker and LLVM through a stdio ASGI bridge. Clean sdist/wheel installation, resource hashes, standalone CLI commands, and portable exports also pass. In-process Python coverage is 90.7% of statements and 80.1% of branches.

New fixes cover structured toolchain failures, calibration input/checksum errors, duplicate execution before preflight, Ctrl+C process/state cleanup, orphan recovery, clean CLI errors, and exact display of saved uint64 seeds. Automated tests use disposable records and cache directories. See [test plan](TEST_PLAN.md) and [full results and limits](TEST_RESULTS_2026-10-20.md). Hosted CI and actual TCP/Uvicorn socket behavior have not been verified in this run.
