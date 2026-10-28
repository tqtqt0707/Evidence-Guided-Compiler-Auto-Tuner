"""Measure a fixed screening pool and publish an explicitly selected showcase.

Run serially on an idle measurement machine: python -m autotuner.showcase
No LLM calls are made. Selection never changes a measured report or a rerun.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT
from .examples import export_example
from .registry import benchmark
from .service import TrialFailure, TuningService


def screening_cases() -> list[dict]:
    cases = []
    for family, sizes in (("pipeline", (4000000, 8000000, 12000000, 14000000, 16000000, 6000000)),
                          ("skew_histogram", (4000000, 8000000, 12000000, 16000000))):
        for index, size in enumerate(sizes):
            seed = (17, 29, 43, 61, 79, 101)[index]
            holdout_size = size - 1000000 if size == 16000000 else size + 1000000
            cases.append({
                "id": f"{family.replace('_', '-')}-{size // 1000000}m",
                "title": f"{benchmark(family)['display_name']} · {size // 1000000}M elements",
                "purpose": ("Producer/consumer loops: test memory-traffic savings from loop fusion."
                            if family == "pipeline" else "Biased branch distribution with scattered bin updates: test control-flow passes."),
                "request": {"benchmark_id": family, "mode": "full", "llm_mode": "off",
                            "tuning_input": {"size": size, "seed": seed, "repeat": 12},
                            "holdout_input": {"size": holdout_size, "seed": seed + 1000, "repeat": 12},
                            "budget_per_strategy": 20, "max_sequence_length": 4,
                            "search_repetitions": 5, "confirmation_rounds": 10},
            })
    # Follow-up pool informed by the first screening: longer pipeline workloads
    # and seeds whose searches previously found fusion. These remain workload
    # variants of the same source, not new independent benchmark programs.
    for size, seed in ((8000000, 79), (10000000, 101), (12000000, 79), (16000000, 101)):
        cases.append({
            "id": f"pipeline-{size // 1000000}m-r24-s{seed}",
            "title": f"Two-stage array pipeline · {size // 1000000}M elements · 24 repeats · seed {seed}",
            "purpose": "Follow-up workload informed by earlier fusion wins; longer execution reduces the relative impact of process startup and timing granularity.",
            "require_o3_win": True,
            "request": {"benchmark_id": "pipeline", "mode": "full", "llm_mode": "off",
                        "tuning_input": {"size": size, "seed": seed, "repeat": 24},
                        "holdout_input": {"size": size + 500000 if size < 16000000 else 15500000,
                                          "seed": seed + 2000, "repeat": 24},
                        "budget_per_strategy": 20, "max_sequence_length": 4,
                        "search_repetitions": 5, "confirmation_rounds": 10},
        })
    return cases


def eligible(summary: dict, *, require_win: bool = False) -> bool:
    """Require <=2% regression against BOTH references on BOTH workloads."""
    close_enough = summary.get("status") == "completed" and all(
        (result := summary.get("comparisons", {}).get(phase, {}).get(reference, {})).get("paired_rounds", 0) >= 10
        and result.get("runtime_reduction_percent", float("-inf")) >= -2
        for phase in ("tuning", "holdout")
        for reference in ("guided_vs_o3", "guided_vs_random")
    )
    return close_enough and (not require_win or all(
        summary["comparisons"][phase]["guided_vs_o3"].get("label") == "win"
        for phase in ("tuning", "holdout")
    ))


def publish(records: list[dict], output: Path) -> None:
    selected = [row for row in records if row.get("selected")]
    catalog = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection": "Selected showcase, not a representative evaluation. At most 2% measured runtime regression versus both Clang -O3 and equal-budget random search, on tuning and holdout inputs. Follow-up cases additionally require a win versus O3 on both inputs and were chosen using earlier screening results. Workload variants reuse two C programs; they are not independent algorithms. Results are exploratory single-machine observations.",
        "screened": len(records), "excluded": len(records) - len(selected),
        "items": selected,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    (output / "screening.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    fields = ["case_id", "benchmark", "input", "size", "seed", "repeat", "o3_median_ns", "o3_mad_ns",
              "guided_median_ns", "guided_mad_ns", "reduction_vs_o3_percent", "faster_pairs_vs_o3", "paired_rounds",
              "random_median_ns", "random_mad_ns", "reduction_vs_random_percent", "o3_comparison_label"]
    with (output / "statistics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in selected:
            for phase, comparisons in row.get("comparisons", {}).items():
                o3, random = comparisons["guided_vs_o3"], comparisons["guided_vs_random"]
                writer.writerow({"case_id": row["id"], "benchmark": row["request"]["benchmark_id"], "input": phase,
                                 **row["request"][f"{phase}_input"],
                                 "o3_median_ns": o3["comparator_median_ns"], "o3_mad_ns": o3["comparator_mad_ns"],
                                 "guided_median_ns": o3["candidate_median_ns"], "guided_mad_ns": o3["candidate_mad_ns"],
                                 "reduction_vs_o3_percent": o3["runtime_reduction_percent"],
                                 "faster_pairs_vs_o3": o3["paired_wins"], "paired_rounds": o3["paired_rounds"],
                                 "random_median_ns": random["comparator_median_ns"], "random_mad_ns": random["comparator_mad_ns"],
                                 "reduction_vs_random_percent": random["runtime_reduction_percent"],
                                 "o3_comparison_label": o3["label"]})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "examples" / "showcase")
    parser.add_argument("--resume", action="store_true", help="Reuse completed screening records with matching source/settings")
    args = parser.parse_args()
    service = TuningService()
    previous_path = args.output / "screening.json"
    previous = {row["id"]: row for row in json.loads(previous_path.read_text())} if args.resume and previous_path.exists() else {}
    records = []
    for case in screening_cases():
        old = previous.get(case["id"])
        source_hash = benchmark(case["request"]["benchmark_id"])["source_sha256"]
        if (old and old.get("source_sha256") == source_hash and old["request"] == case["request"]
                and "error" not in old and (args.output / case["id"] / "bundle.json").is_file()):
            records.append(old)
            continue
        print(f"Measuring {case['id']} (20 guided + 20 random proposals)", flush=True)
        record = {**case, "source_sha256": source_hash, "selected": False}
        try:
            experiment_id = service.create_experiment(case["request"])
            record["experiment_id"] = experiment_id
            summary = service.run_experiment(experiment_id)
            record["comparisons"] = summary["comparisons"]
            record["selected"] = eligible(summary, require_win=case.get("require_o3_win", False))
            # All screened evidence remains downloadable, including excluded runs.
            export_example(experiment_id, case["id"], args.output)
            holdout = summary["comparisons"]["holdout"]
            print(f"  selected={record['selected']}; holdout vs O3={holdout['guided_vs_o3']['runtime_reduction_percent']:.2f}%; vs random={holdout['guided_vs_random']['runtime_reduction_percent']:.2f}%", flush=True)
        except (TrialFailure, RuntimeError, ValueError, OSError) as exc:
            record["selected"] = False
            record["error"] = str(exc)
            print(f"  failed: {exc}", flush=True)
        records.append(record)
        publish(records, args.output)
    publish(records, args.output)
    print(f"Published {sum(row['selected'] for row in records)}/{len(records)} cases to {args.output}", flush=True)


if __name__ == "__main__":
    main()
