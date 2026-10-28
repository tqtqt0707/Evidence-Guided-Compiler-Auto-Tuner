from __future__ import annotations

import itertools
import importlib.metadata
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .actions import ACTIONS, compose, snapshot
from .config import COMPILE_TIMEOUT_S, EXEC_TIMEOUT_S, MAX_CAPTURE_BYTES, TOOL_NAMES, ROOT, sha256_file
from .models import Workload


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    duration_ns: int
    timed_out: bool
    stdout: str
    stderr_excerpt: str
    stdout_path: str
    stderr_path: str
    cwd: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def as_dict(self) -> dict:
        return asdict(self)


def execute(args: list[str], log_dir: Path, label: str, *, timeout: int = COMPILE_TIMEOUT_S, cwd: Path | None = None) -> CommandResult:
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{label}.stdout"
    stderr_path = log_dir / f"{label}.stderr"
    start = time.perf_counter_ns()
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(args, stdout=stdout_file, stderr=stderr_file, cwd=cwd, start_new_session=True)
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            returncode = process.wait()
        except BaseException:
            # Ctrl-C must not leave a compiler or its descendants executing.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
    elapsed = time.perf_counter_ns() - start
    stdout = stdout_path.read_bytes()[:MAX_CAPTURE_BYTES].decode("utf-8", "replace")
    stderr = stderr_path.read_bytes()[-MAX_CAPTURE_BYTES:].decode("utf-8", "replace")
    result = CommandResult(args, returncode, elapsed, timed_out, stdout, stderr, str(stdout_path), str(stderr_path), str((cwd or Path.cwd()).resolve()))
    command_manifest(log_dir / f"{label}.command.json", [result])
    return result


