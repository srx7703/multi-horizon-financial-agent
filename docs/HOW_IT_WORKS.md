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

> **Update at deploy time (D-009)**: this "one-time setup cost" turned into
> a hard block. New GCP projects get auto-rejected for non-zero TPM quota
> until they show baseline usage — a Catch-22 you can't resolve from inside
> the console. Six requests across three Anthropic models in two regions
> rejected within hours each. The pivot was Gemini 2.5 on the same Vertex
> project (no Marketplace activation needed, non-zero default quota
> immediately) — see Tension 5 below for the full story. The architectural
> bet *that this would be a one-line config flip* held up exactly.

### Tension 4 — One model vs layered routing

Easiest call: just use Opus for everything. Model is consistent, no config to
maintain, no debugging "which model wrote this".

**What I picked (ex-ante design)**: three Anthropic models, role-based:

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

> **What v0.1 actually ships (D-009)**: Vertex Anthropic onboarding hit a
> quota Catch-22 (see Tension 5 below). The current `configs/models.yaml`
> assigns Gemini 2.5 Flash to planner / tool_synth / judge and Gemini 2.5
> Pro to the synthesizer — same role boundaries, different vendor. Anthropic
> Opus 4.7 stays wired into the **`synthesizer_b`** slot for the Phase 2 A/B
> harness, so the original 3-way comparison **(Gemini Pro vs Opus vs
> Multi-Horizon Gemma adapter)** is one quota approval away from running.
> The fact that the vendor pivot was a per-role `provider:` flip in YAML
> with no agent-layer code change is the strongest possible evidence that
> the layered-routing abstraction was worth the upfront design cost.

### Tension 5 — When Vertex shut the front door (post-deploy)

This one wasn't a design tension I anticipated. Three days into deployment,
trying to actually run the agent against a real ticker, every Anthropic
quota request through Vertex AI Model Garden was auto-rejected within
hours. Six requests — Opus 4.7, Sonnet 4.6, Haiku 4.5 across `us-east5` and
`global` — same canned response: "your project does not meet the criteria
for the requested quota at this time."

The criteria, as far as I could reverse-engineer, is **baseline historical
usage on the model you're requesting quota for**. New projects get zero
default TPM, so you can't accumulate usage. The only escape hatches are
either (a) a paid GCP support contract for a manual review escalation, or
(b) wait 1-3 business days for a queued review that may or may not approve.

The decision tree at that moment:

1. **Wait** → blocks the entire MVP demo. Bad.
2. **Switch to direct Anthropic API** → works, but requires my own credit
   card on the Anthropic side instead of using the GCP credit pool. Defeats
   half the reason `ANTHROPIC_BACKEND=vertex` was the default.
3. **Switch the synthesizer / planner / judge roles to Gemini on the same
   Vertex project** → Gemini 2.5 has non-zero default quota, no Marketplace
   API activation, no per-region enable dance. Same project, same billing
   line, same credit pool.

