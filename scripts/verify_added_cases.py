"""Reproduce hand-selected pass-vs-Clang-O3 comparisons for the new cases.

These pass choices are hypotheses, not outputs of the guided search. Run under
the same LLVM 21 Linux environment as the main tuner. Raw samples are saved.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from dataclasses import asdict
from pathlib import Path

from autotuner.actions import compose
from autotuner.config import sha256_file
from autotuner.models import Workload
from autotuner.oracle import expected_small
from autotuner.registry import benchmark
from autotuner.toolchain import Toolchain


CASES = {
    "pipeline": ("loop-fusion",),
    "skew_histogram": ("simplifycfg", "licm", "dce"),
}


def require_ok(result, label: str) -> None:
    if not result.ok:
        raise RuntimeError(f"{label}: {result.stderr_excerpt or result.returncode}")


def run_case(tc: Toolchain, name: str, actions: tuple[str, ...], root: Path, rounds: int) -> dict:
    entry = benchmark(name)
    source = Path(entry["source_path"])
    folder = root / name
    folder.mkdir(parents=True)
    o0, o3, empty, candidate = (folder / item for item in ("o0", "o3", "empty", "candidate"))
    seed, empty_bc, tuned_bc = (folder / item for item in ("seed.bc", "empty.bc", "candidate.bc"))
    require_ok(tc.compile_direct(source, "-O0", o0, folder, "compile-o0"), "O0")
    require_ok(tc.compile_direct(source, "-O3", o3, folder, "compile-o3"), "O3")
    require_ok(tc.emit_seed(source, seed, folder), "seed")
    empty_bc.write_bytes(seed.read_bytes())
    for step in tc.object_and_link(empty_bc, empty, folder, "empty"):
        require_ok(step, "empty backend")
    require_ok(tc.optimize(seed, actions, tuned_bc, folder, "opt-candidate"), "candidate opt")
    for step in tc.object_and_link(tuned_bc, candidate, folder, "candidate"):
        require_ok(step, "candidate backend")

    for index, case in enumerate(entry["small_cases"]):
        workload = Workload(**case)
        expected = expected_small(name, workload)
        for label, binary in (("o0", o0), ("o3", o3), ("empty", empty), ("candidate", candidate)):
            run = tc.run_binary(binary, workload, folder, f"oracle-{index}-{label}")
            require_ok(run, f"{name} small {label}")
            if run.stdout != expected:
                raise RuntimeError(f"{name} small {label}: checksum mismatch")

    measurements = {}
    for phase in ("tuning", "holdout"):
        workload = Workload(**entry[f"{phase}_input"])
        reference = tc.run_binary(o3, workload, folder, f"{phase}-reference")
        require_ok(reference, f"{name} {phase} O3")
        for label, binary in (("empty", empty), ("candidate", candidate)):
            check = tc.run_binary(binary, workload, folder, f"{phase}-{label}-check")
            require_ok(check, f"{name} {phase} {label}")
            if check.stdout != reference.stdout:
                raise RuntimeError(f"{name} {phase} {label}: checksum mismatch")
        samples = {"o3": [], "candidate": []}
        pairs = []
        for index in range(rounds):
            order = ("o3", "candidate") if index % 2 == 0 else ("candidate", "o3")
            pair = {}
            for label in order:
                binary = o3 if label == "o3" else candidate
                run = tc.run_binary(binary, workload, folder, f"{phase}-round-{index}-{label}")
                require_ok(run, f"{name} {phase} round {index} {label}")
                if run.stdout != reference.stdout:
                    raise RuntimeError(f"{name} {phase} round {index} {label}: checksum mismatch")
                pair[label] = run.duration_ns
                samples[label].append(run.duration_ns)
            pairs.append(pair)
        median_o3 = statistics.median(samples["o3"])
        median_candidate = statistics.median(samples["candidate"])
        measurements[phase] = {
            "workload": asdict(workload),
            "checksum": reference.stdout.strip(),
            "pairs_ns": pairs,
            "median_o3_ns": median_o3,
            "median_candidate_ns": median_candidate,
            "candidate_faster_pairs": sum(pair["candidate"] < pair["o3"] for pair in pairs),
            "speedup_percent": 100 * (median_o3 - median_candidate) / median_o3,
        }
    return {
        "actions": actions,
        "pipeline": compose(actions),
        "source_sha256": sha256_file(source),
        "binary_sha256": {"o3": sha256_file(o3), "candidate": sha256_file(candidate)},
        "measurements": measurements,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 3 <= args.rounds <= 30:
        parser.error("--rounds must be between 3 and 30")
    tc = Toolchain()
    with tempfile.TemporaryDirectory(prefix="tuner-new-cases-") as temporary:
        root = Path(temporary)
        result = {
            "method": "Hand-selected actions; alternating execution order; whole-process wall time; exact checksum gates",
            "environment": tc.environment(),
            "cases": {name: run_case(tc, name, actions, root, args.rounds) for name, actions in CASES.items()},
        }
        output = args.output or Path("test-results/added-cases.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "results": {
            name: {phase: {key: value for key, value in measurement.items()
                           if key in ("speedup_percent", "candidate_faster_pairs")}
                   for phase, measurement in case["measurements"].items()}
            for name, case in result["cases"].items()}, "rounds": args.rounds}, indent=2))


if __name__ == "__main__":
    main()
