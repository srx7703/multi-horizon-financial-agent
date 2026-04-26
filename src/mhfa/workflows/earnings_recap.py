"""``earnings_recap`` — the MVP end-to-end workflow.

Glues planner → executor → market chart → synthesizer and writes the brief
to disk. This is the function the CLI calls.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from mhfa.agent.executor import execute_plan
from mhfa.agent.planner import build_earnings_recap_plan
from mhfa.agent.synthesizer import synthesize_brief
from mhfa.tools.market_data import plot_price_history


def run_earnings_recap(
    ticker: str,
    *,
    output_dir: str | Path = "outputs",
    period: str = "3mo",
    skip_synthesis: bool = False,
) -> dict[str, Any]:
    """Run the full earnings-recap pipeline for ``ticker``.

    Writes ``<ticker>_<YYYYMMDD>_brief.md``, ``..._chart.png``, and
    ``..._raw.json`` into ``output_dir``. Returns a dict of paths + brief
    + plan + raw, useful for piping into eval.
    """
    ticker = ticker.upper()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")

    plan = build_earnings_recap_plan(ticker, period=period)
    raw = execute_plan(plan)

    chart_path = output_dir / f"{ticker}_{stamp}_chart.png"
    history = raw.get("market.get_quote_history")
    if history:
        try:
            plot_price_history(history, chart_path, title_suffix=f"as of {stamp}")
        except Exception as e:  # noqa: BLE001 — chart is decorative, brief still works without
            raw["_metadata"]["errors"].append(
                {"tool": "plot_price_history", "error": str(e), "type": type(e).__name__}
            )

    raw_path = output_dir / f"{ticker}_{stamp}_raw.json"
    raw_path.write_text(json.dumps(raw, indent=2, default=str))

    brief_path: Path | None = None
    brief_text: str | None = None
    if not skip_synthesis:
        brief_text = synthesize_brief(
            ticker,
            raw,
            chart_path=chart_path.name,  # relative — markdown sits next to it
            period=period,
        )
        brief_path = output_dir / f"{ticker}_{stamp}_brief.md"
        brief_path.write_text(brief_text)

    return {
        "ticker": ticker,
        "plan": [{"tool": s.tool, "args": s.args, "reason": s.reason} for s in plan],
        "raw": raw,
        "brief_text": brief_text,
        "paths": {
            "brief": str(brief_path) if brief_path else None,
            "chart": str(chart_path) if chart_path.exists() else None,
            "raw": str(raw_path),
        },
    }
