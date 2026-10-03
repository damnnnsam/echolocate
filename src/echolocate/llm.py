"""Structured calls to Claude: a JSON schema in, a validated dict out. The brain behind onboarding and scoring."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

import anthropic

from .config import anthropic_key, config, estimate_cost

_client: anthropic.Anthropic | None = None
_lock = threading.Lock()


@dataclass
class Usage:
    in_tokens: int = 0
    out_tokens: int = 0
    cost: float = 0.0
    calls: int = 0

    def add(self, other: "Usage") -> "Usage":
        self.in_tokens += other.in_tokens
        self.out_tokens += other.out_tokens
        self.cost += other.cost
        self.calls += other.calls
        return self


def client() -> anthropic.Anthropic:
    global _client
    with _lock:
        if _client is None:
            key = anthropic_key()
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set (needed for onboarding and scoring)")
            _client = anthropic.Anthropic(api_key=key, max_retries=4, timeout=180)
        return _client


def _effort_for(model: str) -> dict[str, Any]:
    # Haiku 4.5 rejects `effort`; the 4.6+ / 5.x families accept it. Keep extraction cheap.
    return {} if "haiku" in model else {"effort": "low"}


def structured(
    schema: dict[str, Any],
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 16000,
) -> tuple[dict[str, Any], Usage]:
    """Ask for JSON that satisfies `schema` (structured outputs); returns (data, usage)."""
    mdl = model or config.model
    resp = client().messages.create(
        model=mdl,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}, **_effort_for(mdl)},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"model refused ({getattr(resp.stop_details, 'category', None)})")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(text)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"model returned invalid JSON ({resp.stop_reason}): {text[:200]}") from e
    u = resp.usage
    usage = Usage(in_tokens=u.input_tokens, out_tokens=u.output_tokens, cost=estimate_cost(mdl, u.input_tokens, u.output_tokens), calls=1)
    return data, usage


# ---- tiny schema helpers so the onboarding/analysis schemas read like the data they describe
def obj(props: dict[str, Any], required: list[str] | None = None, desc: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "object", "properties": props, "required": required if required is not None else list(props), "additionalProperties": False}
    if desc:
        d["description"] = desc
    return d


def arr(items: dict[str, Any], desc: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "array", "items": items}
    if desc:
        d["description"] = desc
    return d


def s(desc: str | None = None, enum: list[str] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "string"}
    if desc:
        d["description"] = desc
    if enum:
        d["enum"] = enum
    return d


def num(desc: str | None = None, nullable: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {"type": ["number", "null"] if nullable else "number"}
    if desc:
        d["description"] = desc
    return d


def boolean(desc: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "boolean"}
    if desc:
        d["description"] = desc
    return d
