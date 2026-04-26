"""Web-search tool — abstract :class:`SearchProvider` + Tavily / Mock impls.

The interface is the contract the agent depends on; the provider is swappable.
This is decision D-004 in DECISIONS.md: keep an interface so we can swap to
Gemini grounding (GCP-native) later without touching the agent layer.

Selection: ``MHFA_SEARCH_PROVIDER`` env var (``tavily`` default, or ``mock``).
"""
from __future__ import annotations

import os
from typing import Any, Protocol


class SearchProvider(Protocol):
    """Interface every search backend must implement."""

    def search(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        """Return a list of ``{title, url, snippet, content?}`` dicts."""


class TavilyProvider:
    """LLM-shaped web search via Tavily (https://tavily.com).

    Free tier covers MVP (1k queries/month). API key from ``TAVILY_API_KEY``.
    """

    def __init__(self, api_key: str | None = None) -> None:
        from tavily import TavilyClient  # local import — only needed for this provider

        key = api_key or os.environ.get("TAVILY_API_KEY")
        if not key:
            raise RuntimeError(
                "TavilyProvider needs TAVILY_API_KEY. Set it in .env, or use the "
                "mock provider via MHFA_SEARCH_PROVIDER=mock."
            )
        self._client = TavilyClient(api_key=key)

    def search(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        resp = self._client.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
            include_answer=False,
            include_raw_content=False,
        )
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", "")[:500],
                "score": r.get("score"),
            }
            for r in resp.get("results", [])
        ]


class MockProvider:
    """Deterministic offline provider for tests and CI.

    Returns a single hard-coded item. Lets every test and CI run be hermetic
    instead of pinging Tavily on every push.
    """

    def search(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        return [
            {
                "title": f"[mock result] {query}",
                "url": "https://example.com/mock",
                "snippet": (
                    "MockProvider returned this. To use real web results set "
                    "MHFA_SEARCH_PROVIDER=tavily and TAVILY_API_KEY in .env."
                ),
                "score": 1.0,
            }
        ][:max_results]


def get_search_provider() -> SearchProvider:
    """Factory — read env, return the configured provider."""
    name = os.environ.get("MHFA_SEARCH_PROVIDER", "tavily").lower()
    if name == "tavily":
        return TavilyProvider()
    if name == "mock":
        return MockProvider()
    raise ValueError(
        f"MHFA_SEARCH_PROVIDER={name!r} is not recognised. Use 'tavily' or 'mock'."
    )
