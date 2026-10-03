"""One-off setup per brand: read the site, build a profile + ICP + competitors, define topics, write the prompts."""
from __future__ import annotations

import re
from typing import Any

from .config import config
from .db import Brand, Prompt, Topic, insert_brand, insert_prompt, list_brands, list_prompts, list_topics, upsert_topic
from .llm import Usage, arr, boolean, num, obj, s, structured
from .site import Page, crawl_site, normalize_url
from .util import brand_key, domain_of, log, mentions_any, normalize_domain, pool, slugify

BRAND_TOPIC = "Brand & reputation"

PROFILE_SCHEMA = obj({
    "name": s("The brand name as customers say it"),
    "aliases": arr(s(), "Other names people use: product names, short names, legal name. Exclude generic words."),
    "one_liner": s(),
    "description": s("3-5 sentences: what it is, who it serves, how it works"),
    "category": s("The market category buyers would search for, e.g. 'cold email software'"),
    "products": arr(s()),
    "value_props": arr(s()),
    "differentiators": arr(s()),
    "icp": obj({
        "industries": arr(s()),
        "company_sizes": arr(s()),
        "buyer_roles": arr(s()),
        "pains": arr(s()),
        "geographies": arr(s()),
    }),
    "use_cases": arr(s()),
    "pricing_model": s(),
    "competitors": arr(obj({"name": s(), "domain": s(), "aliases": arr(s())}), "5-10 real companies buyers compare against this brand, most direct first"),
})

TOPICS_SCHEMA = obj({
    "topics": arr(obj({
        "name": s("2-5 words"),
        "description": s("What buyers are trying to figure out in this topic"),
        "weight": num("1-5: how commercially important this topic is for the brand"),
    }))
})

PROMPTS_SCHEMA = obj({
    "prompts": arr(obj({
        "text": s(),
        "intent": s(enum=["discovery", "problem", "comparison", "branded"]),
    }))
})


def pages_block(pages: list[Page]) -> str:
    if not pages:
        return "(The website could not be fetched. Use what you know about this domain.)"
    return "\n\n---\n\n".join(f"### {p.url}\nTitle: {p.title}\nMeta description: {p.description}\n\n{p.text}" for p in pages)


def profile_block(b: Brand) -> str:
    p = b.profile
    icp = p.get("icp", {})
    j = lambda xs: "; ".join(xs or [])  # noqa: E731
    lines = [
        f"Brand: {b.name} ({b.domain})",
        f"Also known as: {', '.join(b.aliases)}" if b.aliases else "",
        f"Category: {p.get('category','')}",
        f"What it is: {p.get('description','')}",
        f"Products: {j(p.get('products'))}",
        f"Value props: {j(p.get('value_props'))}",
        f"Differentiators: {j(p.get('differentiators'))}",
        f"ICP industries: {j(icp.get('industries'))}",
        f"ICP company sizes: {j(icp.get('company_sizes'))}",
        f"ICP buyer roles: {j(icp.get('buyer_roles'))}",
        f"ICP pains: {j(icp.get('pains'))}",
        f"Geographies: {j(icp.get('geographies'))}",
        f"Use cases: {j(p.get('use_cases'))}",
        f"Pricing model: {p.get('pricing_model','')}",
        "Competitors: " + "; ".join(f"{c['name']} ({c['domain']})" for c in b.competitors),
    ]
    return "\n".join(x for x in lines if x)


def allocate(total: int, weights: list[float]) -> list[int]:
    """Largest-remainder split of `total` across weights; every bucket gets at least one when possible."""
    if not weights:
        return []
    ssum = sum(weights) or len(weights)
    raw = [total * (w or 1) / ssum for w in weights]
    out = [int(x) for x in raw]
    left = total - sum(out)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - int(raw[i]), reverse=True)
    k = 0
    while left > 0:
        out[order[k % len(order)]] += 1
        k += 1
        left -= 1
    if total >= len(weights):
        for i in range(len(out)):
            while out[i] == 0:
                donor = out.index(max(out))
                out[donor] -= 1
                out[i] += 1
    return out


