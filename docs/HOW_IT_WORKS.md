# How `multi-horizon-financial-agent` Works

> A long-form walkthrough of the design, written for someone who's read the
> README and wants to understand *why* the moving parts are shaped the way
> they are. If you've ever wondered "what would I actually build if I had to
> ship a tool-using LLM agent on a weekend, alone, for a portfolio?" — this is
> the postmortem.

---

## What problem this solves

A working equity-research analyst spends maybe 30-45 minutes per ticker doing
the same recap before each earnings cycle: pull the latest 10-Q, scan recent
8-Ks for catalysts, look at the price chart, skim sell-side commentary, write
half a page. The work is mechanical but the *output* is high-context — it has
to cite real numbers, distinguish a real risk from boilerplate, and not
hallucinate revenue.

That's a textbook tool-using agent task: cheap to define, hard to do well, and
the failure mode (hallucinated numbers in a portfolio brief) is concrete enough
that you can build a real eval for it.

`mhfa earnings-recap NVDA` produces this brief in **<2 minutes** end-to-end.

---

## Design tensions, and how each one resolved

### Tension 1 — "Real agent" vs "weekend MVP"

A "true" agent dynamically plans tool calls turn-by-turn, reflects on
intermediate results, sometimes loops. That's cool. It's also a lot of
plumbing that doesn't help a v0.1 ship.

**What I picked**: the planner returns a *fixed* `ToolCall` list for the
`earnings_recap` workflow. Same five tools every time, in a stable order. Zero
LLM calls in the planning step.

**Why**: for a single-workflow MVP, dynamic planning has no upside — there's
nothing to *decide*. The cost is paid in latency (one extra round-trip) and a
new failure mode (planner emits invalid tool name). Phase 2's `ma_drilldown`
and `sector_compare` workflows will need real planning; that's when the planner
upgrades.

**Cost of this shortcut**: the planner module is essentially a switch
statement today. Architecturally it's still a layer, so swapping in real
planning later is a function-body replacement, not a refactor.

### Tension 2 — Tools as MCP servers vs plain Python

The "right" answer in 2026 is MCP — Anthropic's standard for tool exposure,
hot-pluggable across agent runtimes, lets the tool layer ship as a standalone
artifact (`financial-data-mcp` would be a perfectly nice repo on its own).

**What I picked**: plain Python in v0.1. MCP wrap is Phase 2.

**Why**: MCP costs ~3-4 hours of plumbing per tool to expose properly (server
boilerplate, schema definitions, transport setup), plus debugging is harder
when the tool runs out of process. For a 16h sprint, that math doesn't work.

**The mitigation**: every tool function is shaped *as if* it were going to be
MCP-wrapped — single arg dict in, single result dict out, no globals, no
dependencies on agent-side state. The wrap is mechanical when the time comes.

This is documented as decision **D-001**.

### Tension 3 — Vertex AI vs direct Anthropic API

The Anthropic SDK supports both. Direct API is simpler (one env var, no GCP
auth dance). Vertex routes through your GCP project, which means billing comes
out of your GCP credit pool.

**What I picked**: Vertex by default, direct as opt-in.

**Why specific to this project**: the sister repo
(`multi-horizon-financial-llm`) trains LoRA adapters on TPU v6e-8. That's
already a GCP project with credits, an active billing line, and ADC
configured. Routing this project's Anthropic calls through the same project
puts both repos on a single billing line and lets the credit cover MVP
development (~$30-55) entirely.

**The cost**: Vertex requires per-region per-model "Enable" in Model Garden
*plus* a quota request (defaults are zero TPM). For the user (me), that's a
one-time setup cost; for the project, it's a real friction point in the
Quickstart that I document explicitly. `ANTHROPIC_BACKEND=direct` is one env
var away if anyone wants to skip it.

This is decision **D-008**.

### Tension 4 — One model vs layered routing

Easiest call: just use Opus for everything. Model is consistent, no config to
maintain, no debugging "which model wrote this".

**What I picked**: three models, role-based:

| Role | Model | Why |
|---|---|---|
| Planner | Sonnet 4.6 | Plan quality plateaus before Opus. Plans are structured tool dispatch, not creative writing. |
| Synthesizer | Opus 4.7 | This is the user-facing artifact. Quality drives the entire project value. Worth the cost. |
| Judge (factuality eval) | Haiku 4.5 | Per-claim verification is well-scoped + high-volume. 10× cost cut over Opus, with negligible quality drop on a binary "did this number appear in the source?" task. |

