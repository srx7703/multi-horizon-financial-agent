"""Synthesizer — Claude Opus turns raw tool data into a markdown brief.

System prompt is engineered around a single behavioral rule: every numeric
claim must cite a source already in ``raw_data``. We don't trust the model
not to hallucinate — we tell it explicitly to label unverifiable numbers as
"not disclosed". The factuality eval (eval/factuality.py) measures how well
that rule sticks.
"""
from __future__ import annotations

import json
from typing import Any

from mhfa.models.client import Role, get_client

_BRIEF_TEMPLATE = """You are a senior equity-research analyst writing a one-page brief for a portfolio manager.

The user wants a quarterly recap on **{ticker}**. You have raw tool output below. Your job is synthesis, not retrieval.

# Hard rules
- **Every numeric claim** must trace to a value in raw_data. If you can't find a number, write "not disclosed" — do not estimate.
- Inline-cite the source after each claim, e.g. "(10-Q period ended 2025-12-27)".
- Use the exact section structure below. If a section has no material content, say so explicitly — do not pad.
- Markdown only. No HTML. The chart is referenced by relative path `{chart_path}` — embed it under "Price Action".

# Output template

# {ticker} — Quarterly Earnings Recap

## Executive summary
3-4 sentences with the headline numbers and the analyst takeaway.

## Financial highlights
| Metric | Value | YoY |
|---|---|---|
| Revenue | … | … |
| Net income | … | … |
| EPS (diluted) | … | … |
| Operating cash flow | … | … |

## Price action ({period})
![Price chart]({chart_path})
One paragraph: range, % change vs start, where today sits in the 52-week band.

## Recent catalysts
Bullet list from 8-Ks and web search. Each bullet ends with `(source)`.

## Key risks
Bullets from 10-Q "new_risks" + 10-K "top_risks" (if relevant). Be specific.

## Sources
- 10-Q filed period_end {period_end}: {tenq_url}
- Recent 8-Ks: {eightk_count} events since latest 10-Q
- Market data: yfinance
- Web search: {search_count} hits

---

# Raw data (do not echo verbatim)
```json
{raw_json}
```
"""


def synthesize_brief(
    ticker: str,
    raw_data: dict[str, Any],
    *,
    chart_path: str = "chart.png",
    period: str = "3mo",
    role: Role = "synthesizer",
) -> str:
    """Generate the markdown brief. Returns the raw markdown string."""
    client, model, cfg = get_client(role)

    tenq = raw_data.get("sec.fetch_latest_10q") or {}
    eightks = raw_data.get("sec.fetch_recent_8k") or []
    search_hits = raw_data.get("search.web_search") or []

    period_end = (tenq.get("_source") or {}).get("period_end", "unknown")
    tenq_url = (tenq.get("_source") or {}).get("edgar_url", "")

    # Strip _metadata from the raw payload before handing to the model — the model
    # doesn't need to see executor timings.
    raw_for_model = {k: v for k, v in raw_data.items() if k != "_metadata"}

    user_msg = _BRIEF_TEMPLATE.format(
        ticker=ticker.upper(),
        period=period,
        chart_path=chart_path,
        period_end=period_end,
        tenq_url=tenq_url,
        eightk_count=len(eightks),
        search_count=len(search_hits),
        raw_json=json.dumps(raw_for_model, indent=2, default=str),
    )

    resp = client.messages.create(
        model=model,
        max_tokens=cfg["max_tokens"],
        temperature=cfg.get("temperature", 0.3),
        messages=[{"role": "user", "content": user_msg}],
    )
    # Anthropic SDK returns a list of content blocks; concatenate the text ones.
    return "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
