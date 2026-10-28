"""Compile-only exploration of user-supplied C; never link or run the program."""
from __future__ import annotations

from dataclasses import asdict
import difflib
from pathlib import Path
import tempfile

from .actions import BY_ID, compose
from .config import DATA_DIR
from .features import summarize_ir
from .llm import OpenAIPolicy, OpenAISettings
from .explanation import explain_inspection
from .policies import GuidedPolicy, Proposal
from .toolchain import Toolchain, execute

MAX_SOURCE_BYTES = 65536
MAX_IR_BYTES = 300000


class InspectionError(Exception):
    def __init__(self, code: str, message: str, diagnostics: str = "", status_code: int = 422):
        super().__init__(message)
        self.code, self.diagnostics, self.status_code = code, diagnostics, status_code


def inspect_source(source: str, tc: Toolchain, catalog: dict, *, max_passes: int = 4,
                   action_ids: list[str] | None = None, llm_mode: str | None = None,
                   explain: bool = False) -> dict:
    if not source.strip() or len(source.encode("utf-8")) > MAX_SOURCE_BYTES or "\x00" in source:
        raise ValueError("Provide non-empty single-file C source, at most 64 KiB, without NUL characters")
    if type(max_passes) is not int or not 1 <= max_passes <= 4:
        raise ValueError("max_passes must be an integer from 1 to 4")
    available = tuple(item["id"] for item in catalog["items"] if item["available"])
    if action_ids is not None:
        compose(action_ids)
        if len(action_ids) > max_passes or any(action not in available for action in action_ids):
            raise ValueError("Selected passes must be available and fit max_passes")
    settings = OpenAISettings.from_env()
    mode = settings.resolve_mode(llm_mode) if action_ids is None else "manual"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="inspect-", dir=DATA_DIR) as directory:
        scratch = Path(directory)
        source_path = scratch / "source.c"
        source_path.write_text(source, encoding="utf-8")
        commands = []

        def checked(result, code, message):
            commands.append({"args": result.args, "duration_ms": round(result.duration_ns / 1e6, 2),
                             "returncode": result.returncode, "timed_out": result.timed_out})
            if not result.ok:
                raise InspectionError("COMPILER_TIMEOUT" if result.timed_out else code, message,
                                      result.stderr_excerpt[-12000:].replace(str(scratch), "inspection"))

        def read_ir(bitcode, label):
            # Read the full file, never compute counts from truncated compiler output.
            text = tc.textual_ir(bitcode, scratch, label)
            if len(text.encode("utf-8")) > MAX_IR_BYTES:
                raise InspectionError("IR_TOO_LARGE", "Generated IR is too large for this view; inspect a smaller source file")
            return text

        initial = scratch / "initial.bc"
        checked(execute([tc.paths["clang"], *tc.clang_flags, "-O0", "-Xclang", "-disable-O0-optnone",
                         "-emit-llvm", "-c", str(source_path), "-o", str(initial)], scratch, "compile", cwd=scratch),
                "SOURCE_COMPILE_FAILED", "C compilation failed. Check the compiler diagnostics below.")
        initial_ir = read_ir(initial, "initial-ir")
        stages = [{"title": "Compile C to LLVM IR", "action_id": None, "pipeline": "clang -O0 (optnone disabled)",
                   "ir": initial_ir, "features": summarize_ir(initial_ir), "changed": None, "diff": ""}]

        def add_stage(title, action_id, pipeline, bitcode, label):
            previous = stages[-1]
            ir = read_ir(bitcode, label)
            features = summarize_ir(ir)
            stages.append({"title": title, "action_id": action_id, "pipeline": pipeline,
                           "ir": ir, "features": features, "changed": previous["ir"] != ir,
                           "instruction_delta": features["instruction_count"] - previous["features"]["instruction_count"],
                           "diff": "\n".join(difflib.unified_diff(previous["ir"].splitlines(), ir.splitlines(),
                                                               fromfile="before.ll", tofile="after.ll", lineterm=""))})

        prepared = scratch / "prepared.bc"
        checked(execute([tc.paths["opt"], "-verify-each", "-passes=mem2reg", str(initial), "-o", str(prepared)], scratch, "prepare"),
                "IR_VERIFICATION_FAILED", "LLVM could not prepare valid IR")
        add_stage("Prepare SSA form (mem2reg)", "mem2reg", "mem2reg", prepared, "prepared-ir")
        features = stages[-1]["features"]
        if action_ids is not None:
            proposal = Proposal(tuple(action_ids), "manual", "Passes explicitly selected for inspection.")
        elif mode == "openai":
            proposal = OpenAIPolicy(available, max_passes, 29, features, settings).propose(set(), [])
        else:
            proposal = GuidedPolicy(available, max_passes, 29, features).propose(set(), [])
        if proposal is None:
            raise InspectionError("NO_PROPOSAL", "No pass sequence is available")
        current = prepared
        for index, action_id in enumerate(proposal.action_ids, start=1):
            output = scratch / f"step-{index}.bc"
            checked(tc.optimize(current, (action_id,), output, scratch, f"pass-{index}"),
                    "IR_VERIFICATION_FAILED", f"LLVM rejected the {action_id} transformation")
            add_stage(BY_ID[action_id].display_name, action_id, compose((action_id,)), output, f"step-{index}-ir")
            current = output
        assembly_file = scratch / "final.s"
        checked(execute([tc.paths["llc"], "-O3", "-filetype=asm", "-mcpu=x86-64", "-relocation-model=pic",
                         str(current), "-o", str(assembly_file)], scratch, "assembly"),
                "ASSEMBLY_FAILED", "LLVM could not generate assembly")
        if assembly_file.stat().st_size > MAX_IR_BYTES:
            raise InspectionError("ASSEMBLY_TOO_LARGE", "Generated assembly is too large for this view; inspect a smaller source file")
        assembly = assembly_file.read_text(encoding="utf-8", errors="replace")
        explanation = {"status": "disabled", "message": "Enable the GPT explanation to see readable code and a plain-language walkthrough."}
        if explain:
            if settings.resolve_mode(llm_mode) == "openai":
                explanation = explain_inspection(source, stages, assembly, settings)
            else:
                explanation = {"status": "unavailable", "message": "A readable explanation requires OpenAI mode and a configured API key."}
        return {"status": "completed", "mode": "inspection", "executed": False,
                "correctness": "not_tested", "performance": "not_measured",
                "notice": "Compiler IR verification passed. Your program was not executed; output correctness and speed were not tested.",
                "source": source, "proposal": asdict(proposal), "pipeline": compose(proposal.action_ids),
                "llm": {"mode": mode, "model": settings.model if mode == "openai" else None,
                        "reasoning_effort": settings.reasoning_effort if mode == "openai" else None},
                "stages": stages, "assembly": assembly, "explanation": explanation,
                "commands": commands}
