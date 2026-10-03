"""Score every engine's answer to one prompt in a single Claude call: mentioned? rank? sentiment? who else was named?"""
from __future__ import annotations

from typing import Any

from .config import ENGINE_LABELS, config
from .db import AnswerRow, Brand, save_analysis
from .llm import Usage, arr, boolean, num, obj, s, structured
from .onboarding import competitor_keys
from .util import brand_key, mentions_any

ANALYSIS_SCHEMA = obj({
    "answers": arr(obj({
        "id": s("The answer id, e.g. A1"),
        "target_mentioned": boolean(),
        "target_rank": num("1-based position of the target among the brands in `brands`; null when not mentioned", nullable=True),
        "target_sentiment": s(enum=["positive", "neutral", "negative", "not_mentioned"]),
        "target_sentiment_score": num("-1 (very negative) to 1 (very positive); 0 when not mentioned"),
        "target_context": s("Up to 25 words on how the answer portrays the target; empty when not mentioned"),
        "brands": arr(obj({"name": s(), "sentiment": s(enum=["positive", "neutral", "negative"])}),
                      "Every company/product brand the answer names as an option, in order of first appearance, target included"),
    }))
})


def system_prompt(brand: Brand) -> str:
    known = "\n".join(
        f"- {c['name']} ({c.get('domain','')})" + (f" aka {', '.join(c['aliases'])}" if c.get("aliases") else "") for c in brand.competitors
    ) or "- none listed"
    return (
        "You audit how AI assistants talk about a target brand. For each answer, decide whether the target brand is "
        "mentioned (by name, alias, product name or domain — inside the answer prose, not just in a URL), where it ranks "
        "among the brands the answer names, and how the answer portrays it.\n\n"
        "Sentiment is about the target only: positive = recommended/praised, neutral = listed without judgement, "
        "negative = criticised, discouraged or flagged with drawbacks that dominate.\n"
        "`brands` lists every vendor/product/company the answer offers as an option, in order of first appearance. Skip "
        "publishers, review sites and the AI assistant itself unless they are offered as a solution. Use the canonical "
        "names below whenever a brand refers to one of them.\n\n"
        f"Target brand: {brand.name} ({brand.domain})" + (f", aka {', '.join(brand.aliases)}" if brand.aliases else "") + "\n"
        f"Known competitors:\n{known}"
    )


def _fallback(a: AnswerRow, aliases: list[str]) -> None:
    save_analysis(a.id, {"mentioned": mentions_any(a.response_text, aliases), "rank": None, "sentiment": None, "sentiment_score": None, "context": "", "brands": []}, analyzed=False)


def analyze_prompt(brand: Brand, prompt_text: str, answers: list[AnswerRow]) -> Usage:
    usable = [a for a in answers if a.response_text.strip() and not a.error]
    if not usable:
        return Usage()
    keys = competitor_keys(brand)
    aliases = [brand.name, *brand.aliases]
    try:
        data, usage = structured(
            ANALYSIS_SCHEMA,
            system=system_prompt(brand),
            user=f"Prompt the user asked: {prompt_text}\n\n" + "\n\n".join(
                f'<answer id="A{i+1}" engine="{ENGINE_LABELS.get(a.engine, a.engine)}">\n{a.response_text[:12000]}\n</answer>' for i, a in enumerate(usable)
            ),
            model=config.analysis_model,
            max_tokens=8000,
        )
    except Exception:
        for a in usable:
            _fallback(a, aliases)
        raise
    results = data.get("answers", [])
    for i, a in enumerate(usable):
        r = next((x for x in results if str(x.get("id", "")).strip().upper() == f"A{i+1}"), results[i] if i < len(results) else None)
        if not r:
            _fallback(a, aliases)
            continue
        brands: list[dict[str, Any]] = []
        for b in r.get("brands", []):
            nm = (b.get("name") or "").strip()
            if not nm:
                continue
            known = keys.get(brand_key(nm))
            brands.append({"key": brand_key(known["name"] if known else nm), "name": known["name"] if known else nm,
                           "sentiment": b.get("sentiment") or "neutral", "is_target": bool(known and known["is_target"])})
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for b in brands:  # collapse repeats (product name + company name)
            if b["key"] in seen:
                continue
            seen.add(b["key"])
            unique.append({**b, "rank": len(unique) + 1})
        target = next((b for b in unique if b["is_target"]), None)
        mentioned = bool(r.get("target_mentioned")) or target is not None
        sent = r.get("target_sentiment")
        sentiment = ("neutral" if mentioned else None) if sent == "not_mentioned" or sent not in ("positive", "neutral", "negative") else sent
        if mentioned and target is None:
            target = {"key": brand_key(brand.name), "name": brand.name, "rank": len(unique) + 1, "sentiment": sentiment or "neutral", "is_target": True}
            unique.append(target)
        if target is not None and sentiment:
            target["sentiment"] = sentiment  # one verdict on the target: the explicit one, also in the leaderboard
        score = r.get("target_sentiment_score")
        try:
            score = max(-1.0, min(1.0, float(score))) if score is not None else None
        except (TypeError, ValueError):
            score = None
        save_analysis(a.id, {
            "mentioned": mentioned,
            "rank": (target["rank"] if target else r.get("target_rank")) if mentioned else None,
            "sentiment": sentiment,
            "sentiment_score": score if mentioned else None,
            "context": (r.get("target_context") or "") if mentioned else "",
            "brands": unique,
        })
    return usage
