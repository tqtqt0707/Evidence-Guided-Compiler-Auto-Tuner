"""Export a completed run as a read-only, portable browser example."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .config import ROOT, RUNS_DIR, DATA_DIR
from .service import TuningService


def export_example(experiment_id: str, name: str, output_dir: Path | None = None) -> Path:
    if not name.replace("-", "").isalnum() or name in {".", ".."}:
        raise ValueError("Example name must be alphanumeric with optional hyphens")
    service = TuningService()
    summary = service.report(experiment_id)
    examples_dir = output_dir or (DATA_DIR / "exports" if ROOT.name == "resources" else ROOT / "examples")
    target = examples_dir / name
    target.mkdir(parents=True, exist_ok=True)
    trials = service.list_trials(experiment_id, limit=100)["items"]
    details = {}
    for trial in trials:
        full = service.get_trial(experiment_id, trial["id"])
        details[trial["id"]] = {
            "id": full["id"], "strategy": full["strategy"], "ordinal": full["ordinal"],
            "action_ids": full["action_ids"], "effective_pipeline": full["effective_pipeline"],
            "proposal_reason": full["proposal_reason"], "status": full["status"],
            "error_code": full["error_code"], "ir_instructions_before": full["ir_instructions_before"],
            "ir_instructions_after": full["ir_instructions_after"],
            "commands": full["commands"], "diagnostics": full.get("diagnostics", {}),
            "samples": [{key: sample[key] for key in ("phase", "input_kind", "round", "repetition", "duration_ns", "checksum", "valid")} for sample in full["samples"]],
        }
    (target / "bundle.json").write_text(json.dumps({"summary": summary, "trials": trials, "details": details}, indent=2), encoding="utf-8")
    for filename in summary["artifacts"]:
        if filename == "summary.json":
            (target / filename).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        else:
            shutil.copy2(RUNS_DIR / experiment_id / filename, target / filename)
    index_path = examples_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"items": []}
    index["items"] = [item for item in index["items"] if item["name"] != name]
    index["items"].append({"name": name, "benchmark_id": summary["benchmark_id"], "experiment_id": experiment_id, "mode": summary["mode"]})
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_id")
    parser.add_argument("name")
    parser.add_argument("--output-dir", type=Path, help="Export root; installed wheels default to the user data directory")
    args = parser.parse_args()
    print(export_example(args.experiment_id, args.name, args.output_dir))


if __name__ == "__main__":
    main()
