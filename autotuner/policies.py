from __future__ import annotations

import itertools
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Proposal:
    action_ids: tuple[str, ...]
    proposal_type: str
    reason: str
    parent_trial_id: str | None = None
    evidence_trial_ids: tuple[str, ...] = ()


def unseen_random(actions: tuple[str, ...], max_length: int, seen: set[tuple[str, ...]], rng: random.Random) -> tuple[str, ...] | None:
    for _ in range(300):
        length = rng.randint(1, min(max_length, len(actions)))
        sequence = tuple(rng.sample(actions, length))
        if sequence not in seen:
            return sequence
    pool = [sequence for length in range(1, min(max_length, len(actions)) + 1)
            for sequence in itertools.permutations(actions, length) if sequence not in seen]
    return rng.choice(pool) if pool else None


class RandomPolicy:
    def __init__(self, actions: tuple[str, ...], max_length: int, seed: int):
        self.actions, self.max_length, self.rng = actions, max_length, random.Random(seed)

    def propose(self, seen: set[tuple[str, ...]], history: list[dict]) -> Proposal | None:
        sequence = unseen_random(self.actions, self.max_length, seen, self.rng)
        return Proposal(sequence, "exploration", "Seeded random sequence; no IR or other-policy history used.") if sequence else None


class GuidedPolicy:
    def __init__(self, actions: tuple[str, ...], max_length: int, seed: int, features: dict):
        self.actions, self.max_length, self.rng, self.features = actions, max_length, random.Random(seed), features
        weights = {action: 1.0 for action in actions}
        if features.get("loop_present"):
            for action in ("loop-unroll", "loop-vectorize", "licm"):
                if action in weights:
                    weights[action] += 2.0
        if features.get("conditional_branches", 0):
            if "simplifycfg" in weights:
                weights["simplifycfg"] += 2.0
        if features.get("loads", 0) > 2 and "gvn" in weights:
            weights["gvn"] += 1.0
        self.weights = weights
        self.probes = tuple(sorted(actions, key=lambda action: (-weights[action], action)))

    def propose(self, seen: set[tuple[str, ...]], history: list[dict]) -> Proposal | None:
        for action in self.probes[: min(4, len(self.probes))]:
            if (action,) not in seen:
                return Proposal((action,), "feature_prior", f"One-action probe; feature weight {self.weights[action]:.1f}.")
        if self.rng.random() < 0.25:
            sequence = unseen_random(self.actions, self.max_length, seen, self.rng)
            if sequence:
                return Proposal(sequence, "exploration", "Reserved exploration proposal independent of current best.")
        ranked = sorted((trial for trial in history if trial.get("status") == "valid" and trial.get("search_median_ns") is not None), key=lambda trial: trial["search_median_ns"])[:3]
        for _ in range(100):
            if not ranked:
                break
            parent = self.rng.choice(ranked)
            old = tuple(parent["action_ids"])
            operations = ["replace", "swap"]
            if len(old) < min(self.max_length, len(self.actions)):
                operations.append("append")
            if len(old) > 1:
                operations.append("delete")
            operation = self.rng.choice(operations)
            new = list(old)
            if operation == "append":
                choices = [item for item in self.actions if item not in new]
                if not choices:
                    continue
                new.append(self.rng.choices(choices, weights=[self.weights[item] for item in choices])[0])
            elif operation == "replace":
                choices = [item for item in self.actions if item not in new]
                if not choices:
                    continue
                new[self.rng.randrange(len(new))] = self.rng.choices(choices, weights=[self.weights[item] for item in choices])[0]
            elif operation == "swap":
                if len(new) < 2:
                    continue
                i, j = self.rng.sample(range(len(new)), 2)
                new[i], new[j] = new[j], new[i]
            else:
                del new[self.rng.randrange(len(new))]
            sequence = tuple(new)
            if sequence not in seen:
                return Proposal(sequence, "mutation", f"{operation} of measured trial {parent['id']}.", parent["id"], (parent["id"],))
        sequence = unseen_random(self.actions, self.max_length, seen, self.rng)
        return Proposal(sequence, "exploration", "Unseen fallback after neighbor search was exhausted.") if sequence else None
