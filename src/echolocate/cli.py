from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import ENGINES, ENGINE_LABELS, anthropic_key, config, enabled_engines, engine_route
from .db import delete_brand, find_brand, insert_prompt, list_brands, list_prompts, list_topics, set_prompts_active, update_brand, engine_cost_history
from .metrics import answers_for, build_report, build_trend, headline
from .onboarding import generate_prompts, onboard
from .render import as_json, html_page, markdown
from .runner import rescore, run_brand
from .util import fmt_pct, fmt_usd, log, mentions_any


def _engines_arg(s: str | None) -> list[str] | None:
    if not s:
        return None
    out = [e.strip() for e in s.split(",") if e.strip()]
    bad = [e for e in out if e not in ENGINES]
    if bad:
        sys.exit(f"unknown engine(s): {', '.join(bad)}. Choose from {', '.join(ENGINES)}")
    return out


def cmd_status(a: argparse.Namespace) -> None:
    print(f"echolocate {__version__} · db {config.db_path}")
    print(f"brain: {'ANTHROPIC_API_KEY ok' if anthropic_key() else 'ANTHROPIC_API_KEY MISSING (needed for onboarding + scoring)'} · onboarding {config.model} · scoring {config.analysis_model}")
    print("engines:")
    for e in ENGINES:
        r = engine_route(e)
        print(f"  {'●' if r else '○'} {ENGINE_LABELS[e]:<20} {r or 'no key'}")
    if engine_route("google_ai_mode") == "dataforseo":
        try:
            from .engines import dataforseo
            st = dataforseo.status()
            print(f"dataforseo: {st['login']} · balance ${st['balance']:.2f} · {'verified' if st['verified'] else 'NOT verified — ' + str(st['message'])}")
        except Exception as ex:  # noqa: BLE001
            print(f"dataforseo: {ex}")
    bs = list_brands()
    print(f"brands: {len(bs)}")
    for b in bs:
        h = headline(b.id)
        print(f"  {b.slug:<24} {len(list_prompts(b.id)):>4} prompts · {','.join(b.engines) or 'no engines'}"
              + (f" · {h['run'].run_date} visibility {fmt_pct(h['overall'].visibility)}" if h else " · no runs yet"))


def cmd_brands(a: argparse.Namespace) -> None:
    for b in list_brands():
        h = headline(b.id)
        print(f"{b.slug:<24} {b.name:<28} {b.domain:<24} {len(list_prompts(b.id)):>4} prompts" + (f"  {h['run'].run_date} vis {fmt_pct(h['overall'].visibility)}" if h else "  no runs"))
    if not list_brands():
        print("no brands yet — `echolocate onboard <url>`")


def cmd_onboard(a: argparse.Namespace) -> None:
    engines = _engines_arg(a.engines) or config.default_engines
    if not engines:
        log("warning: no engine keys found — onboarding anyway; add keys to .env before `run`")
    brand, n, usage = onboard(a.url, prompts=a.prompts, topics=a.topics, name=a.name, country=a.country, language=a.language, engines=engines, branded_share=a.branded_share)
    print(f"\n✓ {brand.slug}: {brand.name} — {brand.profile.get('category')}\n  {n} prompts across {len(list_topics(brand.id))} topics · engines: {', '.join(brand.engines)} · onboarding ≈ {fmt_usd(usage.cost)}")
    print(f"  next: echolocate prompts --brand {brand.slug}   (review)   ·   echolocate run --brand {brand.slug} --limit 5   (smoke test)")


def cmd_prompts(a: argparse.Namespace) -> None:
    b = find_brand(a.brand)
    if a.action == "add":
        topics = {t.name: t.id for t in list_topics(b.id)}
        tid = topics.get(a.topic) if a.topic else None
        aliases = [b.name, *b.aliases]
        n = 0
        for t in a.items:
            br = mentions_any(t, aliases)
            if insert_prompt(b.id, t, tid, "branded" if br else "discovery", br):
                n += 1
        print(f"added {n}")
    elif a.action == "remove":
        print(f"removed {set_prompts_active(b.id, [int(i) for i in a.items], False)}")
    elif a.action == "generate":
        n, u = generate_prompts(b, int(a.items[0]) if a.items else 20)
        print(f"added {n} prompts ≈ {fmt_usd(u.cost)}")
    else:
        for p in list_prompts(b.id, active_only=not a.all):
            print(f"[{p.id:>4}] {(p.topic or '-')[:22]:<22} {p.intent:<10}{'B ' if p.branded else '  '}{'' if p.active else '(off) '}{p.text}")


