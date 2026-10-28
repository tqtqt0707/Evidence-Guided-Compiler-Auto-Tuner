# Test results — 20 October 2026

All final executed checks passed. The supported local compiler workflow is functional across the tested cases on Ubuntu WSL with LLVM 21. This is a bounded functional assessment rather than proof that every possible input/environment works.

## Final results

| Check | Result | Scope |
| --- | --- | --- |
| Python suite | **104 passed**, no skips/failures, 49.40 s | Compiler integration, independent oracle, requests, policies, both orchestrators, API worker, records/recovery, reports, failure injection, and CLI behavior |
| Chromium suite | **8 passed**, no skips/failures, 22.56 s | Seven UI/edge-case tests using fixtures and one browser workflow using the actual FastAPI worker and LLVM |
| Clean installation | **Passed** | Source distribution → wheel → fresh virtual environment, outside the editable checkout |
| Installed CLI | **Passed** | Benchmarks, doctor, actions, tune, status, trials, calibration, report export, retry, cancellation, and public-host rejection |
| Resource/export checks | **Passed** | Eight validated actions, three evidence bundles, byte-for-byte packaged resource hashes, consistent exported summaries and examples |
| Python coverage | **90.7% statements; 80.1% branches** | Combined coverage.py score: 88.2%; child processes/browser checks are separate from this figure |

The existing suite was run first and passed (31 Python tests and six browser tests). New targeted checks then reproduced missing-executable/probe-timeout API errors, invalid calibration selection, duplicate queued execution, and Ctrl+C cleanup failures. Each product failure has a regression check in the expanded suite.

## Fixes validated

1. **Toolchain failures:** lost executables and hung version probes now return structured HTTP 503 `TOOLCHAIN_UNAVAILABLE` responses for actions, create, and retry, rather than raw HTTP 500 failures.
2. **Calibration:** an unknown benchmark fails before compiler work. A changing checksum across repetitions fails calibration rather than producing a successful timing record.
3. **Worker ownership:** queued work is atomically claimed before preflight. Duplicate calls cannot compile the same run simultaneously. Schema inspection/migration is serialized during concurrent startups.
4. **Interruptions:** Ctrl+C kills the spawned process group, marks the experiment/unfinished trials interrupted, releases the worker, and returns CLI exit code 130 without a traceback. Restart recovery also reconciles orphaned active records left by older behavior.
5. **CLI failures:** classified trial errors, missing tools, and subprocess timeouts produce clean nonzero exits instead of uncaught tracebacks.
6. **Saved large seeds:** the browser preserves exact unsafe-size JSON integers as text when reading saved results. The uint64 maximum displays as `18446744073709551615` rather than a rounded value. Browser input remains limited to safe integers.
7. **Test safety:** the Python suite redirects records, cache files, calibration output, and run artifacts to disposable directories. The real-browser bridge and clean-install checker also use temporary data stores. Original scientific bundles and C sources were preserved.

The browser harness was also corrected to wait for a newly selected run and completed partial-result rendering, rather than accidentally asserting against the previous view during asynchronous updates.

## Coverage details

- Each C program was compiled at O0 and O3 and checked against the independent Python oracle on sizes 1–17, boundary seeds including zero and uint64 maximum, and repeat counts up to 100. Invalid argument/range cases were rejected.
- Every benchmark ran in quick/full modes under plain and graph orchestration. Full checks included ten tuning/held-out confirmation rounds. A separate real run exercised 20 guided and 20 random proposals, unique budgets, mutations, and policy-local parent evidence.
- Injected unknown passes, invalid IR, compiler/link errors, nonzero executions, timeouts, malformed output, and wrong checksums were rejected before valid search timing/winner selection.
- Timeout tests checked descendant termination and the ability to execute the next command. Interruption tests checked both persisted state and process cleanup.
- Storage checks covered concurrent creation, duplicate execution, migration, restart identity, orphan recovery, binary-size persistence, and report reconstruction. Exported example summaries matched their bundles, and saved scientific sample counts/source hashes matched their records.
- The real-browser workflow created full-mode experiments for dot, matmul, and histogram, inspected trial evidence, downloaded report files, loaded charts, cancelled work, retried it with a parent link, and reopened saved results after reload.
- The clean-install checker built from an sdist, verified the installed resource hashes against the source tree, and exercised the standalone CLI without depending on the editable source installation or optional web packages.

## Reproduction

Run from the repository root in the configured Ubuntu environment:

```bash
python -m pip install -c constraints.txt -e '.[web,test,graph]'
python -m coverage run -m pytest -q --junitxml=test-results/python.xml
python -m coverage report
python -m coverage json -o test-results/coverage.json
npm ci
npx playwright install chromium
AUTOTUNER_TEST_PYTHON="$VIRTUAL_ENV/bin/python" node --test tests/browser.test.cjs tests/live_browser.test.cjs
python scripts/check_package.py
```

Activate the virtual environment before using `$VIRTUAL_ENV`, or give its absolute Python path. On Windows, the browser suite defaults to Ubuntu WSL and the project's documented virtual environment. `PLAYWRIGHT_MODULE_PATH` and `BROWSER_EXECUTABLE` allow an existing local Playwright/Chromium installation to be selected. Build checks require access to the configured pip index for declared build dependencies.

## Limits

The browser sends real HTTP request/response data through a stdio ASGI TestClient bridge. No TCP listener was started, so this run does not verify Uvicorn socket binding, proxies, firewall behavior, remote hosting, or multiple service hosts. Hosted GitHub Actions is configured but has not been executed. Native Windows compiler execution is outside the supported Linux/WSL runner.

Small-input full runs validate program flow, accounting, and reporting; they are not new performance research. This assessment does not exhaust every valid workload, prove semantic equivalence for all inputs, or demonstrate a general speedup. Optional uploads, LLM proposals, and remote multi-user deployment remain outside the implementation. Coverage omissions are recorded in `test-results/coverage.json`; notably, separately executed CLI/package processes are not included in the in-process Python coverage.
