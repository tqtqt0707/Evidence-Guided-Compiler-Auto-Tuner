from __future__ import annotations

from dataclasses import asdict, dataclass
import os

from .registry import benchmark


@dataclass(frozen=True)
class Workload:
    size: int
    seed: int
    repeat: int

    @classmethod
    def parse(cls, value: dict, limits: dict) -> "Workload":
        if not isinstance(value, dict) or set(value) != {"size", "seed", "repeat"}:
            raise ValueError("Workload must contain size, seed, and repeat")
        if any(type(value[key]) is not int for key in ("size", "seed", "repeat")):
            raise ValueError("Workload fields must be integers")
        size, seed, repeat = value["size"], value["seed"], value["repeat"]
        if not limits["size_min"] <= size <= limits["size_max"]:
            raise ValueError(f"size must be between {limits['size_min']} and {limits['size_max']}")
        if not 0 <= seed <= 2**64 - 1:
            raise ValueError("seed must be an unsigned 64-bit integer")
        if not 1 <= repeat <= 100:
            raise ValueError("repeat must be between 1 and 100")
        return cls(size, seed, repeat)

    def args(self) -> list[str]:
        return ["--size", str(self.size), "--seed", str(self.seed), "--repeat", str(self.repeat)]


@dataclass(frozen=True)
class ExperimentRequest:
    benchmark_id: str
    mode: str
    tuning_input: Workload
    holdout_input: Workload | None
    strategies: tuple[str, ...]
    budget_per_strategy: int
    max_sequence_length: int
    search_repetitions: int
    confirmation_rounds: int
    llm_mode: str = "auto"

    @classmethod
    def parse(cls, value: dict) -> "ExperimentRequest":
        if not isinstance(value, dict):
            raise ValueError("Request must be a JSON object")
        allowed = {"benchmark_id", "mode", "tuning_input", "holdout_input", "strategies", "budget_per_strategy", "max_sequence_length", "search_repetitions", "confirmation_rounds", "llm_mode"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown request fields: {', '.join(sorted(unknown))}")
        if "benchmark_id" not in value or "mode" not in value:
            raise ValueError("benchmark_id and mode are required")
        entry = benchmark(value["benchmark_id"])
        mode = value["mode"]
        if mode not in ("quick", "full"):
            raise ValueError("mode must be quick or full")
        tuning_default = entry["quick_input"] if mode == "quick" else entry["tuning_input"]
        tuning = Workload.parse(value.get("tuning_input", tuning_default), entry)
        holdout = None if mode == "quick" else Workload.parse(value.get("holdout_input", entry["holdout_input"]), entry)
        if mode == "full" and tuning == holdout:
            raise ValueError("Full mode requires a distinct holdout workload")
        if mode == "quick" and value.get("holdout_input") is not None:
            raise ValueError("Quick mode has no holdout workload")
        strategies = tuple(value.get("strategies", ["guided"] if mode == "quick" else ["guided", "random"]))
        if not strategies or len(set(strategies)) != len(strategies) or set(strategies) - {"guided", "random"}:
            raise ValueError("strategies must be unique guided/random values")
        if mode == "full" and set(strategies) != {"guided", "random"}:
            raise ValueError("Full mode requires guided and random strategies")
        budget = value.get("budget_per_strategy", 5 if mode == "quick" else 20)
        length = value.get("max_sequence_length", 4)
        repetitions = value.get("search_repetitions", 3 if mode == "quick" else 5)
        rounds = value.get("confirmation_rounds", 3 if mode == "quick" else 10)
        for name, number, minimum, maximum in (("budget_per_strategy", budget, 1, 20), ("max_sequence_length", length, 1, 4), ("search_repetitions", repetitions, 3, 10), ("confirmation_rounds", rounds, 3, 15)):
            if type(number) is not int or not minimum <= number <= maximum:
                raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
        llm_mode = value.get("llm_mode", os.environ.get("AUTOTUNER_LLM_MODE", "auto"))
        if llm_mode not in ("auto", "off", "openai"):
            raise ValueError("llm_mode must be auto, off, or openai")
        return cls(value["benchmark_id"], mode, tuning, holdout, strategies, budget, length, repetitions, rounds, llm_mode)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["strategies"] = list(self.strategies)
        return data
