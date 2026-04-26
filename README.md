# Multi-Horizon Financial Agent

> Tool-using research agent that turns a single ticker into a citation-grounded,
> chart-illustrated quarterly brief in under two minutes.
>
> Sister project of [**multi-horizon-financial-llm**](https://github.com/srx7703/multi-horizon-financial-llm) — the
> Gemma LoRA adapter trained there will plug in as a swappable synthesis
> backend in Phase 2 (A/B vs Claude Opus, paired-t methodology continuous with
> the sister repo's eval).

![CI](https://img.shields.io/github/actions/workflow/status/srx7703/multi-horizon-financial-agent/ci.yml?branch=main&label=tests&logo=github)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python)
![Claude](https://img.shields.io/badge/Claude-Sonnet%204.5%20%7C%20Opus%204.5%20%7C%20Haiku%204.5-D97757?logo=anthropic)
![License](https://img.shields.io/badge/license-MIT-green)

> **Status — v0.1 (MVP)**: `earnings_recap` end-to-end works on tickers indexed
> in the sister repo (69 S&P 500). Factuality eval implemented; baseline number
> pinned in `eval/runs/`. Phase 2 (adapter A/B + 2 more workflows) on roadmap.

---

## TL;DR

```bash
mhfa earnings-recap NVDA --output ./outputs
# → outputs/NVDA_<YYYYMMDD>_brief.md  (markdown)
# → outputs/NVDA_<YYYYMMDD>_chart.png (price chart)
# → outputs/NVDA_<YYYYMMDD>_raw.json  (raw tool output, for eval)
```

The brief is structured: exec summary, financial highlights table, price action
+ chart, recent catalysts, key risks, sources. Every numeric claim is required
to cite a source already in the raw tool output (the synthesizer's hard rule),
and the factuality eval verifies that rule held.

---

## Architecture

```
                    User: "earnings-recap NVDA"
                              │
                              ▼
                       ┌──────────────┐
                       │   Planner    │  Sonnet 4.5  →  ordered ToolCall list
                       └──────┬───────┘
                              ▼
                       ┌──────────────┐
                       │   Executor   │  sequential, fail-soft per tool
                       └──────┬───────┘
                              ▼
        ┌──────────┬──────────┼──────────┬──────────┐
        ▼          ▼          ▼          ▼          ▼
    ┌───────┐ ┌───────┐  ┌────────┐ ┌────────┐ ┌────────┐
    │SEC 10Q│ │SEC 8-K│  │yfinance│ │company │ │ Tavily │
    └───────┘ └───────┘  └────────┘ │  info  │ │ search │
                                    └────────┘ └────────┘
                              │
                              ▼  raw evidence (JSON)
                       ┌──────────────┐
                       │ Synthesizer  │  Opus 4.5  →  markdown brief
                       └──────┬───────┘
                              ▼
                       ┌──────────────┐
                       │  Factuality  │  Haiku 4.5  →  verified-claims score
                       │  eval (Phase │
                       │  1 baseline) │
                       └──────────────┘
```

**Layered model routing** (`configs/models.yaml`) — Sonnet for the planner,
Opus for the user-facing synthesizer, Haiku for high-volume eval. One file
controls every model choice; cost/quality ablations are config changes, not
code changes. Phase 2 adds a `synthesizer_b` slot for the Multi-Horizon Gemma
adapter to enable A/B.

**Tool layer is plain Python in v0.1.** MCP wrapping is a Phase 2 task — the
function signatures are deliberately MCP-shaped (single dict in, single dict
out) so the wrapping is mechanical.

---

## Quickstart

```bash
git clone https://github.com/srx7703/multi-horizon-financial-agent
cd multi-horizon-financial-agent
pip install -e ".[dev]"            # or: uv sync

cp .env.example .env
$EDITOR .env                       # fill TAVILY_API_KEY, GCP_PROJECT_ID, MHFA_LOCAL_SEC_DIR

mhfa earnings-recap NVDA
```

### Required env vars

| Var | Why |
|---|---|
| `ANTHROPIC_BACKEND` | `vertex` (default) routes through GCP and eats your GCP credit; `direct` uses `ANTHROPIC_API_KEY` |
| `GCP_PROJECT_ID` + `VERTEX_REGION` | Required when backend=vertex. `us-east5` has Opus / Sonnet / Haiku |
| `TAVILY_API_KEY` | Free tier 1k q/mo. Skip with `MHFA_SEARCH_PROVIDER=mock` for tests |
| `SEC_USER_AGENT` | SEC blocks requests without contact-info UA — use real name + email |
| `MHFA_LOCAL_SEC_DIR` | Path to a dir holding `summaries/`, `summaries_10q/`, `summaries_8k/` (the sister repo's data dump) |

---

## How a brief is produced

1. **Plan** (`agent/planner.py`) — for `earnings_recap` the plan is fixed:
   latest 10-Q + recent 8-Ks + 3-month price + company info + web hits.
2. **Execute** (`agent/executor.py`) — calls each tool, captures per-tool
   timing, never crashes on a single tool failure.
3. **Chart** — yfinance close prices → matplotlib PNG.
4. **Synthesize** (`agent/synthesizer.py`) — Opus gets raw JSON + a hard prompt
   that requires every numeric claim to trace to a source. Output is markdown
   with inline citations.
5. **(Optional) Eval** (`eval/factuality.py`) — Haiku-as-judge extracts every
   factual claim from the brief, then verifies each one against `raw_data`.
   Score = verified / total.

---

## Eval

| Metric | Method | MVP baseline |
|---|---|---|
| Factuality | Two-pass Haiku (extract → verify) | _to be filled by hour 14 of MVP sprint_ |
| Comprehensiveness | Rubric (0–3) over 5 axes | _Phase 2_ |
| BERTScore F1 vs golden | RoBERTa-large, paired t-test | _Phase 2 — same as sister repo for narrative continuity_ |

Hand-curated golden briefs live in `eval/golden/`. They are written from raw
sources by hand, never LLM-generated (decision D-006 — avoids
evaluator/generator collapse).

---

## Layout

```
src/mhfa/
├── tools/             SEC, market data, web search — pluggable
├── agent/             planner + executor + synthesizer
├── models/            client.py — Vertex / direct API switch + role config
├── workflows/         earnings_recap (more in Phase 2)
├── eval/              factuality (Phase 1) + ab_harness (Phase 2)
└── cli.py             entry point: `mhfa earnings-recap <TICKER>`
configs/models.yaml    model routing (which Claude per role)
eval/golden/           hand-curated reference briefs
tests/                 hermetic — uses MockProvider + fake SEC dir
```

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for full phasing. Headline:

- **v0.1 (this release)** — `earnings_recap` end-to-end, factuality eval,
  3+ golden briefs, CI green.
- **v0.5** — Multi-Horizon Gemma adapter integration, true A/B with paired-t,
  `ma_drilldown` + `sector_compare` workflows.
- **v1.0** — Streamlit UI, Docker self-host, watchlist cron, cost-aware
  routing, observability dashboard, public release.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Sister repo

[`multi-horizon-financial-llm`](https://github.com/srx7703/multi-horizon-financial-llm)
is the fine-tuning + RAG side: 69 S&P 500 tickers × 381 SEC filings, two
PEFT LoRA adapters (Gemma 2 27B and Gemma 4 31B) trained on TPU v6e-8.
HF Hub: `Srx7703/gemma-{2-27b,4-31b}-financial-adapter`. The two repos
cross-reference; the agent here will A/B those adapters against Opus in
Phase 2.
