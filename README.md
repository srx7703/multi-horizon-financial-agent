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
![Gemini](https://img.shields.io/badge/Gemini-2.5%20Pro%20%7C%202.5%20Flash-4285F4?logo=google)
![Claude](https://img.shields.io/badge/Claude-Opus%204.7%20(synthesizer__b)-D97757?logo=anthropic)
![License](https://img.shields.io/badge/license-MIT-green)

> **Status — v0.1 (MVP)**: `earnings_recap` end-to-end works on tickers indexed
> in the sister repo (69 S&P 500). Factuality baseline pinned at **1.00 (17/17
> claims verified)** on the NVDA reference run — see `eval/runs/`. Anthropic is
> wired into the `synthesizer_b` slot for the Phase 2 A/B harness; v0.1 ships
> with Gemini 2.5 because Vertex's new-project quota algorithm rejected six
> Anthropic quota requests in a row (decision D-009).

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
                       │   Planner    │  Gemini 2.5 Flash → ordered ToolCall list
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
                       │ Synthesizer  │  Gemini 2.5 Pro → markdown brief
                       └──────┬───────┘     (synthesizer_b: Opus 4.7, Phase 2)
                              ▼
                       ┌──────────────┐
                       │  Factuality  │  Gemini 2.5 Flash → verified/total
                       │     judge    │  (thinking_budget: 0, JSON mode)
                       └──────────────┘
```

**Layered model routing** (`configs/models.yaml`) — Gemini 2.5 Flash for the
planner / mid-loop summaries / judge (cost-and-latency tier), Gemini 2.5 Pro
for the user-facing synthesizer (quality tier). One file controls every model
choice; cost/quality ablations are config changes, not code changes. Phase 2
turns this into a three-way A/B: **Gemini 2.5 Pro vs Claude Opus 4.7 vs
Multi-Horizon Gemma adapter** through the `synthesizer_b` / `synthesizer_c`
slots.

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
| `GCP_PROJECT_ID` + `VERTEX_REGION` | Required for Gemini on Vertex. `us-central1` is the default and has Gemini 2.5 GA. `us-east5` is the Anthropic region (used only when a role's `provider: anthropic` — see D-009) |
| `ANTHROPIC_BACKEND` | Only matters when a role's `provider: anthropic` (v0.1: just `synthesizer_b`, a Phase 2 slot). `vertex` (default) routes through GCP; `direct` uses `ANTHROPIC_API_KEY` |
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
4. **Synthesize** (`agent/synthesizer.py`) — Gemini 2.5 Pro gets raw JSON + a
   hard prompt that requires every numeric claim to trace to a source and
   forbids estimation (the system rule writes "not disclosed" instead of
   guessing). Output is markdown with inline citations.
5. **(Optional) Eval** (`eval/factuality.py`) — Gemini 2.5 Flash-as-judge runs
   two passes: extract every factual claim from the brief, then verify each
   against `raw_data`. Both passes use Gemini's JSON mode + `thinking_budget: 0`
   so the judge stays cheap and parseable. Score = verified / total.

---

## Eval

| Metric | Method | v0.1 baseline |
|---|---|---|
| Factuality | Two-pass Gemini Flash (extract claims → verify each against raw_data) | **1.00 (17/17)** on `eval/runs/NVDA_20260426_factuality.json` |
| Comprehensiveness | Rubric (0–3) over 5 axes | _Phase 2_ |
| BERTScore F1 vs golden | RoBERTa-large, paired t-test | _Phase 2 — same metric as sister repo for narrative continuity_ |

Hand-curated golden briefs live in `eval/golden/`. They are written from raw
sources by hand, never LLM-generated (decision D-006 — avoids
evaluator/generator collapse).

---

## Layout

```
src/mhfa/
├── tools/             SEC, market data, web search — pluggable
├── agent/             planner + executor + synthesizer
├── models/            client.py — provider-agnostic complete_text adapter
│                      (dispatches Gemini ↔ Anthropic per role)
├── workflows/         earnings_recap (more in Phase 2)
├── eval/              factuality (v0.1) + ab_harness (Phase 2)
└── cli.py             entry point: `mhfa earnings-recap <TICKER>`
configs/models.yaml    role → {provider, model, max_tokens, temperature, …}
eval/golden/           hand-curated reference briefs (D-006)
eval/runs/             pinned eval results (factuality, BERTScore in Phase 2)
tests/                 hermetic — fake completion adapter + fake SEC dir
```

---

## Further reading

- [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) — long-form walkthrough of
  the design tensions and how each one resolved (planner shape, Vertex vs
  direct, layered routing, hand-curated golden briefs, what I'd do
  differently). Read this if you want to understand *why* the moving parts
  are shaped the way they are.
- [`DECISIONS.md`](DECISIONS.md) — D-001 through D-009, terse one-paragraph
  rationale per architectural choice. D-009 covers the Vertex Anthropic
  quota Catch-22 and the pivot to Gemini 2.5.
- [`SPRINT_PLAN.md`](SPRINT_PLAN.md) — the actual hour-by-hour MVP plan.
- [`eval/golden/`](eval/golden/) — hand-curated reference briefs used as the
  Phase 2 A/B baseline.

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
