"""DataForSEO: Google AI Mode + AI Overview (SERP API), and a fallback route for the ChatGPT/Gemini UI scrapers
and the Claude/Perplexity LLM Responses products when you have no vendor key. Live mode only; costs are billed figures."""
from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from ..config import dataforseo_auth
from . import Answer, ProviderFatalError, citations_from

BASE = "https://api.dataforseo.com/v3"
FATAL = {40100, 40104, 40200, 40201, 40203, 40204, 40207, 40210}
RETRYABLE = {40101, 40103, 40202, 40209, 50000, 50001, 50301, 50303, 50401}
NO_RESULTS = 40102
LOCATION_CODES = {
    "us": 2840, "gb": 2826, "uk": 2826, "ca": 2124, "au": 2036, "nz": 2554, "ie": 2372, "de": 2276, "fr": 2250, "es": 2724, "it": 2380,
    "nl": 2528, "be": 2056, "ch": 2756, "at": 2040, "se": 2752, "no": 2578, "dk": 2208, "fi": 2246, "pl": 2616, "pt": 2620, "br": 2076,
    "mx": 2484, "ar": 2032, "in": 2356, "jp": 2392, "sg": 2702, "za": 2710, "ae": 2784, "il": 2376, "kr": 2410, "cz": 2203, "lv": 2428,
}
ROUTES = {
    "chatgpt": "/ai_optimization/chat_gpt/llm_scraper/live/advanced",
    "gemini": "/ai_optimization/gemini/llm_scraper/live/advanced",
    "claude": "/ai_optimization/claude/llm_responses/live",
    "perplexity": "/ai_optimization/perplexity/llm_responses/live",
    "google_ai_mode": "/serp/google/ai_mode/live/advanced",
    "google_ai_overview": "/serp/google/organic/live/advanced",
}
_models: dict[str, str] = {}


def _auth() -> str:
    a = dataforseo_auth()
    if not a:
        raise ProviderFatalError("DataForSEO: DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set")
    return "Basic " + base64.b64encode(f"{a[0]}:{a[1]}".encode()).decode()


def call(method: str, path: str, body: Any = None, raw: bool = False) -> dict[str, Any]:
    for i in range(1, 5):
        try:
            with httpx.Client(timeout=150) as c:
                r = c.request(method, BASE + path, headers={"Authorization": _auth(), "Content-Type": "application/json"}, json=body)
        except httpx.HTTPError as e:
            if i >= 3:
                raise RuntimeError(f"DataForSEO network error: {e}") from e
            time.sleep(2 * i)
            continue
        if r.status_code == 401:
            raise ProviderFatalError("DataForSEO: invalid login or password (401)")
        if r.status_code == 402:
            raise ProviderFatalError("DataForSEO: payment required — top up your balance (402)")
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            j = {}
        if raw:
            return j
        top = int(j.get("status_code") or r.status_code)
        task = (j.get("tasks") or [None])[0] or {}
        code = int(task.get("status_code") or top)
        msg = str(task.get("status_message") or j.get("status_message") or r.reason_phrase)
        if top in FATAL or code in FATAL:
            raise ProviderFatalError(f"DataForSEO {code}: {msg}")
        if (top in RETRYABLE or code in RETRYABLE or r.status_code >= 500) and i < 4:
            time.sleep((10 if code in (40202, 40209) else 3) * i)
            continue
        if top not in (20000, 20100):
            raise RuntimeError(f"DataForSEO {top}: {j.get('status_message')}")
        if code in (20000, 20100, NO_RESULTS):
            return task
        raise RuntimeError(f"DataForSEO {code}: {msg}")
    raise RuntimeError("DataForSEO: gave up")


def status() -> dict[str, Any]:
    t = call("GET", "/appendix/user_data")
    r = (t.get("result") or [{}])[0]
    probe = call("GET", "/ai_optimization/perplexity/llm_responses/models", raw=True)
    return {
        "login": r.get("login", ""),
        "balance": float((r.get("money") or {}).get("balance") or 0),
        "verified": probe.get("status_code") != 40104,
        "message": None if probe.get("status_code") == 20000 else f"{probe.get('status_code')} {probe.get('status_message')}",
    }


