"""Perplexity Sonar — searches the web by default."""
from __future__ import annotations

import httpx

from ..config import config, estimate_cost, perplexity_key
from ..util import retry
from . import Answer, ProviderFatalError, citations_from


def ask(prompt: str, country: str, language: str) -> Answer:
    model = config.perplexity_model
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 2048}
    def _post() -> httpx.Response:
        with httpx.Client(timeout=150) as c:
            return c.post("https://api.perplexity.ai/chat/completions", headers={"Authorization": f"Bearer {perplexity_key()}", "Content-Type": "application/json"}, json=body)

    r = retry(_post, attempts=3, base_delay=2.0, retry_on=(httpx.TransportError,))
    if r.status_code in (401, 403):
        raise ProviderFatalError(f"Perplexity: key rejected ({r.status_code}) {r.text[:200]}")
    if r.status_code >= 400:
        raise RuntimeError(f"Perplexity HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()
    text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
    cites = [{"url": s.get("url"), "title": s.get("title")} for s in d.get("search_results") or []] or [{"url": u} for u in d.get("citations") or []]
    u = d.get("usage") or {}
    in_t, out_t = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    return Answer(
        engine="perplexity", model=d.get("model") or model, available=bool(text), text=text, citations=citations_from(cites),
        cost=estimate_cost(d.get("model") or model, in_t, out_t, searches=1, engine="perplexity"), in_tokens=in_t, out_tokens=out_t,
    )
