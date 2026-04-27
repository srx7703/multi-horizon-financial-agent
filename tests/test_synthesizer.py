"""Tests for the synthesizer — uses a fake completion adapter (no network).

The synthesizer is mostly prompt-engineering plus a single ``complete_text``
call. We verify:

* the prompt embeds the raw_data we pass in (so the model has evidence)
* `_metadata` is stripped before being shown to the model (executor internals)
* the chart path + period make it into the prompt template
* the system rule is propagated
* the role parameter lets us A/B against ``synthesizer_b``
"""
from __future__ import annotations

from typing import Any

import pytest

from mhfa.agent import synthesizer


class _CompleteRecorder:
    """Drop-in for ``complete_text`` that captures all calls + returns canned text."""

    def __init__(self, return_text: str = "# AAPL — Quarterly Earnings Recap\n\nstub.") -> None:
        self.calls: list[dict[str, Any]] = []
        self.return_text = return_text

    def __call__(
        self,
        role: str,
        *,
        user_message: str,
        system_message: str | None = None,
    ) -> str:
        self.calls.append(
            {"role": role, "user_message": user_message, "system_message": system_message}
        )
        return self.return_text


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _CompleteRecorder:
    rec = _CompleteRecorder()
    monkeypatch.setattr(synthesizer, "complete_text", rec)
    return rec


def _sample_raw() -> dict[str, Any]:
    return {
        "sec.fetch_latest_10q": {
            "ticker": "AAPL",
            "fiscal_period": "Q1 2025",
            "revenue": "$120B",
            "_source": {
                "filing_type": "10-Q",
                "period_end": "2025-12-27",
                "edgar_url": "https://sec.gov/aapl-10q",
            },
        },
        "sec.fetch_recent_8k": [
            {"ticker": "AAPL", "event_type": "Earnings", "filing_date": "2025-02-01"}
        ],
        "search.web_search": [
            {"title": "Apple beats", "url": "https://x", "snippet": "..."}
        ],
        "_metadata": {
            "duration_ms": 999,
            "errors": [{"tool": "x", "error": "boom", "type": "X"}],
            "calls": [],
        },
    }


def test_synthesize_returns_string(recorder: _CompleteRecorder) -> None:
    out = synthesizer.synthesize_brief(
        "aapl", _sample_raw(), chart_path="aapl_chart.png", period="3mo"
    )
    assert isinstance(out, str)
    assert "AAPL" in out


def test_synthesize_routes_to_synthesizer_role_by_default(
    recorder: _CompleteRecorder,
) -> None:
    synthesizer.synthesize_brief("aapl", _sample_raw())
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["role"] == "synthesizer"


def test_synthesize_routes_to_synthesizer_b_for_ab(
    recorder: _CompleteRecorder,
) -> None:
    """Phase 2 A/B: pass role=synthesizer_b to run the alternate model."""
    synthesizer.synthesize_brief("aapl", _sample_raw(), role="synthesizer_b")
    assert recorder.calls[0]["role"] == "synthesizer_b"


def test_synthesize_strips_metadata_from_prompt(recorder: _CompleteRecorder) -> None:
    """Executor internals (_metadata) must not leak into the model prompt."""
    synthesizer.synthesize_brief("aapl", _sample_raw())
    user_msg = recorder.calls[0]["user_message"]
    assert "_metadata" not in user_msg
    assert "duration_ms" not in user_msg
    assert "DispatchMissing" not in user_msg


def test_synthesize_embeds_evidence_and_chart(recorder: _CompleteRecorder) -> None:
    """The user message should contain raw_data + the chart path + period."""
    synthesizer.synthesize_brief(
        "aapl", _sample_raw(), chart_path="aapl_20260101_chart.png", period="6mo"
    )
    user_msg = recorder.calls[0]["user_message"]
    assert "$120B" in user_msg
    assert "Q1 2025" in user_msg
    assert "Apple beats" in user_msg
    assert "2025-12-27" in user_msg
    assert "https://sec.gov/aapl-10q" in user_msg
    assert "aapl_20260101_chart.png" in user_msg
    assert "6mo" in user_msg


def test_synthesize_propagates_system_rule(recorder: _CompleteRecorder) -> None:
    """The 'do not estimate' rule must be in the system message."""
    synthesizer.synthesize_brief("aapl", _sample_raw())
    sys_msg = recorder.calls[0]["system_message"]
    assert sys_msg is not None
    assert "not disclosed" in sys_msg
    assert "estimate" in sys_msg.lower()


def test_synthesize_handles_missing_source_gracefully(recorder: _CompleteRecorder) -> None:
    """If the 10-Q lacks _source, template formatting should still work."""
    raw = _sample_raw()
    raw["sec.fetch_latest_10q"].pop("_source")
    out = synthesizer.synthesize_brief("aapl", raw)
    assert isinstance(out, str)
    user_msg = recorder.calls[0]["user_message"]
    assert "unknown" in user_msg
