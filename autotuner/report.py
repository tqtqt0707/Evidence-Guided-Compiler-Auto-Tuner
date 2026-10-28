from __future__ import annotations

import csv
import json
import statistics
import math
from pathlib import Path

from .config import RUNS_DIR, sha256_file
from .storage import Store


def median_mad(values: list[int]) -> tuple[int, int]:
    median = int(statistics.median(values))
    mad = int(statistics.median(abs(value - median) for value in values))
    return median, mad


def outcome(comparator: list[dict], candidate: list[dict], mode: str) -> dict:
    comparator = [row for row in comparator if row.get("valid", True) and row.get("round") is not None]
    candidate = [row for row in candidate if row.get("valid", True) and row.get("round") is not None]
    if not comparator or not candidate:
        return {"label": "unavailable", "reason": "Missing confirmation samples"}
    by_round_a = {row["round"]: row["duration_ns"] for row in comparator}
    by_round_b = {row["round"]: row["duration_ns"] for row in candidate}
    pairs = [(round_number, by_round_a[round_number], by_round_b[round_number]) for round_number in sorted(set(by_round_a) & set(by_round_b))]
    if not pairs:
        return {"label": "unavailable", "reason": "No matched confirmation rounds"}
    median_a, mad_a = median_mad([a for _, a, _ in pairs])
    median_b, mad_b = median_mad([b for _, _, b in pairs])
    wins = sum(b < a for _, a, b in pairs)
    losses = sum(b > a for _, a, b in pairs)
    reduction = 100 * (median_a - median_b) / median_a if median_a else 0
    label = "demo_only" if mode == "quick" else "inconclusive"
    if mode == "full" and len(pairs) >= 10:
        required = math.ceil(0.8 * len(pairs))
        if reduction >= 2 and wins >= required:
            label = "win"
        elif reduction <= -2 and losses >= required:
            label = "loss"
    return {"label": label, "comparator_median_ns": median_a, "candidate_median_ns": median_b,
            "comparator_mad_ns": mad_a, "candidate_mad_ns": mad_b,
            "speedup": median_a / median_b if median_b else None,
            "runtime_reduction_percent": reduction, "paired_wins": wins,
            "paired_losses": losses, "paired_rounds": len(pairs),
            "paired_differences_ns": [{"round": r, "comparator_minus_candidate_ns": a - b} for r, a, b in pairs]}


