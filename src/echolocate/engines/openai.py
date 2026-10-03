"""ChatGPT via the OpenAI Responses API with the web_search tool on."""
from __future__ import annotations

import httpx

from ..config import config, estimate_cost, openai_key
from ..util import retry
from . import Answer, ProviderFatalError, citations_from


def ask(prompt: str, country: str, language: str) -> Answer:
    model = config.openai_model
    body = {
        "model": model,
        "input": prompt,
        "tools": [{"type": "web_search", "user_location": {"type": "approximate", "country": country.upper()[:2]}}],
        "max_output_tokens": 2048,
    }
    def _post() -> httpx.Response:
        with httpx.Client(timeout=150) as c:
            return c.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {openai_key()}", "Content-Type": "application/json"}, json=body)

    r = retry(_post, attempts=3, base_delay=2.0, retry_on=(httpx.TransportError,))
    if r.status_code in (401, 403):
        raise ProviderFatalError(f"OpenAI: key rejected ({r.status_code}) {r.text[:200]}")
    if r.status_code == 429 and "insufficient_quota" in r.text:
        raise ProviderFatalError("OpenAI: insufficient quota")
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()
    texts, cites, searches = [], [], 0
    for item in d.get("output", []):
        if item.get("type") == "web_search_call":
            searches += 1
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                texts.append(part.get("text", ""))
                for a in part.get("annotations", []):
                    if a.get("type") == "url_citation":
                        cites.append({"url": a.get("url"), "title": a.get("title")})
    text = "\n".join(texts).strip()
    u = d.get("usage") or {}
    in_t, out_t = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    return Answer(
        engine="chatgpt", model=d.get("model") or model, available=bool(text), text=text, citations=citations_from(cites),
        cost=estimate_cost(d.get("model") or model, in_t, out_t, searches=searches, engine="chatgpt"), in_tokens=in_t, out_tokens=out_t,
    )
