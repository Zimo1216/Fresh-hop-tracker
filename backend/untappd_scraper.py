"""Track A: on_draft detection via Untappd's public beer-list pages.

No LLM involved — this is structured HTML we parse directly, which is what
makes it trustworthy enough to mark a beer on_draft without corroboration.
Untappd sits behind Cloudflare bot management that TLS/JA3-fingerprints
Python's `requests`/urllib3 and serves it a JS challenge page even with a
convincing User-Agent (verified: plain `requests` got a 403 "Just a
moment..." page while `curl` and `curl_cffi` with browser impersonation
both got real 200 content). So this module uses `curl_cffi` specifically,
not `requests`.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup
from curl_cffi import requests as creq

from . import config

_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}
_ADDED_RE = re.compile(r"Added\s+(\d{2})/(\d{2})/(\d{2})")
_BLOCK_MARKERS = ["just a moment", "captcha", "are you human", "attention required"]


class UntappdFetchError(Exception):
    pass


def _fetch_html(untappd_beer_list_url: str) -> str:
    url = untappd_beer_list_url.strip().rstrip("/")
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}sort=created_at_desc"
    try:
        resp = creq.get(url, headers=_HEADERS, impersonate="chrome", timeout=20)
    except Exception as e:
        raise UntappdFetchError(f"request failed: {e}") from e
    if resp.status_code != 200:
        raise UntappdFetchError(f"HTTP {resp.status_code}")
    lowered = resp.text[:2000].lower()
    if any(marker in lowered for marker in _BLOCK_MARKERS):
        raise UntappdFetchError("blocked (anti-bot challenge page returned)")
    return resp.text


def _parse_added_date(text: str):
    m = _ADDED_RE.search(text or "")
    if not m:
        return None
    mm, dd, yy = (int(x) for x in m.groups())
    try:
        return datetime(2000 + yy, mm, dd).date()
    except ValueError:
        return None


def _matches_fresh_hop(*texts) -> bool:
    combined = " ".join(t or "" for t in texts).lower()
    return any(kw in combined for kw in config.FRESH_HOP_KEYWORDS)


def _parse_beer_entries(html: str):
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for item in soup.select(".beer-item"):
        name_a = item.select_one(".beer-details p.name a")
        if not name_a:
            continue
        beer_name = name_a.get_text(strip=True)
        href = name_a.get("href", "")
        beer_url = f"https://untappd.com{href}" if href.startswith("/") else href

        style_el = item.select_one(".beer-details p.style")
        style = style_el.get_text(strip=True) if style_el else ""

        desc_el = item.select_one(".beer-details p.desc[class*=desc-full]") or item.select_one(
            ".beer-details p.desc"
        )
        desc = desc_el.get_text(strip=True) if desc_el else ""
        desc = re.sub(r"\s*(Read More|Read Less)\s*$", "", desc).strip()

        date_el = item.select_one(".details .details-item.date")
        added_date = _parse_added_date(date_el.get_text(strip=True)) if date_el else None

        entries.append(
            {"beer_name": beer_name, "beer_url": beer_url, "style": style, "desc": desc, "added_date": added_date}
        )
    return entries


def check_brewery(untappd_url: str):
    """Fetch and parse one brewery's Untappd beer list.

    Returns (status, message, releases) where status is 'found' | 'no_results'
    | 'no_matches', and releases is a list of dicts with beer_name,
    release_date (ISO str), source_url, evidence_snippet — ready to pass to
    db.upsert_fresh_hop_release(..., source_type='untappd'). Raises
    UntappdFetchError on network/blocking failure (caller should NOT fall
    back to Track B for this brewery — just record the failure and retry
    next refresh).
    """
    html = _fetch_html(untappd_url)
    entries = _parse_beer_entries(html)
    if not entries:
        return "no_results", "Untappd page returned no beer entries (page structure may have changed)", []

    cutoff = datetime.strptime(config.DATA_START_DATE, "%Y-%m-%d").date()
    releases = []
    for e in entries:
        if e["added_date"] is None or e["added_date"] < cutoff:
            continue
        if not _matches_fresh_hop(e["beer_name"], e["style"], e["desc"]):
            continue
        evidence = f"Untappd — \"{e['beer_name']}\" ({e['style']}), Added {e['added_date'].strftime('%m/%d/%y')}"
        if e["desc"]:
            evidence += f": {e['desc'][:180]}"
        releases.append(
            {
                "beer_name": e["beer_name"],
                "release_date": e["added_date"].isoformat(),
                "source_url": e["beer_url"],
                "evidence_snippet": evidence,
            }
        )

    if not releases:
        return (
            "no_matches",
            f"{len(entries)} beer(s) checked, none matched fresh hop keywords since {config.DATA_START_DATE}",
            releases,
        )
    return "found", f"{len(releases)} fresh hop release(s) found on Untappd", releases
