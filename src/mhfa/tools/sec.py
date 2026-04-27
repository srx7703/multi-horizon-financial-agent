"""SEC EDGAR tool — provides 10-K / 10-Q / 8-K data to the agent.

MVP backend: read pre-distilled JSON summaries from a local directory
(:envvar:`MHFA_LOCAL_SEC_DIR`). The sister repo ``multi-horizon-financial-llm``
already produces 381 such summaries (69 tickers × 10-K/10-Q/8-K) — point this at
its ``summaries_10q/`` and we get zero-latency, schema-stable, parseable input
for free, instead of redoing the ``sec-edgar-downloader → BeautifulSoup → Item
1A`` parse loop in 60 minutes.

Phase 2 will add a live ``sec-edgar-downloader`` fallback so the tool also works
without the sister repo's data dump checked out.

Schemas (see sister repo's ``hybrid_retriever._doc_text`` for source):

* 10-K  ``summaries/{TICKER}_{YEAR}_summary.json`` — top_risks, strategic_highlights, mda_summary, analyst_note
* 10-Q  ``summaries_10q/{TICKER}_{YYYY-MM-DD}_10Q.json`` — fiscal_period, revenue, net_income, key_metrics, qoq_changes, new_risks, management_tone, analyst_note
* 8-K   ``summaries_8k/{TICKER}_{YYYY-MM-DD}_{ID}_8K.json`` — event_type, headline, materiality, investor_impact, key_figures
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

FilingType = Literal["10-K", "10-Q", "8-K"]

_SUBDIR_BY_TYPE: dict[FilingType, str] = {
    "10-K": "summaries",
    "10-Q": "summaries_10q",
    "8-K": "summaries_8k",
}


class SECDataNotFound(Exception):
    """Raised when no local summary matches the request."""


def _local_root() -> Path:
    """Return the configured local SEC summaries directory, or raise."""
    raw = os.environ.get("MHFA_LOCAL_SEC_DIR")
    if not raw:
        raise SECDataNotFound(
            "MHFA_LOCAL_SEC_DIR is not set. Point it at the sister repo's parent "
            "dir that contains summaries/, summaries_10q/, summaries_8k/."
        )
    p = Path(raw).expanduser()
    if not p.is_dir():
        raise SECDataNotFound(f"MHFA_LOCAL_SEC_DIR={p} is not a directory.")
    return p


def _list_summaries(filing_type: FilingType, ticker: str) -> list[Path]:
    """Return all summary file paths for a ticker, sorted oldest → newest."""
    subdir = _local_root() / _SUBDIR_BY_TYPE[filing_type]
    if not subdir.is_dir():
        raise SECDataNotFound(f"Expected subdir not present: {subdir}")
    matches = sorted(subdir.glob(f"{ticker.upper()}_*"))
    if not matches:
        raise SECDataNotFound(
            f"No {filing_type} summaries found for {ticker} in {subdir}"
        )
    return matches


def _parse_period(filing_type: FilingType, path: Path) -> str:
    """Pull the period token out of the filename (year for 10-K, date for 10-Q/8-K)."""
    parts = path.stem.split("_")
    if filing_type == "10-K":
        return parts[1]  # e.g. "2025"
    return parts[1]  # e.g. "2025-12-27" — also works for 8-K


def fetch_latest_10q(ticker: str) -> dict[str, Any]:
    """Return the most recent 10-Q summary for ``ticker``.

    Schema is documented in the module docstring; downstream synthesizer/planner
    code should rely on these field names being stable.
    """
    paths = _list_summaries("10-Q", ticker)
    latest = paths[-1]
    payload = json.loads(latest.read_text())
    payload["_source"] = {
        "filing_type": "10-Q",
        "path": str(latest),
        "period_end": _parse_period("10-Q", latest),
        "edgar_url": _edgar_url(ticker, "10-Q"),
    }
    return payload  # type: ignore[no-any-return]


def fetch_latest_10k(ticker: str) -> dict[str, Any]:
    """Return the most recent 10-K (annual) summary for ``ticker``."""
    paths = _list_summaries("10-K", ticker)
    latest = paths[-1]
    payload = json.loads(latest.read_text())
    payload["_source"] = {
        "filing_type": "10-K",
        "path": str(latest),
        "fiscal_year": _parse_period("10-K", latest),
        "edgar_url": _edgar_url(ticker, "10-K"),
    }
    return payload  # type: ignore[no-any-return]


def fetch_recent_8k(
    ticker: str, *, since: date | None = None, max_items: int = 5
) -> list[dict[str, Any]]:
    """Return up to ``max_items`` most-recent 8-K event summaries for ``ticker``.

    Filter by ``since`` (inclusive) if provided; otherwise simply take the last
    ``max_items`` chronologically. 8-Ks carry the catalysts a 10-Q misses
    (M&A, mgmt change, guidance update) so the synthesizer needs them.
    """
    paths = _list_summaries("8-K", ticker)
    items: list[dict[str, Any]] = []
    for p in reversed(paths):  # newest first
        date_str = _parse_period("8-K", p)
        if since:
            try:
                if date.fromisoformat(date_str) < since:
                    continue
            except ValueError:
                pass  # keep — better to over-include than drop on parse fail
        s = json.loads(p.read_text())
        s["_source"] = {
            "filing_type": "8-K",
            "path": str(p),
            "filing_date": date_str,
            "edgar_url": _edgar_url(ticker, "8-K"),
        }
        items.append(s)
        if len(items) >= max_items:
            break
    return items


def _edgar_url(ticker: str, filing_type: FilingType) -> str:
    """SEC EDGAR full-history URL for a ticker × filing type — used in citations."""
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={ticker.upper()}&type={filing_type}"
        "&dateb=&owner=include&count=40"
    )


def list_indexed_tickers() -> list[str]:
    """Best-effort: scan all three subdirs and return the union of tickers seen.

    Useful for the CLI's ``--list-tickers`` and for the planner's "is this
    ticker in our corpus?" precheck.

    Accepts US-style tickers including punctuated share-class variants
    (BRK.B / BRK-A / BF.B). Filters out junk tokens (e.g. fiscal-year stubs)
    by requiring uppercase first char, length ≤6, and only letters / dots /
    hyphens in the symbol.
    """
    tickers: set[str] = set()
    root = _local_root()
    for sub in _SUBDIR_BY_TYPE.values():
        d = root / sub
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            t = p.stem.split("_")[0]
            if (
                1 <= len(t) <= 6
                and t[0].isupper()
                and all(c.isalpha() or c in ".-" for c in t)
            ):
                tickers.add(t)
    return sorted(tickers)
