"""Optional LangGraph orchestration for the bounded search loop.

The graph controls transitions; policies and compiler measurements are the same
objects used by the plain Python loop, including optional OpenAI proposals.
"""

from __future__ import annotations

from typing import Callable, TypedDict

from .policies import Proposal


class SearchState(TypedDict, total=False):
    turn: int
    total_turns: int
    current_strategy: str
    current_ordinal: int
    current_proposal: Proposal | None
    current_trial_id: str | None
    trial_ids: list[str]
    seen_sequences: dict[str, list[list[str]]]
    feature_summary: dict
    stop_reason: str | None


def run_search_graph(strategies: tuple[str, ...], budget: int, policies: dict,
                     features: dict, evaluate: Callable[[str, int, Proposal], dict]) -> tuple[dict[str, list[dict]], SearchState]:
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError("Install the graph extra: pip install -e '.[graph]'") from exc

    histories: dict[str, list[dict]] = {strategy: [] for strategy in strategies}
    seen: dict[str, set[tuple[str, ...]]] = {strategy: set() for strategy in strategies}
    exhausted: set[str] = set()

    def inspect_ir(state: SearchState) -> dict:
        return {"feature_summary": features, "seen_sequences": {strategy: [] for strategy in strategies}, "trial_ids": []}

    def propose(state: SearchState) -> dict:
        turn = state["turn"]
        strategy = strategies[turn % len(strategies)]
        ordinal = turn // len(strategies) + 1
        proposal = None if strategy in exhausted else policies[strategy].propose(seen[strategy], histories[strategy])
        if proposal is None:
            exhausted.add(strategy)
        else:
            seen[strategy].add(proposal.action_ids)
        return {"current_strategy": strategy, "current_ordinal": ordinal, "current_proposal": proposal, "current_trial_id": None,
                "seen_sequences": {key: [list(item) for item in sorted(values)] for key, values in seen.items()}}

    def compile_verify_measure(state: SearchState) -> dict:
        proposal = state["current_proposal"]
        if proposal is None:
            return {"current_trial_id": None}
        trial = evaluate(state["current_strategy"], state["current_ordinal"], proposal)
        histories[state["current_strategy"]].append(trial)
        return {"current_trial_id": trial["id"]}

    def evaluate_result(state: SearchState) -> dict:
        trial_ids = list(state["trial_ids"])
        if state["current_trial_id"]:
            trial_ids.append(state["current_trial_id"])
        stop_reason = "budget_exhausted" if state["turn"] + 1 >= state["total_turns"] else None
        return {"turn": state["turn"] + 1, "trial_ids": trial_ids, "stop_reason": stop_reason}

    graph = StateGraph(SearchState)
    graph.add_node("inspect_ir", inspect_ir)
    graph.add_node("propose", propose)
    graph.add_node("compile_verify_measure", compile_verify_measure)
    graph.add_node("evaluate", evaluate_result)
    graph.add_edge(START, "inspect_ir")
    graph.add_edge("inspect_ir", "propose")
    graph.add_edge("propose", "compile_verify_measure")
    graph.add_edge("compile_verify_measure", "evaluate")
    graph.add_conditional_edges("evaluate", lambda state: END if state["turn"] >= state["total_turns"] else "propose")
    final = graph.compile().invoke({"turn": 0, "total_turns": budget * len(strategies), "trial_ids": [], "seen_sequences": {}, "feature_summary": {}, "stop_reason": None}, config={"recursion_limit": 5 * budget * len(strategies) + 10})
    return histories, final
