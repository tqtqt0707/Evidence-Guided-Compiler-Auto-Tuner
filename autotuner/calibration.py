from __future__ import annotations

import json
from pathlib import Path

from .config import DATA_DIR
from .models import Workload
from .oracle import parse_checksum
from .registry import registry
from .report import median_mad
from .toolchain import Toolchain
from .storage import utc_now


def calibrate(benchmark_id: str | None = None) -> dict:
    entries = registry()
    if benchmark_id is not None and benchmark_id not in entries:
        raise ValueError(f"Unknown benchmark: {benchmark_id}")
    tc = Toolchain()
    scratch = DATA_DIR / "calibration"
    scratch.mkdir(parents=True, exist_ok=True)
    output = {"collected_at": utc_now(), "environment": tc.environment(), "benchmarks": {}}
    for entry in entries.values():
        if benchmark_id is not None and entry["id"] != benchmark_id:
            continue
        directory = scratch / entry["id"]
        directory.mkdir(parents=True, exist_ok=True)
        binary = directory / "o3"
        build = tc.compile_direct(Path(entry["source_path"]), "-O3", binary, directory, "build-o3")
        if not build.ok:
            raise RuntimeError(f"Calibration compile failed for {entry['id']}: {build.stderr_excerpt}")
        cases = {}
        for name in ("quick_input", "tuning_input", "holdout_input"):
            workload = Workload.parse(entry[name], entry)
            observations = []
            expected_checksum = None
            for repetition in range(3):
                result = tc.run_binary(binary, workload, directory, f"{name}-{repetition}")
                if not result.ok:
                    raise RuntimeError(f"Calibration execution failed for {entry['id']}/{name}: {result.stderr_excerpt}")
                checksum = parse_checksum(result.stdout)
                if expected_checksum is not None and checksum != expected_checksum:
                    raise RuntimeError(f"Calibration checksum changed for {entry['id']}/{name}")
                expected_checksum = checksum
                observations.append(result.duration_ns)
            median, mad = median_mad(observations)
            cases[name] = {"workload": entry[name], "samples_ns": observations, "median_ns": median, "mad_ns": mad}
        output["benchmarks"][entry["id"]] = {"source_sha256": entry["source_sha256"], "cases": cases}
    (scratch / "calibration.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output
