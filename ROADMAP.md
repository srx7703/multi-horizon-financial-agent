# Multi-Horizon Financial Agent — Roadmap

> Tool-using financial research agent that integrates a domain-tuned LLM
> (Multi-Horizon LoRA adapter, sister project) as a specialist synthesis backend.
>
> **North Star**: Replace 30 minutes of manual quarterly-earnings research with a
> 2-minute agent run that produces a citation-grounded, chart-illustrated brief
> good enough to act on.

---

## 0. Why this project

Two sentences for the resume:

1. The base agent (Claude tool-use loop over SEC EDGAR / market data / web search) is the
   "wide" half: orchestration, planning, multi-source synthesis.
2. The Multi-Horizon LoRA adapter (gemma-financial-distillation, sibling repo) is the
   "deep" half: a 27B/31B model fine-tuned on 1,060 SEC-derived QA pairs, plugged into
   the agent's synthesis step as one of the swappable backends.

Together they form an **end-to-end financial LLM stack with quantitative eval**:
fine-tuning + tool use + LLM-as-judge factuality + paired-t-test A/B between
backends. This is the differentiation that a single agent project or a single
fine-tune project cannot offer alone.

---

## 1. Architecture (target — full version)

```
                          ┌─────────────────────────────────┐
                          │         User / Watchlist         │
                          │   (CLI · Streamlit · cron job)   │
                          └────────────────┬────────────────┘
                                           │ query + UserContext
                                           ▼
                          ┌─────────────────────────────────┐
                          │            Planner               │
                          │  (Claude Sonnet · structured)    │
                          │  query → ordered tool plan       │
                          └────────────────┬────────────────┘
                                           │ plan
                                           ▼
                          ┌─────────────────────────────────┐
                          │            Executor              │
                          │   parallel + sequential calls    │
                          └────────────────┬────────────────┘
                                           │ tool calls
        ┌─────────────────┬────────────────┼────────────────┬─────────────────┐
        ▼                 ▼                ▼                ▼                 ▼
  ┌──────────┐     ┌──────────┐    ┌──────────┐     ┌──────────┐     ┌──────────┐
  │   SEC    │     │  Market  │    │  Macro   │     │   Web    │     │ Earnings │
  │  EDGAR   │     │ (yfin)   │    │  (FRED)  │     │ (Tavily) │     │   Call   │
  └──────────┘     └──────────┘    └──────────┘     └──────────┘     └──────────┘
        │                 │                │                │                 │
        └─────────────────┴────────────────┼────────────────┴─────────────────┘
                                           │ raw evidence
                                           ▼
                          ┌─────────────────────────────────┐
                          │          Synthesizer             │
                          │   ┌─────────────────────────┐    │
                          │   │  routing.yaml decides:  │    │
                          │   │   · Opus 4.7 (default)  │    │
                          │   │   · MH adapter (A/B)    │    │
                          │   └─────────────────────────┘    │
                          └────────────────┬────────────────┘
                                           │ markdown brief + chart
                                           ▼
                          ┌─────────────────────────────────┐
                          │          Eval Harness            │
                          │  factuality (LLM-as-judge)       │
                          │  comprehensiveness rubric        │
                          │  paired t-test (A vs B)          │
                          └─────────────────────────────────┘
```

### Layered model routing

A single `configs/models.yaml` controls which model handles each step. This makes
the cost / quality tradeoff explicit and lets us run ablations without code changes.

| Step | Default model | Rationale |
|---|---|---|
| Planner | `claude-sonnet-4-6` | Structured output, low latency, plan quality plateaus before Opus |
| Per-tool synthesis (per-doc summary) | `claude-sonnet-4-6` | Mid-loop, frequency-bound, Sonnet is cost/quality sweet spot |
| **Final brief synthesis** | `claude-opus-4-7` | User-facing artifact; quality drives the entire project's value |
| LLM-as-judge / factuality | `claude-haiku-4-5` | High-volume eval calls; cost-sensitive; rubric is well-scoped |
| **Synthesis A/B variant** | `multi-horizon-gemma-adapter` | Phase 2 specialist backend |

### Tool layer = MCP server

The tool layer is built as an **MCP server from day one**. This is a deliberate
architectural choice with two compounding benefits:

1. Hits the keyword "MCP/类协议/Skills" in target JDs (Ant Group LLM algo role,
   Alibaba Agent PM role).
