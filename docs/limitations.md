# Limitations

- The study covers three built-in single-file integer C programs on one x86-64 WSL2 laptop. It does not establish a general LLVM tuning advantage.
- Direct `clang -O3` uses a broader, target-aware optimization pipeline than a short sequence appended to a common `-O1` bitcode seed. The empty-action control isolates the added pass effect; the `-O3` comparison is practical context.
- A run uses one random seed per strategy. Even with fresh finalist confirmation, the guided-versus-random result is descriptive for these particular searches. Multiple independent search seeds are a future study.
- Wall time includes process startup, deterministic input generation, and checksum output. Full workloads were calibrated to reduce the influence of startup, but they are not microarchitectural kernel-only measurements.
- WSL2 scheduling, laptop thermal/frequency behavior, caches, and background activity can alter timing. Raw observations and paired rounds are supplied so apparent small gains can be treated as inconclusive.
- IR feature counts are approximate textual summaries. Fewer IR instructions, or a changed IR hash, do not imply faster machine code.
- A passing checksum is evidence for the tested inputs, not a formal proof that every input is correct. The C and Python oracle share a documented input/checksum specification, and large inputs also crosscheck three compiler paths.
- The in-process API worker preserves completed rows and marks an interrupted run after restart. It is not a durable distributed queue and cannot safely serve multiple hosts or users.
- The local API accepts only built-in benchmarks, binds to localhost, and has no authentication. Arbitrary uploaded C, cloud execution, LLM-backed proposal selection, and remote deployment require separate design and validation.
- Cancellation is cooperative between compiler stages/executions, not immediate process interruption. Retry creates a new experiment and does not resume partial search state.
- Full win/loss classification needs at least ten matched confirmation rounds, a 2% median difference, and agreement in at least 80% of paired rounds. User-selected shorter full runs remain inconclusive. These thresholds are exploratory, not a significance test.
- The original proposal deck and speech PDFs are historical artifacts. Their latency and AI-service promises do not describe this implementation. Use the current presentation notes and measured demo timings for reviews.
