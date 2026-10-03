from __future__ import annotations

import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Callable, Iterable, TypeVar
from urllib.parse import urlparse

T = TypeVar("T")
R = TypeVar("R")

_log_lock = threading.Lock()


def log(msg: str) -> None:
    with _log_lock:
        print(msg, file=sys.stderr, flush=True)


def today() -> str:
    return date.today().isoformat()


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "brand"


_TLD = re.compile(r"\.(com|io|ai|co|net|org|app|dev|de|at|ch|uk|fr|es|it|nl|se)$")


def brand_key(name: str) -> str:
    """Canonical key for a brand name: 'Lemlist.com' / 'lemlist' / 'LemList Inc' -> 'lemlist'."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()
    s = _TLD.sub("", s)
    s = re.sub(r"\b(inc|llc|ltd|gmbh|ag|corp|corporation|company|co)\b\.?$", "", s).strip()
    return re.sub(r"[^a-z0-9]+", "", s)


def normalize_domain(d: str) -> str:
    d = (d or "").strip().lower()
    if "://" in d:
        d = urlparse(d).hostname or ""
    d = d.split("/")[0].split("?")[0]
    return d[4:] if d.startswith("www.") else d


def domain_of(url: str) -> str:
    try:
        return normalize_domain(urlparse(url if "://" in url else f"https://{url}").hostname or "")
    except Exception:
        return ""


def match_domain(candidate: str | None, target: str | None) -> bool:
    c, t = normalize_domain(candidate or ""), normalize_domain(target or "")
    return bool(c and t) and (c == t or c.endswith("." + t))


def mentions_any(text: str, aliases: Iterable[str]) -> bool:
    for a in aliases:
        a = (a or "").strip()
        if len(a) < 2:
            continue
        pat = r"(?<![\w])" + re.escape(a) + r"(?![\w])"
        if re.search(pat, text, flags=re.IGNORECASE):
            return True
    return False


def pool(items: list[T], concurrency: int, fn: Callable[[T], R], on_error: str = "raise") -> list[R | Exception]:
    """Run fn over items with a thread pool; results in input order. on_error='collect' keeps exceptions as values."""
    out: list = [None] * len(items)
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(items)))) as ex:
        futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
        for f in as_completed(futs):
            i = futs[f]
            try:
                out[i] = f.result()
            except Exception as e:  # noqa: BLE001
                if on_error == "raise":
                    for other in futs:
                        other.cancel()
                    raise
                out[i] = e
    return out


def retry(fn: Callable[[], R], attempts: int = 3, base_delay: float = 2.0, retry_on: tuple[type[BaseException], ...] = (Exception,)) -> R:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except retry_on as e:  # noqa: PERF203
            last = e
            if i + 1 < attempts:
                time.sleep(base_delay * (i + 1))
    assert last is not None
    raise last


def fmt_pct(x: float | None, digits: int = 0) -> str:
    return "–" if x is None else f"{x * 100:.{digits}f}%"


def fmt_num(x: float | None, digits: int = 1) -> str:
    return "–" if x is None else f"{x:.{digits}f}"


def fmt_usd(x: float | None) -> str:
    return "–" if x is None else f"${x:,.2f}" if x >= 0.01 or x == 0 else f"${x:.4f}"