2. The MCP server can be split out as a standalone open-source artifact later
   (`financial-data-mcp`), giving us two GitHub portfolio pieces from one
   build.

---

## 2. Phases & success criteria

### Phase MVP (v0.1) — One weekend, ~12–16h

**Scope**: end-to-end `earnings_recap` workflow on a single ticker.
**Done when**:
- Run `mhfa earnings-recap NVDA` and get a markdown brief + price chart in <2 min
- 5 golden briefs in `eval/golden/` (hand-curated)
- Factuality eval implemented and a baseline number recorded
- `README.md`, `ARCHITECTURE.md`, `DECISIONS.md` v0.1 committed
- CI green (lint + 1 unit test per tool)
- v0.1 release tag pushed
**Out of scope**: Multi-Horizon adapter integration, multiple workflows, web UI,
                  cost-aware routing, retry/backoff, observability

### Phase 2 (v0.5) — 2–3 weeks part-time, ~30h

**Scope**: Multi-Horizon adapter integration + true A/B eval + 2 more workflows.
**Done when**:
- Multi-Horizon Gemma adapter served as a callable model (vLLM endpoint or HF
  Inference Endpoint), registered in `configs/models.yaml` as `mh-gemma-adapter`
- `eval/ab_harness.py` runs the same query through both Opus and the adapter,
  produces a paired diff with statistical test (BERTScore vs golden + paired
  t-test, mirroring Multi-Horizon methodology)
