"""Tests for tools/sec.py — uses a tmp dir of fake summaries, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mhfa.tools import sec


@pytest.fixture
def fake_sec_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a fake summaries tree and point MHFA_LOCAL_SEC_DIR at it."""
    (tmp_path / "summaries").mkdir()
    (tmp_path / "summaries_10q").mkdir()
    (tmp_path / "summaries_8k").mkdir()

    # 10-K
    (tmp_path / "summaries" / "TEST_2024_summary.json").write_text(
        json.dumps({"ticker": "TEST", "fiscal_year": 2024, "top_risks": ["r1"], "mda_summary": "mda"})
    )
    # 10-Q (two — fetch_latest_10q should pick the newer date)
    (tmp_path / "summaries_10q" / "TEST_2024-09-30_10Q.json").write_text(
        json.dumps({"ticker": "TEST", "fiscal_period": "Q3 2024", "revenue": "$10B"})
    )
    (tmp_path / "summaries_10q" / "TEST_2024-12-31_10Q.json").write_text(
        json.dumps({"ticker": "TEST", "fiscal_period": "Q4 2024", "revenue": "$12B"})
    )
    # 8-K (multiple)
    for d in ("2024-12-01", "2025-01-15", "2025-02-20"):
        (tmp_path / "summaries_8k" / f"TEST_{d}_000001_8K.json").write_text(
            json.dumps({"ticker": "TEST", "filing_date": d, "event_type": "guidance"})
        )

    monkeypatch.setenv("MHFA_LOCAL_SEC_DIR", str(tmp_path))
    return tmp_path


def test_fetch_latest_10q_picks_newest(fake_sec_root: Path) -> None:
    out = sec.fetch_latest_10q("TEST")
    assert out["fiscal_period"] == "Q4 2024"
    assert out["_source"]["filing_type"] == "10-Q"
    assert out["_source"]["period_end"] == "2024-12-31"
    assert "edgar_url" in out["_source"]


def test_fetch_latest_10k(fake_sec_root: Path) -> None:
    out = sec.fetch_latest_10k("TEST")
    assert out["fiscal_year"] == 2024
    assert out["_source"]["filing_type"] == "10-K"


def test_fetch_recent_8k_returns_newest_first(fake_sec_root: Path) -> None:
    out = sec.fetch_recent_8k("TEST", max_items=2)
    assert len(out) == 2
    assert out[0]["_source"]["filing_date"] == "2025-02-20"
    assert out[1]["_source"]["filing_date"] == "2025-01-15"


def test_unknown_ticker_raises(fake_sec_root: Path) -> None:
    with pytest.raises(sec.SECDataNotFound):
        sec.fetch_latest_10q("MISSING")


def test_list_indexed_tickers(fake_sec_root: Path) -> None:
    assert sec.list_indexed_tickers() == ["TEST"]


def test_unset_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFA_LOCAL_SEC_DIR", raising=False)
    with pytest.raises(sec.SECDataNotFound):
        sec.fetch_latest_10q("ANY")
