# Custom C code inspection

The app now accepts a pasted single-file C17 source or a `.c` file up to 64 KiB under **Inspect your own C code**, above the benchmark form. A function is enough; `main`, runtime inputs, and expected outputs are unnecessary.

Click **Load example code** for a starting point, then **Inspect code**. Results include:

- The selected pass sequence and its rationale (OpenAI or local rules).
- Initial LLVM IR from Clang at `-O0` with `optnone` disabled.
- Explicit `mem2reg` preparation into SSA form.
- Each selected pass applied individually, with LLVM verification, an IR diff, and feature counts.
- Before/after IR and final x86-64 assembly from `llc -O3`.
- A downloadable JSON trace, including the original source and compiler commands.

The submitted program is never linked or executed. Compiler IR verification checks structural validity; it does not prove output correctness or a runtime improvement. No performance ranking is produced. The source stays on the local server; OpenAI receives extracted IR feature counts. Inspections are temporary and do not enter the benchmark experiment history.

Compilation errors are displayed with diagnostics. Inspection and web benchmark creation/retry are mutually exclusive to avoid interfering with timing measurements. Invalid input and failed compilation release the inspection lock and clean up temporary compiler files.

Supported scope: one C translation unit and locally available system headers, without custom compiler flags or additional uploaded headers/source files. Large generated IR or assembly is rejected explicitly rather than showing misleading truncated counts. The view displays LLVM IR and assembly, not rewritten C.

## Verification

- **143 Python tests passed**, including 15 new inspection tests.
- **8 browser tests passed**. The real-browser workflow inspected a function without `main` through FastAPI/LLVM, checked before/after IR and assembly, and read the JSON download before continuing the existing benchmark, cancellation, retry, and report checks.
- New backend checks cover diagnostics, timeouts, input limits, temporary-file cleanup, allowed pass validation, source privacy at the proposal boundary, and the absence of program execution/linking.
- Browser checks use offline proposals by default. The existing GPT-6 Luna settings and proposal adapter are reused for live inspection.
- A live GPT-6 Luna inspection also completed on the running app: four suggested passes were verified, with the example's textual IR count changing from 40 to 16 across preparation and the selected passes. This is a structural observation, not a measured speedup.

API: `POST /v1/inspections`. Required field: `source`. Optional fields: `max_passes`, `llm_mode`, and `action_ids` for a manual allowlisted pass sequence.