def _line_svg(trials: list[dict]) -> str:
    width, height, margin = 720, 300, 45
    colors = {"guided": "#19c6a7", "random": "#ee9b55"}
    valid = [trial for trial in trials if trial["status"] == "valid" and trial["search_median_ns"]]
    maximum = max((trial["search_median_ns"] for trial in valid), default=1)
    minimum = min((trial["search_median_ns"] for trial in valid), default=0)
    spread = max(1, maximum - minimum)
    max_ordinal = max((trial["ordinal"] for trial in trials), default=1)
    paths = []
    for strategy in colors:
        best = None
        points = []
        for trial in sorted((row for row in trials if row["strategy"] == strategy), key=lambda row: row["ordinal"]):
            if trial["status"] == "valid" and trial["search_median_ns"]:
                best = min(best, trial["search_median_ns"]) if best is not None else trial["search_median_ns"]
            if best is not None:
                x = margin + (trial["ordinal"] - 1) * (width - 2 * margin) / max(1, max_ordinal - 1)
                y = height - margin - (best - minimum) * (height - 2 * margin) / spread
                points.append(f"{x:.1f},{y:.1f}")
        if points:
            paths.append(f'<polyline fill="none" stroke="{colors[strategy]}" stroke-width="3" points="{" ".join(points)}"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="#10202b"/><path d="M{margin} {margin}V{height-margin}H{width-margin}" stroke="#8da4ad" fill="none"/><text x="{margin}" y="25" fill="#e7f5f5" font-family="sans-serif" font-size="16">Best search runtime by proposal (lower is better)</text>{"".join(paths)}<text x="50" y="285" fill="#19c6a7" font-family="sans-serif">Guided</text><text x="145" y="285" fill="#ee9b55" font-family="sans-serif">Random</text></svg>'


def _pareto_svg(trials: list[dict]) -> str:
    width, height, margin = 720, 300, 45
    valid = [trial for trial in trials if trial["status"] == "valid" and trial["search_median_ns"] and trial["executable_bytes"]]
    max_size = max((trial["executable_bytes"] for trial in valid), default=1)
    min_size = min((trial["executable_bytes"] for trial in valid), default=0)
    max_time = max((trial["search_median_ns"] for trial in valid), default=1)
    min_time = min((trial["search_median_ns"] for trial in valid), default=0)
    circles = []
    for trial in valid:
        x = margin + (trial["executable_bytes"] - min_size) * (width - 2 * margin) / max(1, max_size - min_size)
        y = height - margin - (trial["search_median_ns"] - min_time) * (height - 2 * margin) / max(1, max_time - min_time)
        color = "#19c6a7" if trial["strategy"] == "guided" else "#ee9b55"
        circles.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="#10202b"/><path d="M{margin} {margin}V{height-margin}H{width-margin}" stroke="#8da4ad" fill="none"/><text x="{margin}" y="25" fill="#e7f5f5" font-family="sans-serif" font-size="16">Executable size vs search runtime</text>{"".join(circles)}<text x="300" y="285" fill="#e7f5f5" font-family="sans-serif">Size →</text></svg>'


def build_report(store: Store, experiment_id: str, *, write_files: bool = True) -> dict:
    experiment = store.one("SELECT * FROM experiments WHERE id=?", (experiment_id,))
    if not experiment:
        raise KeyError(experiment_id)
    request = json.loads(experiment["request_json"])
    trials = store.all("SELECT * FROM trials WHERE experiment_id=? ORDER BY strategy,ordinal", (experiment_id,))
    variants = store.all("SELECT * FROM variants WHERE experiment_id=?", (experiment_id,))
    samples = store.all("SELECT * FROM samples WHERE experiment_id=? ORDER BY created_at,id", (experiment_id,))
    for trial in trials:
        trial["action_ids"] = json.loads(trial.pop("action_ids_json"))
        trial["evidence_trial_ids"] = json.loads(trial.pop("evidence_trial_ids_json"))
    variants_by_kind = {variant["kind"]: variant for variant in variants if variant["trial_id"] is None}
    selection_rows = store.all("SELECT strategy,trial_id FROM selections WHERE experiment_id=?", (experiment_id,))
    selected_ids = {row["strategy"]: row["trial_id"] for row in selection_rows}
    winners = {strategy: next((row for row in trials if row["id"] == selected_ids.get(strategy) and row["status"] == "valid"), None) for strategy in request["strategies"]}
    def binary_size(variant: dict) -> int | None:
        return variant.get("executable_bytes")
    sample_index: dict[tuple[str, str, str], list[dict]] = {}
    for row in samples:
        if row["valid"] and row["phase"].startswith("confirmation"):
            sample_index.setdefault((row["variant_id"], row["phase"], row["input_kind"]), []).append(row)
    comparisons = {}
    phases = [("tuning", "confirmation_tuning")]
    if request["mode"] == "full":
        phases.append(("holdout", "confirmation_holdout"))
    for input_kind, phase in phases:
        comparisons[input_kind] = {}
        o3 = variants_by_kind.get("o3")
        empty = variants_by_kind.get("empty")
        guided_trial = winners.get("guided")
        random_trial = winners.get("random")
        guided_variant = next((row for row in variants if guided_trial and row["trial_id"] == guided_trial["id"]), None)
        random_variant = next((row for row in variants if random_trial and row["trial_id"] == random_trial["id"]), None)
        def confirmed_stats(variant: dict | None) -> dict | None:
            rows = sample_index.get((variant["id"], phase, input_kind), []) if variant else []
            if not rows:
                return None
            median, mad = median_mad([row["duration_ns"] for row in rows])
            return {"median_ns": median, "mad_ns": mad, "samples_ns": [row["duration_ns"] for row in rows],
                    "executable_bytes": binary_size(variant)}
        comparisons[input_kind]["variants"] = {key: confirmed_stats(value) for key, value in
                                               (("o3", o3), ("empty", empty), ("guided", guided_variant), ("random", random_variant))}
        for name, left, right in (("guided_vs_o3", o3, guided_variant), ("guided_vs_empty", empty, guided_variant),
                                  ("random_vs_o3", o3, random_variant), ("random_vs_empty", empty, random_variant),
                                  ("guided_vs_random", random_variant, guided_variant)):
            a = sample_index.get((left["id"], phase, input_kind), []) if left else []
            b = sample_index.get((right["id"], phase, input_kind), []) if right else []
            result = outcome(a, b, request["mode"])
            if left and right:
                left_size, right_size = binary_size(left), binary_size(right)
                if left_size and right_size is not None:
                    delta = right_size - left_size
                    result["size_delta_bytes"] = delta
                    result["size_delta_percent"] = 100 * delta / left_size
            comparisons[input_kind][name] = result
    failures = {}
    for trial in trials:
        if trial["status"] != "valid":
            failures[trial["status"]] = failures.get(trial["status"], 0) + 1
    summary = {
        "schema_version": 1, "experiment_id": experiment_id, "status": "completed" if write_files and experiment["status"] == "running" else experiment["status"],
        "mode": request["mode"], "benchmark_id": request["benchmark_id"],
        "request": request, "environment": json.loads(experiment["environment_json"]),
        "catalog_hash": experiment["catalog_hash"], "source_sha256": experiment["source_sha256"],
        "baseline_variants": {key: {"id": value["id"], "executable_bytes": binary_size(value)} for key, value in variants_by_kind.items()},
        "winners": {key: ({"id": row["id"], "status": row["status"], "action_ids": row["action_ids"], "effective_pipeline": row["effective_pipeline"], "search_median_ns": row["search_median_ns"], "executable_bytes": row["executable_bytes"], "ir_instructions_before": row["ir_instructions_before"], "ir_instructions_after": row["ir_instructions_after"]} if row else None) for key, row in winners.items()},
        "comparisons": comparisons, "failure_counts": failures,
        "trial_count": len(trials), "sample_count": len(samples),
        "interpretation": "Exploratory single-machine observations; quick mode is demo-only and has no held-out claim.",
        "search_outcomes": {strategy: {"attempted": sum(row["strategy"] == strategy for row in trials),
                                      "budget": request["budget_per_strategy"],
                                      "exhausted": sum(row["strategy"] == strategy for row in trials) < request["budget_per_strategy"]}
                            for strategy in request["strategies"]},
        "artifacts": ["summary.json", "trials.csv", "samples.csv", "environment.json", "search.svg", "pareto.svg"],
    }
    summary["explanations"] = explain_results(summary)
    if write_files:
        run_dir = RUNS_DIR / experiment_id
        run_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "summary.json": (json.dumps(summary, indent=2).encode(), "application/json"),
            "environment.json": (json.dumps(summary["environment"], indent=2).encode(), "application/json"),
            "search.svg": (_line_svg(trials).encode(), "image/svg+xml"),
            "pareto.svg": (_pareto_svg(trials).encode(), "image/svg+xml"),
        }
        for name, rows in (("trials.csv", trials), ("samples.csv", samples)):
            import io
            buffer = io.StringIO()
            if rows:
                writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})
            files[name] = (buffer.getvalue().encode(), "text/csv")
        for name, (data, media_type) in files.items():
            target = run_dir / name
            temporary = run_dir / (name + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
            store.add_artifact(experiment_id, name, name, sha256_file(target), media_type)
    return summary


def explain_results(summary: dict) -> list[str]:
    """Describe measured outcomes without inferring runtime from IR counts."""
    if summary["mode"] == "quick":
        return [f"Demo only: {summary['request']['confirmation_rounds']} confirmation rounds on the tuning input. No held-out performance claim is available."]
    explanations = []
    for input_kind, comparisons in summary["comparisons"].items():
        for name, result in comparisons.items():
            if name == "variants":
                continue
            readable = name.replace("_vs_", " versus ").replace("o3", "Clang -O3").replace("empty", "empty-action control")
            if result["label"] == "unavailable":
                explanations.append(f"{input_kind}: {readable} is unavailable because a valid confirmed finalist is missing.")
            else:
                explanations.append(f"{input_kind}: {readable}: {result['label']}; runtime reduction {result['runtime_reduction_percent']:+.2f}%, faster in {result['paired_wins']}/{result['paired_rounds']} paired rounds.")
    tuning = summary["comparisons"].get("tuning", {})
    holdout = summary["comparisons"].get("holdout", {})
    for name, result in tuning.items():
        if name != "variants" and result.get("label") == "win" and holdout.get(name, {}).get("label") != "win":
            explanations.append(f"The tuning win for {name.replace('_vs_', ' versus ')} did not reproduce on holdout; it is workload-specific.")
    explanations.append("These are observations on this machine and these inputs. IR instruction changes do not establish a runtime improvement.")
    return explanations
