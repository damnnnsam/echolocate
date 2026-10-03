"""The daily job: every active prompt × every engine, then score. Resumable — re-running the same day only retries failures."""
from __future__ import annotations

import threading
from typing import Any

from .analyze import analyze_prompt
from .config import ENGINE_LABELS, config
from .db import (Brand, Run, add_run_llm_usage, answers_for_prompt, existing_pairs, finish_run, get_or_create_run, get_prompt,
                 list_prompts, unanalyzed_prompt_ids, upsert_answer)
from .engines import ProviderFatalError, ask
from .llm import Usage
from .util import log, pool, today


def run_brand(brand: Brand, run_date: str | None = None, limit: int | None = None, engines: list[str] | None = None,
              prompt_ids: list[int] | None = None, force: bool = False, quiet: bool = False) -> Run:
    date = run_date or today()
    engines = engines or brand.engines
    if not engines:
        raise RuntimeError("no engines enabled for this brand — check `echolocate status` and `echolocate engines --add …`")
    prompts = list_prompts(brand.id)
    if prompt_ids:
        prompts = [p for p in prompts if p.id in set(prompt_ids)]
    if limit:
        prompts = prompts[:limit]
    if not prompts:
        raise RuntimeError("no active prompts")
    run = get_or_create_run(brand.id, date, engines)
    have = existing_pairs(run.id)
    todo = [(p, e) for p in prompts for e in engines if force or (p.id, e) not in have or have[(p.id, e)].error]
    say = (lambda m: None) if quiet else log
    say(f"→ {brand.name} · {date} · {len(prompts)} prompts × {len(engines)} engines · {len(todo)} calls to make ({len(prompts)*len(engines)-len(todo)} already stored)")

    fatal: dict[str, str] = {}  # engine -> account-level error; other engines keep going
    done = [0]
    lock = threading.Lock()

    def one(pair: tuple[Any, str]) -> None:
        p, e = pair
        if e in fatal:
            upsert_answer(run.id, p.id, e, {"engine": e, "available": False, "error": fatal[e]})
            return
        try:
            a = ask(e, p.text, brand.domain, brand.country, brand.language)
        except ProviderFatalError as ex:
            with lock:
                if e not in fatal:
                    fatal[e] = str(ex)
                    log(f"  ✖ {ENGINE_LABELS.get(e, e)}: {ex} — skipping this engine for the rest of the run")
            upsert_answer(run.id, p.id, e, {"engine": e, "available": False, "error": str(ex)})
            return
        upsert_answer(run.id, p.id, e, a.as_dict())
        with lock:
            done[0] += 1
            n = done[0]
        if n % 10 == 0 or n == len(todo):
            say(f"  {n}/{len(todo)} answers" + (f" · last: {ENGINE_LABELS.get(e, e)} {'ok' if not a.error else 'ERR ' + a.error[:60]}" if not quiet else ""))

    pool(todo, config.engine_concurrency, one, on_error="collect")

    # Phase 2: score each prompt's answers (all engines in one call).
    pids = unanalyzed_prompt_ids(run.id)
    say(f"→ Scoring {len(pids)} prompts with {config.analysis_model} …")
    usage = Usage()
    errs: list[str] = []

    def score(pid: int) -> None:
        answers = answers_for_prompt(run.id, pid)
        try:
            u = analyze_prompt(brand, get_prompt(pid).text, answers)
        except Exception as ex:  # noqa: BLE001
            with lock:
                errs.append(f"prompt {pid}: {ex}")
            return
        with lock:
            usage.add(u)

    pool(pids, config.prompt_concurrency, score, on_error="collect")
    add_run_llm_usage(run.id, usage.in_tokens, usage.out_tokens, usage.cost)
    for e in errs[:5]:
        log(f"  scoring error: {e}")

    rows = existing_pairs(run.id)
    failed = sum(1 for a in rows.values() if a.error)
    if fatal and len(fatal) == len(engines):
        status, err = "failed", "; ".join(f"{k}: {v}" for k, v in fatal.items())
    elif fatal or failed or errs:
        status, err = "partial", "; ".join([*(f"{ENGINE_LABELS.get(k, k)} down: {v[:80]}" for k, v in fatal.items()), *([f"{failed} answer errors"] if failed else []), *([f"{len(errs)} scoring errors"] if errs else [])])
    else:
        status, err = "complete", None
    run = finish_run(run.id, status, err)
    say(f"✓ {status}: {run.api_calls} answers, engines ≈ ${run.api_cost:.2f}, scoring ≈ ${run.llm_cost:.2f}" + (f" — {err}" if err else ""))
    return run


def rescore(brand: Brand, run_date: str | None = None) -> Usage:
    """Re-run scoring for answers left unanalyzed (e.g. after a scoring outage)."""
    from .db import latest_run
    run = latest_run(brand.id, run_date)
    if not run:
        raise RuntimeError("no run to score")
    usage = Usage()
    for pid in unanalyzed_prompt_ids(run.id):
        usage.add(analyze_prompt(brand, get_prompt(pid).text, answers_for_prompt(run.id, pid)))
    add_run_llm_usage(run.id, usage.in_tokens, usage.out_tokens, usage.cost)
    return usage
