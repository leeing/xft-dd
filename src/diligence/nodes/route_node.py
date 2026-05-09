"""route_node: fan-out via LangGraph Send API -- one branch per active dimension."""

from __future__ import annotations

from langgraph.types import Send

from diligence.state import DiligenceState


def route_node(state: DiligenceState) -> list[Send]:
    """Send one search task per active dimension."""
    return [
        Send("search_summarize_node", {**state, "current_dimension": dim})
        for dim in state["active_dimensions"]
    ]
