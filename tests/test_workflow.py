"""End-to-end workflow test for ``earnings_recap`` — fully hermetic.

Mocks every external surface (SEC summaries on disk, market_data + plot,
synthesizer's Anthropic client) so the test runs offline with no API keys
and exercises the actual control flow:

* plan → execute → chart → write raw.json → synthesize → write brief.md
* skip_synthesis path (no LLM call at all)
* output filenames embed ticker + date stamp
* return dict shape matches what the CLI consumes
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mhfa.agent import synthesizer
from mhfa.tools import market_data
from mhfa.workflows import earnings_recap

_FAKE_HISTORY = {
    "ticker": "AAPL",
    "period": "3mo",
    "prices": [{"date": "2026-01-26", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100}],
    "summary": {
        "start_close": 100.0,
        "end_close": 110.0,
        "pct_change": 10.0,
        "high_52w": 120.0,
        "low_52w": 80.0,
        "avg_volume": 50000000,
    },
}

_FAKE_INFO = {
    "ticker": "AAPL",
    "long_name": "Apple Inc.",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "market_cap": 3_000_000_000_000,
    "trailing_pe": 30.0,
    "forward_pe": 28.0,
    "price_to_book": 50.0,
    "dividend_yield": 0.005,
    "currency": "USD",
}


@pytest.fixture
def hermetic_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """SEC summaries on disk + mock search + stubbed market_data + fake LLM client."""
    # 1. Local SEC summaries
    (tmp_path / "summaries_10q").mkdir()
    (tmp_path / "summaries_8k").mkdir()
    (tmp_path / "summaries").mkdir()
    (tmp_path / "summaries_10q" / "AAPL_2025-12-27_10Q.json").write_text(
        json.dumps({
            "ticker": "AAPL",
            "fiscal_period": "Q1 2026",
            "revenue": "$120B",
            "net_income": "$30B",
            "key_metrics": ["EPS $1.50"],
            "qoq_changes": [],
            "new_risks": ["FX headwind"],
            "management_tone": "cautiously optimistic",
            "analyst_note": "in-line with consensus",
        })
    )
    (tmp_path / "summaries_8k" / "AAPL_2026-02-01_000001_8K.json").write_text(
        json.dumps({
            "ticker": "AAPL",
            "filing_date": "2026-02-01",
            "event_type": "Earnings",
            "headline": "Q1 results",
            "materiality": "High",
            "investor_impact": "...",
            "key_figures": [],
        })
    )
    monkeypatch.setenv("MHFA_LOCAL_SEC_DIR", str(tmp_path))
    monkeypatch.setenv("MHFA_SEARCH_PROVIDER", "mock")

    # 2. Stub market_data — yfinance is not hermetic
    monkeypatch.setattr(market_data, "get_quote_history", lambda ticker, period="3mo": _FAKE_HISTORY)
    monkeypatch.setattr(market_data, "get_company_info", lambda ticker: _FAKE_INFO)
    # plot_price_history is imported into the workflow module, patch there
    monkeypatch.setattr(earnings_recap, "plot_price_history", lambda *a, **kw: None)

    # 3. Stub the provider-agnostic completion adapter used by synthesizer
    monkeypatch.setattr(
        synthesizer,
        "complete_text",
        lambda role, *, user_message, system_message=None: (
            "# AAPL — Quarterly Earnings Recap\n\nstub brief."
        ),
    )
    return tmp_path


def test_workflow_writes_three_artifacts(hermetic_env: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    result = earnings_recap.run_earnings_recap("aapl", output_dir=out_dir)

    # Paths exist
    raw_path = Path(result["paths"]["raw"])
    brief_path = Path(result["paths"]["brief"])
    assert raw_path.exists()
    assert brief_path.exists()

    # Naming convention: <TICKER>_<YYYYMMDD>_{raw.json|brief.md|chart.png}
    assert raw_path.name.startswith("AAPL_")
    assert raw_path.name.endswith("_raw.json")
    assert brief_path.name.endswith("_brief.md")

    # Brief content is what the fake LLM produced
    assert "stub brief" in brief_path.read_text()
    assert result["brief_text"] is not None
    assert "stub brief" in result["brief_text"]

    # Raw JSON is parseable and has the executor's metadata
    raw = json.loads(raw_path.read_text())
    assert "_metadata" in raw
    assert raw["_metadata"]["errors"] == []
    assert raw["sec.fetch_latest_10q"]["fiscal_period"] == "Q1 2026"


def test_workflow_skip_synthesis_path(hermetic_env: Path, tmp_path: Path) -> None:
    """--skip-synthesis should not write a brief and should not call the LLM."""
    result = earnings_recap.run_earnings_recap(
        "aapl", output_dir=tmp_path / "outputs", skip_synthesis=True
    )
    assert result["brief_text"] is None
    assert result["paths"]["brief"] is None
    # raw still exists
    assert Path(result["paths"]["raw"]).exists()


def test_workflow_returns_plan_summary(hermetic_env: Path, tmp_path: Path) -> None:
    result = earnings_recap.run_earnings_recap(
        "aapl", output_dir=tmp_path / "outputs", skip_synthesis=True
    )
    plan_tools = [step["tool"] for step in result["plan"]]
    assert "sec.fetch_latest_10q" in plan_tools
    assert "market.get_quote_history" in plan_tools
    assert "search.web_search" in plan_tools


def test_workflow_chart_failure_is_soft(
    hermetic_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """plot_price_history exception should be captured to errors, not propagated."""
    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("matplotlib exploded")
    monkeypatch.setattr(earnings_recap, "plot_price_history", boom)

    result = earnings_recap.run_earnings_recap(
        "aapl", output_dir=tmp_path / "outputs", skip_synthesis=True
    )
    raw = json.loads(Path(result["paths"]["raw"]).read_text())
    errs = raw["_metadata"]["errors"]
    assert any(e["tool"] == "plot_price_history" for e in errs)
    assert any("matplotlib exploded" in e["error"] for e in errs)
