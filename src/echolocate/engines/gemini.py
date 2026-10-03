"""Gemini with Google Search grounding — the closest API analogue of the Gemini app with search on."""
from __future__ import annotations

import re

import httpx

from ..config import config, estimate_cost, gemini_key
from ..util import retry
from . import Answer, ProviderFatalError, citations_from

_DOMAIN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


def ask(prompt: str, country: str, language: str) -> Answer:
    model = config.gemini_model
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"maxOutputTokens": 4096},
    }
    def _post() -> httpx.Response:
        with httpx.Client(timeout=150) as c:
            return c.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": gemini_key() or "", "Content-Type": "application/json"},
            json=body,
        )

    r = retry(_post, attempts=3, base_delay=2.0, retry_on=(httpx.TransportError,))
    if r.status_code in (401, 403):
        raise ProviderFatalError(f"Gemini: key rejected ({r.status_code}) {r.text[:200]}")
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()
    cand = (d.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts if p.get("text") and not p.get("thought")).strip()
    gm = cand.get("groundingMetadata") or {}
    chunks = gm.get("groundingChunks") or []
    # Grounding URIs are redirects; the chunk title is usually the source's hostname.
    cites = []
    for ch in chunks:
        w = ch.get("web") or {}
        title = str(w.get("title") or "")
        cites.append({"url": w.get("uri"), "title": title, "domain": title.lower() if _DOMAIN.match(title.lower()) else ""})
    um = d.get("usageMetadata") or {}
    in_t = int(um.get("promptTokenCount") or 0)
    out_t = int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0)
    used_search = bool(gm.get("webSearchQueries"))
    actual_model = d.get("modelVersion") or model
    return Answer(
        engine="gemini", model=actual_model, available=bool(text), text=text, citations=citations_from(cites),
        cost=estimate_cost(actual_model, in_t, out_t, searches=1 if used_search else 0, engine="gemini"), in_tokens=in_t, out_tokens=out_t,
    )
