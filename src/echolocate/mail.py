"""Daily report by email through Resend."""
from __future__ import annotations

import httpx

from .config import config


def send(subject: str, html_body: str) -> str:
    r = config.resend
    if not r:
        raise RuntimeError("RESEND_API_KEY, ECHO_EMAIL_FROM and ECHO_EMAIL_TO must all be set")
    key, sender, to = r
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"from": sender, "to": [t.strip() for t in to.split(",")], "subject": subject, "html": html_body},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Resend {resp.status_code}: {resp.text[:300]}")
    return str(resp.json().get("id", ""))
