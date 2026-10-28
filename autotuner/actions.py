from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import stable_hash


@dataclass(frozen=True)
class Action:
    id: str
    display_name: str
    pipeline: str
    ir_unit: str
    hypothesis: str


# These templates were parsed with opt-21 on Ubuntu 26.04. Doctor validates them
# again against the installed binary, including every ordered two-action pipeline.
ACTIONS = (
    Action("instcombine", "Instruction combine", "function(instcombine)", "function", "Combine redundant scalar instructions."),
    Action("simplifycfg", "Simplify control flow", "function(simplifycfg)", "function", "Simplify branches and basic blocks."),
    Action("gvn", "Global value numbering", "function(gvn)", "function", "Eliminate redundant computations and loads."),
    Action("dce", "Dead code elimination", "function(dce)", "function", "Remove newly dead instructions."),
    Action("loop-unroll", "Loop unrolling", "function(loop-unroll)", "function", "Expose repeated loop work to scalar optimization."),
    Action("loop-vectorize", "Loop vectorization", "function(loop-vectorize)", "function", "Use SIMD operations where legal and profitable."),
    Action("licm", "Loop invariant code motion", "function(loop-mssa(licm))", "loop", "Move invariant work out of loops."),
    Action("slp-vectorizer", "SLP vectorization", "function(slp-vectorizer)", "function", "Pack independent scalar operations into vectors."),
    Action("loop-fusion", "Loop fusion", "function(loop-fusion)", "function", "Fuse adjacent loops when dependences permit."),
    Action("loop-interchange", "Loop interchange", "function(loop-interchange)", "function", "Change nested-loop order when legal and profitable."),
)

BY_ID = {action.id: action for action in ACTIONS}


def compose(action_ids: tuple[str, ...] | list[str]) -> str:
    if not action_ids or len(action_ids) > 4 or len(set(action_ids)) != len(action_ids):
        raise ValueError("A sequence must contain 1–4 distinct action IDs")
    try:
        return ",".join(BY_ID[action_id].pipeline for action_id in action_ids)
    except KeyError as exc:
        raise ValueError(f"Unknown action ID: {exc.args[0]}") from exc


def snapshot(available: set[str] | None = None, reasons: dict[str, str] | None = None) -> dict:
    available = available if available is not None else set(BY_ID)
    reasons = reasons or {}
    items = []
    for action in ACTIONS:
        item = asdict(action)
        item["available"] = action.id in available
        item["unavailable_reason"] = reasons.get(action.id)
        items.append(item)
    return {"items": items, "catalog_hash": stable_hash(items)}
