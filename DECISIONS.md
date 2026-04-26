# Architecture Decisions

Each decision is a short, dated note: what we picked, what we passed on, and
the one or two sentences of reasoning. Mirrors the convention used in the
sister repo so the two readme as one body of work.

---

## D-001 — Tool layer: plain Python in v0.1, MCP wrapper in Phase 2

**Decision**: Tool functions are ordinary Python (`tools/sec.py`,
`tools/market_data.py`, `tools/search.py`) for the MVP. The Anthropic MCP SDK
wrap is a Phase 2 task.

**Rationale**: MCP is the long-term destination — keyword for Ant LLM algo /
Alibaba Agent JDs and lets the tool layer ship as a standalone artifact
later (`financial-data-mcp`). But day-one MCP wrapping is over-engineering
for a 16h MVP sprint and adds friction to debugging. Function signatures
were designed MCP-shaped (single arg dict in, single result dict out) so the
Phase 2 wrap is mechanical.

---

## D-002 — Layered model routing: Sonnet plan / Opus synth / Haiku judge

**Decision**: Per-role model assignments live in `configs/models.yaml`:
Sonnet 4.5 for the planner (structured tool plan), Opus 4.5 for the final
synthesizer (the user-facing artifact), Haiku 4.5 for LLM-as-judge eval
(per-claim verification, high volume).

**Rationale**: Cost vs quality. Plan-quality plateaus before Opus and the
planner fires on every run; Opus is reserved for the brief itself, where
quality is the entire product. Haiku is a 10× cost cut on the judge step
where the rubric is well-scoped and per-call work is small.

---

## D-003 — yfinance, with Alpha Vantage fallback deferred to Phase 2

**Decision**: yfinance only for v0.1. Alpha Vantage fallback in Phase 2.

**Rationale**: yfinance is unofficial and breaks every few months, but it's
zero-friction for the MVP. Per-tool error capture in the executor means a
yfinance failure logs to `_metadata.errors` and the synthesizer is told
prices are missing — better than a hard crash. Phase 2 adds the fallback
once we're past first dogfood pain.

---

## D-004 — Tavily for MVP, abstracted behind `SearchProvider` interface

**Decision**: `MHFA_SEARCH_PROVIDER` env var picks the backend; `tavily`
default, `mock` for tests/CI. Gemini grounding (GCP-native) is the planned
swap-in, slotting behind the same interface.

**Rationale**: Tavily is purpose-built for LLM search loops (returns
`{title, url, snippet}` directly). The abstract interface means we can swap
to Gemini grounding for full GCP-native cost flow without touching the agent
layer. The mock provider keeps CI hermetic — no API key needed for tests.

---

## D-005 — Markdown briefs as primary output (not HTML or PDF)

**Decision**: `mhfa earnings-recap` writes markdown.

**Rationale**: Markdown is diff-able, greppable, eval-friendly. A markdown
brief is text the factuality eval can claim-extract; an HTML/PDF brief is
not. PDF is a Phase 3 nicety if a real user asks for it.

---

## D-006 — Hand-curated golden briefs, not LLM-generated references

**Decision**: Every file in `eval/golden/` is written by a human from raw
10-Q + price + news. LLM may draft, but every number is verified by hand
before commit.

**Rationale**: Evaluator-generator collapse — if the reference set is
LLM-generated, the eval scores model-style, not factual accuracy. The
sister repo's distillation eval uses the same principle (golden answers
were Gemini 3.1 Pro outputs only because Gemini 3.1 Pro is the teacher,
not the model under test). Here Opus is the model under test, so its
outputs cannot also be the golden.

---

## D-007 — BERTScore + paired t-test for Phase 2 A/B (sister-repo continuity)

**Decision**: Phase 2's A/B harness uses BERTScore F1 against the golden set
+ paired t-test for significance, mirroring the sister repo's eval.

**Rationale**: Telling one coherent story across two repos is part of the
portfolio narrative ("end-to-end LLM stack with quantitative eval"). Same
metric on both sides also lets us claim "same eval methodology, downstream
result" rather than reasoning about two unrelated numbers.

---

## D-008 — Anthropic via Vertex AI by default

**Decision**: `ANTHROPIC_BACKEND=vertex` is the default in `.env.example`;
direct API is the opt-in fallback.

**Rationale**: Sister repo already runs in the same GCP project; routing
Claude through Vertex AI puts both projects on a single billing line and
lets the GCP credit cover the MVP development cost (~$30-55) entirely. The
direct path stays a one-env-var switch for users without GCP.

---

## Revision log

- v0.1 — D-001 through D-008 captured at MVP scaffold time.