class Toolchain:
    def __init__(self) -> None:
        missing = [name for name, executable in TOOL_NAMES.items() if not shutil.which(executable)]
        if missing:
            raise RuntimeError(f"Missing LLVM tools: {', '.join(missing)}. Run inside Ubuntu WSL and install clang-21 llvm-21.")
        self.paths = {name: str(Path(shutil.which(executable) or "").resolve()) for name, executable in TOOL_NAMES.items()}
        self.clang_flags = ["-std=c17", "-fno-lto", "-fPIE", "-march=x86-64"]
        self.llc_flags = ["-O3", "-filetype=obj", "-mcpu=x86-64", "-relocation-model=pic"]
        self.link_flags = ["-pie"]
        self._environment: dict | None = None

    def environment(self) -> dict:
        if self._environment is not None:
            return dict(self._environment)
        versions = {}
        numeric_versions = set()
        for name, path in self.paths.items():
            result = subprocess.run([path, "--version"], capture_output=True, text=True, check=False, timeout=10)
            lines = (result.stdout or result.stderr).splitlines()
            versions[name] = lines[0] if result.returncode == 0 and lines else "unavailable"
            match = re.search(r"\bversion\s+(\d+\.\d+\.\d+)", versions[name])
            if not match or not match.group(1).startswith("21."):
                raise RuntimeError(f"Expected LLVM 21, found {name}: {versions[name]}")
            numeric_versions.add(match.group(1))
        if len(numeric_versions) != 1:
            raise RuntimeError(f"LLVM tools must have matching versions: {versions}")
        cpu_model = "unknown"
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            match = re.search(r"^model name\s*:\s*(.+)$", cpuinfo.read_text(errors="replace"), re.MULTILINE)
            if match:
                cpu_model = match.group(1)
        triple = subprocess.run([self.paths["clang"], "-dumpmachine"], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        if not triple.startswith("x86_64") or sys.platform != "linux":
            raise RuntimeError(f"This measurement runner requires x86-64 Linux; found {sys.platform}/{triple}")
        libraries = {}
        for package in ("fastapi", "uvicorn", "pydantic", "langgraph"):
            try:
                libraries[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                libraries[package] = None
        revision = None
        dirty = None
        if shutil.which("git"):
            commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
            revision = commit.stdout.strip() if commit.returncode == 0 else None
            status = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
            dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        self._environment = {"paths": self.paths, "versions": versions, "target_triple": triple,
                "clang_flags": self.clang_flags, "llc_flags": self.llc_flags,
                "link_flags": self.link_flags, "python": sys.version,
                "platform": platform.platform(), "kernel": platform.release(),
                "cpu_model": cpu_model, "wsl": "microsoft" in platform.release().lower(),
                "libraries": libraries, "git_commit": revision, "git_dirty": dirty, "source_root": str(ROOT)}
        return dict(self._environment)

    def compile_direct(self, source: Path, level: str, output: Path, log_dir: Path, label: str) -> CommandResult:
        return execute([self.paths["clang"], *self.clang_flags, level, str(source), "-o", str(output), *self.link_flags], log_dir, label)

    def emit_seed(self, source: Path, output: Path, log_dir: Path) -> CommandResult:
        return execute([self.paths["clang"], *self.clang_flags, "-O1", "-emit-llvm", "-c", str(source), "-o", str(output)], log_dir, "seed")

    def optimize(self, seed: Path, action_ids: tuple[str, ...], output: Path, log_dir: Path, label: str = "opt") -> CommandResult:
        return execute([self.paths["opt"], "-verify-each", f"-passes={compose(action_ids)}", str(seed), "-o", str(output)], log_dir, label)

    def object_and_link(self, bitcode: Path, output: Path, log_dir: Path, label: str) -> list[CommandResult]:
        obj = output.with_suffix(".o")
        first = execute([self.paths["llc"], *self.llc_flags, str(bitcode), "-o", str(obj)], log_dir, f"{label}-llc")
        if not first.ok:
            return [first]
        second = execute([self.paths["clang"], *self.link_flags, str(obj), "-o", str(output)], log_dir, f"{label}-link")
        return [first, second]

    def run_binary(self, binary: Path, workload: Workload, log_dir: Path, label: str) -> CommandResult:
        return execute([str(binary), *workload.args()], log_dir, label, timeout=EXEC_TIMEOUT_S)

    def textual_ir(self, bitcode: Path, log_dir: Path, label: str) -> str:
        result = execute([self.paths["llvm_dis"], str(bitcode), "-o", "-"], log_dir, label)
        if not result.ok:
            raise RuntimeError(f"IR disassembly failed: {result.stderr_excerpt or result.returncode}")
        full_text = Path(result.stdout_path).read_text(encoding="utf-8", errors="replace")
        return "\n".join(line for line in full_text.splitlines()
                         if not line.startswith(("; ModuleID =", "source_filename =")))

    def validate_actions(self, source: Path, scratch: Path) -> dict:
        scratch.mkdir(parents=True, exist_ok=True)
        seed = scratch / "smoke.bc"
        seed_result = self.emit_seed(source, seed, scratch)
        if not seed_result.ok:
            raise RuntimeError(f"Smoke seed failed: {seed_result.stderr_excerpt}")
        available: set[str] = set()
        reasons: dict[str, str] = {}
        for action in ACTIONS:
            result = self.optimize(seed, (action.id,), scratch / f"{action.id}.bc", scratch, f"smoke-{action.id}")
            if result.ok:
                available.add(action.id)
            else:
                reasons[action.id] = result.stderr_excerpt.strip()[:500] or "pass rejected"
        # Explicit adaptors make mixed action types composable; verify the claim.
        for left, right in itertools.permutations(sorted(available), 2):
            result = self.optimize(seed, (left, right), scratch / "pair.bc", scratch, f"pair-{left}-{right}")
            if not result.ok:
                reasons[right] = f"ordered pair {left},{right} failed: {result.stderr_excerpt.strip()[:250]}"
                available.discard(right)
                break
        result = snapshot(available, reasons)
        result["llvm_version"] = self.environment()["versions"]["opt"]
        if len(available) < 4:
            raise RuntimeError(f"INSUFFICIENT_ACTIONS: {len(available)} valid actions; {reasons}")
        # A parse check alone misses backend/link and behavioral failures.
        smoke_id = sorted(available)[0]
        smoke_bc = scratch / f"{smoke_id}.bc"
        smoke_binary = scratch / "smoke-executable"
        smoke_steps = self.object_and_link(smoke_bc, smoke_binary, scratch, "smoke-executable")
        if not all(step.ok for step in smoke_steps):
            raise RuntimeError("Smoke action passed opt but failed object/link generation")
        workload = Workload(7, 11, 2)
        smoke_run = self.run_binary(smoke_binary, workload, scratch, "smoke-run")
        from .oracle import expected_small
        if not smoke_run.ok or smoke_run.stdout != expected_small("dot", workload):
            raise RuntimeError("Smoke action executable failed independent checksum validation")
        result["smoke"] = {"action_id": smoke_id, "checksum": smoke_run.stdout.strip(), "binary_sha256": sha256_file(smoke_binary)}
        return result


def command_manifest(path: Path, results: list[CommandResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([result.as_dict() for result in results], indent=2), encoding="utf-8")
