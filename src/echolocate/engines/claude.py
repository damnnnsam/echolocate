"""Claude with the web_search server tool — what a Claude.ai user with search on would see."""
from __future__ import annotations

from ..config import config, estimate_cost
from ..llm import client
from . import Answer, ProviderFatalError, citations_from

import anthropic


def ask(prompt: str, country: str, language: str) -> Answer:
    model = config.claude_model
    try:
        resp = client().messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
            tools=[{
                "type": "web_search_20250305" if ("haiku" in model or "4-5" in model) else "web_search_20260209",
                "name": "web_search",
                "max_uses": 5,
                "user_location": {"type": "approximate", "country": country.upper()[:2]},
            }],
        )
    except anthropic.AuthenticationError as e:
        raise ProviderFatalError(f"Claude: {e}") from e
    except anthropic.PermissionDeniedError as e:
        raise ProviderFatalError(f"Claude: {e}") from e
    texts: list[str] = []
    cites: list[dict] = []
    fallback: list[dict] = []
    for block in resp.content:
        t = getattr(block, "type", "")
        if t == "text":
            texts.append(block.text)
            for c in getattr(block, "citations", None) or []:
                if getattr(c, "type", "") == "web_search_result_location":
                    cites.append({"url": c.url, "title": c.title})
        elif t == "web_search_tool_result":
            for rres in getattr(block, "content", None) or []:
                if getattr(rres, "type", "") == "web_search_result":
                    fallback.append({"url": rres.url, "title": rres.title})
    text = "".join(texts).strip()
    u = resp.usage
    searches = int(getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0)
    return Answer(
        engine="claude", model=resp.model, available=bool(text), text=text, citations=citations_from(cites or fallback),
        cost=estimate_cost(resp.model, u.input_tokens, u.output_tokens, searches=searches, engine="claude"),
        in_tokens=u.input_tokens, out_tokens=u.output_tokens,
    )
