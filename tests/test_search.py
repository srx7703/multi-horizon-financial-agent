"""Tests for the search provider — the mock path is hermetic, no network."""
from __future__ import annotations

import pytest

from mhfa.tools.search import MockProvider, get_search_provider


def test_mock_provider_returns_one() -> None:
    out = MockProvider().search("hello", max_results=3)
    assert len(out) == 1
    assert out[0]["url"] == "https://example.com/mock"


def test_factory_picks_mock_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MHFA_SEARCH_PROVIDER", "mock")
    p = get_search_provider()
    assert isinstance(p, MockProvider)


def test_factory_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MHFA_SEARCH_PROVIDER", "bogus")
    with pytest.raises(ValueError, match="not recognised"):
        get_search_provider()


def test_tavily_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("MHFA_SEARCH_PROVIDER", "tavily")
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        get_search_provider()