def _model(se: str) -> str:
    if se not in _models:
        t = call("GET", f"/ai_optimization/{se}/llm_responses/models")
        names = [str(m.get("model_name")) for m in (t.get("result") or []) if m.get("web_search_supported") is not False]
        if se == "perplexity":
            pick = next((n for n in names if n == "sonar-pro"), None) or next((n for n in names if n == "sonar"), None) or (names[0] if names else None)
        else:
            sonnets = sorted(n for n in names if "sonnet" in n)
            pick = sonnets[-1] if sonnets else (sorted(names)[-1] if names else None)
        if not pick:
            raise RuntimeError(f"DataForSEO returned no {se} models")
        _models[se] = pick
    return _models[se]


def _payload(engine: str, prompt: str, country: str, language: str) -> dict[str, Any]:
    loc = {"location_code": LOCATION_CODES.get(country.lower(), 2840), "language_code": language}
    if engine == "chatgpt":
        return {"keyword": prompt[:700], **loc, "force_web_search": True}
    if engine == "gemini":
        return {"keyword": prompt[:700], **loc}
    if engine == "claude":
        return {"user_prompt": prompt[:500], "model_name": _model("claude"), "web_search": True, "force_web_search": True,
                "web_search_country_iso_code": country.upper(), "max_output_tokens": 2048}
    if engine == "perplexity":
        return {"user_prompt": prompt[:500], "model_name": _model("perplexity"), "web_search_country_iso_code": country.upper(), "max_output_tokens": 2048}
    if engine == "google_ai_mode":
        return {"keyword": prompt[:700], **loc}
    if engine == "google_ai_overview":
        return {"keyword": prompt[:700], **loc, "depth": 10, "load_async_ai_overview": True}
    raise RuntimeError(f"unknown engine {engine}")


def _scraper(engine: str, r: dict, cost: float) -> Answer:
    items = r.get("items") or []
    text = str(r.get("markdown") or "") or "\n\n".join(x for x in (i.get("markdown") or i.get("original_text") or i.get("text") or "" for i in items) if x)
    sources = [*(r.get("sources") or []), *[s for i in items for s in (i.get("sources") or [])]]
    return Answer(engine=engine, model=r.get("model"), available=bool(text.strip()), text=text.strip(), citations=citations_from(sources), cost=cost, cost_source="billed")


def _llm_response(engine: str, r: dict, cost: float) -> Answer:
    messages = [i.get("sections") or [] for i in (r.get("items") or []) if i.get("type") == "message"]
    texts = ["".join(s.get("text") or "" for s in secs).strip() for secs in messages]
    body = [t for i, t in enumerate(texts) if t and (i == len(texts) - 1 or len(t) >= 200)]
    text = "\n\n".join(body or [t for t in texts if t]).strip()
    annotations = [a for secs in messages for s in secs for a in (s.get("annotations") or [])]
    return Answer(engine=engine, model=r.get("model_name"), available=bool(text), text=text, citations=citations_from(annotations), cost=cost, cost_source="billed")


def _serp_ai(engine: str, r: dict, cost: float) -> Answer:
    block = next((i for i in (r.get("items") or []) if i.get("type") == "ai_overview"), None)
    if not block:
        return Answer(engine=engine, available=False, cost=cost, cost_source="billed")
    parts = block.get("items") or []
    text = str(block.get("markdown") or "") or "\n\n".join(x for x in (p.get("markdown") or p.get("text") or "" for p in parts) if x)
    refs = [*(block.get("references") or []), *[x for p in parts for x in [*(p.get("references") or []), *(p.get("links") or [])]]]
    return Answer(engine=engine, available=bool(text.strip()), text=text.strip(), citations=citations_from(refs), cost=cost, cost_source="billed")


def ask(engine: str, prompt: str, country: str, language: str) -> Answer:
    t = call("POST", ROUTES[engine], [_payload(engine, prompt, country, language)])
    cost = float(t.get("cost") or 0)
    result = (t.get("result") or [None])[0]
    if t.get("status_code") == NO_RESULTS or not result:
        return Answer(engine=engine, available=False, cost=cost, cost_source="billed")
    if engine in ("chatgpt", "gemini"):
        a = _scraper(engine, result, cost)
    elif engine in ("claude", "perplexity"):
        a = _llm_response(engine, result, cost)
    else:
        a = _serp_ai(engine, result, cost)
    a.model = f"dataforseo:{a.model or engine}"
    return a