- `EVAL.md` documents methodology + the headline number
  (e.g., "MH adapter wins 12/20 vs Opus on financial brief task; paired t = 2.3,
  p = 0.03")
- Two additional workflows live: `ma_drilldown`, `sector_compare`
- `UserContext` plumbing: `portfolio.yaml` + `watchlist.yaml` are read and
  injected into planner prompts when present
- v0.5 release tag pushed
**Out of scope**: Web UI, public deployment, blog post, observability dashboard

### Phase 3 (v1.0) — 4 weeks part-time, ~40h

**Scope**: Productize and distribute.
**Done when**:
- Streamlit web UI with streaming output; `docker compose up` self-host
- Watchlist mode: cron-triggered daily brief on user portfolio
- 8+ tools live (FRED macro, earnings call transcripts, insider trading)
- Cost-aware router: query complexity → Sonnet vs Opus auto-selection, with
  per-call cost logging
- Trace observability: SQLite log of every plan + tool call + cost
- Public release: HN Show HN, X demo video, blog post on knowledge base of
  choice
- v1.0 release tag pushed

---

## 3. Detailed task breakdown — Phase 2 & 3

### Phase 2 — Multi-Horizon integration

- [ ] Decide adapter serving path:
  - Option A: vLLM on a small GPU instance (~$30–40/mo, full control, more setup)
  - Option B: HF Inference Endpoint (~$0.50/h while live, simpler, less control)
  - **Recommendation**: A for the project narrative ("self-hosted serving"), B
    for first integration test
- [ ] Add `synthesis_model` field to `models.yaml`, plumb through
      `synthesizer.py`
- [ ] Add `MultiHorizonClient` adapter class with same interface as `AnthropicClient`
- [ ] Build `eval/ab_harness.py`:
  - Inputs: list of tickers, two model configs (A, B)
  - For each ticker: run pipeline twice (only synthesis step varies)
  - Score each brief against `eval/golden/<ticker>.md` with BERTScore
  - Aggregate: paired t-test, win rate, 95% CI on score difference
  - Output: `eval/runs/run_phase2_v0.5.json` + a markdown report
- [ ] Run on N=20 tickers, document headline result in `EVAL.md`
- [ ] Add new workflow: `ma_drilldown(acquirer, target)` — pulls 8-K, news,
      sector comparables
- [ ] Add new workflow: `sector_compare(tickers)` — multi-ticker side-by-side
- [ ] Implement `UserContext` reader (`portfolio.yaml`, `watchlist.yaml`,
      `preferences.yaml`)
- [ ] Inject UserContext into planner system prompt when present
- [ ] Update README with the headline A/B number + sister-repo cross-link

### Phase 3 — Productization

- [ ] Streamlit UI — single page, ticker input, live streaming brief, chart
      embed, cost display
- [ ] `docker-compose.yml` self-host setup (Streamlit + MCP server + .env
      template)
- [ ] Watchlist cron: nightly job runs each ticker in `portfolio.yaml`, writes
      to `~/.mhfa/briefs/YYYY-MM-DD/<ticker>.md`
- [ ] Optional Telegram bot integration (push briefs to chat)
- [ ] Cost router (`agent/router.py`): query complexity heuristic →
      Sonnet/Opus selection
- [ ] Trace store (`agent/trace.py`): SQLite of `(run_id, query, plan, tool
      calls, tokens, cost, output)`
- [ ] Observability page in Streamlit: list runs, click to see full trace
- [ ] More tools: FRED, earnings transcripts (Tavily-scraped), insider trading
      (SEC Form 4)
- [ ] Public artifacts:
  - HN Show HN post
  - 30-second X / 小红书 demo video
  - Blog post: "Domain-tuned LLM + Agent Orchestration: A Case Study in
    Financial Research" (English on Medium, Chinese on Zhihu)
- [ ] Update README with traction (stars, HN points, clones)

---

## 4. Architecture decisions (DECISIONS.md template)

Each significant decision goes here as a 4-line entry. Mirroring the
Multi-Horizon convention. Examples we already know we'll add:

- **D-001**: Tool layer built as MCP server, not bare Python functions
- **D-002**: Sonnet for planner, Opus for synthesizer, Haiku for judge
  (cost vs quality)
- **D-003**: yfinance with Alpha Vantage fallback (yfinance is unofficial and
  breaks)
- **D-004**: Tavily for MVP, abstract `SearchProvider` interface so we can swap
  to Gemini grounding (GCP-native) later
- **D-005**: Markdown briefs (not HTML/PDF) as primary output — diff-able,
  greppable, eval-friendly
- **D-006**: Hand-curated golden briefs as eval reference, not LLM-generated
  references (avoid evaluator-generator collapse)
- **D-007**: BERTScore + paired t-test as primary A/B metric (consistent with
  Multi-Horizon methodology)

---

## 5. Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Ecosystem, MCP SDK |
| Package manager | `uv` | Fast, reproducible |
| Lint/format | `ruff` | One tool, fast |
| Type-check | `mypy --strict` on `src/` | Pays off in agent code |
| Test | `pytest` + `pytest-mock` | Standard |
| LLM SDK | `anthropic` (direct or via Vertex AI) | First-class tool use |
| MCP | `mcp` Python SDK | Official |
| SEC | `sec-edgar-downloader` + custom parser | Already known from MH |
| Market data | `yfinance` w/ `alpha_vantage` fallback | Free for MVP |
| Web search | `tavily-python` w/ Gemini grounding fallback | LLM-shaped output |
| Charts | `matplotlib` (PNG, embedded into markdown) | Simple, reliable |
| Eval | `bert-score` + `scipy.stats` | MH methodology continuity |
| UI (Phase 3) | `streamlit` | Fastest Python UI |
| Trace | SQLite | Zero-ops, queryable |
| Hosting | local + `docker compose` | Self-host pitch |

---

## 6. Budget — full project lifecycle

All estimates in USD. Anthropic costs assume Claude API direct or Vertex AI
(GCP credit-eligible) at posted Sonnet 4.6 / Opus 4.7 / Haiku 4.5 rates as of
project start.

| Phase | Item | Cost |
|---|---|---|
| MVP development | API testing (~50 brief runs) | $30–50 |
| MVP development | Tavily (free tier, 1000 q/mo) | $0 |
| MVP development | GCP storage/logs | <$5 |
| **MVP total** | | **$30–55** |
| Phase 2 dev | API (eval runs, A/B harness) | $80–150 |
| Phase 2 dev | Adapter serving (vLLM small GPU, 4–6 wk) | $30–60 |
| Phase 2 dev | Tavily Pro (if testing volume up) | $0–30 |
| **Phase 2 total** | | **$110–240** |
| Phase 3 dev | API (UI testing, watchlist runs) | $80–120 |
| Phase 3 dev | Hosting (Cloud Run / similar) | $5–15 |
| **Phase 3 total** | | **$85–135** |
| **Build-phase total** | | **$225–430** |
| Steady state | Self dogfood (7–10 briefs/wk) | $15–30/mo |
| Steady state | Tavily | $0–30/mo |
| Steady state | Adapter hosting (if kept) | $30–40/mo |
| **Steady state total** | | **$45–100/mo** |

**Per-brief cost target (steady state, layered model config)**: $0.30–0.70.

GCP credit eligibility: Anthropic models can be invoked via **Vertex AI** with
GCP billing, so the build-phase total is largely cover-able by GCP credit if
the credit pool is $500+.

---

## 7. Eval methodology

Continuity with Multi-Horizon is intentional — same evaluator stack means the
two repos tell one coherent story.

### Reference set
- 20 hand-curated golden briefs in `eval/golden/`
- Diverse: 5 mega-cap tech, 5 financials, 5 industrials/energy, 5 small-cap
- Each golden brief: written by hand from raw 10-Q + price + news (~30 min each)
- Quarterly refresh

### Metrics
- **Factuality (LLM-as-judge)**: Haiku 4.5 traces every numeric claim in the
  brief back to the raw tool output. Score = (verified claims) / (total claims).
- **Comprehensiveness (rubric, 0–3)**: covers financials, risk, recent
  catalysts, forward outlook, valuation context.
- **BERTScore F1**: against golden brief, RoBERTa-large embedding (same as
  Multi-Horizon).

### A/B test (Phase 2)
- Same 20 tickers run through both backends.
- Paired t-test on BERTScore F1 difference.
- Win rate (binary) with 95% CI via bootstrap.
- Report headline as `Adapter wins X/20, paired t=Y, p=Z`.

### Acceptance bars
- MVP: factuality ≥ 0.85 on the 5 golden briefs.
- Phase 2: A/B test produces interpretable result (regardless of direction —
  even "no significant difference" is publishable, with the right framing).

---

## 8. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| yfinance silently breaks | High | Medium | Alpha Vantage fallback; integration test in CI |
| SEC EDGAR rate-limits | Medium | Medium | Cache + correct User-Agent header; obey 10 req/s |
| LLM hallucinates numbers in brief | Medium | High | Factuality eval as gate; cite sources inline |
| Tavily query budget hit | Low | Low | Free tier 1k/mo is plenty; abstract interface |
| Adapter serving instability (Phase 2) | Medium | Medium | HF Inference Endpoint as fallback path |
| Project drifts off-scope | High | High | This roadmap; weekly self-review against phase exit criteria |
| GCP credit exhaustion | Low | High | Per-call cost logging from MVP day 1; budget alarm |

---

## 9. Resume / portfolio narrative once complete

(Drafted now to keep the build aimed at the right outcome.)

> **Multi-Horizon Financial LLM Stack** — End-to-end domain-LLM system spanning
> fine-tuning, agent orchestration, and quantitative evaluation. Trained a Gemma
> LoRA adapter on 1,060 SEC-derived QA pairs (BERTScore +5.76% over base, paired
> t = 10.42). Built a tool-using agent (planner + executor + synthesizer over
> SEC EDGAR / market data / web search / FRED) and integrated the adapter as a
> swappable synthesis backend. Established A/B eval harness with paired t-test
> on golden-brief reference set; published methodology and results. Open-source,
> CI-tested, MCP-compliant tool layer.
>
> Repos: [multi-horizon-financial-llm], [multi-horizon-financial-agent].
> HF: Srx7703/gemma-{2-27b,4-31b}-financial-adapter.

This single paragraph hits: **fine-tuning · agents · MCP · eval · open source ·
end-to-end**. That covers the keyword surface for Ant LLM algo, Alibaba Agent
PM, and Pinduoduo foundation algo simultaneously.

---

## 10. Out of scope (explicit)

To avoid drift, these are deliberately excluded:

- Trading execution. This is a research tool, not an execution platform.
- Multi-asset (crypto, fixed income, FX). US equities only.
- Multilingual briefs. English only for MVP, Chinese summaries can be a
  Phase-3 nicety.
- Multi-user auth. Self-host model.
- Realtime streaming data. Daily granularity is sufficient.
- Agent self-improvement / RLHF on agent traces. Out of scope; a separate
  project.

---

## 11. Sister project

`multi-horizon-financial-llm` (formerly `multi-horizon-financial-agent`) is the
fine-tuning + RAG sibling. The two repos cross-reference in their READMEs:

- This repo's README: "The synthesis backend can route to a domain-tuned
  Gemma adapter; see [sister repo] for training methodology."
- Sister repo's README: "Adapters are served as backends to the agent in
  [this repo]; see EVAL.md there for downstream A/B results."

This bidirectional link is part of the portfolio story.

---

## Revision log

- v0.1 — initial roadmap committed
