from __future__ import annotations

import json
from pathlib import Path

from .config import BENCHMARKS, sha256_file


def registry() -> dict[str, dict]:
    data = json.loads((BENCHMARKS / "manifest.json").read_text(encoding="utf-8"))
    for benchmark_id, entry in data.items():
        entry["id"] = benchmark_id
        entry["source_path"] = str((BENCHMARKS / entry["source"]).resolve())
        entry["source_sha256"] = sha256_file(Path(entry["source_path"]))
    return data


def benchmark(benchmark_id: str) -> dict:
    items = registry()
    if benchmark_id not in items:
        raise ValueError(f"Unknown benchmark: {benchmark_id}")
    return items[benchmark_id]
