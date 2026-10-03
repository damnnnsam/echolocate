from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from .util import log, pool

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
USEFUL = re.compile(
    r"(about|company|pricing|plans|product|platform|features|solutions|use-?cases|customers|case-studies|why|compare|vs|alternative|integrations|industries|who-we-serve|services)",
    re.I,
)


@dataclass
class Page:
    url: str
    title: str
    description: str
    text: str


def normalize_url(s: str) -> str:
    s = s.strip()
    return s if re.match(r"^https?://", s, re.I) else f"https://{s}"


def html_to_text(h: str) -> str:
    h = re.sub(r"<(script|style|noscript|svg|iframe|template)[\s\S]*?</\1>", " ", h, flags=re.I)
    h = re.sub(r"<!--[\s\S]*?-->", " ", h)
    h = re.sub(r"</(p|div|section|article|li|h[1-6]|br|tr|header|footer)>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = htmllib.unescape(h)
    h = re.sub(r"[ \t\f\v]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    return h.strip()


def _meta(h: str, name: str) -> str:
    for pat in (
        rf'<meta[^>]+(?:name|property)=["\']{name}["\'][^>]*content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:name|property)=["\']{name}["\']',
    ):
        m = re.search(pat, h, re.I)
        if m:
            return htmllib.unescape(m.group(1)).strip()
    return ""


def fetch_page(url: str, max_chars: int) -> tuple[Page, str] | None:
    try:
        with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}) as c:
            r = c.get(url)
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
            return None
        h = r.text
        m = re.search(r"<title[^>]*>([\s\S]*?)</title>", h, re.I)
        title = htmllib.unescape(m.group(1)).strip() if m else ""
        desc = _meta(h, "description") or _meta(h, "og:description")
        return Page(url=str(r.url), title=title, description=desc, text=html_to_text(h)[:max_chars]), h
    except Exception:  # noqa: BLE001
        return None


def crawl_site(url: str, max_pages: int = 6) -> tuple[str, list[Page]]:
    """Homepage plus up to max_pages same-site pages that look like about/pricing/product pages."""
    start = normalize_url(url)
    home = fetch_page(start, 12_000)
    if not home:
        log(f"  could not fetch {start}; profiling from the domain alone")
        return start, []
    page, h = home
    origin = urlparse(page.url)
    host = origin.hostname.replace("www.", "", 1) if origin.hostname else ""
    seen = {origin.path.rstrip("/") or "/"}
    candidates: list[str] = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\']', h, re.I):
        try:
            u = urlparse(urljoin(page.url, m.group(1)))
            if not u.hostname or u.hostname.replace("www.", "", 1) != host:
                continue
            p = u.path.rstrip("/") or "/"
            if p in seen or not USEFUL.search(p) or re.search(r"\.(pdf|png|jpe?g|svg|zip)$", p, re.I) or p.count("/") > 3:
                continue
            seen.add(p)
            candidates.append(f"{u.scheme}://{u.netloc}{p}")
        except Exception:  # noqa: BLE001
            continue
    extra = pool(candidates[:max_pages], 6, lambda u: fetch_page(u, 6_000), on_error="collect")
    pages = [page] + [x[0] for x in extra if isinstance(x, tuple)]
    return page.url, pages
