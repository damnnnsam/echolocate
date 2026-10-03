"""Turn a run's answers into the report: visibility, citations, sentiment, share of voice, leaderboard, gaps, wins/losses."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .config import ENGINE_LABELS
from .db import AnswerRow, Brand, Mention, Run, answers_for_run, latest_run, list_brands, list_runs, mentions_for_run, previous_run
from .util import brand_key, domain_of, match_domain

SENTIMENT_POINTS = {"positive": 100.0, "neutral": 50.0, "negative": 0.0}


def _avg(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _rate(n: int, d: int) -> float | None:
    return n / d if d else None


def _ok(a: AnswerRow) -> bool:
    return a.available and not a.error


@dataclass
class EngineStats:
    engine: str
    label: str
    answered: int
    unavailable: int
    errors: int
    visibility: float | None
    mention_rate_all: float | None
    citation_rate: float | None
    avg_citation_position: float | None
    avg_rank: float | None
    top3_rate: float | None
    sentiment: float | None
    positive: int
    neutral: int
    negative: int
    share_of_voice: float | None


def engine_stats(engine: str, answers: list[AnswerRow], mentions: list[Mention]) -> EngineStats:
    ok = [a for a in answers if _ok(a)]
    unbranded = [a for a in ok if not a.branded]
    mentioned = [a for a in ok if a.mentioned]
    mu = [a for a in unbranded if a.mentioned]
    cited = [a for a in ok if a.cited]
    ids = {a.id for a in unbranded}
    ubm = [m for m in mentions if m.answer_id in ids]
    sents = [((a.sentiment_score or 0) + 1) / 2 * 100 for a in mentioned if a.sentiment_score is not None]
    return EngineStats(
        engine=engine, label="All engines" if engine == "all" else ENGINE_LABELS.get(engine, engine),
        answered=len(ok), unavailable=sum(1 for a in answers if not a.available and not a.error), errors=sum(1 for a in answers if a.error),
        visibility=_rate(len(mu), len(unbranded)), mention_rate_all=_rate(len(mentioned), len(ok)), citation_rate=_rate(len(cited), len(ok)),
        avg_citation_position=_avg([float(a.citation_position) for a in cited if a.citation_position is not None]),
        avg_rank=_avg([float(a.mention_rank) for a in mu if a.mention_rank is not None]),
        top3_rate=_rate(sum(1 for a in mu if a.mention_rank is not None and a.mention_rank <= 3), len(unbranded)),
        sentiment=_avg(sents), positive=sum(1 for a in mentioned if a.sentiment == "positive"), neutral=sum(1 for a in mentioned if a.sentiment == "neutral"),
        negative=sum(1 for a in mentioned if a.sentiment == "negative"), share_of_voice=_rate(sum(1 for m in ubm if m.is_target), len(ubm)),
    )


def stats_for_run(run: Run) -> dict[str, Any]:
    answers = answers_for_run(run.id)
    mentions = mentions_for_run(run.id)
    engines = [engine_stats(e, [a for a in answers if a.engine == e], mentions) for e in run.engines]
    return {"overall": engine_stats("all", answers, mentions), "engines": engines, "answers": answers, "mentions": mentions}


def headline(brand_id: int, date: str | None = None) -> dict[str, Any] | None:
    run = latest_run(brand_id, date, finished_only=True)
    if not run:
        return None
    return {"run": run, "overall": stats_for_run(run)["overall"]}


def build_report(brand: Brand, date: str | None = None) -> dict[str, Any]:
    run = latest_run(brand.id, date)
    if not run:
        raise RuntimeError(f"No runs yet for {brand.name}. Run `echolocate run` first.")
    st = stats_for_run(run)
    overall, engines, answers, mentions = st["overall"], st["engines"], st["answers"], st["mentions"]
    prev = previous_run(brand.id, run.run_date)
    prev_st = stats_for_run(prev) if prev else None

    ok = [a for a in answers if _ok(a)]
    unbranded = [a for a in ok if not a.branded]
    ub_ids = {a.id for a in unbranded}
    ubm = [m for m in mentions if m.answer_id in ub_ids]
    known_domains: dict[str, str | None] = {brand_key(brand.name): brand.domain}
    for c in brand.competitors:
        known_domains[brand_key(c["name"])] = c.get("domain")
    groups: dict[str, list[Mention]] = {}
    for m in ubm:
        groups.setdefault(m.brand_key, []).append(m)
    for k in known_domains:
        groups.setdefault(k, [])

    all_brands = []
    for key, ms in groups.items():
        is_target = key == brand_key(brand.name) or any(m.is_target for m in ms)
        domain = known_domains.get(key)
        name = brand.name if is_target else next((c["name"] for c in brand.competitors if brand_key(c["name"]) == key), ms[0].name if ms else key)
        answer_ids = {m.answer_id for m in ms}
        all_brands.append({
            "key": key, "name": name, "is_target": is_target, "known": key in known_domains, "domain": domain,
            "mentions": len(answer_ids), "mention_rate": _rate(len(answer_ids), len(unbranded)),
            "avg_rank": _avg([float(m.rank) for m in ms if m.rank is not None]),
            "sentiment": _avg([SENTIMENT_POINTS.get(m.sentiment or "", 50.0) for m in ms if m.sentiment]),
            "citation_rate": _rate(sum(1 for a in unbranded if any(match_domain(c.get("domain") or domain_of(c["url"]), domain) for c in a.citations)), len(unbranded)) if domain else None,
            "share_of_voice": _rate(len(ms), len(ubm)),
        })
    by_rate = lambda b: ((b["mention_rate"] or 0), b["mentions"])  # noqa: E731
    leaderboard = sorted([b for b in all_brands if b["known"] or b["is_target"]], key=by_rate, reverse=True)
    emerging = sorted([b for b in all_brands if not b["known"] and not b["is_target"] and b["mentions"] > 0], key=by_rate, reverse=True)[:8]

    domain_counts: dict[str, int] = {}
    for a in ok:
        for d in {c.get("domain") or domain_of(c["url"]) for c in a.citations}:
            if d:
                domain_counts[d] = domain_counts.get(d, 0) + 1
    top_domains = [
        {"domain": d, "answers": n, "share": n / max(1, len(ok)),
         "owner": "you" if match_domain(d, brand.domain) else ("competitor" if any(match_domain(d, c.get("domain")) for c in brand.competitors) else None)}
        for d, n in sorted(domain_counts.items(), key=lambda kv: kv[1], reverse=True)[:15]
    ]

    topic_map: dict[str, list[AnswerRow]] = {}
    for a in unbranded:
        topic_map.setdefault(a.topic or "Uncategorised", []).append(a)
    topics = sorted(
        [{"topic": t, "answered": len(xs), "visibility": _rate(sum(1 for a in xs if a.mentioned), len(xs))} for t, xs in topic_map.items()],
        key=lambda t: t["visibility"] or 0, reverse=True,
    )

    wins: list[dict] = []
    losses: list[dict] = []
    if prev_st:
        before = {(a.prompt_id, a.engine): bool(a.mentioned) for a in prev_st["answers"] if _ok(a)}
        by_prompt: dict[int, dict] = {}
        for a in ok:
            was = before.get((a.prompt_id, a.engine))
            if was is None or was == bool(a.mentioned):
                continue
            e = by_prompt.setdefault(a.prompt_id, {"prompt_id": a.prompt_id, "prompt": a.prompt_text, "won": [], "lost": []})
            (e["won"] if a.mentioned else e["lost"]).append(ENGINE_LABELS.get(a.engine, a.engine))
        for e in by_prompt.values():
            if e["won"]:
                wins.append({"prompt_id": e["prompt_id"], "prompt": e["prompt"], "engines": e["won"]})
            if e["lost"]:
                losses.append({"prompt_id": e["prompt_id"], "prompt": e["prompt"], "engines": e["lost"]})
        wins.sort(key=lambda x: len(x["engines"]), reverse=True)
        losses.sort(key=lambda x: len(x["engines"]), reverse=True)

    by_answer: dict[int, list[Mention]] = {}
    for m in ubm:
        by_answer.setdefault(m.answer_id, []).append(m)
    gap_map: dict[int, dict] = {}
    for a in unbranded:
        if a.mentioned:
            continue
        rivals = [m for m in by_answer.get(a.id, []) if not m.is_target]
        if not rivals:
            continue
        g = gap_map.setdefault(a.prompt_id, {"prompt_id": a.prompt_id, "prompt": a.prompt_text, "topic": a.topic, "competitors": [], "missing_on": []})
        g["missing_on"].append(ENGINE_LABELS.get(a.engine, a.engine))
        for r in sorted(rivals, key=lambda m: m.rank or 99):
            if r.name not in g["competitors"]:
                g["competitors"].append(r.name)
    gaps = sorted(gap_map.values(), key=lambda g: (len(g["missing_on"]), len(g["competitors"])), reverse=True)[:15]

    rival_domains = {c.get("domain") for c in brand.competitors}
    peers = []
    for b in list_brands():
        if b.id == brand.id or not (b.domain in rival_domains or any(c.get("domain") == brand.domain for c in b.competitors)):
            continue
        h = headline(b.id, run.run_date)
        if h:
            o = h["overall"]
            peers.append({"name": b.name, "domain": b.domain, "date": h["run"].run_date, "visibility": o.visibility, "sentiment": o.sentiment, "citation_rate": o.citation_rate})

    return {
        "brand": {"id": brand.id, "slug": brand.slug, "name": brand.name, "domain": brand.domain, "category": brand.profile.get("category", "")},
        "run": {"id": run.id, "date": run.run_date, "status": run.status, "error": run.error, "engines": run.engines,
                "prompts": len({a.prompt_id for a in answers}), "unbranded_prompts": len({a.prompt_id for a in answers if not a.branded}),
                "api_cost": run.api_cost, "llm_cost": run.llm_cost, "llm_tokens": {"in": run.llm_input_tokens, "out": run.llm_output_tokens},
                "billed": all(a.cost_source == "billed" for a in answers) if answers else False},
        "overall": asdict(overall), "engines": [asdict(e) for e in engines],
        "previous": {"date": prev.run_date, "overall": asdict(prev_st["overall"]), "engines": [asdict(e) for e in prev_st["engines"]]} if prev and prev_st else None,
        "leaderboard": leaderboard, "emerging": emerging, "top_domains": top_domains, "topics": topics,
        "wins": wins[:10], "losses": losses[:10], "gaps": gaps, "peers": peers,
    }


def build_trend(brand: Brand, days: int = 30) -> list[dict[str, Any]]:
    out = []
    for r in reversed([r for r in list_runs(brand.id, days) if r.status in ("complete", "partial")]):
        st = stats_for_run(r)
        out.append({"date": r.run_date, "status": r.status, "overall": asdict(st["overall"]), "engines": [asdict(e) for e in st["engines"]]})
    return out


def answers_for(brand: Brand, date: str | None = None, engine: str | None = None, prompt_id: int | None = None, mentioned: bool | None = None) -> list[AnswerRow]:
    run = latest_run(brand.id, date)
    if not run:
        return []
    return [
        a for a in answers_for_run(run.id)
        if (not engine or a.engine == engine) and (prompt_id is None or a.prompt_id == prompt_id) and (mentioned is None or bool(a.mentioned) == mentioned)
    ]
