"""Factuality eval — LLM-as-judge that checks every numeric claim in a brief
traces back to ``raw_data``. Phase 1 / Hour 14 in the sprint plan.

Two passes (kept simple — Haiku is cheap):

1. **Extract** the list of numeric / factual claims from the brief.
2. **Verify** each claim against raw_data. Outcome ∈ {verified, not_found, contradicted}.

Aggregate score = verified / total. The flagged list is the actionable part —
that's what tells you whether the synthesizer needs a tighter prompt or
whether a tool failed silently.
"""
from __future__ import annotations

import json
from typing import Any

from mhfa.models.client import Role, get_client

_EXTRACT_PROMPT = """You will see a financial research brief. List every claim that
is a NUMERIC FACT (money, percentage, ratio, count, share price, growth rate)
or a SPECIFIC NAMED EVENT (acquisition, leadership change, guidance update).

Return a JSON array of strings. Each string is one self-contained claim,
quoted as close to the brief's wording as possible.

Brief:
{brief}

Return ONLY a JSON array. No prose."""

_VERIFY_PROMPT = """You will see a single claim and a JSON blob of source data.

Decide if the claim is **verified** (the exact value/event appears in the
sources), **contradicted** (sources show a different value/event), or
**not_found** (sources don't mention this claim either way).

Return JSON: {{"verdict": "verified"|"contradicted"|"not_found", "reason": "<one sentence>"}}.

Claim:
{claim}

Sources:
```json
{raw_json}
```
"""


def _extract_claims(brief: str, role: Role = "judge") -> list[str]:
    client, model, cfg = get_client(role)
    resp = client.messages.create(
        model=model,
        max_tokens=cfg["max_tokens"],
        temperature=0.0,
        messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(brief=brief)}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    # Tolerate model wrapping the JSON in ```json fences.
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [str(x) for x in items if isinstance(x, str)]


def _verify_one(claim: str, raw_data: dict[str, Any], role: Role = "judge") -> dict[str, str]:
    client, model, cfg = get_client(role)
    raw_for_judge = {k: v for k, v in raw_data.items() if k != "_metadata"}
    resp = client.messages.create(
        model=model,
        max_tokens=400,
        temperature=0.0,
        messages=[
            {
                "role": "user",
                "content": _VERIFY_PROMPT.format(
                    claim=claim,
                    raw_json=json.dumps(raw_for_judge, default=str)[:30000],  # keep prompt cheap
                ),
            }
        ],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        verdict_obj = json.loads(text)
        return {"verdict": str(verdict_obj.get("verdict", "not_found")),
                "reason": str(verdict_obj.get("reason", ""))}
    except json.JSONDecodeError:
        return {"verdict": "not_found", "reason": "judge returned non-JSON"}


def check_factuality(
    brief: str, raw_data: dict[str, Any], *, role: Role = "judge"
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
