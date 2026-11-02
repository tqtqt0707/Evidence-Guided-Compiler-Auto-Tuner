"""Counterexample search, compiler migration and conservative measured dispatch.

These are experimental methods, not claims of research priority or universal safety.
Run ``python -m autotuner.deployment --help`` for the reproducible local workflow.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import itertools
import json
import math
from pathlib import Path
import random
import shutil
import statistics
import time
from datetime import datetime, timezone
from uuid import uuid4

from . import config
from .actions import BY_ID, compose
from .features import summarize_ir
from .models import Workload
from .oracle import FNV_OFFSET, hash_u64, next_u64, expected_small
from .policies import GuidedPolicy
from .registry import benchmark
from .research import ResearchRunner, atomic_json
from .research_analysis import compare_samples, parse_remarks
from .toolchain import Toolchain, execute


@dataclass(frozen=True)
class DeploymentWorkload(Workload):
    distribution: str = "native"

    def args(self):
        args = super().args()
        return args if self.distribution == "native" else [*args, "--distribution", self.distribution]


def validate_request(data):
    if not isinstance(data, dict):
        raise ValueError("Deployment request must be an object")
    defaults = dict(benchmark_id="histogram", actions=["simplifycfg", "loop-vectorize"],
                    size=100000, seed=29, repeat=8, rounds=10, search_budget=12,
                    min_size=1000, max_size=1000000, envelope_budget=18,
                    compiler_versions=[21], distributions=None)
    if set(data) - set(defaults):
        raise ValueError("Unknown deployment request fields")
    req = {**defaults, **data}
    if not isinstance(req["benchmark_id"], str):
        raise ValueError("benchmark_id must be a string")
    entry = benchmark(req["benchmark_id"])
    for key, low, high in [("size", 1, entry["size_max"]), ("seed", 0, 2**53-1),
                           ("repeat", 1, 100), ("rounds", 3, 15), ("search_budget", 4, 40),
                           ("min_size", 1, entry["size_max"]), ("max_size", 1, entry["size_max"]),
                           ("envelope_budget", 6, 36)]:
        if type(req[key]) is not int or not low <= req[key] <= high:
            raise ValueError(f"{key} must be an integer from {low} to {high}")
    if not req["min_size"] <= req["size"] <= req["max_size"]:
        raise ValueError("Require min_size <= size <= max_size")
    actions = req["actions"]
    if not isinstance(actions, list) or any(not isinstance(x, str) for x in actions):
        raise ValueError("actions must be a list of action IDs")
    compose(actions)
    versions = req["compiler_versions"]
    if (not isinstance(versions, list) or not 1 <= len(versions) <= 2
            or any(type(v) is not int or not 18 <= v <= 30 for v in versions)
            or len(set(versions)) != len(versions)):
        raise ValueError("Choose one or two distinct LLVM major versions from 18 to 30")
    allowed = ["uniform", "skewed", "alternating"] if req["benchmark_id"] in {"histogram", "skew_histogram"} else ["native"]
    if req["distributions"] is None:
        req["distributions"] = allowed
    ds = req["distributions"]
    if (not isinstance(ds, list) or not ds or any(not isinstance(x, str) or x not in allowed for x in ds)
            or len(set(ds)) != len(ds)):
        raise ValueError("Unsupported or duplicate workload distributions")
    return req


def available_compilers():
    import os
    if os.name == "nt":
        return []
    return [v for v in range(18, 31) if all(shutil.which(f"{name}-{v}")
            for name in ("clang", "opt", "llc", "llvm-dis"))]


def histogram_oracle(w):
    values, state = [], w.seed
    for i in range(w.size):
        state, raw = next_u64(state)
        raw &= 0xffffffff
        if w.distribution == "skewed":
            raw = raw | 1 if raw & 7 else raw & ~1
        elif w.distribution == "alternating":
            raw = (raw & ~1) | (i & 1)
        values.append(raw)
    digest = FNV_OFFSET
    for r in range(w.repeat):
        values[r % w.size] ^= r + 1
        bins = [0] * 256
        for value in values:
            key = (value >> 8) & 255 if value & 1 else (value >> 16) & 255 if value & 2 else value & 255
            bins[key] += 1
        for count in bins:
            digest = hash_u64(digest, count)
    return f"checksum:{digest:016x}\n"


def context_key(environment):
    return config.stable_hash({k: environment[k] for k in
        ("versions", "target_triple", "cpu_model", "clang_flags", "llc_flags", "link_flags")})


def evidence_delta(diagnostics):
    """Rank edits by observed cross-version remark changes, not invented reasons."""
    counts = []
    for diag in diagnostics:
        values = {}
        for remark in diag.get("remarks", []):
            key = (remark["pass"], remark["kind"], remark["name"])
            values[key] = values.get(key, 0) + 1
        counts.append(values)
    if len(counts) < 2:
        return {}
    return {p: sum(abs(counts[0].get(k, 0) - counts[1].get(k, 0))
                   for k in counts[0].keys() | counts[1].keys() if k[0] == p)
            for p in {k[0] for c in counts for k in c}}


def repair_sequences(actions, deltas):
    # Full-sequence removals first; ranking changes when actual compiler evidence differs.
    ranked = sorted(range(len(actions)), key=lambda i: (-deltas.get(actions[i], 0), i))
    result = []
    for i in ranked:
        if len(actions) > 1:
            result.append(tuple(actions[:i] + actions[i+1:]))
        for replacement in BY_ID:
            if replacement not in actions:
                result.append(tuple(actions[:i] + [replacement] + actions[i+1:]))
    result.extend(itertools.permutations(actions))
    return list(dict.fromkeys(result))


def worst_ratio(records, candidate, workloads, versions):
    rows = [r for r in records if r["candidate"] == candidate and r["workload"] in workloads]
    indexed = {(config.stable_hash(r["workload"]), r["compiler"]): r for r in rows}
    needed = [(config.stable_hash(w), v) for w in workloads for v in versions]
    if any(k not in indexed or indexed[k]["status"] != "valid"
           or not isinstance(indexed[k].get("ratio"), (int, float))
           or not math.isfinite(indexed[k]["ratio"]) or indexed[k]["ratio"] <= 0 for k in needed):
        return math.inf
    return max(indexed[k]["ratio"] for k in needed)


def cell_decision(points, low, high, distribution, rounds):
    required_sizes = {low, (low+high)//2, high}
    complete = all(len({p["workload"]["seed"] for p in points
        if p["workload"]["size"] == size and p["status"] == "valid"}) >= 2 for size in required_sizes)
    if complete and rounds >= 10 and all(p.get("comparison", {}).get("label") == "win" for p in points):
        decision = "optimized"
    elif any(p.get("comparison", {}).get("label") == "loss" for p in points):
        decision = "baseline"
    else:
        decision = "insufficient_evidence"
    return dict(min_size=low, max_size=high, distribution=distribution, decision=decision,
                measured_points=points, interpretation="Empirical interpolation; no universal performance guarantee")


def choose_dispatch(envelope, workload):
    if workload.repeat != envelope["repeat"]:
        return "baseline", "Repeat count outside measured context"
    matches = [c for c in envelope["cells"] if c["distribution"] == workload.distribution
               and c["min_size"] <= workload.size <= c["max_size"]]
    if matches and all(c["decision"] == "optimized" for c in matches):
        return "optimized", "Inside a calibrated empirical region"
    return "baseline", "Outside a calibrated winning region or insufficient evidence"


class DeploymentCompiler(ResearchRunner):
    def __init__(self, req, directory, major, progress, deadline):
        # Use the existing build/paired measurement implementation without running the eight-view suite.
        self.req, self.directory = req, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.progress, self.deadline = progress, deadline
        self.rng, self.built, self.commands = random.Random(req["seed"]), {}, []
        self.tc = Toolchain(major)
        self.environment = self.tc.environment()

    def prepare(self):
        self.stage("prepare-compiler-" + str(self.tc.major))
        self.source = self.directory / "source.c"
        source = (config.BENCHMARKS / "deployment_histogram.c" if self.req["benchmark_id"] in
                  {"histogram", "skew_histogram"} else Path(benchmark(self.req["benchmark_id"])["source_path"]))
        shutil.copy2(source, self.source)
        self.o0, self.o3, self.seed = (self.directory / p for p in ("o0", "o3", "initial.bc"))
        self.compile_source(self.source, self.o0)
        self.compile_source(self.source, self.o3, "-O3")
        result = self.checked(execute([self.tc.paths["clang"], *self.tc.clang_flags, "-O1",
            "-gline-tables-only", "-emit-llvm", "-c", str(self.source), "-o", str(self.seed)], self.directory, "seed"))
        self.seed_ns = result.duration_ns
        self.initial_ir = self.tc.textual_ir(self.seed, self.directory, "initial")
        (self.directory / "initial.ll").write_text(self.initial_ir, encoding="utf-8")
        for distribution in self.req["distributions"]:
            for size in (1, 7):
                w = DeploymentWorkload(size, 11, 2, distribution)
                oracle = self.oracle(w)
                if any(self.run(b, w, f"control-{distribution}-{size}-{b.name}")[0] != oracle for b in (self.o0, self.o3)):
                    raise RuntimeError("Compiler controls disagree with independent Python oracle")

    def oracle(self, w):
        return histogram_oracle(w) if w.distribution != "native" else expected_small(self.req["benchmark_id"], w)

    def small_fixtures(self, row, cases):
        for i, w in enumerate(cases):
            if self.run(self.binary(row), w, f"fixture-{row['id']}-{i}")[0] != self.oracle(w):
                return {"status": "failed", "workload": asdict(w)}
        return {"status": "passed", "cases": len(cases)}

    def expected(self, w, label):
        return self.run(self.o0, w, label + "-reference")[0]

    def text_bytes(self, binary):
        # Code size is optional here; absence never invalidates timing evidence.
        return None

    def diagnostic(self, sequence):
        row = self.build(sequence)
        folder = self.directory / row["id"]
        remarks = folder / "remarks.yaml"
        try:
            items = parse_remarks(remarks.read_text(encoding="utf-8")) if remarks.exists() else []
        except ValueError:
            items = []
        return dict(compiler=self.tc.major, status=row["status"], remarks=items,
                    instruction_count=row.get("ir_instructions"), error=row.get("error"))

    def measure(self, sequence, workload, rounds, label, candidate="candidate"):
        result = dict(candidate=candidate, compiler=self.tc.major, workload=asdict(workload), actions=list(sequence), status="failed")
        row = self.build(sequence)
        if row["status"] not in {"built", "valid"}:
            return {**result, "error": row.get("error", "Build failed")}
        fixtures = [DeploymentWorkload(n, 11, 2, d) for n in (1, 7) for d in self.req["distributions"]]
        try:
            if self.small_fixtures(row, fixtures)["status"] != "passed":
                return {**result, "error": "Independent Python oracle mismatch"}
            samples = self.paired({"candidate": self.binary(row), "o3": self.o3}, workload, rounds, label)
            comparison = compare_samples(samples["candidate"], samples["o3"])
            return {**result, "status": "valid", "samples_ns": samples, "comparison": comparison,
                    "ratio": statistics.median(samples["candidate"]) / statistics.median(samples["o3"])}
        except (RuntimeError, OSError) as exc:
            return {**result, "error": str(exc)}


class DeploymentRunner:
    def __init__(self, request, directory, progress=None):
        self.req = validate_request(request)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.progress = progress or (lambda stage: None)
        self.deadline = time.monotonic() + 900
        self.rng = random.Random(self.req["seed"])

    def execute(self):
        req = self.req
        compilers = [DeploymentCompiler(req, self.directory / f"llvm-{v}", v, self.progress, self.deadline)
                     for v in req["compiler_versions"]]
        for compiler in compilers:
            compiler.prepare()
        # The last requested version is the proposed deployment target.
        primary = compilers[-1]
        diagnostics = [c.diagnostic(req["actions"]) for c in compilers]
        delta = evidence_delta(diagnostics)
        repairs = repair_sequences(req["actions"], delta)
        policy = GuidedPolicy(tuple(BY_ID), 4, req["seed"], summarize_ir(primary.initial_ir))
        # Entire seed namespaces are disjoint, including small-fixture seed 11.
        training_seed = req["seed"]
        reserved = {training_seed, 11}
        def fresh_seed():
            value = 1009
            while value in reserved:
                value += 1
            reserved.add(value)
            return value
        calibration_seeds = [fresh_seed(), fresh_seed()]
        holdout_seeds = [fresh_seed(), fresh_seed()]
        workloads = [asdict(DeploymentWorkload(req["size"], training_seed, req["repeat"], req["distributions"][0]))]
        sequences, records, events, seen = [tuple(req["actions"])], [], [], {tuple(req["actions"])}
        spent = 0

        def evaluate(sequence, workload, label, rounds=None, candidate=None):
            rounds = req["rounds"] if rounds is None else rounds
            return [c.measure(sequence, DeploymentWorkload(**workload), rounds, label,
                              candidate or "c" + str(sequences.index(sequence))) for c in compilers]

        records.extend(evaluate(sequences[0], workloads[0], "search-0"))
        spent += 1
        while spent < req["search_budget"]:
            scores = [worst_ratio(records, f"c{i}", workloads, req["compiler_versions"]) for i in range(len(sequences))]
            best = min(range(len(scores)), key=lambda i: scores[i])
            remaining = req["search_budget"] - spent
            # Alternate failure discovery and repair; a confirmed-looking search regression
            # shifts the next affordable operation toward candidate repair.
            challenge = len(workloads) < 8 and len(sequences) <= remaining and (
                len(workloads) == 1 or (scores[best] <= 1 and len(events) % 2 == 0))
            if challenge:
                best_rows = [r for r in records if r["candidate"] == f"c{best}" and r["status"] == "valid"]
                anchor = max(best_rows, key=lambda r: r["ratio"])["workload"] if best_rows else workloads[-1]
                new = None
                for attempt in range(200):
                    size = self.rng.choice([req["min_size"], req["max_size"], max(req["min_size"], anchor["size"]//2),
                        min(req["max_size"], anchor["size"]*2), self.rng.randint(req["min_size"], req["max_size"])])
                    proposal = asdict(DeploymentWorkload(size, training_seed, req["repeat"], self.rng.choice(req["distributions"])))
                    if proposal not in workloads:
                        new = proposal
                        break
                if new is not None:
                    for i, sequence in enumerate(sequences):
                        records.extend(evaluate(sequence, new, f"search-{spent}"))
                        spent += 1
                    workloads.append(new)
                    events.append(dict(operation="challenge_workload", workload=new, spent=spent))
                    continue
            if len(workloads) > remaining or len(sequences) >= 8:
                break
            sequence = next((s for s in repairs if s not in seen), None)
            if sequence is None:
                proposal = policy.propose(seen, [])
                sequence = proposal.action_ids if proposal else None
            if sequence is None:
                break
            seen.add(sequence)
            sequences.append(sequence)
            for workload in workloads:
                records.extend(evaluate(sequence, workload, f"search-{spent}"))
                spent += 1
            events.append(dict(operation="repair_sequence", actions=list(sequence), spent=spent,
                               evidence="Cross-version remark deltas" if delta else "Local sequence edits; no cross-version remark delta"))

        scores = [worst_ratio(records, f"c{i}", workloads, req["compiler_versions"]) for i in range(len(sequences))]
        best = min(range(len(scores)), key=lambda i: scores[i])
        selected = sequences[best] if math.isfinite(scores[best]) else None
        # Search-derived failure hypotheses are confirmed with fresh, longer measurements.
        counterexamples = []
        suspects = list(dict.fromkeys((r["candidate"], config.stable_hash(r["workload"])) for r in records
                                     if r.get("ratio", 0) > 1.02))[:8]
        for j, (candidate, workload_hash) in enumerate(suspects):
            workload = next(w for w in workloads if config.stable_hash(w) == workload_hash)
            sequence = sequences[int(candidate[1:])]
            counterexamples.extend(evaluate(sequence, workload, f"counterexample-{j}", req["rounds"], candidate))

        self.progress("calibrate-deployment-envelope")
        cells, envelope_spent = [], 0
        queue = [(req["min_size"], req["max_size"], d) for d in req["distributions"]]
        while queue and selected is not None:
            low, high, distribution = queue.pop(0)
            points = []
            for size, seed in itertools.product(sorted({low, (low+high)//2, high}), calibration_seeds):
                if envelope_spent >= req["envelope_budget"]:
                    break
                w = DeploymentWorkload(size, seed, req["repeat"], distribution)
                points.append(primary.measure(selected, w, req["rounds"], f"envelope-{envelope_spent}"))
                envelope_spent += 1
            cell = cell_decision(points, low, high, distribution, req["rounds"])
            # Refine observed mixed boundaries; children require their own complete evidence.
            labels = {p.get("comparison", {}).get("label") for p in points}
            if "win" in labels and "loss" in labels and high-low > 1 and envelope_spent < req["envelope_budget"]:
                mid = (low+high)//2
                queue.extend([(low, mid, distribution), (mid+1, high, distribution)])
            else:
                cells.append(cell)
            if envelope_spent >= req["envelope_budget"]:
                # Unmeasured regions remain explicit fallback cells.
                cells.extend(cell_decision([], lo, hi, d, req["rounds"]) for lo, hi, d in queue)
                queue.clear()
        if not cells:
            cells = [cell_decision([], req["min_size"], req["max_size"], d, req["rounds"]) for d in req["distributions"]]
        envelope = dict(cells=cells, repeat=req["repeat"], compiler=req["compiler_versions"][-1],
                        calibration_seeds=calibration_seeds, selection="Frozen before final holdout",
                        guarantee="Empirical only. Unmeasured conditions use direct -O3.")

        self.progress("reserved-holdout-and-dispatch")
        holdout = []
        # Freeze all choices above. Final holdout is never used to repair or train the policy.
        for i, (distribution, seed) in enumerate(itertools.product(req["distributions"], holdout_seeds)):
            size = self.rng.randint(req["min_size"], req["max_size"])
            w = DeploymentWorkload(size, seed, req["repeat"], distribution)
            observations = evaluate(selected, asdict(w), f"holdout-{i}", req["rounds"]) if selected else []
            holdout.append(dict(workload=asdict(w), comparisons=observations, decision=choose_dispatch(envelope, w)[0]))
        baseline = primary.o3
        optimized = primary.binary(primary.build(selected)) if selected else None
        artifacts = dict(baseline=dict(path=str(baseline.relative_to(self.directory)), sha256=config.sha256_file(baseline)),
                         optimized=dict(path=str(optimized.relative_to(self.directory)), sha256=config.sha256_file(optimized)) if optimized else None)
        environment = primary.environment
        result = dict(schema_version=1, kind="deployment", status="completed", id=self.directory.name,
            created_at=datetime.now(timezone.utc).isoformat(),
            request=req, source=primary.source.read_text(encoding="utf-8"),
            provenance=dict(environment=environment, context_hash=context_key(environment),
                source_sha256=config.sha256_file(primary.source), measurement="Whole-process paired wall time, including generation/checksum; separate warmups"),
            migration=dict(status="compared" if len(compilers)==2 else "requires_second_compiler", diagnostics=diagnostics,
                           environments=[c.environment for c in compilers],
                           remark_delta=delta, versions=req["compiler_versions"],
                           candidate_scores=[dict(candidate=f"c{i}", actions=list(s), worst_ratio=scores[i] if math.isfinite(scores[i]) else None)
                                             for i, s in enumerate(sequences)]),
            search=dict(budget=req["search_budget"], spent=spent, unused=req["search_budget"]-spent,
                unit="One candidate/workload comparison across all requested compilers at the requested paired-round count",
                events=events, workloads=workloads, records=records, selected_actions=list(selected) if selected else None,
                baseline_preferred=not math.isfinite(scores[best]) or scores[best]>=1),
            counterexamples=counterexamples, envelope=envelope, holdout=holdout, artifacts=artifacts,
            budgets=dict(envelope_spent=envelope_spent, envelope_limit=req["envelope_budget"],
                         final_holdout_cases=len(holdout), counterexample_comparisons=len(counterexamples)),
            limits=["Experimental methods; research novelty and performance superiority are unestablished",
                    "No universal correctness or performance guarantee; independent small-input checks and per-execution checksums only",
                    "Single-host measurements; short runs are demo_only and cannot authorize optimized dispatch",
                    "Search, calibration and final holdout seed namespaces are disjoint; no final-holdout feedback"],
            commands=[dict(compiler=c.tc.major, commands=c.commands) for c in compilers])
        atomic_json(self.directory / "dispatch-policy.json", result)
        # Real launcher includes Python startup, policy lookup, checks and exec overhead.
        # Measure it against the same baseline; do not mistake raw variant timings for dispatch performance.
        report_path = self.directory / "report.json"
        import sys
        launcher = self.directory / "dispatch"
        launcher.write_text("#!" + sys.executable + "\nfrom autotuner.deployment import dispatch\n"
                            + "from pathlib import Path\nimport sys\n"
                            + "dispatch(Path(__file__).parent / 'dispatch-policy.json', sys.argv[1:])\n", encoding="utf-8")
        launcher.chmod(0o755)
        dispatch_samples = []
        for i, case in enumerate(holdout):
            w = DeploymentWorkload(**case["workload"])
            samples = primary.paired({"dispatcher": launcher, "o3": primary.o3}, w, req["rounds"], f"dispatch-{i}")
            dispatch_samples.append(dict(workload=case["workload"], samples_ns=samples,
                comparison=compare_samples(samples["dispatcher"], samples["o3"])))
        result["dispatch_confirmation"] = dispatch_samples
        result["budgets"]["dispatch_confirmation_cases"] = len(dispatch_samples)
        result["commands"] = [dict(compiler=c.tc.major, commands=c.commands) for c in compilers]
        atomic_json(report_path, result)
        self.progress("completed")
        return result


def dispatch(report_path, argv, replace=True):
    """Execute only local, hash-matching artifacts under the recorded compiler/CPU context."""
    import os
    parser = argparse.ArgumentParser(description="Dispatch within a measured empirical envelope")
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--distribution", default="native")
    args = parser.parse_args(argv)
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("kind") != "deployment" or report.get("status") != "completed":
        raise ValueError("Not a completed deployment report")
    req = validate_request(report["request"])
    Workload.parse(dict(size=args.size, seed=args.seed, repeat=args.repeat), benchmark(req["benchmark_id"]))
    if args.distribution not in req["distributions"]:
        raise ValueError("Distribution unsupported by this report")
    w = DeploymentWorkload(args.size, args.seed, args.repeat, args.distribution)
    current = Toolchain(req["compiler_versions"][-1]).environment()
    if context_key(current) != report["provenance"]["context_hash"]:
        raise ValueError("Compiler or CPU context changed; recalibrate before dispatch")
    decision, reason = choose_dispatch(report["envelope"], w)
    if not report["artifacts"].get("optimized"):
        decision = "baseline"
    artifact = report["artifacts"][decision]
    binary = (report_path.parent / artifact["path"]).resolve()
    if not binary.is_relative_to(report_path.parent) or not binary.is_file() or config.sha256_file(binary) != artifact["sha256"]:
        if decision == "optimized":
            artifact = report["artifacts"]["baseline"]
            binary = (report_path.parent / artifact["path"]).resolve()
            decision, reason = "baseline", "Optimized artifact unavailable or changed"
        if not binary.is_relative_to(report_path.parent) or not binary.is_file() or config.sha256_file(binary) != artifact["sha256"]:
            raise ValueError("Baseline artifact unavailable or changed; rerun experiment")
    if replace:
        os.execv(str(binary), [str(binary), *w.args()])
    return dict(decision=decision, reason=reason, binary=str(binary))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    tune = sub.add_parser("tune")
    tune.add_argument("--request", type=Path, help="JSON request; omit for defaults")
    tune.add_argument("--output-dir", type=Path, required=True)
    choose = sub.add_parser("dispatch")
    choose.add_argument("report", type=Path)
    args, rest = parser.parse_known_args()
    if args.command == "dispatch":
        dispatch(args.report, rest)
    else:
        if rest:
            parser.error("Unrecognized tuning arguments")
        request = json.loads(args.request.read_text()) if args.request else {}
        directory = args.output_dir / ("deployment-" + str(uuid4()))
        DeploymentRunner(request, directory, lambda stage: print(stage, flush=True)).execute()
        print(directory / "report.json")


if __name__ == "__main__":
    main()
