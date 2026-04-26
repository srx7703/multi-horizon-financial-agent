"""Tests for planner + executor — uses MHFA mock providers, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mhfa.agent.executor import execute_plan
from mhfa.agent.planner import ToolCall, build_earnings_recap_plan


@pytest.fixture
def fake_sec_and_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Local SEC summaries + mock search provider — covers the executor's no-net path."""
    (tmp_path / "summaries_10q").mkdir()
    (tmp_path / "summaries_8k").mkdir()
    (tmp_path / "summaries").mkdir()
    (tmp_path / "summaries_10q" / "TEST_2025-03-31_10Q.json").write_text(
        json.dumps({"ticker": "TEST", "fiscal_period": "Q1 2025", "revenue": "$5B"})
    )
    (tmp_path / "summaries_8k" / "TEST_2025-04-01_000001_8K.json").write_text(
        json.dumps({"ticker": "TEST", "filing_date": "2025-04-01", "event_type": "guidance"})
    )
    monkeypatch.setenv("MHFA_LOCAL_SEC_DIR", str(tmp_path))
    monkeypatch.setenv("MHFA_SEARCH_PROVIDER", "mock")
    return tmp_path


def test_plan_has_expected_steps() -> None:
    plan = build_earnings_recap_plan("nvda")
    tools = [s.tool for s in plan]
    assert "sec.fetch_latest_10q" in tools
    assert "sec.fetch_recent_8k" in tools
    assert "market.get_quote_history" in tools
    assert "search.web_search" in tools
    # All args should reference the upper-cased ticker.
    for s in plan:
        if "ticker" in s.args:
            assert s.args["ticker"] == "NVDA"


def test_executor_records_unknown_tool() -> None:
    out = execute_plan([ToolCall(tool="bogus.thing", args={})])
    errs = out["_metadata"]["errors"]
    assert len(errs) == 1
    assert errs[0]["type"] == "DispatchMissing"


def test_executor_runs_sec_and_search_no_network(fake_sec_and_search: Path) -> None:
    plan = [
        ToolCall(tool="sec.fetch_latest_10q", args={"ticker": "TEST"}),
        ToolCall(tool="sec.fetch_recent_8k", args={"ticker": "TEST"}),
        ToolCall(tool="search.web_search", args={"query": "anything", "max_results": 1}),
    ]
    out = execute_plan(plan)
    assert out["sec.fetch_latest_10q"]["fiscal_period"] == "Q1 2025"
    assert out["sec.fetch_recent_8k"][0]["event_type"] == "guidance"
    assert out["search.web_search"][0]["url"] == "https://example.com/mock"
    assert out["_metadata"]["errors"] == []


def test_executor_continues_on_tool_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If one tool errors the rest still run and the error is captured, not raised."""
    monkeypatch.delenv("MHFA_LOCAL_SEC_DIR", raising=False)
    monkeypatch.setenv("MHFA_SEARCH_PROVIDER", "mock")
    out = execute_plan(
        [
            ToolCall(tool="sec.fetch_latest_10q", args={"ticker": "X"}),  # will fail
            ToolCall(tool="search.web_search", args={"query": "x"}),  # will succeed
        ]
    )
    assert "search.web_search" in out
    assert any(e["tool"] == "sec.fetch_latest_10q" for e in out["_metadata"]["errors"])