def cmd_engines(a: argparse.Namespace) -> None:
    b = find_brand(a.brand)
    cur = list(b.engines)
    for e in _engines_arg(a.add) or []:
        if e not in cur:
            cur.append(e)
    cur = [e for e in cur if e not in (_engines_arg(a.remove) or [])]
    if a.add or a.remove:
        update_brand(b.id, settings={**b.settings, "engines": cur})
    print(f"{b.slug}: {', '.join(cur) or '(none)'}   keys available for: {', '.join(enabled_engines()) or 'none'}")


def cmd_run(a: argparse.Namespace) -> None:
    brands = list_brands() if a.all else [find_brand(a.brand)]
    for b in brands:
        run = run_brand(b, run_date=a.date, limit=a.limit, engines=_engines_arg(a.engines), force=a.force)
        if a.email:
            from .mail import send
            rep = build_report(b, run.run_date)
            print("  emailed " + send(f"{b.name} — AI visibility {run.run_date}: {fmt_pct(rep['overall']['visibility'])}", html_page(rep, build_trend(b, 30))))


def cmd_analyze(a: argparse.Namespace) -> None:
    u = rescore(find_brand(a.brand), a.date)
    print(f"scored: {u.calls} calls ≈ {fmt_usd(u.cost)}")


def cmd_report(a: argparse.Namespace) -> None:
    b = find_brand(a.brand)
    rep = build_report(b, a.date)
    out = {"md": markdown, "json": as_json, "html": lambda r: html_page(r, build_trend(b, 30))}[a.format](rep)
    if a.output:
        Path(a.output).write_text(out)
        print(f"wrote {a.output}")
    else:
        print(out)


def cmd_trend(a: argparse.Namespace) -> None:
    rows = build_trend(find_brand(a.brand), a.days)
    if a.json:
        print(json.dumps(rows, indent=2, default=str))
        return
    print(f"{'date':<12}{'visibility':>11}{'SoV':>8}{'cited':>8}{'sentiment':>11}  per engine")
    for t in rows:
        o = t["overall"]
        per = " ".join(f"{e['engine'][:6]}={fmt_pct(e['visibility'])}" for e in t["engines"])
        print(f"{t['date']:<12}{fmt_pct(o['visibility']):>11}{fmt_pct(o['share_of_voice']):>8}{fmt_pct(o['citation_rate']):>8}{('–' if o['sentiment'] is None else str(round(o['sentiment']))):>11}  {per}")


def cmd_answers(a: argparse.Namespace) -> None:
    mentioned = False if a.missed else (True if a.mentioned else None)
    rows = answers_for(find_brand(a.brand), a.date, a.engine, a.prompt, mentioned)
    for r in rows[: a.limit]:
        print(f"\n[{r.prompt_id}] {r.prompt_text}\n  {ENGINE_LABELS.get(r.engine, r.engine)} · {r.model} · mentioned={r.mentioned} rank={r.mention_rank} sentiment={r.sentiment} cited={r.cited}" + (f" · ERROR {r.error}" if r.error else ""))
        if r.brand_context:
            print(f"  context: {r.brand_context}")
        if r.citations:
            print("  sources: " + ", ".join(c["domain"] or c["url"] for c in r.citations[:8]))
        print("  " + (r.response_text if a.full else r.response_text[:500].replace("\n", " ") + ("…" if len(r.response_text) > 500 else "")))
    if not rows:
        print("no answers match")


