"""Markdown and HTML views of a report."""
from __future__ import annotations

import html
import json
from typing import Any

from .util import fmt_num, fmt_pct, fmt_usd


def _delta(cur: float | None, prev: float | None, pct: bool = True) -> str:
    if cur is None or prev is None:
        return ""
    d = cur - prev
    if abs(d) < (0.005 if pct else 0.05):
        return " (=)"
    sign = "+" if d > 0 else "−"
    return f" ({sign}{abs(d)*100:.0f}pp)" if pct else f" ({sign}{abs(d):.1f})"


def markdown(rep: dict[str, Any]) -> str:
    b, r, o, prev = rep["brand"], rep["run"], rep["overall"], rep.get("previous")
    po = prev["overall"] if prev else {}
    L: list[str] = []
    L.append(f"# {b['name']} — AI visibility, {r['date']}")
    L.append(f"_{b['category']} · {b['domain']} · {r['prompts']} prompts ({r['unbranded_prompts']} unbranded) · {', '.join(r['engines'])} · run {r['status']}_")
    if r.get("error"):
        L.append(f"> ⚠ {r['error']}")
    L.append("")
    L.append("## Headline")
    L.append(f"- **Visibility** {fmt_pct(o['visibility'])}{_delta(o['visibility'], po.get('visibility'))} — share of unbranded answers that mention you")
    L.append(f"- **Share of voice** {fmt_pct(o['share_of_voice'])}{_delta(o['share_of_voice'], po.get('share_of_voice'))}")
    L.append(f"- **Citation rate** {fmt_pct(o['citation_rate'])}{_delta(o['citation_rate'], po.get('citation_rate'))} — answers citing {b['domain']}")
    L.append(f"- **Sentiment** {fmt_num(o['sentiment'], 0)}/100{_delta(o['sentiment'], po.get('sentiment'), pct=False)} ({o['positive']}+ / {o['neutral']}○ / {o['negative']}−)")
    L.append(f"- **Avg rank when named** {fmt_num(o['avg_rank'])} · top-3 in {fmt_pct(o['top3_rate'])} of unbranded answers")
    if prev:
        L.append(f"- vs previous run {prev['date']}")
    L.append("")
    L.append("## By engine")
    L.append("| Engine | Answered | Visibility | Share of voice | Cited | Sentiment | Avg rank |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for e in rep["engines"]:
        pe = next((x for x in prev["engines"] if x["engine"] == e["engine"]), {}) if prev else {}
        note = f" ({e['unavailable']} n/a, {e['errors']} err)" if e["unavailable"] or e["errors"] else ""
        L.append(f"| {e['label']} | {e['answered']}{note} | {fmt_pct(e['visibility'])}{_delta(e['visibility'], pe.get('visibility'))} | {fmt_pct(e['share_of_voice'])} | {fmt_pct(e['citation_rate'])} | {fmt_num(e['sentiment'],0)} | {fmt_num(e['avg_rank'])} |")
    L.append("")
    L.append("## Leaderboard (who the engines recommend for your prompts)")
    L.append("| Brand | Mention rate | Share of voice | Avg rank | Sentiment | Cited |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for x in rep["leaderboard"]:
        nm = f"**{x['name']}** (you)" if x["is_target"] else x["name"]
        L.append(f"| {nm} | {fmt_pct(x['mention_rate'])} | {fmt_pct(x['share_of_voice'])} | {fmt_num(x['avg_rank'])} | {fmt_num(x['sentiment'],0)} | {fmt_pct(x['citation_rate'])} |")
    if rep["emerging"]:
        L.append("")
        L.append("**Also recommended (not tracked yet):** " + ", ".join(f"{x['name']} ({fmt_pct(x['mention_rate'])})" for x in rep["emerging"]))
    L.append("")
    L.append("## Topics")
    for t in rep["topics"]:
        L.append(f"- {t['topic']}: {fmt_pct(t['visibility'])} of {t['answered']} answers")
    if rep["gaps"]:
        L.append("")
        L.append("## Gaps — prompts where engines recommend competitors, not you")
        for g in rep["gaps"]:
            L.append(f"- [{g['prompt_id']}] {g['prompt']}\n  - missing on {', '.join(g['missing_on'])}; recommended: {', '.join(g['competitors'][:5])}")
    if rep["wins"] or rep["losses"]:
        L.append("")
        L.append("## Gained / lost since previous run")
        for w in rep["wins"]:
            L.append(f"- ✚ [{w['prompt_id']}] {w['prompt']} — {', '.join(w['engines'])}")
        for w in rep["losses"]:
            L.append(f"- ✖ [{w['prompt_id']}] {w['prompt']} — {', '.join(w['engines'])}")
    if rep["top_domains"]:
        L.append("")
        L.append("## Sources the engines cite most")
        for d in rep["top_domains"]:
            tag = " ← you" if d["owner"] == "you" else (" ← competitor" if d["owner"] == "competitor" else "")
            L.append(f"- {d['domain']} — {d['answers']} answers ({fmt_pct(d['share'])}){tag}")
    if rep["peers"]:
        L.append("")
        L.append("## Tracked peers (their own prompt sets)")
        for p in rep["peers"]:
            L.append(f"- {p['name']}: visibility {fmt_pct(p['visibility'])}, sentiment {fmt_num(p['sentiment'],0)}, cited {fmt_pct(p['citation_rate'])} ({p['date']})")
    L.append("")
    cost_note = "billed" if r.get("billed") else "estimated from list prices"
    L.append(f"_Costs this run: engines {fmt_usd(r['api_cost'])} ({cost_note}), scoring {fmt_usd(r['llm_cost'])} ({r['llm_tokens']['in']:,} in / {r['llm_tokens']['out']:,} out tokens)._")
    return "\n".join(L)


_CSS = """
:root{--bg:#fafaf8;--fg:#1a1a1a;--mut:#6b6b6b;--line:#e4e2dc;--card:#fff;--acc:#1f5fbf;--pos:#1d7a4b;--neg:#b23a2f}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#ececec;--mut:#9a9a9a;--line:#2a2a2a;--card:#181818;--acc:#7fb0ff;--pos:#5fcf8f;--neg:#ff8a7a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,Inter,Segoe UI,sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px}h1{font-size:26px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px;letter-spacing:.01em}
.sub{color:var(--mut);font-size:13px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:20px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.tile b{display:block;font-size:26px;font-weight:600}
.tile span{color:var(--mut);font-size:12px}.d{font-size:12px;margin-left:6px}.up{color:var(--pos)}.dn{color:var(--neg)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line)}th{color:var(--mut);font-weight:500;font-size:12px}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.you td{background:color-mix(in srgb,var(--acc) 8%,transparent)}ul{padding-left:18px}li{margin:4px 0}.g{color:var(--mut);font-size:13px}
.bar{display:inline-block;height:8px;background:var(--acc);border-radius:4px;vertical-align:middle;margin-right:8px}
.sent{display:inline-block;padding:1px 7px;border-radius:999px;font-size:12px;border:1px solid var(--line)}.warn{background:#fff3cd;color:#664d03;padding:8px 12px;border-radius:8px;font-size:13px}
svg{display:block;max-width:100%}footer{color:var(--mut);font-size:12px;margin-top:40px}
"""


def _tile(label: str, value: str, delta: str = "") -> str:
    cls = "up" if delta.startswith(" (+") else ("dn" if delta.startswith(" (−") else "")
    return f'<div class="tile"><b>{value}<span class="d {cls}">{html.escape(delta.strip())}</span></b><span>{html.escape(label)}</span></div>'


def _sparkline(points: list[tuple[str, float | None]]) -> str:
    vals = [(d, v) for d, v in points if v is not None]
    if len(vals) < 2:
        return ""
    w, h = 600, 90
    n = len(vals)
    xs = [i * (w - 20) / (n - 1) + 10 for i in range(n)]
    ys = [h - 10 - v * (h - 20) for _, v in vals]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="var(--acc)"><title>{d}: {v*100:.0f}%</title></circle>' for (d, v), x, y in zip(vals, xs, ys))
    return f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}"><path d="{path}" fill="none" stroke="var(--acc)" stroke-width="2"/>{dots}</svg><div class="g">Visibility, {vals[0][0]} → {vals[-1][0]}</div>'


def html_page(rep: dict[str, Any], trend: list[dict[str, Any]] | None = None) -> str:
    b, r, o, prev = rep["brand"], rep["run"], rep["overall"], rep.get("previous")
    po = prev["overall"] if prev else {}
    e = html.escape
    H: list[str] = []
    H.append(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{e(b['name'])} — AI visibility</title><style>{_CSS}</style></head><body><main>")
    H.append(f"<h1>{e(b['name'])} <span class='sub'>· AI visibility · {e(r['date'])}</span></h1>")
    H.append(f"<div class='sub'>{e(b['category'])} · {e(b['domain'])} · {r['prompts']} prompts ({r['unbranded_prompts']} unbranded) · {e(', '.join(r['engines']))} · run {e(r['status'])}" + (f" · vs {e(prev['date'])}" if prev else "") + "</div>")
    if r.get("error"):
        H.append(f"<p class='warn'>⚠ {e(r['error'])}</p>")
    H.append("<div class='tiles'>")
    H.append(_tile("Visibility (unbranded)", fmt_pct(o["visibility"]), _delta(o["visibility"], po.get("visibility"))))
    H.append(_tile("Share of voice", fmt_pct(o["share_of_voice"]), _delta(o["share_of_voice"], po.get("share_of_voice"))))
    H.append(_tile("Citation rate", fmt_pct(o["citation_rate"]), _delta(o["citation_rate"], po.get("citation_rate"))))
    H.append(_tile("Sentiment /100", fmt_num(o["sentiment"], 0), _delta(o["sentiment"], po.get("sentiment"), pct=False)))
    H.append(_tile("Avg rank when named", fmt_num(o["avg_rank"]), _delta(po.get("avg_rank"), o["avg_rank"], pct=False) if po.get("avg_rank") else ""))
    H.append(_tile("Top-3 rate", fmt_pct(o["top3_rate"]), _delta(o["top3_rate"], po.get("top3_rate"))))
    H.append("</div>")
    if trend:
        sp = _sparkline([(t["date"], t["overall"]["visibility"]) for t in trend])
        if sp:
            H.append("<h2>Trend</h2>" + sp)
    H.append("<h2>By engine</h2><table><tr><th>Engine</th><th class='n'>Answered</th><th class='n'>Visibility</th><th class='n'>Share of voice</th><th class='n'>Cited</th><th class='n'>Sentiment</th><th class='n'>Avg rank</th></tr>")
    for x in rep["engines"]:
        px = next((y for y in prev["engines"] if y["engine"] == x["engine"]), {}) if prev else {}
        note = f" <span class='g'>({x['unavailable']} n/a, {x['errors']} err)</span>" if x["unavailable"] or x["errors"] else ""
        H.append(f"<tr><td>{e(x['label'])}</td><td class='n'>{x['answered']}{note}</td><td class='n'>{fmt_pct(x['visibility'])}<span class='d'>{e(_delta(x['visibility'], px.get('visibility')).strip())}</span></td><td class='n'>{fmt_pct(x['share_of_voice'])}</td><td class='n'>{fmt_pct(x['citation_rate'])}</td><td class='n'>{fmt_num(x['sentiment'],0)}</td><td class='n'>{fmt_num(x['avg_rank'])}</td></tr>")
    H.append("</table>")
    H.append("<h2>Leaderboard — who the engines recommend for your prompts</h2><table><tr><th>Brand</th><th>Mention rate</th><th class='n'>Share of voice</th><th class='n'>Avg rank</th><th class='n'>Sentiment</th><th class='n'>Cited</th></tr>")
    for x in rep["leaderboard"]:
        w = int((x["mention_rate"] or 0) * 160)
        H.append(f"<tr class='{'you' if x['is_target'] else ''}'><td>{e(x['name'])}{' <span class=g>(you)</span>' if x['is_target'] else ''}</td><td><span class='bar' style='width:{w}px'></span>{fmt_pct(x['mention_rate'])}</td><td class='n'>{fmt_pct(x['share_of_voice'])}</td><td class='n'>{fmt_num(x['avg_rank'])}</td><td class='n'>{fmt_num(x['sentiment'],0)}</td><td class='n'>{fmt_pct(x['citation_rate'])}</td></tr>")
    H.append("</table>")
    if rep["emerging"]:
        H.append("<p class='g'>Also recommended, not tracked yet: " + ", ".join(f"{e(x['name'])} ({fmt_pct(x['mention_rate'])})" for x in rep["emerging"]) + "</p>")
    H.append("<h2>Topics</h2><table><tr><th>Topic</th><th>Visibility</th><th class='n'>Answers</th></tr>")
    for t in rep["topics"]:
        H.append(f"<tr><td>{e(t['topic'])}</td><td><span class='bar' style='width:{int((t['visibility'] or 0)*160)}px'></span>{fmt_pct(t['visibility'])}</td><td class='n'>{t['answered']}</td></tr>")
    H.append("</table>")
    if rep["gaps"]:
        H.append("<h2>Gaps — engines recommend competitors, not you</h2><ul>")
        for g in rep["gaps"]:
            H.append(f"<li>{e(g['prompt'])}<div class='g'>missing on {e(', '.join(g['missing_on']))} · recommended: {e(', '.join(g['competitors'][:5]))}</div></li>")
        H.append("</ul>")
    if rep["wins"] or rep["losses"]:
        H.append("<h2>Gained / lost since previous run</h2><ul>")
        for w in rep["wins"]:
            H.append(f"<li><span class='up'>✚</span> {e(w['prompt'])} <span class='g'>— {e(', '.join(w['engines']))}</span></li>")
        for w in rep["losses"]:
            H.append(f"<li><span class='dn'>✖</span> {e(w['prompt'])} <span class='g'>— {e(', '.join(w['engines']))}</span></li>")
        H.append("</ul>")
    if rep["top_domains"]:
        H.append("<h2>Sources the engines cite most</h2><table><tr><th>Domain</th><th class='n'>Answers</th><th class='n'>Share</th><th></th></tr>")
        for d in rep["top_domains"]:
            tag = "you" if d["owner"] == "you" else ("competitor" if d["owner"] == "competitor" else "")
            H.append(f"<tr class='{'you' if tag == 'you' else ''}'><td>{e(d['domain'])}</td><td class='n'>{d['answers']}</td><td class='n'>{fmt_pct(d['share'])}</td><td class='g'>{tag}</td></tr>")
        H.append("</table>")
    if rep["peers"]:
        H.append("<h2>Tracked peers</h2><ul>" + "".join(f"<li>{e(p['name'])}: visibility {fmt_pct(p['visibility'])}, sentiment {fmt_num(p['sentiment'],0)}, cited {fmt_pct(p['citation_rate'])} <span class='g'>({e(p['date'])}, own prompt set)</span></li>" for p in rep["peers"]) + "</ul>")
    cost_note = "billed" if r.get("billed") else "estimated from list prices"
    H.append(f"<footer>Costs this run: engines {fmt_usd(r['api_cost'])} ({cost_note}), scoring {fmt_usd(r['llm_cost'])} · {r['llm_tokens']['in']:,} in / {r['llm_tokens']['out']:,} out tokens · echolocate</footer>")
    H.append("</main></body></html>")
    return "".join(H)


def as_json(rep: dict[str, Any]) -> str:
    return json.dumps(rep, indent=2, ensure_ascii=False)
