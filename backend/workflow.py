"""Typed graph routing only; existing owned tools and HTTP budgets remain authoritative."""
from typing import Any, TypedDict
from langgraph.graph import StateGraph, START, END


class InvestigationState(TypedDict, total=False):
    owner: str
    evidence_scope: dict[str, str | None]
    observations: list[dict[str, Any]]
    selected_excerpts: list[dict[str, Any]]
    classification: dict[str, Any] | None
    stage: str
    usage: dict[str, Any]
    deadline: float
    tool_activity: list[dict[str, Any]]
    tool_rounds: int
    pending_calls: list[dict[str, Any]]
    ready: bool
    report: dict[str, Any]
    corrections: int
    validation_error: str | None


def investigation_graph(nodes):
    graph = StateGraph(InvestigationState)
    for name, node in nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, "fetch_owned")
    for left, right in zip(("fetch_owned", "select_blocks", "classify", "prioritize"), ("select_blocks", "classify", "prioritize", "investigate")):
        graph.add_edge(left, right)
    graph.add_conditional_edges("investigate", lambda state: "report" if state.get("ready") else "tools")
    graph.add_edge("tools", "investigate")
    graph.add_edge("report", "validate")
    graph.add_conditional_edges("validate", lambda state: "correct" if state.get("validation_error") else "save")
    graph.add_edge("correct", "validate")
    graph.add_edge("save", END)
    # No checkpointer/replay: persisted interruptions require a new explicit job.
    return graph.compile()


class SupplementState(TypedDict, total=False):
    owner: str
    investigation_id: str
    evidence_scope: dict[str, Any]
    classification: dict[str, Any] | None
    stage: str
    usage: dict[str, Any]
    deadline: float
    selected_excerpts: list[dict[str, Any]]
    result: dict[str, Any]


def supplement_graph(nodes):
    graph = StateGraph(SupplementState)
    for name, node in nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, "saved_evidence")
    graph.add_edge("saved_evidence", "review")
    graph.add_edge("review", "validate_and_save")
    graph.add_edge("validate_and_save", END)
    return graph.compile()
