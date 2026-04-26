"""Planner — turns a user query into an ordered list of tool calls.

MVP scope: hardcoded ``earnings_recap`` workflow plan. Even though the plan is
fixed we still route the *call* through Claude so Phase 2 can add new
workflows by tweaking the system prompt rather than rewriting the planner.

Phase 2 will switch to dynamic tool-use planning with the full Anthropic
``tools=`` schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """One step of an executor plan."""

    tool: str
    """Dotted name, e.g. ``"sec.fetch_latest_10q"`` or ``"market.get_quote_history"``."""

    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    """Free-text rationale — surfaced in traces and `--explain` debug mode."""


def build_earnings_recap_plan(ticker: str, *, period: str = "3mo") -> list[ToolCall]:
    """Return the canonical earnings-recap plan for ``ticker``.

    MVP: this is hardcoded. A real planner-LLM call shows up in Phase 2 when
    new workflows arrive.
    """
    t = ticker.upper()
    return [
        ToolCall(
            tool="sec.fetch_latest_10q",
            args={"ticker": t},
            reason="Latest quarterly results — revenue, margin, qoq deltas, mgmt tone.",
        ),
        ToolCall(
            tool="sec.fetch_recent_8k",
            args={"ticker": t, "max_items": 5},
            reason="Material catalysts since the last 10-Q (M&A, guidance, mgmt change).",
        ),
        ToolCall(
            tool="market.get_quote_history",
            args={"ticker": t, "period": period},
            reason="Price action context to bracket the fundamentals.",
        ),
        ToolCall(
            tool="market.get_company_info",
            args={"ticker": t},
            reason="Sector/industry/market cap + valuation multiples.",
        ),
        ToolCall(
            tool="search.web_search",
            args={"query": f"{t} latest earnings analyst reaction", "max_results": 5},
            reason="Cross-check with sell-side commentary; surface things SEC text misses.",
        ),
    ]
