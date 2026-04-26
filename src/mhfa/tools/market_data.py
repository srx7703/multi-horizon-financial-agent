"""Market data tool — yfinance for prices + matplotlib for the chart.

yfinance is unofficial and breaks every few months; treat any failure as
recoverable, log it to ``_metadata.errors`` upstream, and never crash the
agent loop. Phase 2 will add an ``alpha_vantage`` fallback.

Returned summaries are pre-computed (high_52w, pct_change, …) instead of
handed to the LLM — letting the synthesizer "do math" reliably hallucinates.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless — must be set before pyplot import
import matplotlib.pyplot as plt
import yfinance as yf


class MarketDataError(Exception):
    """Raised when yfinance returns something we can't use."""


def get_quote_history(ticker: str, period: str = "3mo") -> dict[str, Any]:
    """OHLCV history + summary stats for ``ticker``.

    ``period``: yfinance period string ("1mo", "3mo", "6mo", "1y", "2y", "5y").
    Note US trading days ≠ calendar days — period="3mo" returns ~63 rows.
    """
    t = yf.Ticker(ticker)
    df = t.history(period=period, auto_adjust=False)
    if df is None or df.empty:
        raise MarketDataError(f"yfinance returned empty history for {ticker} ({period})")

    # Pull a 1-year window for 52-week high/low even when caller asked for shorter.
    df_1y = t.history(period="1y", auto_adjust=False)
    high_52w = float(df_1y["High"].max()) if not df_1y.empty else float(df["High"].max())
    low_52w = float(df_1y["Low"].min()) if not df_1y.empty else float(df["Low"].min())

    start_close = float(df["Close"].iloc[0])
    end_close = float(df["Close"].iloc[-1])
    pct_change = (end_close - start_close) / start_close * 100.0

    prices = [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]),
        }
        for idx, row in df.iterrows()
    ]

    return {
        "ticker": ticker.upper(),
        "period": period,
        "prices": prices,
        "summary": {
            "start_close": round(start_close, 2),
            "end_close": round(end_close, 2),
            "pct_change": round(pct_change, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "avg_volume": int(df["Volume"].mean()),
        },
    }


def get_company_info(ticker: str) -> dict[str, Any]:
    """Name, sector, market cap, common valuation multiples.

    yfinance ``.info`` is wide and inconsistent across tickers; we pluck just
    the fields the synthesizer actually uses and fail-soft on missing keys.
    """
    info = yf.Ticker(ticker).info or {}

    def pick(*keys: str) -> Any:
        for k in keys:
            if k in info and info[k] is not None:
                return info[k]
        return None

    return {
        "ticker": ticker.upper(),
        "long_name": pick("longName", "shortName"),
        "sector": pick("sector"),
        "industry": pick("industry"),
        "market_cap": pick("marketCap"),
        "trailing_pe": pick("trailingPE"),
        "forward_pe": pick("forwardPE"),
        "price_to_book": pick("priceToBook"),
        "dividend_yield": pick("dividendYield"),
        "currency": pick("currency", "financialCurrency"),
    }


def plot_price_history(
    history: dict[str, Any], save_path: str | Path, *, title_suffix: str = ""
) -> Path:
    """Render a clean close-price line chart to PNG.

    ``history`` is the output of :func:`get_quote_history`. Returns the path
    written, for easy chaining into the synthesizer's markdown citation.
    """
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    dates = [p["date"] for p in history["prices"]]
    closes = [p["close"] for p in history["prices"]]
    ticker = history["ticker"]
    period = history["period"]
    summary = history["summary"]

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(dates, closes, linewidth=1.8, color="#2563eb")
    ax.fill_between(dates, closes, min(closes), alpha=0.08, color="#2563eb")

    title = f"{ticker} close price · last {period}"
    if title_suffix:
        title = f"{title} — {title_suffix}"
    ax.set_title(title, fontsize=12, pad=10)
    ax.set_ylabel("USD")
    ax.set_xlabel("")

    # Thin x-tick labels — show ~6 dates max so they don't overlap.
    n = len(dates)
    if n > 6:
        step = n // 6
        ax.set_xticks(range(0, n, step))
        ax.set_xticklabels([dates[i] for i in range(0, n, step)], rotation=30, ha="right")

    annotation = (
        f"start ${summary['start_close']:.2f}  "
        f"end ${summary['end_close']:.2f}  "
        f"({summary['pct_change']:+.1f}%)"
    )
    ax.text(
        0.01, 0.97, annotation,
        transform=ax.transAxes, fontsize=9, verticalalignment="top",
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.8, "edgecolor": "#cbd5e1"},
    )

    fig.tight_layout()
    fig.savefig(save_path, dpi=140)
    plt.close(fig)
    return save_path
