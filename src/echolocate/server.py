"""REST API + HTML dashboard. Localhost only unless ECHO_API_TOKEN is set."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import ENGINES, config
from .db import find_brand, get_brand, insert_prompt, latest_run, list_brands, list_prompts, list_topics, set_prompts_active
from .metrics import answers_for, build_report, build_trend, headline
from .onboarding import onboard
from .render import as_json, html_page, markdown
from .runner import run_brand
from .util import log

_running: dict[str, dict[str, Any]] = {}


def start_run(slug: str, **kw: Any) -> dict[str, Any]:
    st = _running.get(slug)
    if st and st["status"] == "running":
        return st
    st = {"status": "running", "started": True, "error": None}
    _running[slug] = st

    def go() -> None:
        try:
            run = run_brand(get_brand(slug), quiet=True, **kw)
            st.update(status=run.status, run_date=run.run_date, answers=run.api_calls)
        except Exception as e:  # noqa: BLE001
            st.update(status="failed", error=str(e))

    threading.Thread(target=go, daemon=True).start()
    return st


def run_status(slug: str) -> dict[str, Any]:
    st = _running.get(slug)
    if st:
        return st
    b = get_brand(slug)
    r = latest_run(b.id)
    return {"status": r.status if r else "never", "run_date": r.run_date if r else None, "answers": r.api_calls if r else 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        log(f"  {self.address_string()} {fmt % args}")

    def _send(self, code: int, body: str, ctype: str = "application/json") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def _authed(self, q: dict[str, list[str]]) -> bool:
        tok = config.api_token
        if not tok:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {tok}" or q.get("token", [""])[0] == tok

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._json({"error": "unauthorized"}, 401)
        parts = [p for p in u.path.split("/") if p]
        try:
            if parts == ["v1", "brands"] or parts == []:
                out = []
                for b in list_brands():
                    h = headline(b.id)
                    out.append({"slug": b.slug, "name": b.name, "domain": b.domain, "engines": b.engines, "prompts": len(list_prompts(b.id)),
                                "latest": {"date": h["run"].run_date, "visibility": h["overall"].visibility, "sentiment": h["overall"].sentiment} if h else None})
                return self._json({"version": __version__, "brands": out})
            if parts[:2] == ["dashboard"] + parts[1:2] and len(parts) == 2:
                b = get_brand(parts[1])
                return self._send(200, html_page(build_report(b, q.get("date", [None])[0]), build_trend(b, 30)), "text/html")
            if len(parts) >= 3 and parts[:2] == ["v1", "brands"]:
                b = get_brand(parts[2])
                what = parts[3] if len(parts) > 3 else ""
                if what == "report":
                    fmt = q.get("format", ["json"])[0]
                    rep = build_report(b, q.get("date", [None])[0])
                    if fmt == "md":
                        return self._send(200, markdown(rep), "text/markdown")
                    if fmt == "html":
                        return self._send(200, html_page(rep, build_trend(b, 30)), "text/html")
                    return self._send(200, as_json(rep))
                if what == "trend":
                    return self._json(build_trend(b, int(q.get("days", ["30"])[0])))
                if what == "prompts":
                    return self._json([p.__dict__ for p in list_prompts(b.id, active_only=q.get("all", ["0"])[0] != "1")])
                if what == "topics":
                    return self._json([t.__dict__ for t in list_topics(b.id)])
                if what == "runs":
                    return self._json(run_status(b.slug))
                if what == "answers":
                    m = q.get("mentioned", [None])[0]
                    rows = answers_for(b, q.get("date", [None])[0], q.get("engine", [None])[0], None, None if m is None else m in ("1", "true"))
                    return self._json([{k: v for k, v in a.__dict__.items()} for a in rows])
                if what == "":
                    return self._json({"slug": b.slug, "name": b.name, "domain": b.domain, "aliases": b.aliases, "profile": b.profile, "competitors": b.competitors, "settings": b.settings})
            return self._json({"error": "not found"}, 404)
        except KeyError as e:
            return self._json({"error": str(e)}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._json({"error": "unauthorized"}, 401)
        parts = [p for p in u.path.split("/") if p]
        try:
            body = self._body()
            if parts == ["v1", "brands"]:
                brand, n, usage = onboard(body["url"], prompts=int(body.get("prompts", 100)), topics=int(body.get("topics", 8)), name=body.get("name"),
                                          country=body.get("country"), language=body.get("language"), engines=body.get("engines"))
                return self._json({"slug": brand.slug, "name": brand.name, "prompts": n, "cost": usage.cost}, 201)
            if len(parts) == 4 and parts[:2] == ["v1", "brands"]:
                b = get_brand(parts[2])
                if parts[3] == "runs":
                    return self._json(start_run(b.slug, limit=body.get("limit"), engines=body.get("engines"), force=bool(body.get("force"))), 202)
                if parts[3] == "prompts":
                    topics = {t.name: t.id for t in list_topics(b.id)}
                    tid = topics.get(body.get("topic")) if body.get("topic") else None
                    aliases = [b.name, *b.aliases]
                    from .util import mentions_any
                    added = [insert_prompt(b.id, t, tid, "branded" if mentions_any(t, aliases) else "discovery", mentions_any(t, aliases)) for t in body.get("prompts", [])]
                    return self._json({"added": sum(1 for a in added if a)}, 201)
            return self._json({"error": "not found"}, 404)
        except KeyError as e:
            return self._json({"error": str(e)}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)

    def do_DELETE(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._json({"error": "unauthorized"}, 401)
        parts = [p for p in u.path.split("/") if p]
        try:
            if len(parts) == 4 and parts[:2] == ["v1", "brands"] and parts[3] == "prompts":
                b = get_brand(parts[2])
                ids = [int(i) for i in self._body().get("ids", [])]
                return self._json({"removed": set_prompts_active(b.id, ids, False)})
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)


def serve(port: int | None = None) -> None:
    port = port or config.port
    host = "0.0.0.0" if config.api_token else "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), Handler)
    log(f"echolocate serving on http://{host}:{port}  (dashboard: /dashboard/<brand>, API: /v1/brands)" + ("" if config.api_token else " — localhost only; set ECHO_API_TOKEN to expose"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