**Why it matters for the portfolio narrative**: this is a real ablation
question (cost vs quality at each step), and one config file controls all
three knobs. Phase 2's A/B harness will swap `synthesizer` for the Multi-Horizon
Gemma adapter without touching agent code at all. That's the single config
file pulling triple duty: production routing, ablation experimentation, and
adapter A/B.

This is decision **D-002**.

---

## The tool layer in more detail

### `tools/sec.py` — SEC EDGAR

The tempting first version of this is "fetch from EDGAR + parse the HTML."
That's 60-90 minutes of work (sec-edgar-downloader, BeautifulSoup, the Item 1A
section detection, error handling for filings that change format).

I skipped all of it. The sister repo
(`multi-horizon-financial-llm`) already produced **381 pre-distilled JSON
summaries** (69 S&P 500 tickers × 10-K + 10-Q + 8-K, schemas stable). Each one
is a structured object: `revenue`, `net_income`, `key_metrics`, `qoq_changes`,
`new_risks`, `management_tone`, `analyst_note`.

So `tools/sec.py` reads from `MHFA_LOCAL_SEC_DIR` instead of the network.
Setup time: 5 minutes. Schema is stable and richer than what live EDGAR parsing
would have given me. The sister repo paid the data-engineering cost; this repo
collects the dividend.

Phase 2 will add a live fallback so the tool also works without the sister
repo's data dump. For now, the supported tickers are the 69 in
`mhfa list-tickers`.

### `tools/market_data.py` — yfinance + matplotlib

yfinance is unofficial — it's a screen-scraper of Yahoo Finance, and it breaks
every few months when Yahoo changes layout. Two design choices fall out of
that:

1. **Per-tool error capture in the executor**: a yfinance failure is a normal
   case, not an exceptional one. The executor catches the exception, writes
   it to `_metadata.errors`, and the synthesizer is told "prices are missing,
   work without them." The brief degrades gracefully instead of crashing.
2. **Pre-computed summary statistics**: rather than handing the raw OHLCV to
   the LLM and asking it to compute % change, I do `(end_close /
   start_close - 1) * 100` in Python and pass the pre-computed number. LLMs
   are bad at floating-point math; Python isn't.

Phase 2 adds Alpha Vantage as a fallback (decision D-003).

### `tools/search.py` — Tavily, abstracted

Tavily is a search API purpose-built for LLM loops — it returns
`{title, url, snippet, score}` directly, no HTML scraping needed.

The interesting choice is the `SearchProvider` Protocol abstraction:

```python
class SearchProvider(Protocol):
    def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]: ...

class TavilyProvider: ...
class MockProvider: ...

def get_search_provider() -> SearchProvider:
    name = os.getenv("MHFA_SEARCH_PROVIDER", "tavily")
    ...
```

Two payoffs from this 30-line abstraction:

1. **Hermetic CI**: `MHFA_SEARCH_PROVIDER=mock` means tests run with no API
   key, no network. CI never flakes on Tavily downtime.
2. **Phase 2 swap**: Gemini grounding is the planned production backend (it's
   a Vertex-native search-augmented endpoint, so it stays in the same billing
   pool as everything else). The agent layer doesn't change — only `search.py`
   gets a new provider class.

Decision D-004.

---

## The synthesizer is the entire product

The synthesizer is a single function: `synthesize_brief(ticker, raw_data,
chart_path, period)`. It hands Opus a hand-engineered prompt with one hard
behavioral rule:

> **Every numeric claim** must trace to a value in raw_data. If you can't find
> a number, write "not disclosed" — do not estimate.

