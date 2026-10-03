from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(Path.cwd() / ".env")
load_dotenv(ROOT / ".env")

ENGINES: list[str] = ["chatgpt", "gemini", "claude", "perplexity", "google_ai_mode", "google_ai_overview"]
ENGINE_LABELS: dict[str, str] = {
    "chatgpt": "ChatGPT",
    "gemini": "Gemini",
    "claude": "Claude",
    "perplexity": "Perplexity",
    "google_ai_mode": "Google AI Mode",
    "google_ai_overview": "Google AI Overview",
}


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name) or default)
    except ValueError:
        return default


def anthropic_key() -> str | None:
    return env("ANTHROPIC_API_KEY")


def gemini_key() -> str | None:
    return env("GEMINI_API_KEY") or env("GOOGLE_AI_API_KEY")


def openai_key() -> str | None:
    return env("OPENAI_API_KEY")


def perplexity_key() -> str | None:
    return env("PERPLEXITY_API_KEY")


def dataforseo_auth() -> tuple[str, str] | None:
    login, pw = env("DATAFORSEO_LOGIN"), env("DATAFORSEO_PASSWORD")
    return (login, pw) if login and pw else None


def engine_route(engine: str) -> str | None:
    """Which client answers for an engine: 'native' (vendor API) or 'dataforseo'. None = no key for it."""
    native = {
        "chatgpt": openai_key(),
        "gemini": gemini_key(),
        "claude": anthropic_key(),
        "perplexity": perplexity_key(),
    }
    if engine in native and native[engine]:
        return "native"
    if dataforseo_auth():
        return "dataforseo"
    return None


def enabled_engines() -> list[str]:
    return [e for e in ENGINES if engine_route(e)]


class Config:
    @property
    def model(self) -> str:
        return env("ECHO_MODEL", "claude-sonnet-5-5") or "claude-sonnet-5-5"

    @property
    def analysis_model(self) -> str:
        return env("ECHO_ANALYSIS_MODEL", "claude-haiku-4-5") or "claude-haiku-4-5"

    @property
    def gemini_model(self) -> str:
        return env("ECHO_GEMINI_MODEL", "gemini-flash-latest") or "gemini-flash-latest"

    @property
    def openai_model(self) -> str:
        return env("ECHO_OPENAI_MODEL", "gpt-5") or "gpt-5"

    @property
    def claude_model(self) -> str:
        return env("ECHO_CLAUDE_MODEL", "claude-sonnet-5-5") or "claude-sonnet-5-5"

    @property
    def perplexity_model(self) -> str:
        return env("ECHO_PERPLEXITY_MODEL", "sonar") or "sonar"

    @property
    def country(self) -> str:
        return (env("ECHO_COUNTRY", "us") or "us").lower()

    @property
    def language(self) -> str:
        return (env("ECHO_LANGUAGE", "en") or "en").lower()

    @property
    def default_engines(self) -> list[str]:
        raw = env("ECHO_ENGINES")
        if raw:
            return [e.strip() for e in raw.split(",") if e.strip() in ENGINES]
        return [e for e in enabled_engines() if e != "claude"] or enabled_engines()

    @property
    def db_path(self) -> Path:
        return Path(env("ECHO_DB") or (ROOT / "data" / "echolocate.db"))

    @property
    def engine_concurrency(self) -> int:
        return env_int("ECHO_ENGINE_CONCURRENCY", 6)

    @property
    def prompt_concurrency(self) -> int:
        return env_int("ECHO_PROMPT_CONCURRENCY", 4)

    @property
    def api_token(self) -> str | None:
        return env("ECHO_API_TOKEN")

    @property
    def port(self) -> int:
        return env_int("PORT", 8787)

    @property
    def resend(self) -> tuple[str, str, str] | None:
        k, f, t = env("RESEND_API_KEY"), env("ECHO_EMAIL_FROM"), env("ECHO_EMAIL_TO")
        return (k, f, t) if k and f and t else None


config = Config()

# ---- cost estimates (USD). Vendor APIs do not return a bill per call, so these are list-price
# estimates keyed by model-name prefix: (per 1M input tokens, per 1M output tokens). Edit freely.
TOKEN_PRICES: list[tuple[str, float, float]] = [
    ("claude-opus-5-5", 4.0, 20.0),
    ("claude-opus", 5.0, 25.0),
    ("claude-sonnet-5", 2.0, 10.0),
    ("claude-sonnet", 3.0, 15.0),
    ("claude-haiku", 1.0, 5.0),
    ("claude-fable", 10.0, 50.0),
    ("gemini-3", 0.5, 3.0),
    ("gemini-2.5-flash-lite", 0.1, 0.4),
    ("gemini-2.5-flash", 0.3, 2.5),
    ("gemini-2.5-pro", 1.25, 10.0),
    ("gemini", 0.3, 2.5),
    ("gpt-5-mini", 0.25, 2.0),
    ("gpt-5-nano", 0.05, 0.4),
    ("gpt-5", 1.25, 10.0),
    ("gpt-4.1", 2.0, 8.0),
    ("sonar-pro", 3.0, 15.0),
    ("sonar", 1.0, 1.0),
]
# Per web-search / grounded request fees.
SEARCH_FEES: dict[str, float] = {"claude": 0.01, "chatgpt": 0.01, "gemini": 0.035, "perplexity": 0.005}


def estimate_cost(model: str | None, in_tokens: int, out_tokens: int, searches: int = 0, engine: str | None = None) -> float:
    m = (model or "").lower()
    rate_in = rate_out = 0.0
    for prefix, i, o in TOKEN_PRICES:
        if m.startswith(prefix):
            rate_in, rate_out = i, o
            break
    cost = in_tokens / 1e6 * rate_in + out_tokens / 1e6 * rate_out
    if engine and searches:
        cost += SEARCH_FEES.get(engine, 0.0) * searches
    return round(cost, 6)