def build_profile(url: str, name_hint: str | None = None) -> dict[str, Any]:
    log(f"→ Reading {normalize_url(url)} …")
    final_url, pages = crawl_site(url)
    log(f"  fetched {len(pages)} page(s)")
    log("→ Building company profile, ICP and competitor set with Claude …")
    data, usage = structured(
        PROFILE_SCHEMA,
        system=(
            "You are a senior market analyst. From a company's website you produce a precise positioning profile: "
            "what it sells, the category buyers would search for, its ideal customer profile, and the real competitors buyers "
            "evaluate it against. Be specific and factual; use your own market knowledge to fill gaps the website leaves, "
            "especially for competitors. Never invent products the site does not support."
        ),
        user=f"Website: {final_url}\n{('The brand is called: ' + name_hint) if name_hint else ''}\n\nPages fetched from the site:\n\n{pages_block(pages)}",
        max_tokens=6000,
    )
    domain = domain_of(final_url)
    competitors = []
    for c in data.get("competitors", []):
        nm, dm = (c.get("name") or "").strip(), normalize_domain(c.get("domain") or "")
        if nm and dm != domain:
            competitors.append({"name": nm, "domain": dm, "aliases": [a for a in c.get("aliases", []) if a]})
    name = (name_hint or data.get("name") or domain).strip()
    aliases = list(dict.fromkeys(a.strip() for a in data.get("aliases", []) if a and a.strip().lower() != name.lower()))
    profile = {k: v for k, v in data.items() if k not in ("name", "aliases", "competitors")}
    return {"final_url": final_url, "name": name, "aliases": aliases, "profile": profile, "competitors": competitors, "usage": usage}


def generate_topics(brand: Brand, count: int) -> tuple[list[Topic], Usage]:
    log(f"→ Defining {count} prompt topics …")
    data, usage = structured(
        TOPICS_SCHEMA,
        system=(
            "You design prompt sets for tracking a brand's visibility in AI assistants (ChatGPT, Claude, Gemini, Perplexity, "
            "Google AI Overviews). A topic is a cluster of questions real prospective customers ask an AI assistant while "
            "looking for a solution in this brand's market. Topics must be about the buyer's problem space and category, "
            "not about the brand itself. Cover the funnel: category discovery, specific pains/jobs-to-be-done, use cases, "
            "segment-specific needs (industry, company size, role), and evaluation criteria (pricing, integrations, alternatives)."
        ),
        user=f"{profile_block(brand)}\n\nDefine exactly {count} distinct, non-overlapping topics.",
    )
    topics = [
        upsert_topic(brand.id, t["name"].strip(), (t.get("description") or "").strip(), min(5, max(1, round(float(t.get("weight", 3))))))
        for t in data.get("topics", [])[:count]
    ]
    return topics, usage


def _prompts_for_topic(brand: Brand, topic: Topic, count: int, existing: list[str], language: str) -> tuple[list[dict], Usage]:
    branded = topic.name == BRAND_TOPIC
    aka = f" or {', '.join(brand.aliases)}" if brand.aliases else ""
    rules = (
        f'Every prompt must name {brand.name} explicitly. Mix: reviews and reputation ("is {brand.name} any good for…"), '
        f'pricing and value, head-to-head comparisons with the named competitors, "alternatives to {brand.name}", and '
        f'fit for specific ICP situations. Use intent "branded".'
        if branded
        else f"Prompts must NOT mention {brand.name}{aka}. They may name competitors (e.g. \"alternatives to <competitor>\", "
        f"\"<competitor> vs <competitor>\"). Use intent \"discovery\" (best X for Y / recommend a tool), \"problem\" (how do I solve a pain), or \"comparison\"."
    )
    data, usage = structured(
        PROMPTS_SCHEMA,
        system=(
            "You write the exact messages real people type into AI assistants (ChatGPT, Gemini, Perplexity, Claude, Google) "
            "when they need a product or service. We send each prompt to every assistant daily and check whether the brand "
            "is recommended, so prompts must be the kind whose honest answer names specific vendors or products.\n\n"
            "Write like real users: vary length (from 6-word queries to 2-3 sentence messages with context), vary persona "
            "(roles, company sizes, industries, budgets, regions from the ICP), include concrete constraints "
            "(team size, stack, budget, compliance), and keep one question per prompt. Keep every prompt under 400 characters. "
            "No numbering, no quotes, no hashtags."
        ),
        user=(
            f"{profile_block(brand)}\n\nTopic: {topic.name}\nTopic focus: {topic.description}\n\n"
            f'Write exactly {count} prompts for this topic in language "{language}".\n{rules}\n'
            + (("\nDo not repeat or closely paraphrase these existing prompts:\n" + "\n".join(f"- {e}" for e in existing)) if existing else "")
        ),
    )
    return data.get("prompts", []), usage


