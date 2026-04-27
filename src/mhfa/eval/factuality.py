"""Factuality eval — LLM-as-judge that checks every numeric claim in a brief
traces back to ``raw_data``. Phase 1 / Hour 14 in the sprint plan.

Two passes (kept simple — judge tier is the cheapest tier in models.yaml):

1. **Extract** the list of numeric / factual claims from the brief.
2. **Verify** each claim against raw_data. Outcome ∈ {verified, not_found, contradicted}.

Aggregate score = verified / total. The flagged list is the actionable part —
that's what tells you whether the synthesizer needs a tighter prompt or
whether a tool failed silently.

Provider-agnostic via :func:`mhfa.models.client.complete_text` — works the same
whether judge is Gemini Flash (v0.1 default) or Haiku (Phase 2 swap).
"""
from __future__ import annotations

import json
from typing import Any

from mhfa.models.client import complete_text

_EXTRACT_SYSTEM = (
    "You are a careful factuality auditor. Output ONLY a JSON array of strings. "
    "No prose, no markdown fences."
)

_EXTRACT_PROMPT = """List every claim in the brief below that is a NUMERIC FACT
(money, percentage, ratio, count, share price, growth rate) or a SPECIFIC
NAMED EVENT (acquisition, leadership change, guidance update).

Each claim must be self-contained, quoted close to the brief's wording.

Brief:
{brief}

Return ONLY a JSON array of strings."""

_VERIFY_SYSTEM = (
    "You are a careful factuality auditor. Output ONLY a JSON object with "
    "fields 'verdict' and 'reason'. No prose, no markdown fences."
)

_VERIFY_PROMPT = """Decide if the claim below is **verified** (the exact
value/event appears in the source data), **contradicted** (sources show a
different value/event), or **not_found** (sources don't mention this claim).

Return JSON: {{"verdict": "verified"|"contradicted"|"not_found", "reason": "<one sentence>"}}.

Claim:
{claim}

Sources:
```json
{raw_json}
```
"""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # ```json or just ```
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _extract_claims(brief: str, role: str = "judge") -> list[str]:
    text = complete_text(
        role,
        system_message=_EXTRACT_SYSTEM,
        user_message=_EXTRACT_PROMPT.format(brief=brief),
        response_format="json",
    )
    text = _strip_code_fence(text)
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [str(x) for x in items if isinstance(x, str)]


def _verify_one(
    claim: str, raw_data: dict[str, Any], role: str = "judge"
) -> dict[str, str]:
    raw_for_judge = {k: v for k, v in raw_data.items() if k != "_metadata"}
    text = complete_text(
        role,
        system_message=_VERIFY_SYSTEM,
        user_message=_VERIFY_PROMPT.format(
            claim=claim,
            raw_json=json.dumps(raw_for_judge, default=str)[:30000],
        ),
        response_format="json",
    )
    text = _strip_code_fence(text)
    try:
        verdict_obj = json.loads(text)
        return {
            "verdict": str(verdict_obj.get("verdict", "not_found")),
            "reason": str(verdict_obj.get("reason", "")),
        }
    except json.JSONDecodeError:
        return {"verdict": "not_found", "reason": "judge returned non-JSON"}


def check_factuality(
    brief: str, raw_data: dict[str, Any], *, role: str = "judge"
) -> dict[str, Any]:
    """Score a brief by what fraction of its factual claims are verifiable.

    Returns:
        ``{"score": float, "total_claims": int, "verified_claims": int,
           "flagged": [{"claim": str, "verdict": str, "reason": str}, ...]}``
    """
    claims = _extract_claims(brief, role=role)
    flagged: list[dict[str, str]] = []
    verified = 0
    for c in claims:
        v = _verify_one(c, raw_data, role=role)
        if v["verdict"] == "verified":
            verified += 1
        else:
            flagged.append({"claim": c, "verdict": v["verdict"], "reason": v["reason"]})

    total = len(claims)
    score = (verified / total) if total else 0.0
    return {
        "score": round(score, 3),
        "total_claims": total,
        "verified_claims": verified,
        "flagged": flagged,
    }
