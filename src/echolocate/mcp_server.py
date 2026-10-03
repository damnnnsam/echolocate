"""MCP server so Claude Code / Cursor can ask: 'how did we do in Gemini today, and which prompts did we lose?'"""
from __future__ import annotations

import json
from typing import Any

try:  # mcp 2.x renamed FastMCP → MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .config import ENGINES, ENGINE_LABELS, enabled_engines, engine_route
from .db import find_brand, insert_prompt, list_brands, list_prompts, list_topics, set_prompts_active, update_brand
from .metrics import answers_for, build_report, build_trend, headline
from .onboarding import generate_prompts, onboard
from .render import markdown
from .server import run_status, start_run
from .util import mentions_any

mcp = FastMCP("echolocate")


def _brand(brand: str | None):
    return find_brand(brand or None)


@mcp.tool()
def echolocate_status() -> str:
    """Which engines have keys and which brands are tracked."""
    lines = ["Engines: " + ", ".join(f"{e} ({engine_route(e) or 'no key'})" for e in ENGINES)]
    for b in list_brands():
        h = headline(b.id)
        lines.append(f"- {b.slug}: {b.name} ({b.domain}) · {len(list_prompts(b.id))} prompts · engines {','.join(b.engines)}" + (f" · latest {h['run'].run_date} visibility {h['overall'].visibility:.0%}" if h and h['overall'].visibility is not None else ""))
    return "\n".join(lines)


@mcp.tool()
def echolocate_brands() -> str:
    """List tracked brands with their latest headline numbers."""
    return echolocate_status()


@mcp.tool()
def echolocate_report(brand: str | None = None, date: str | None = None, format: str = "md") -> str:
    """Daily report for a brand (slug/domain; optional when only one brand is tracked). format: md | json."""
    rep = build_report(_brand(brand), date)
    return json.dumps(rep, default=str) if format == "json" else markdown(rep)


@mcp.tool()
def echolocate_trend(brand: str | None = None, days: int = 30) -> str:
    """Visibility / sentiment / citation rate per day, overall and per engine."""
    rows = build_trend(_brand(brand), days)
    out = ["date | visibility | share of voice | cited | sentiment"]
    for t in rows:
        o = t["overall"]
        f = lambda x: "–" if x is None else f"{x*100:.0f}%"  # noqa: E731
        out.append(f"{t['date']} | {f(o['visibility'])} | {f(o['share_of_voice'])} | {f(o['citation_rate'])} | {'–' if o['sentiment'] is None else round(o['sentiment'])}")
    return "\n".join(out)


@mcp.tool()
def echolocate_answers(brand: str | None = None, engine: str | None = None, mentioned: bool | None = None, date: str | None = None, limit: int = 20, full: bool = False) -> str:
    """Raw answers from the latest run. mentioned=false shows what an engine said when it did not mention you."""
    rows = answers_for(_brand(brand), date, engine, None, mentioned)[:limit]
    out = []
    for a in rows:
        out.append(f"### [{a.prompt_id}] {a.prompt_text}\n{ENGINE_LABELS.get(a.engine, a.engine)} · mentioned={a.mentioned} rank={a.mention_rank} sentiment={a.sentiment} cited={a.cited}"
                   + (f"\n{a.response_text}" if full else f"\n{a.response_text[:400]}…") + ("\nsources: " + ", ".join(c['domain'] for c in a.citations[:8]) if a.citations else ""))
    return "\n\n".join(out) or "no answers"


@mcp.tool()
def echolocate_prompts(brand: str | None = None) -> str:
    """List the active prompts with ids, topics and intent."""
    return "\n".join(f"[{p.id}] ({p.topic or '-'} · {p.intent}{' · branded' if p.branded else ''}) {p.text}" for p in list_prompts(_brand(brand).id)) or "no prompts"


@mcp.tool()
def echolocate_add_prompts(prompts: list[str], brand: str | None = None, topic: str | None = None) -> str:
    """Add prompts by hand. topic = an existing topic name (optional)."""
    b = _brand(brand)
    tid = next((t.id for t in list_topics(b.id) if t.name == topic), None) if topic else None
    aliases = [b.name, *b.aliases]
    n = sum(1 for t in prompts if insert_prompt(b.id, t, tid, "branded" if mentions_any(t, aliases) else "discovery", mentions_any(t, aliases)))
    return f"added {n} prompts"


@mcp.tool()
def echolocate_remove_prompts(ids: list[int], brand: str | None = None) -> str:
    """Deactivate prompts by id."""
    return f"removed {set_prompts_active(_brand(brand).id, ids, False)} prompts"


@mcp.tool()
def echolocate_generate_prompts(count: int = 20, brand: str | None = None) -> str:
    """Have Claude write `count` more prompts across the brand's topics."""
    n, u = generate_prompts(_brand(brand), count)
    return f"added {n} prompts (≈${u.cost:.2f})"


@mcp.tool()
def echolocate_onboard(url: str, prompts: int = 100, topics: int = 8, engines: list[str] | None = None) -> str:
    """Onboard a new brand from its URL: profile, ICP, competitors, topics and prompts."""
    b, n, u = onboard(url, prompts=prompts, topics=topics, engines=engines)
    return f"onboarded {b.slug} ({b.name}, {b.profile.get('category')}) with {n} prompts; competitors: {', '.join(c['name'] for c in b.competitors)} (≈${u.cost:.2f})"


@mcp.tool()
def echolocate_run(brand: str | None = None, limit: int | None = None, engines: list[str] | None = None, force: bool = False) -> str:
    """Start today's run in the background (all prompts × engines, then scoring). Poll with echolocate_run_status."""
    b = _brand(brand)
    st = start_run(b.slug, limit=limit, engines=engines, force=force)
    return json.dumps(st)


@mcp.tool()
def echolocate_run_status(brand: str | None = None) -> str:
    """Status of the background run (or of the latest stored run)."""
    return json.dumps(run_status(_brand(brand).slug), default=str)


@mcp.tool()
def echolocate_engines(brand: str | None = None, add: list[str] | None = None, remove: list[str] | None = None) -> str:
    """Show or change the engines a brand is tracked on."""
    b = _brand(brand)
    cur = list(b.engines)
    for e in add or []:
        if e in ENGINES and e not in cur:
            cur.append(e)
    cur = [e for e in cur if e not in (remove or [])]
    if add or remove:
        update_brand(b.id, settings={**b.settings, "engines": cur})
    return f"{b.slug}: {', '.join(cur)} (keys available for: {', '.join(enabled_engines())})"


def main() -> None:
    mcp.run()
