"""Anthropic client factory with Vertex AI / direct-API switch.

One entrypoint, ``get_client(role)``, returns ``(client, model_name, role_config)``.
Reads :doc:`/configs/models.yaml` so model choices are config, not code.

ANTHROPIC_BACKEND env var picks the wire path:
* ``vertex`` (default) → ``AnthropicVertex`` — billing flows through GCP, eats the
  GCP credit. Requires ``GCP_PROJECT_ID`` + ``VERTEX_REGION`` (us-east5 has
  Opus/Sonnet/Haiku as of writing).
* ``direct`` → ``Anthropic`` — uses ``ANTHROPIC_API_KEY``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from anthropic import Anthropic, AnthropicVertex

Role = Literal["planner", "tool_synth", "synthesizer", "judge"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _REPO_ROOT / "configs" / "models.yaml"


@lru_cache(maxsize=1)
def load_models_config() -> dict[str, Any]:
    """Load and cache configs/models.yaml."""
    with _CONFIG_PATH.open() as f:
        return yaml.safe_load(f)  # type: ignore[no-any-return]


def get_role_config(role: Role) -> dict[str, Any]:
    """Return the per-role config block (model, max_tokens, temperature, …)."""
    cfg = load_models_config()["roles"]
    if role not in cfg:
        raise KeyError(f"role={role!r} not in configs/models.yaml; have {list(cfg)}")
    return cfg[role]  # type: ignore[no-any-return]


def get_client(role: Role) -> tuple[Anthropic | AnthropicVertex, str, dict[str, Any]]:
    """Return ``(client, model_name, role_config)`` for a given agent role.

    The client is wire-level (Vertex or direct); the role config has model name,
    max_tokens, temperature, and any rationale notes. Caller composes them into
    ``client.messages.create(model=..., max_tokens=..., ...)``.
    """
    role_cfg = get_role_config(role)
    backend = os.getenv("ANTHROPIC_BACKEND", "vertex").lower()

    if backend == "vertex":
        project = os.environ.get("GCP_PROJECT_ID")
        region = os.environ.get("VERTEX_REGION", "us-east5")
        if not project:
            raise RuntimeError(
                "ANTHROPIC_BACKEND=vertex but GCP_PROJECT_ID is not set. "
                "Either set it in .env or switch to ANTHROPIC_BACKEND=direct."
            )
        client: Anthropic | AnthropicVertex = AnthropicVertex(
            project_id=project, region=region
        )
    elif backend == "direct":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_BACKEND=direct but ANTHROPIC_API_KEY is unset.")
        client = Anthropic()
    else:
        raise ValueError(
            f"ANTHROPIC_BACKEND={backend!r} is not recognised. Use 'vertex' or 'direct'."
        )

    return client, role_cfg["model"], role_cfg
