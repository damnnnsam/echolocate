from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import config

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS brands (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  domain TEXT NOT NULL,
  url TEXT NOT NULL,
  aliases TEXT NOT NULL DEFAULT '[]',
  profile TEXT NOT NULL DEFAULT '{}',
  competitors TEXT NOT NULL DEFAULT '[]',
  settings TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY,
  brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  weight REAL NOT NULL DEFAULT 1,
  UNIQUE(brand_id, name)
);
CREATE TABLE IF NOT EXISTS prompts (
  id INTEGER PRIMARY KEY,
  brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
  text TEXT NOT NULL,
  intent TEXT NOT NULL DEFAULT 'discovery',
  branded INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(brand_id, text)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
  run_date TEXT NOT NULL,
  status TEXT NOT NULL,
  engines TEXT NOT NULL,
  prompt_count INTEGER NOT NULL DEFAULT 0,
  api_calls INTEGER NOT NULL DEFAULT 0,
  api_cost REAL NOT NULL DEFAULT 0,
  llm_input_tokens INTEGER NOT NULL DEFAULT 0,
  llm_output_tokens INTEGER NOT NULL DEFAULT 0,
  llm_cost REAL NOT NULL DEFAULT 0,
  error TEXT,
  started_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  UNIQUE(brand_id, run_date)
);
CREATE TABLE IF NOT EXISTS answers (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
  engine TEXT NOT NULL,
  model TEXT,
  available INTEGER NOT NULL DEFAULT 1,
  response_text TEXT NOT NULL DEFAULT '',
  citations TEXT NOT NULL DEFAULT '[]',
  cited INTEGER NOT NULL DEFAULT 0,
  citation_position INTEGER,
  error TEXT,
  cost REAL NOT NULL DEFAULT 0,
  cost_source TEXT NOT NULL DEFAULT 'estimate',
  in_tokens INTEGER NOT NULL DEFAULT 0,
  out_tokens INTEGER NOT NULL DEFAULT 0,
  analyzed INTEGER NOT NULL DEFAULT 0,
  mentioned INTEGER,
  mention_rank INTEGER,
  sentiment TEXT,
  sentiment_score REAL,
  brand_context TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(run_id, prompt_id, engine)
);
CREATE TABLE IF NOT EXISTS mentions (
  id INTEGER PRIMARY KEY,
  answer_id INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
  brand_key TEXT NOT NULL,
  name TEXT NOT NULL,
  rank INTEGER,
  sentiment TEXT,
  is_target INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_answers_run ON answers(run_id);
CREATE INDEX IF NOT EXISTS idx_mentions_answer ON mentions(answer_id);
CREATE INDEX IF NOT EXISTS idx_prompts_brand ON prompts(brand_id);
"""

_local = threading.local()
_init_lock = threading.Lock()
_initialized: set[str] = set()


def db() -> sqlite3.Connection:
    path = config.db_path
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == str(path):
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    with _init_lock:
        if str(path) not in _initialized:
            conn.executescript(SCHEMA)
            _initialized.add(str(path))
        else:
            conn.execute("PRAGMA foreign_keys = ON")
    _local.conn = conn
    _local.path = str(path)
    return conn


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- brands
@dataclass
class Brand:
    id: int
    slug: str
    name: str
    domain: str
    url: str
    aliases: list[str]
    profile: dict[str, Any]
    competitors: list[dict[str, Any]]  # {name, domain, aliases}
    settings: dict[str, Any]  # {country, language, engines}
    created_at: str

    @property
    def engines(self) -> list[str]:
        return list(self.settings.get("engines") or [])

    @property
    def country(self) -> str:
        return self.settings.get("country") or config.country

    @property
    def language(self) -> str:
        return self.settings.get("language") or config.language


def _brand(r: sqlite3.Row) -> Brand:
    return Brand(
        id=r["id"], slug=r["slug"], name=r["name"], domain=r["domain"], url=r["url"],
        aliases=json.loads(r["aliases"]), profile=json.loads(r["profile"]),
        competitors=json.loads(r["competitors"]), settings=json.loads(r["settings"]), created_at=r["created_at"],
    )


def insert_brand(slug: str, name: str, domain: str, url: str, aliases: list[str], profile: dict, competitors: list[dict], settings: dict) -> Brand:
    cur = db().execute(
        "INSERT INTO brands(slug,name,domain,url,aliases,profile,competitors,settings) VALUES(?,?,?,?,?,?,?,?)",
        (slug, name, domain, url, json.dumps(aliases), json.dumps(profile), json.dumps(competitors), json.dumps(settings)),
    )
    return get_brand(cur.lastrowid)  # type: ignore[arg-type]


def update_brand(brand_id: int, **fields: Any) -> Brand:
    cols = {"aliases", "profile", "competitors", "settings", "name"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in cols:
            raise ValueError(f"cannot update {k}")
        sets.append(f"{k} = ?")
        vals.append(v if isinstance(v, str) else json.dumps(v))
    if sets:
        db().execute(f"UPDATE brands SET {', '.join(sets)} WHERE id = ?", (*vals, brand_id))
    return get_brand(brand_id)


def get_brand(ref: int | str) -> Brand:
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        r = db().execute("SELECT * FROM brands WHERE id = ?", (int(ref),)).fetchone()
    else:
        r = db().execute("SELECT * FROM brands WHERE slug = ? OR domain = ?", (ref, ref)).fetchone()
    if not r:
        raise KeyError(f"no brand '{ref}' (see `echolocate brands`)")
    return _brand(r)


def find_brand(ref: str | None) -> Brand:
    """A named brand, or the only one if there is exactly one."""
    if ref:
        return get_brand(ref)
    rows = list_brands()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise KeyError("no brands yet — run `echolocate onboard <url>`")
    raise KeyError("several brands tracked; pass --brand <slug>: " + ", ".join(b.slug for b in rows))


def list_brands() -> list[Brand]:
    return [_brand(r) for r in db().execute("SELECT * FROM brands ORDER BY id")]


def delete_brand(brand_id: int) -> None:
    db().execute("DELETE FROM brands WHERE id = ?", (brand_id,))


# ---------------------------------------------------------------- topics
@dataclass
class Topic:
    id: int
    brand_id: int
    name: str
    description: str
    weight: float


def upsert_topic(brand_id: int, name: str, description: str, weight: float) -> Topic:
    db().execute(
        "INSERT INTO topics(brand_id,name,description,weight) VALUES(?,?,?,?) "
        "ON CONFLICT(brand_id,name) DO UPDATE SET description=excluded.description, weight=excluded.weight",
        (brand_id, name, description, weight),
    )
    r = db().execute("SELECT * FROM topics WHERE brand_id=? AND name=?", (brand_id, name)).fetchone()
    return Topic(r["id"], r["brand_id"], r["name"], r["description"], r["weight"])


def list_topics(brand_id: int) -> list[Topic]:
    return [Topic(r["id"], r["brand_id"], r["name"], r["description"], r["weight"]) for r in db().execute("SELECT * FROM topics WHERE brand_id=? ORDER BY id", (brand_id,))]


# ---------------------------------------------------------------- prompts
@dataclass
class Prompt:
    id: int
    brand_id: int
    topic_id: int | None
    text: str
    intent: str
    branded: bool
    active: bool
    topic: str | None = None


def _prompt(r: sqlite3.Row) -> Prompt:
    return Prompt(r["id"], r["brand_id"], r["topic_id"], r["text"], r["intent"], bool(r["branded"]), bool(r["active"]), r["topic"] if "topic" in r.keys() else None)


def insert_prompt(brand_id: int, text: str, topic_id: int | None, intent: str, branded: bool) -> Prompt | None:
    try:
        cur = db().execute("INSERT INTO prompts(brand_id,topic_id,text,intent,branded) VALUES(?,?,?,?,?)", (brand_id, topic_id, text.strip(), intent, int(branded)))
    except sqlite3.IntegrityError:
        return None
    return get_prompt(cur.lastrowid)  # type: ignore[arg-type]


def get_prompt(prompt_id: int) -> Prompt:
    r = db().execute("SELECT p.*, t.name AS topic FROM prompts p LEFT JOIN topics t ON t.id=p.topic_id WHERE p.id=?", (prompt_id,)).fetchone()
    if not r:
        raise KeyError(f"no prompt {prompt_id}")
    return _prompt(r)


def list_prompts(brand_id: int, active_only: bool = True) -> list[Prompt]:
    q = "SELECT p.*, t.name AS topic FROM prompts p LEFT JOIN topics t ON t.id=p.topic_id WHERE p.brand_id=?" + (" AND p.active=1" if active_only else "") + " ORDER BY p.id"
    return [_prompt(r) for r in db().execute(q, (brand_id,))]


def set_prompts_active(brand_id: int, ids: list[int], active: bool) -> int:
    if not ids:
        return 0
    cur = db().execute(f"UPDATE prompts SET active=? WHERE brand_id=? AND id IN ({','.join('?' * len(ids))})", (int(active), brand_id, *ids))
    return cur.rowcount


# ---------------------------------------------------------------- runs
@dataclass
class Run:
    id: int
    brand_id: int
    run_date: str
    status: str  # running | complete | partial | failed
    engines: list[str]
    prompt_count: int
    api_calls: int
    api_cost: float
    llm_input_tokens: int
    llm_output_tokens: int
    llm_cost: float
    error: str | None
    started_at: str
    finished_at: str | None


def _run(r: sqlite3.Row) -> Run:
    return Run(
        r["id"], r["brand_id"], r["run_date"], r["status"], json.loads(r["engines"]), r["prompt_count"], r["api_calls"],
        r["api_cost"], r["llm_input_tokens"], r["llm_output_tokens"], r["llm_cost"], r["error"], r["started_at"], r["finished_at"],
    )


def get_or_create_run(brand_id: int, run_date: str, engines: list[str]) -> Run:
    r = db().execute("SELECT * FROM runs WHERE brand_id=? AND run_date=?", (brand_id, run_date)).fetchone()
    if r:
        run = _run(r)
        merged = list(dict.fromkeys([*run.engines, *engines]))
        db().execute("UPDATE runs SET status='running', engines=?, error=NULL, finished_at=NULL WHERE id=?", (json.dumps(merged), run.id))
        return get_run(run.id)
    cur = db().execute("INSERT INTO runs(brand_id,run_date,status,engines) VALUES(?,?,'running',?)", (brand_id, run_date, json.dumps(engines)))
    return get_run(cur.lastrowid)  # type: ignore[arg-type]


def get_run(run_id: int) -> Run:
    return _run(db().execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def finish_run(run_id: int, status: str, error: str | None = None) -> Run:
    agg = db().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(cost),0) AS cost, COUNT(DISTINCT prompt_id) AS prompts FROM answers WHERE run_id=?", (run_id,)
    ).fetchone()
    db().execute(
        "UPDATE runs SET status=?, error=?, finished_at=?, api_calls=?, api_cost=?, prompt_count=? WHERE id=?",
        (status, error, now(), agg["n"], agg["cost"], agg["prompts"], run_id),
    )
    return get_run(run_id)


def add_run_llm_usage(run_id: int, in_tokens: int, out_tokens: int, cost: float) -> None:
    db().execute(
        "UPDATE runs SET llm_input_tokens=llm_input_tokens+?, llm_output_tokens=llm_output_tokens+?, llm_cost=llm_cost+? WHERE id=?",
        (in_tokens, out_tokens, cost, run_id),
    )


def latest_run(brand_id: int, run_date: str | None = None, finished_only: bool = False) -> Run | None:
    q = "SELECT * FROM runs WHERE brand_id=?"
    args: list[Any] = [brand_id]
    if run_date:
        q += " AND run_date=?"
        args.append(run_date)
    if finished_only:
        q += " AND status IN ('complete','partial')"
    q += " ORDER BY run_date DESC LIMIT 1"
    r = db().execute(q, args).fetchone()
    return _run(r) if r else None


def previous_run(brand_id: int, before_date: str) -> Run | None:
    r = db().execute(
        "SELECT * FROM runs WHERE brand_id=? AND run_date<? AND status IN ('complete','partial') ORDER BY run_date DESC LIMIT 1", (brand_id, before_date)
    ).fetchone()
    return _run(r) if r else None


def list_runs(brand_id: int, limit: int = 30) -> list[Run]:
    return [_run(r) for r in db().execute("SELECT * FROM runs WHERE brand_id=? ORDER BY run_date DESC LIMIT ?", (brand_id, limit))]


# ---------------------------------------------------------------- answers
@dataclass
class AnswerRow:
    id: int
    run_id: int
    prompt_id: int
    engine: str
    model: str | None
    available: bool
    response_text: str
    citations: list[dict[str, Any]]
    cited: bool
    citation_position: int | None
    error: str | None
    cost: float
    cost_source: str
    in_tokens: int
    out_tokens: int
    analyzed: bool
    mentioned: bool | None
    mention_rank: int | None
    sentiment: str | None
    sentiment_score: float | None
    brand_context: str | None
    prompt_text: str = ""
    branded: bool = False
    topic: str | None = None
    intent: str = ""


def _answer(r: sqlite3.Row) -> AnswerRow:
    k = r.keys()
    return AnswerRow(
        id=r["id"], run_id=r["run_id"], prompt_id=r["prompt_id"], engine=r["engine"], model=r["model"], available=bool(r["available"]),
        response_text=r["response_text"], citations=json.loads(r["citations"]), cited=bool(r["cited"]), citation_position=r["citation_position"],
        error=r["error"], cost=r["cost"], cost_source=r["cost_source"], in_tokens=r["in_tokens"], out_tokens=r["out_tokens"],
        analyzed=bool(r["analyzed"]), mentioned=None if r["mentioned"] is None else bool(r["mentioned"]), mention_rank=r["mention_rank"],
        sentiment=r["sentiment"], sentiment_score=r["sentiment_score"], brand_context=r["brand_context"],
        prompt_text=r["prompt_text"] if "prompt_text" in k else "", branded=bool(r["branded"]) if "branded" in k else False,
        topic=r["topic"] if "topic" in k else None, intent=r["intent"] if "intent" in k else "",
    )


_ANSWER_SELECT = (
    "SELECT a.*, p.text AS prompt_text, p.branded AS branded, p.intent AS intent, t.name AS topic "
    "FROM answers a JOIN prompts p ON p.id=a.prompt_id LEFT JOIN topics t ON t.id=p.topic_id "
)


def upsert_answer(run_id: int, prompt_id: int, engine: str, a: dict[str, Any]) -> int:
    db().execute(
        "INSERT INTO answers(run_id,prompt_id,engine,model,available,response_text,citations,cited,citation_position,error,cost,cost_source,in_tokens,out_tokens,analyzed,mentioned,mention_rank,sentiment,sentiment_score,brand_context) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,NULL,NULL,NULL,NULL) "
        "ON CONFLICT(run_id,prompt_id,engine) DO UPDATE SET model=excluded.model, available=excluded.available, response_text=excluded.response_text, "
        "citations=excluded.citations, cited=excluded.cited, citation_position=excluded.citation_position, error=excluded.error, cost=excluded.cost, "
        "cost_source=excluded.cost_source, in_tokens=excluded.in_tokens, out_tokens=excluded.out_tokens, analyzed=0, mentioned=NULL, mention_rank=NULL, "
        "sentiment=NULL, sentiment_score=NULL, brand_context=NULL",
        (
            run_id, prompt_id, engine, a.get("model"), int(a.get("available", True)), a.get("text", ""), json.dumps(a.get("citations", [])),
            int(a.get("cited", False)), a.get("citation_position"), a.get("error"), float(a.get("cost", 0.0)), a.get("cost_source", "estimate"),
            int(a.get("in_tokens", 0)), int(a.get("out_tokens", 0)),
        ),
    )
    r = db().execute("SELECT id FROM answers WHERE run_id=? AND prompt_id=? AND engine=?", (run_id, prompt_id, engine)).fetchone()
    db().execute("DELETE FROM mentions WHERE answer_id=?", (r["id"],))
    return r["id"]


def answers_for_run(run_id: int) -> list[AnswerRow]:
    return [_answer(r) for r in db().execute(_ANSWER_SELECT + "WHERE a.run_id=? ORDER BY a.prompt_id, a.engine", (run_id,))]


def answers_for_prompt(run_id: int, prompt_id: int) -> list[AnswerRow]:
    return [_answer(r) for r in db().execute(_ANSWER_SELECT + "WHERE a.run_id=? AND a.prompt_id=? ORDER BY a.engine", (run_id, prompt_id))]


def existing_pairs(run_id: int) -> dict[tuple[int, str], AnswerRow]:
    return {(a.prompt_id, a.engine): a for a in answers_for_run(run_id)}


def unanalyzed_prompt_ids(run_id: int) -> list[int]:
    return [r["prompt_id"] for r in db().execute(
        "SELECT DISTINCT prompt_id FROM answers WHERE run_id=? AND analyzed=0 AND error IS NULL AND available=1 AND length(response_text)>0 ORDER BY prompt_id", (run_id,)
    )]


def save_analysis(answer_id: int, res: dict[str, Any], analyzed: bool = True) -> None:
    conn = db()
    conn.execute(
        "UPDATE answers SET analyzed=?, mentioned=?, mention_rank=?, sentiment=?, sentiment_score=?, brand_context=? WHERE id=?",
        (int(analyzed), int(bool(res["mentioned"])), res.get("rank"), res.get("sentiment"), res.get("sentiment_score"), res.get("context", ""), answer_id),
    )
    conn.execute("DELETE FROM mentions WHERE answer_id=?", (answer_id,))
    conn.executemany(
        "INSERT INTO mentions(answer_id,brand_key,name,rank,sentiment,is_target) VALUES(?,?,?,?,?,?)",
        [(answer_id, b["key"], b["name"], b.get("rank"), b.get("sentiment"), int(b.get("is_target", False))) for b in res.get("brands", [])],
    )


@dataclass
class Mention:
    answer_id: int
    brand_key: str
    name: str
    rank: int | None
    sentiment: str | None
    is_target: bool


def mentions_for_run(run_id: int) -> list[Mention]:
    return [
        Mention(r["answer_id"], r["brand_key"], r["name"], r["rank"], r["sentiment"], bool(r["is_target"]))
        for r in db().execute("SELECT m.* FROM mentions m JOIN answers a ON a.id=m.answer_id WHERE a.run_id=?", (run_id,))
    ]


def engine_cost_history(brand_id: int) -> dict[str, float]:
    """Average cost per answered prompt per engine, over all runs — turns list prices into observed prices."""
    out: dict[str, float] = {}
    for r in db().execute(
        "SELECT a.engine, AVG(a.cost) AS c FROM answers a JOIN runs r ON r.id=a.run_id WHERE r.brand_id=? AND a.error IS NULL GROUP BY a.engine", (brand_id,)
    ):
        out[r["engine"]] = float(r["c"] or 0)
    return out
