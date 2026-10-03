"""One `ask()` per engine. Each returns an Answer: the text a buyer would have seen plus the sources it cited."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import engine_route
from ..util import domain_of, match_domain


class ProviderFatalError(Exception):
    """Account-level failure (bad key, no balance): stop the run instead of failing every prompt one by one."""


@dataclass
class Answer:
    engine: str
    model: str | None = None
    available: bool = True  # False when the engine showed nothing for the prompt (e.g. no AI Overview)
    text: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)  # {position, url, title, domain}
    cited: bool = False
    citation_position: int | None = None
    error: str | None = None
    cost: float = 0.0
    cost_source: str = "estimate"  # 'estimate' (list price × tokens) or 'billed' (provider returned the charge)
    in_tokens: int = 0
    out_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def citations_from(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        url = str(it.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        domain = str(it.get("domain") or "") or domain_of(url)
        out.append({"position": len(out) + 1, "url": url, "title": str(it.get("title") or it.get("source") or ""), "domain": domain})
    return out


def with_domain_match(a: Answer, domain: str) -> Answer:
    hit = next((c for c in a.citations if match_domain(c.get("domain") or domain_of(c["url"]), domain)), None)
    a.cited = hit is not None
    a.citation_position = hit["position"] if hit else None
    return a


def ask(engine: str, prompt: str, domain: str, country: str, language: str) -> Answer:
    """Dispatch to the client that has a key for this engine. Never raises for prompt-level failures."""
    route = engine_route(engine)
    if route is None:
        return Answer(engine=engine, available=False, error="no API key configured for this engine")
    try:
        if route == "native":
            if engine == "gemini":
                from . import gemini as mod
            elif engine == "claude":
                from . import claude as mod
            elif engine == "chatgpt":
                from . import openai as mod
            elif engine == "perplexity":
                from . import perplexity as mod
            else:
                raise RuntimeError(f"no native client for {engine}")
            a = mod.ask(prompt, country, language)
        else:
            from . import dataforseo
            a = dataforseo.ask(engine, prompt, country, language)
        return with_domain_match(a, domain)
    except ProviderFatalError:
        raise
    except Exception as e:  # noqa: BLE001
        return Answer(engine=engine, error=f"{type(e).__name__}: {str(e)[:300]}")