def cmd_cost(a: argparse.Namespace) -> None:
    b = find_brand(a.brand)
    n = len(list_prompts(b.id))
    hist = engine_cost_history(b.id)
    from .config import SEARCH_FEES, estimate_cost
    defaults = {"chatgpt": estimate_cost(config.openai_model, 1500, 600, 1, "chatgpt"), "gemini": estimate_cost(config.gemini_model, 1500, 800, 1, "gemini"),
                "claude": estimate_cost(config.claude_model, 6000, 700, 3, "claude"), "perplexity": estimate_cost(config.perplexity_model, 1500, 500, 1, "perplexity"),
                "google_ai_mode": 0.002, "google_ai_overview": 0.002}
    total = 0.0
    print(f"{b.slug}: {n} prompts/day")
    for e in b.engines:
        c = hist.get(e, defaults.get(e, 0.0))
        src = "observed avg" if e in hist else "list-price estimate"
        total += c * n
        print(f"  {ENGINE_LABELS[e]:<20} {fmt_usd(c)}/answer ({src}) → {fmt_usd(c*n)}/day")
    scoring = estimate_cost(config.analysis_model, 4000 * len(b.engines) // max(1, len(b.engines)) + 2500, 500) * n
    total += scoring
    print(f"  {'scoring (' + config.analysis_model + ')':<20} ≈ {fmt_usd(scoring)}/day")
    print(f"  total ≈ {fmt_usd(total)}/day · {fmt_usd(total*30)}/month")


def cmd_serve(a: argparse.Namespace) -> None:
    from .server import serve
    serve(a.port)


def cmd_mcp(a: argparse.Namespace) -> None:
    from .mcp_server import main as mcp_main
    mcp_main()


def cmd_cron(a: argparse.Namespace) -> None:
    hh, mm = a.at.split(":")
    from .config import ROOT
    print(f"{int(mm)} {int(hh)} * * * cd '{ROOT}' && uv run echolocate run --all{' --email' if config.resend else ''} >> '{ROOT}/data/cron.log' 2>&1")
    print("# add with: crontab -e", file=sys.stderr)


def cmd_email(a: argparse.Namespace) -> None:
    from .mail import send
    b = find_brand(a.brand)
    rep = build_report(b, a.date)
    print("sent " + send(f"{b.name} — AI visibility {rep['run']['date']}: {fmt_pct(rep['overall']['visibility'])}", html_page(rep, build_trend(b, 30))))


def cmd_delete(a: argparse.Namespace) -> None:
    b = find_brand(a.brand)
    if not a.yes:
        sys.exit(f"this deletes {b.slug} with all prompts, runs and answers. Re-run with --yes.")
    delete_brand(b.id)
    print(f"deleted {b.slug}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="echolocate", description="Send prompts into the AI engines, listen for your brand bouncing back.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def brand_opt(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--brand", "-b", help="brand slug or domain (optional when only one is tracked)")

    sub.add_parser("status", help="keys, engines, brands").set_defaults(fn=cmd_status)
    sub.add_parser("brands", help="list tracked brands").set_defaults(fn=cmd_brands)
    s = sub.add_parser("onboard", help="profile a site and write its prompt set")
    s.add_argument("url")
    s.add_argument("--prompts", type=int, default=100)
    s.add_argument("--topics", type=int, default=8)
    s.add_argument("--name")
    s.add_argument("--country")
    s.add_argument("--language")
    s.add_argument("--engines", help="comma-separated; default: every engine with a key, minus claude")
    s.add_argument("--branded-share", type=float, default=0.15)
    s.set_defaults(fn=cmd_onboard)
    s = sub.add_parser("prompts", help="list / add / remove / generate prompts")
    brand_opt(s)
    s.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove", "generate"])
    s.add_argument("items", nargs="*", help="prompt texts (add), ids (remove), or a count (generate)")
    s.add_argument("--topic")
    s.add_argument("--all", action="store_true", help="include deactivated prompts")
    s.set_defaults(fn=cmd_prompts)
    s = sub.add_parser("engines", help="show / change a brand's engines")
    brand_opt(s)
    s.add_argument("--add")
    s.add_argument("--remove")
    s.set_defaults(fn=cmd_engines)
    s = sub.add_parser("run", help="today's run: every prompt × every engine, then scoring")
    brand_opt(s)
    s.add_argument("--all", action="store_true")
    s.add_argument("--limit", type=int)
    s.add_argument("--date")
    s.add_argument("--engines")
    s.add_argument("--force", action="store_true", help="re-ask pairs that already have an answer today")
    s.add_argument("--email", action="store_true")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("analyze", help="re-score answers left unanalyzed")
    brand_opt(s)
    s.add_argument("--date")
    s.set_defaults(fn=cmd_analyze)
    s = sub.add_parser("report", help="daily report")
    brand_opt(s)
    s.add_argument("--date")
    s.add_argument("-f", "--format", choices=["md", "json", "html"], default="md")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("trend", help="day by day")
    brand_opt(s)
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_trend)
    s = sub.add_parser("answers", help="raw answers from the latest run")
    brand_opt(s)
    s.add_argument("-e", "--engine", choices=ENGINES)
    s.add_argument("--missed", action="store_true", help="only answers that did not mention you")
    s.add_argument("--mentioned", action="store_true")
    s.add_argument("--prompt", type=int)
    s.add_argument("--date")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--full", action="store_true")
    s.set_defaults(fn=cmd_answers)
    s = sub.add_parser("cost", help="estimated spend per day / month")
    brand_opt(s)
    s.set_defaults(fn=cmd_cost)
    s = sub.add_parser("serve", help="REST API + dashboard")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("mcp", help="MCP server over stdio").set_defaults(fn=cmd_mcp)
    s = sub.add_parser("cron", help="print a crontab line")
    s.add_argument("--at", default="07:00")
    s.set_defaults(fn=cmd_cron)
    s = sub.add_parser("email", help="email the latest report")
    brand_opt(s)
    s.add_argument("--date")
    s.set_defaults(fn=cmd_email)
    s = sub.add_parser("delete-brand", help="delete a brand and all its data")
    s.add_argument("brand")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_delete)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except (KeyError, RuntimeError) as e:
        sys.exit(f"error: {e}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
