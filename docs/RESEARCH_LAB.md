# Research Lab: implementation and evaluation

Open /research in the same local app. The existing tuning service remains available at /.

## Eight implemented extensions

1. **Pass contribution and order experiments.** Compile the full sequence, empty control, each leave-one-out sequence, and distinct permutations (1–4 allowlisted actions). Every variant uses the same -O1 seed and backend. Compare fresh, randomized, paired execution rounds; retain failed compilations and no-change IR. Removing a pass is a controlled intervention on this sequence, not a universal claim about that pass.
2. **Actual optimization remarks.** Record LLVM passed, missed and analysis remarks from opt. Parse its tagged YAML with a safe loader and preserve available source locations. No remark is invented when the compiler is silent.
3. **Robustness map.** Freeze the tuning-selected guided sequence and measure predeclared size/seed cells against direct -O3. Reserve a distinct final holdout, independent of the exploratory map.
4. **Adaptive experience search.** Match historical training records by compiler versions, target, flags and catalog hash; exclude the same source hash. Feature-neighbor rewards and exploration prioritize unseen pass sequences. Guided, random and adaptive search receive equal proposal budgets. Only explicitly designated training runs contribute tuning observations; no holdout observation enters memory. Improvement from learning is a question to evaluate, not a guarantee.
5. **Custom C benchmarking.** A trusted local user supplies uint64_t kernel(uint64_t size, uint64_t seed) plus a separately compiled reference function. A generated driver hashes repeated results. Control disagreement, malformed checksums, timeouts and wrong results reject the experiment. Linux prlimit caps CPU, address space (except sanitizer runs), file size and descriptors. **This is not a security sandbox; do not expose it to untrusted users or remote networks.**
6. **Constrained selection.** Select fastest, smallest .text, or balanced candidates subject to text-size, build-cost and slowdown constraints. Exclude invalid/nonfinite/missing-metric candidates. Show the feasible Pareto frontier. Selection uses tuning/search observations, not final holdout.
7. **Interactive CFG.** Parse actual LLVM labels and terminators, including multiline switch targets. Compute dominance back edges and preserve emitted debug source lines. Click blocks to view their actual instructions. The viewer limits large graphs to 30 nodes; JSON contains all parsed blocks.
8. **Stronger validation.** Test deterministic fixtures and seeded randomized inputs against Python built-in oracles or the supplied reference. Run source ASan/UBSan builds separately from timing. An optional alive-tv adapter records supported-case validation, unsupported/counterexample, timeout, unknown or not-installed states. Missing Alive2 never counts as a proof.

## Evidence and reproducibility

Run inside the documented x86-64 Linux / LLVM 21 environment:

    python -m autotuner serve --host 127.0.0.1 --port 8000
    python -m autotuner.research --output ./test-results/research.json --size 250000 --rounds 10 --budget 4
    python -m pytest tests/test_research.py tests/test_research_integration.py -q
    python scripts/generate_research_evidence.py

The job JSON includes request, source hash, environment, build commands, paired raw timings, bootstrap intervals, rejected variants, three search histories, reserved-holdout confirmation, grid cells, actual remarks, CFGs, constraints and validation results. Jobs persist under AUTOTUNER_DATA_DIR/research/. Evaluation does not overwrite historical reports.

Reported timings include process startup and generation/checksum overhead. Custom timings also include prlimit launcher overhead. Three initial search samples rank candidates; separate warmups are excluded from fresh confirmation. At least ten matched rounds, a 2% median runtime reduction, and 80% faster pairs are required for a practical win label. Bootstrap intervals are exploratory and not corrected for multiple comparisons. Short CI workloads validate execution and evidence handling; CI-host results do not establish laptop performance.

## API

- GET /v1/research/capabilities
- GET/POST /v1/research/jobs
- GET /v1/research/jobs/{uuid}
- GET /v1/research/jobs/{uuid}/report
- GET /v1/research/jobs/{uuid}/download
- POST /v1/research/objectives

Research work shares the app's exclusive compiler lock with classic tuning and code inspection. This avoids running competing experiments while timing. Jobs have bounded input, pass, candidate, sample and wall-clock limits.

## References

- LLVM optimization remarks: https://llvm.org/docs/Remarks.html
- Alive2 supported transformations and build requirements: https://github.com/AliveToolkit/alive2

These components extend this project's implementation; no claim of globally novel compiler algorithms is made.
