# Five-minute demonstration

1. **Problem (30 seconds):** LLVM `-O3` is a strong baseline. The question is whether a small, program-specific pass search can find an alternative on three selected kernels.
2. **Pipeline (45 seconds):** Show `python -m autotuner doctor`, then explain the `-O1` bitcode seed, validated `opt` actions, correctness gate, and empty-action control.
3. **Live quick run (allow up to 90 seconds):** Open the local webpage, select a benchmark, and start quick mode. State that its result is `demo_only` and uses a five-candidate budget. The 16 October verification measured default quick runs at 2.357 seconds for dot, 2.380 for matmul, and 2.994 for histogram after preflight was ready on this machine. These are observations, not a latency guarantee. Inspect one trial's pass string, checksum checks, and raw timings.
4. **Saved full experiment (90 seconds):** Open a previously saved full run. Compare guided and random results under equal budgets, then compare each finalist with empty control and direct `-O3`. Show tuning and held-out outcomes and one failed/no-op trial.
5. **Limits (45 seconds):** Point to raw samples and explain why a close result is inconclusive. Say that three workloads and one machine do not establish universal improvement.

Run the complete script twice on the presentation machine. Record actual quick-run duration. If the live run is still in progress, clearly switch to a previously measured saved report; never describe saved data as live output. Keep the terminal font large and do not promise a 5–10 second full optimization.

Use the current implementation-review deck under `../../output/presentations/` and `presentation_notes.md`. The Review 1 deck and original speech PDFs are historical. The page now also exposes cooperative cancellation, a fresh linked retry, and partial evidence for failed/cancelled runs.
