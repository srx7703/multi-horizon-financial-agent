"""Run factuality eval for a single ticker.

Usage:
    python scripts/eval_one.py <TICKER> <DATE>

Reads:
    outputs/<TICKER>_<DATE>_brief.md
    outputs/<TICKER>_<DATE>_raw.json

Writes:
    eval/runs/<TICKER>_<DATE>_factuality.json

Designed to be called per-ticker so eval can be parallelised across tickers
(per-claim verification inside one ticker stays serial — Gemini Flash rate
limits are friendlier when concurrency lives at the ticker level).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Load .env from repo root (matches the mhfa CLI's behaviour) before importing
# anything that needs GCP_PROJECT_ID at module load time.
_REPO = Path(__file__).resolve().parent.parent
_env = _REPO / ".env"
if _env.exists():
    load_dotenv(_env)

from mhfa.eval.factuality import check_factuality  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: eval_one.py <TICKER> <DATE>", file=sys.stderr)
        return 2
    ticker, date = sys.argv[1], sys.argv[2]
    repo = Path(__file__).resolve().parent.parent
    brief_path = repo / "outputs" / f"{ticker}_{date}_brief.md"
    raw_path = repo / "outputs" / f"{ticker}_{date}_raw.json"
    out_path = repo / "eval" / "runs" / f"{ticker}_{date}_factuality.json"

    brief = brief_path.read_text(encoding="utf-8")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    t0 = time.time()
    result = check_factuality(brief, raw)
    elapsed = time.time() - t0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"[{ticker}] score={result['score']} "
        f"verified={result['verified_claims']}/{result['total_claims']} "
        f"flagged={len(result['flagged'])} elapsed={elapsed:.1f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
