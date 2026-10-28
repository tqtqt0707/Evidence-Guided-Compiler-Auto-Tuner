from __future__ import annotations

import re

from .config import stable_hash


def summarize_ir(text: str) -> dict:
    instructions = [line.strip() for line in text.splitlines() if line.startswith("  ") and not line.lstrip().startswith((";", "!"))]
    branches = sum(bool(re.match(r"(?:%[\w.]+\s*=\s*)?br\s+i1\b", line)) for line in instructions)
    features = {
        "instruction_count": len(instructions),
        "basic_blocks": len(re.findall(r"(?m)^[\w.$-]+:\s*(?:;.*)?$", text)),
        "conditional_branches": branches,
        "phi_nodes": sum(" phi " in f" {line} " for line in instructions),
        "loads": sum(bool(re.search(r"\bload\b", line)) for line in instructions),
        "stores": sum(bool(re.search(r"\bstore\b", line)) for line in instructions),
        "loop_metadata_refs": len(re.findall(r"!llvm\.loop", text)),
        "loop_present": "!llvm.loop" in text or "vector.body" in text or "for.body" in text,
        "extractor": "text-ir-v1",
        "caveat": "Approximate textual IR counts; not a runtime predictor.",
    }
    features["fingerprint"] = stable_hash(features)
    return features