That single sentence is the entire reason the factuality eval works. Without
it, Opus will smooth over gaps with plausible-sounding numbers (a la "revenue
grew approximately 15%") and the eval can't distinguish those from hallucinated
specifics.

The prompt also fixes the output structure (exec summary → financial table →
price action → catalysts → risks → sources) and asks for inline citations.
That structure makes the brief diff-able against the golden, and makes
claim-extraction in the eval trivially reliable.

The output is markdown. Not HTML, not PDF (decision D-005). Markdown is
diff-able, greppable, and the factuality eval can claim-extract from it. PDF
is a Phase 3 nicety if a real user asks for it.

---

## Why hand-curated golden briefs

The factuality eval scores `verified_claims / total_claims` against
`raw_data`. The "golden" briefs in `eval/golden/` aren't used for that
particular score — they're used in the Phase 2 BERTScore + paired-t A/B
between Opus and the Multi-Horizon Gemma adapter (continuous methodology with
the sister repo).

The temptation: have Opus write the golden, then verify by hand. Saves time.
Don't do this.

**Why it's wrong**: if the golden is LLM-generated, the eval scores
*model-style*, not factual accuracy. Two models that share the same training
distribution will look agreeably similar to each other; two models from
different families won't, even if both are factually correct. This is the
"evaluator-generator collapse" failure mode.

The sister repo's distillation eval uses the same principle — its golden
answers are Gemini 3.1 Pro outputs, but only because Gemini 3.1 Pro is the
*teacher* in distillation, not the model under test.

Here, Opus is the model under test, so its outputs cannot also be the golden.
Every number in `eval/golden/` was verified by hand against the raw 10-Q + 8-K
+ market data before commit. Decision **D-006**.

The first golden, `eval/golden/NVDA_20260426.md`, takes about 25 minutes to
write properly. Sprint plan budgets 3 of these for v0.1 (NVDA, AAPL, MSFT).
Budget will go up in Phase 2 when the A/B harness needs statistical power.

---

## Two storylines this project is telling

This repo doesn't stand alone — it's the second half of a portfolio narrative
that started with `multi-horizon-financial-llm`. Reading both, the story is:

> *"Take a domain (financial filings), train a domain-specific Gemma adapter
> with PEFT on TPU. Then build the agent that consumes its outputs, with a
> factuality eval that lets you A/B the adapter against frontier closed
> models. Same eval methodology, end-to-end ownership of the stack."*

Concretely:

| Sister repo proves | This repo proves |
|---|---|
| Can train + evaluate a domain LLM (LoRA, BERTScore, paired-t) | Can wire frontier LLMs into a tool-using agent with a domain-relevant eval |
| Can run TPU infrastructure (XLA, sharding, distributed training) | Can run multi-model production infrastructure (Vertex routing, role-based config, fail-soft execution) |
| Domain knowledge: SEC filings, earnings cycle | Domain knowledge: equity research workflow, citation discipline |

The Phase 2 A/B harness is where the two stories collide — it directly
compares the trained adapter against Opus on the *same* generation task
(briefs from raw evidence) using the *same* metric (BERTScore F1 against the
hand-curated golden, paired-t for significance). That comparison is the punch
line.

---

## What I'd do differently

A few things I'd change if I were starting over:

1. **Build the golden set before the synthesizer prompt.** I tuned the
   prompt before writing any reference briefs, so my "this looks good"
   intuition was doing all the work. Writing one golden first would have
   surfaced structural decisions earlier (e.g., the "not disclosed" rule
   came from realizing the model kept inventing operating cash flow numbers
   that aren't in the 10-Q summary).
2. **Wire the eval before the second workflow.** Phase 2 plans to add
   `ma_drilldown` and `sector_compare` workflows alongside the eval.
   Building eval first would constrain the second workflow's design (forces
   the same evidence-discipline pattern), which is exactly what you want.
3. **Cost telemetry from day one.** Right now I know the per-call cost from
   napkin math (~$0.30/brief). Logging actual token counts to
   `_metadata.cost_usd` would have made cost-vs-quality ablation trivial
   later. This is queued for v0.5.

---

## Where to read next

- [`README.md`](../README.md) — the elevator pitch + Quickstart
- [`DECISIONS.md`](../DECISIONS.md) — D-001 through D-008, terse rationale per
  architectural choice
- [`ROADMAP.md`](../ROADMAP.md) — what v0.5 and v1.0 add
- [`SPRINT_PLAN.md`](../SPRINT_PLAN.md) — the actual hour-by-hour MVP plan
- [`eval/golden/NVDA_20260426.md`](../eval/golden/NVDA_20260426.md) — the
  first hand-curated reference brief, with curation notes for the eval
- Sister repo: [`multi-horizon-financial-llm`](https://github.com/srx7703/multi-horizon-financial-llm)
