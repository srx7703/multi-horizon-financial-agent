"""Tests for the synthesizer — uses a fake Anthropic client (no network).

The synthesizer is mostly prompt-engineering plus a single ``messages.create``
call. We verify:

* the system prompt embeds the raw_data we pass in (so the model has evidence)
* `_metadata` is stripped before being shown to the model (executor internals)
* the chart path + period make it into the prompt template
* the response is concatenated text from all content blocks
* the returned brief is a string, not the SDK object
"""
from __future__ import annotations

from typing import Any

import pytest

from mhfa.agent import synthesizer


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeNonTextBlock:
    """Stand-in for tool-use / image blocks we should ignore."""

    def __init__(self) -> None:
        self.type = "tool_use"


class _FakeResponse:
    def __init__(self, blocks: list[Any]) -> None:
        self.content = blocks


class _FakeMessages:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None
        self.return_blocks: list[Any] = [_FakeBlock("# AAPL — Quarterly Earnings Recap\n\nstubbed.")]

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.last_kwargs = kwargs
        return _FakeResponse(self.return_blocks)


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    """Replace synthesizer.get_client with one that returns a fake."""
    fc = _FakeClient()
    monkeypatch.setattr(
        synthesizer,
        "get_client",
        lambda role: (fc, "claude-fake-1", {"max_tokens": 1234, "temperature": 0.5}),
    )
    return fc


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


def test_synthesize_returns_string(fake_client: _FakeClient) -> None:
    out = synthesizer.synthesize_brief("aapl", _sample_raw(), chart_path="aapl_chart.png", period="3mo")
    assert isinstance(out, str)
    assert "AAPL" in out  # fake response is normalized upper


def test_synthesize_uses_role_config(fake_client: _FakeClient) -> None:
    synthesizer.synthesize_brief("aapl", _sample_raw())
    kwargs = fake_client.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "claude-fake-1"
    assert kwargs["max_tokens"] == 1234
    assert kwargs["temperature"] == 0.5


def test_synthesize_strips_metadata_from_prompt(fake_client: _FakeClient) -> None:
    """Executor internals (_metadata) must not leak into the model prompt."""
    synthesizer.synthesize_brief("aapl", _sample_raw())
    user_msg = fake_client.messages.last_kwargs["messages"][0]["content"]  # type: ignore[index]
    assert "_metadata" not in user_msg
    assert "duration_ms" not in user_msg
    assert "DispatchMissing" not in user_msg


def test_synthesize_embeds_evidence_and_chart(fake_client: _FakeClient) -> None:
    """The user message should contain raw_data + the chart path + period."""
    synthesizer.synthesize_brief(
        "aapl", _sample_raw(), chart_path="aapl_20260101_chart.png", period="6mo"
    )
    user_msg = fake_client.messages.last_kwargs["messages"][0]["content"]  # type: ignore[index]
    # Evidence is in there
    assert "$120B" in user_msg
    assert "Q1 2025" in user_msg
    assert "Apple beats" in user_msg
    # Citation primitives propagated to the template fields
    assert "2025-12-27" in user_msg
    assert "https://sec.gov/aapl-10q" in user_msg
    # Period + chart filename surfaced
    assert "aapl_20260101_chart.png" in user_msg
    assert "6mo" in user_msg


def test_synthesize_concatenates_text_blocks(
    monkeypatch: pytest.MonkeyPatch, fake_client: _FakeClient
) -> None:
    """Multi-block responses should be joined; non-text blocks ignored."""
    fake_client.messages.return_blocks = [
        _FakeBlock("part one. "),
        _FakeNonTextBlock(),
        _FakeBlock("part two."),
    ]
    out = synthesizer.synthesize_brief("aapl", _sample_raw())
    assert out == "part one. part two."


def test_synthesize_handles_missing_source_gracefully(fake_client: _FakeClient) -> None:
    """If the 10-Q lacks _source, template formatting should still work."""
    raw = _sample_raw()
    raw["sec.fetch_latest_10q"].pop("_source")
    out = synthesizer.synthesize_brief("aapl", raw)
    assert isinstance(out, str)
    user_msg = fake_client.messages.last_kwargs["messages"][0]["content"]  # type: ignore[index]
    assert "unknown" in user_msg  # period_end fallback
