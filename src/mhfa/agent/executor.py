"""Executor — runs a list of :class:`~mhfa.agent.planner.ToolCall` items.

MVP: sequential. Phase 3 swaps in asyncio for parallel tool calls. The
executor is intentionally dumb — failures get caught per-tool and recorded in
``_metadata.errors`` rather than crashing the run, so the synthesizer can be
told "the 8-K fetch failed, work without it".
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from mhfa.agent.planner import ToolCall
from mhfa.tools import market_data, sec
from mhfa.tools.search import get_search_provider


def _build_dispatch() -> dict[str, Callable[..., Any]]:
    """Map tool dotted-name → callable. Lazy on the search provider."""
    return {
        "sec.fetch_latest_10q": sec.fetch_latest_10q,
        "sec.fetch_latest_10k": sec.fetch_latest_10k,
        "sec.fetch_recent_8k": sec.fetch_recent_8k,
        "market.get_quote_history": market_data.get_quote_history,
        "market.get_company_info": market_data.get_company_info,
        "search.web_search": lambda query, max_results=5: get_search_provider().search(
            query, max_results=max_results
        ),
    }


def execute_plan(plan: list[ToolCall]) -> dict[str, Any]:
    """Run each call sequentially. Return aggregated dict + metadata.

    Output dict shape:
        {
            "<tool dotted name>": <tool result>,
            ...,
            "_metadata": {
                "duration_ms": int,
                "errors": [{"tool": str, "error": str, "type": str}, ...],
                "calls": [{"tool": str, "args": dict, "duration_ms": int, "ok": bool}, ...],
            },
        }
    """
    dispatch = _build_dispatch()
    out: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    calls: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    for step in plan:
        if step.tool not in dispatch:
            errors.append({"tool": step.tool, "error": "unknown tool", "type": "DispatchMissing"})
            calls.append({"tool": step.tool, "args": step.args, "duration_ms": 0, "ok": False})
            continue

        fn = dispatch[step.tool]
        s0 = time.perf_counter()
        try:
            result = fn(**step.args)
            out[step.tool] = result
            calls.append(
                {
                    "tool": step.tool,
                    "args": step.args,
                    "duration_ms": int((time.perf_counter() - s0) * 1000),
                    "ok": True,
                }
            )
        except Exception as e:  # noqa: BLE001 — agent must be tolerant of every tool
            errors.append({"tool": step.tool, "error": str(e), "type": type(e).__name__})
            calls.append(
                {
                    "tool": step.tool,
                    "args": step.args,
                    "duration_ms": int((time.perf_counter() - s0) * 1000),
                    "ok": False,
                }
            )

    out["_metadata"] = {
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "errors": errors,
        "calls": calls,
    }
    return out