I picked (3). Total time from "first auto-rejection email" to "first NVDA
brief running on Gemini 2.5 Pro" was about 90 minutes, almost all of it
spent on the SDK migration (Anthropic's `client.messages.create` shape vs.
Google's `client.models.generate_content`) — *zero* time on the agent
layer, the workflows, the eval, the tools, or the prompt templates.

The reason that 90 minutes wasn't 9 hours: the layered routing decision
(D-002, Tension 4 above) had already established `complete_text(role, *,
user_message, system_message)` as the only entry point into a model.
Adding a `provider: gemini | anthropic` field to each role in
`configs/models.yaml` and dispatching inside `complete_text` was a
self-contained change in `src/mhfa/models/client.py`. The synthesizer,
the factuality judge, the tests — all of them stayed the same.

There was one Gemini-specific gotcha worth recording: **Gemini 2.5 Flash
has thinking-mode on by default**, and thinking tokens count against
`max_output_tokens`. The first eval run reported `0/0 verified` with no
flagged claims because the judge's claim-extraction call returned 92
characters of truncated JSON — thinking had eaten the entire 1000-token
budget before any visible text was produced. Two-line fix: add a
`thinking_budget: 0` field to the judge role in `models.yaml` and pass it
through to `types.ThinkingConfig` inside `_complete_gemini`. This is the
kind of provider-quirk that the abstraction *should* hide and now does.

This is decision **D-009**, and it's the most honest thing in this
project's design history — most portfolio repos hide the parts where
infrastructure didn't cooperate; this one made it the punchline of the
abstraction story.

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
chart_path, period, role="synthesizer")`. It hands the configured model
(Gemini 2.5 Pro for v0.1, Opus 4.7 once `synthesizer_b` lights up) a
hand-engineered prompt with one hard behavioral rule:

> **Every numeric claim** must trace to a value in raw_data. If you can't find
> a number, write "not disclosed" — do not estimate.

That single sentence is the entire reason the factuality eval works. Without
it, the model will smooth over gaps with plausible-sounding numbers (a la
"revenue grew approximately 15%") and the eval can't distinguish those from
hallucinated specifics.

The prompt also fixes the output structure (exec summary → financial table →
price action → catalysts → risks → sources) and asks for inline citations.
That structure makes the brief diff-able against the golden, and makes
claim-extraction in the eval trivially reliable.

The output is markdown. Not HTML, not PDF (decision D-005). Markdown is
diff-able, greppable, and the factuality eval can claim-extract from it. PDF
is a Phase 3 nicety if a real user asks for it.

The `role` parameter is what makes the Phase 2 A/B harness mechanical:
calling `synthesize_brief(..., role="synthesizer_b")` runs the same prompt
against whatever model that role points to in `configs/models.yaml`, with
no other code changes. Same prompt, same `raw_data`, same chart, same
output schema → two briefs, paired-t against the golden set.

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
write properly. v0.1 ships with **5 hand-curated goldens — NVDA, AAPL,
MSFT, META, JPM** — chosen to span sectors (semiconductors, consumer
electronics, software/cloud, social media + capex, banking) so the
prompt's robustness gets tested across very different filing shapes.
Budget will go up in Phase 2 when the A/B harness needs statistical
power (paired-t F1 difference at α=0.05 wants ~20+ tickers).

---

## Empirical results (v0.1)

The MVP's load-bearing claim is "factuality eval works and the synthesizer
respects the no-estimate rule." Both are testable. Here's what 5 real runs
on 2026-04-26 actually produced:

| Ticker | Sector | Brief score | Claims verified | Brief artifact |
|---|---|---|---|---|
| NVDA | Semiconductors | 1.00 | 17 / 17 | `outputs/NVDA_20260426_brief.md` |
| AAPL | Consumer Electronics | 0.958 | 23 / 24 | `outputs/AAPL_20260426_brief.md` |
| MSFT | Software / Cloud | 0.944 | 17 / 18 | `outputs/MSFT_20260426_brief.md` |
| META | Internet Content | 1.00 | 11 / 11 | `outputs/META_20260426_brief.md` |
| JPM | Banks | 0.955 | 21 / 22 | `outputs/JPM_20260426_brief.md` |

Mean 0.971 across 5 tickers. Two perfect runs (NVDA, META), three single-flag
runs whose flagged claims are listed in the per-run JSON.

(Per-run JSON pinned in `eval/runs/<TICKER>_20260426_factuality.json`.)

### What the eval actually catches

The 3 flagged claims, one per imperfect run, fall into two distinct shapes:

- **MSFT — `EPS (diluted) +59.8%` → `contradicted`.** The brief rounded
  $5.16 / $3.23 to +59.8%; the judge computed 59.75% and refused to call
  it verified. This is rounding-precision strict mode. Useful as a signal
  if you care about exact reproducibility; arguably noise if you don't.
- **AAPL — `5 hits` → `not_found`** and **JPM — `+16.0%` → `not_found`.**
  Both cases are the *extract* step grabbing a number out of context
  (AAPL's footer "Web search: 5 hits" metadata; JPM's bare "+16.0%"
  detached from its EPS-YoY anchor). The verify step then can't trace it,
  correctly. These are eval-pipeline noise more than synthesizer errors —
  a tighter extract prompt would suppress them, at the risk of also
  suppressing real misses.

The interesting result is what the eval **didn't** catch on JPM. The
Gemini 2.5 Pro brief writes: *"reported Q1 2026 net income of $16.5
billion and EPS of $5.94, a strong year-over-year increase from $14.6
billion and $5.07, respectively"*. The "$14.6B / $5.07 baseline" is the
**Q3 2025** net income / EPS — not the Q1 2025 baseline the sentence
implies. Q1 2025 isn't in the indexed corpus at all. The judge verifies
each fragment individually ($16.5B ✓, $5.94 ✓, $14.6B ✓, $5.07 ✓) and
moves on; it doesn't reason about whether the sentence's *temporal
pairing* is honest. That's the bridge to the next section — and the
reason the JPM golden's curation notes flag this case explicitly, so the
limitation is documented even when the score doesn't expose it.

### What the eval *can't* catch (yet)

The factuality eval verifies that **claims trace to evidence**. It does
not verify:

- **Temporal correctness** beyond per-claim — combining two
  individually-true facts into a misleading sentence still scores well.
  (The JPM Q1-2026-vs-Q3-2025 mis-pairing is exactly this; both numbers
  resolve to `verified`, the score doesn't drop, and only golden
  curation surfaced it.)
- **Salience** — a brief that lists every value from `raw_data` would
  score 1.00 but be useless. The Phase 2 BERTScore eval against the
  hand-curated golden is what catches under- and over-comprehensiveness.
- **Calibrated uncertainty** — "approximately $51B" vs "$51.0B" both
  verify to the source, but only one is honestly hedged. This is a
  prompt-engineering concern more than an eval concern.

The right framing is: factuality is the **floor** (no hallucinated
numbers), BERTScore-vs-golden is the **shape** (same coverage as a human
would write), comprehensiveness rubric is the **value** (analyst would
pay for this). v0.1 nails the floor; v0.5's A/B harness adds the shape;
the rubric ships with v1.0.

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
4. **Trust the abstraction earlier.** When the Vertex Anthropic quota
   rejection emails started arriving, my first instinct was to fight the
   quota system (escalation tickets, region-shopping, model-shopping). It
   took an hour of that to remember the whole reason `provider:` was a
   per-role config field was *exactly this case*. Pivoting to Gemini took
   90 minutes once I let the abstraction do its job. Lesson: when an
   external constraint hits, ask "does the architecture already handle
   this?" before asking "how do I fight the constraint?"

---

## Where to read next

- [`README.md`](../README.md) — the elevator pitch + Quickstart
- [`DECISIONS.md`](../DECISIONS.md) — D-001 through D-009, terse rationale per
  architectural choice. D-009 is the most recent and covers the Vertex →
  Gemini pivot.
- [`ROADMAP.md`](../ROADMAP.md) — what v0.5 and v1.0 add
- [`SPRINT_PLAN.md`](../SPRINT_PLAN.md) — the actual hour-by-hour MVP plan
- [`eval/golden/`](../eval/golden/) — the 5 hand-curated reference briefs
- [`eval/runs/`](../eval/runs/) — pinned factuality scores per ticker
- [`eval/golden/NVDA_20260426.md`](../eval/golden/NVDA_20260426.md) — the
  first hand-curated reference brief, with curation notes for the eval
- Sister repo: [`multi-horizon-financial-llm`](https://github.com/srx7703/multi-horizon-financial-llm)
