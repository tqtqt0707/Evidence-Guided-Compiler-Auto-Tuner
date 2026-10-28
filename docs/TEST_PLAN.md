# 1. Issue Breakdown

The supported application must compile the three built-in C programs, reject incorrect or failed candidates, compare guided/random proposals fairly, preserve raw records, and show accurate tuning/held-out results. Existing tests passed at the start of this audit (31 Python and six browser checks), but browser responses were fixtures and several failure paths lacked direct coverage.

# 2. Repository Areas Requiring Deep Research

- `models.py`, `registry.py`, and C argument parsing: strict request boundaries, unsigned seeds, workload defaults, and invalid inputs.
- `oracle.py`, `toolchain.py`, and `service.py`: independent checksums, compile/run failures, process-group timeouts, both orchestrators, cache reuse, and confirmation gates.
- `storage.py` and `report.py`: exclusive worker ownership, restart recovery, legacy migrations, sample filtering, stable artifacts, and regeneration.
- `api.py`, `cli.py`, and `calibration.py`: documented commands/routes, graceful unavailable-tool behavior, cancellation/retry, pagination, and errors.
- `web/app.js`: complete user flow against the actual worker, stale responses, settings, saved runs, controls, and downloads.
- `setup.py`, `MANIFEST.in`, and CI: distributable resources and execution outside an editable checkout.

# 3. Fix Validation / Testing Checklist

- [x] Run the pre-existing Python and Chromium suites before changes.
- [x] Isolate all automated runs from the user's SQLite database and cached evidence.
- [x] Compare all C benchmarks at O0/O3 against the independent Python oracle over selected boundary seeds/sizes and repeated workloads; reject invalid C arguments.
- [x] Exercise quick/full mode for every benchmark with plain/graph orchestration, including held-out rounds and regenerated reports.
- [x] Inject pass-parse, compilation, IR, runtime, timeout, malformed-output, and checksum failures and prove that none becomes a winner or valid timing.
- [x] Verify request boundaries and degraded toolchain/API errors, including lost executables and hung probes.
- [x] Check exclusive worker ownership, duplicate execution, legacy schema migration, stale records, and clean cancellation/retry behavior.
- [x] Verify Ctrl+C marks interrupted work, kills spawned processes, releases ownership, and returns a clean CLI error.
- [x] Check CLI commands, calibration errors, exports, and portable bundle consistency; document the untested network/server boundary.
- [x] Exercise the browser against real FastAPI requests and LLVM work, with no simulated business-route responses.
- [x] Build an sdist, build a wheel from it, install it in a clean virtual environment outside the checkout, and run it there.
- [x] Run the complete final suites and record exact results, fixes, and limits in `TEST_RESULTS_2026-10-20.md`.
