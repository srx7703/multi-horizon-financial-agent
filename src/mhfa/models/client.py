"""LLM client factory + provider-agnostic completion adapter.

Two layers:

* :func:`get_client` — low-level. Returns the raw SDK client for a role
  (Anthropic ``Anthropic``/``AnthropicVertex`` or Google ``genai.Client``)
  alongside the resolved model name and role config. Useful when a caller
  needs SDK-specific features (streaming, tool-use, etc.).
* :func:`complete_text` — high-level. Provider-agnostic ``user_message →
  text`` completion. The synthesizer and the factuality judge both use this
  so the rest of the codebase doesn't have to know which vendor is wired up.

Roles (planner, tool_synth, synthesizer, judge, synthesizer_b, …) are
defined in ``configs/models.yaml``. Each role names a provider; the adapter
dispatches accordingly.

Backends:

* ``provider: gemini`` — ``google-genai`` SDK on Vertex AI. Reads
  ``GCP_PROJECT_ID`` + ``VERTEX_REGION`` (default ``us-east5``; Gemini works
  in ``us-central1`` too). Eats GCP credit.
* ``provider: anthropic`` — Anthropic SDK with ``ANTHROPIC_BACKEND`` env
  var picking between ``vertex`` (default — also through GCP) and
  ``direct`` (uses ``ANTHROPIC_API_KEY``).

v0.1 ships Gemini for all four primary roles (decision D-009). Anthropic
remains in ``synthesizer_b`` for Phase 2 A/B once Vertex quota is granted.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from anthropic import Anthropic, AnthropicVertex
from google import genai

Role = Literal[
    "planner", "tool_synth", "synthesizer", "judge", "synthesizer_b"
]
Provider = Literal["anthropic", "gemini"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _REPO_ROOT / "configs" / "models.yaml"


@lru_cache(maxsize=1)
def load_models_config() -> dict[str, Any]:
    """Load and cache configs/models.yaml."""
    with _CONFIG_PATH.open() as f:
        return yaml.safe_load(f)  # type: ignore[no-any-return]


def get_role_config(role: str) -> dict[str, Any]:
    """Return the per-role config block (provider, model, max_tokens, …)."""
    cfg = load_models_config()["roles"]
    if role not in cfg:
        raise KeyError(f"role={role!r} not in configs/models.yaml; have {list(cfg)}")
    return cfg[role]  # type: ignore[no-any-return]


# --- Low-level: get the raw SDK client ------------------------------------


def get_client(
    role: str,
) -> tuple[Anthropic | AnthropicVertex | genai.Client, str, dict[str, Any]]:
    """Return ``(client, model_name, role_config)`` for ``role``.

    The client type depends on the role's ``provider`` field. Keep this for
    callers that need streaming / tool-use / SDK-native features. For plain
    text completion prefer :func:`complete_text`.
    """
    role_cfg = get_role_config(role)
    provider: Provider = role_cfg.get("provider", "gemini")

    if provider == "gemini":
        client: Any = _build_gemini_client()
    elif provider == "anthropic":
        client = _build_anthropic_client()
    else:
        raise ValueError(
            f"Unknown provider={provider!r} for role={role!r} in models.yaml"
        )

    return client, role_cfg["model"], role_cfg


def _build_gemini_client() -> genai.Client:
    project = os.environ.get("GCP_PROJECT_ID")
    region = os.environ.get("VERTEX_REGION", "us-central1")
    if not project:
        raise RuntimeError(
            "provider=gemini requires GCP_PROJECT_ID. Set it in .env."
        )
    # Gemini 2.5 is broadly available in us-central1; us-east5 only has
    # Anthropic. Auto-pick if user pinned us-east5 for Anthropic but Gemini
    # isn't there.
    if region == "us-east5":
        region = "us-central1"
    return genai.Client(vertexai=True, project=project, location=region)


def _build_anthropic_client() -> Anthropic | AnthropicVertex:
    backend = os.getenv("ANTHROPIC_BACKEND", "vertex").lower()
    if backend == "vertex":
        project = os.environ.get("GCP_PROJECT_ID")
        region = os.environ.get("VERTEX_REGION", "us-east5")
        if not project:
            raise RuntimeError(
                "ANTHROPIC_BACKEND=vertex but GCP_PROJECT_ID is not set."
            )
        return AnthropicVertex(project_id=project, region=region)
    if backend == "direct":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_BACKEND=direct but ANTHROPIC_API_KEY is unset."
            )
        return Anthropic()
    raise ValueError(
        f"ANTHROPIC_BACKEND={backend!r} is not recognised. Use 'vertex' or 'direct'."
    )


# --- High-level: provider-agnostic text completion ------------------------


def complete_text(
    role: str,
    *,
    user_message: str,
    system_message: str | None = None,
    response_format: Literal["text", "json"] = "text",
) -> str:
    """Run a one-shot text completion using the role's configured provider.

    Returns the model's response as a single string. Hides SDK differences
    so the rest of the codebase (synthesizer, factuality judge) can stay
    provider-agnostic.

    Both Anthropic and Gemini support a "system" message that sets the
    overall behavioral frame; the user message contains the task + evidence.
    Gemini calls it ``system_instruction``, Anthropic calls it ``system`` —
    we normalize.

    ``response_format='json'`` flips Gemini into structured-output mode
    (``response_mime_type='application/json'``) so the judge always gets
    parseable JSON back. Anthropic ignores the flag — the system prompt
    already tells it to emit JSON, and Anthropic doesn't have a native
    JSON-mode flag (just prompt discipline).
    """
    role_cfg = get_role_config(role)
    provider: Provider = role_cfg.get("provider", "gemini")
    model = role_cfg["model"]
    max_tokens = role_cfg.get("max_tokens", 4096)
    temperature = role_cfg.get("temperature", 0.3)
    thinking_budget = role_cfg.get("thinking_budget")  # None = SDK default

    if provider == "gemini":
        return _complete_gemini(
            model=model,
            user_message=user_message,
            system_message=system_message,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            thinking_budget=thinking_budget,
        )
    if provider == "anthropic":
        return _complete_anthropic(
            model=model,
            user_message=user_message,
            system_message=system_message,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    raise ValueError(f"Unknown provider={provider!r}")


def _complete_gemini(
    *,
    model: str,
    user_message: str,
    system_message: str | None,
    max_tokens: int,
    temperature: float,
    response_format: Literal["text", "json"] = "text",
    thinking_budget: int | None = None,
) -> str:
    from google.genai import types

    client = _build_gemini_client()
    config_kwargs: dict[str, Any] = {
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_message:
        config_kwargs["system_instruction"] = system_message
    if response_format == "json":
        config_kwargs["response_mime_type"] = "application/json"
    # Gemini 2.5 Flash defaults to thinking-on, which silently eats
    # max_output_tokens before any visible text. Roles that just need a
    # mechanical extraction/verification (e.g. judge) should set
    # thinking_budget: 0 in models.yaml.
    if thinking_budget is not None:
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_budget=thinking_budget
        )
    cfg = types.GenerateContentConfig(**config_kwargs)
    resp = client.models.generate_content(
        model=model, contents=user_message, config=cfg
    )
    return (resp.text or "").strip()


def _complete_anthropic(
    *,
    model: str,
    user_message: str,
    system_message: str | None,
    max_tokens: int,
    temperature: float,
) -> str:
    client = _build_anthropic_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user_message}],
    }
    if system_message:
        kwargs["system"] = system_message
    resp = client.messages.create(**kwargs)
    return "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