def _norm(t: str) -> str:
    return re.sub(r"[^\w]+", " ", t.lower(), flags=re.UNICODE).strip()


def generate_prompts(brand: Brand, count: int, branded_share: float = 0.15, language: str | None = None) -> tuple[int, Usage]:
    """Adds `count` new prompts across the brand's topics (weighted), skipping near-duplicates."""
    topics = list_topics(brand.id)
    if not topics:
        raise RuntimeError("brand has no topics yet")
    brand_topic = next((t for t in topics if t.name == BRAND_TOPIC), None)
    others = [t for t in topics if t.name != BRAND_TOPIC]
    branded_count = round(count * branded_share) if brand_topic else 0
    split = allocate(count - branded_count, [t.weight for t in others])
    plan: list[tuple[Topic, int]] = [(t, n) for t, n in zip(others, split) if n > 0]
    if brand_topic and branded_count > 0:
        plan.append((brand_topic, branded_count))

    existing = list_prompts(brand.id, active_only=False)
    seen = {_norm(p.text) for p in existing}
    aliases = [brand.name, *brand.aliases]
    usage = Usage()
    added = 0
    lang = language or brand.language
    import threading
    lock = threading.Lock()

    def work(item: tuple[Topic, int]) -> None:
        nonlocal added
        topic, n = item
        need = n
        for _attempt in range(2):
            if need <= 0:
                break
            with lock:
                prior = [p.text for p in existing if p.topic_id == topic.id]
            res, u = _prompts_for_topic(brand, topic, need, prior, lang)
            with lock:
                usage.add(u)
                for p in res:
                    if need <= 0:
                        break
                    text = (p.get("text") or "").strip()
                    key = _norm(text)
                    if not key or key in seen or len(text) > 500:
                        continue
                    is_branded = mentions_any(text, aliases)
                    if topic.name != BRAND_TOPIC and is_branded:
                        continue
                    seen.add(key)
                    intent = "branded" if is_branded else ("discovery" if p.get("intent") == "branded" else (p.get("intent") or "discovery"))
                    row = insert_prompt(brand.id, text, topic.id, intent, is_branded)
                    if row:
                        existing.append(row)
                        added += 1
                        need -= 1
        log(f"  {topic.name}: {n - need}/{n} prompts")

    pool(plan, 4, work)
    return added, usage


def onboard(url: str, prompts: int = 100, topics: int = 8, name: str | None = None, country: str | None = None,
            language: str | None = None, engines: list[str] | None = None, branded_share: float = 0.15) -> tuple[Brand, int, Usage]:
    domain = domain_of(normalize_url(url))
    for b in list_brands():
        if b.domain == domain:
            raise RuntimeError(f"{domain} is already tracked as '{b.slug}'. Use `echolocate prompts generate` to add prompts.")
    p = build_profile(url, name)
    slug = slugify(p["name"])
    if any(b.slug == slug for b in list_brands()):
        slug = f"{slug}-{slugify(domain)}"
    brand = insert_brand(
        slug=slug, name=p["name"], domain=domain_of(p["final_url"]) or domain, url=p["final_url"], aliases=p["aliases"],
        profile=p["profile"], competitors=p["competitors"],
        settings={"country": country or config.country, "language": language or config.language, "engines": engines or config.default_engines},
    )
    log(f"  {brand.name} — {brand.profile.get('category','')}")
    log(f"  competitors: {', '.join(c['name'] for c in brand.competitors)}")
    _, tu = generate_topics(brand, topics)
    if branded_share > 0:
        upsert_topic(brand.id, BRAND_TOPIC, f"How AI assistants describe {brand.name}: reputation, pricing, comparisons and alternatives.", 3)
    log(f"→ Generating {prompts} prompts …")
    added, gu = generate_prompts(brand, prompts, branded_share=branded_share, language=brand.language)
    usage = Usage().add(p["usage"]).add(tu).add(gu)
    return brand, added, usage


def competitor_keys(brand: Brand) -> dict[str, dict[str, Any]]:
    m: dict[str, dict[str, Any]] = {}
    for a in [brand.name, *brand.aliases]:
        m[brand_key(a)] = {"name": brand.name, "domain": brand.domain, "is_target": True}
    for c in brand.competitors:
        for a in [c["name"], *c.get("aliases", [])]:
            m.setdefault(brand_key(a), {"name": c["name"], "domain": c.get("domain"), "is_target": False})
    return m
