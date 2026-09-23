"""Refresh pipeline: two independent tracks, merged into the same table.

Track A (Untappd, see untappd_scraper.py): structured HTML parsing of each
brewery's public Untappd beer list. No LLM, no Tavily. This is the
authoritative source for on_draft — a beer showing up there with a recent
"Added" date is treated as confirmed without needing IG/news corroboration.

Track B (this file, Tavily + Claude): narrower now — only for *upcoming*
announcements (predictions/teasers that haven't hit Untappd yet), scoped to
Instagram content published on/after IG_SEARCH_START_DATE. Two things this
track is deliberately paranoid about, because both bit us in practice:
  1. Tavily quota: a single Instagram-restricted search per brewery,
     `basic` depth (half the credit cost of `advanced`), and breweries
     searched recently are skipped unless the caller asks to force a
     re-search.
  2. Grounding: Claude sometimes "borrows" a date/beer from one brewery's
     search result and attaches it to a different, unrelated brewery in the
     same batch. Every extracted release must carry a verbatim evidence
     quote, and that quote is checked in code (not just prompted for) to
     actually contain the brewery's name before it's allowed into the DB.

The two tracks never fall back to each other: if Untappd fetch fails for a
brewery, we record the failure and move on rather than compensating with an
extra Track B call (that reintroduces exactly the misattribution risk Track
A was built to avoid).
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from . import config, db
from .untappd_scraper import UntappdFetchError, check_brewery as untappd_check_brewery

TAVILY_URL = "https://api.tavily.com/search"

_SUFFIX_RE = re.compile(
    r"\b(brewing company|brewing co|brew ?house|brewery|brewing|brews|beer co)\b", re.IGNORECASE
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = _SUFFIX_RE.sub(" ", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _brewery_mentioned(text: str, brewery_name: str) -> bool:
    """Loose, case-insensitive check that `brewery_name` (or its core name
    with common suffixes like "Brewing"/"Brewery" stripped) appears in
    `text`. Used to catch Claude misattributing info to the wrong brewery.
    """
    core = _normalize(brewery_name)
    if not core:
        return False
    return core in _normalize(text)


def _normalize_url(url: str) -> str:
    """Strip query string/fragment/trailing slash so a source_url the model
    echoes back with e.g. different tracking params still matches the
    Tavily result it came from."""
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}".rstrip("/").lower()


def _is_instagram_url(url: str) -> bool:
    try:
        return "instagram.com" in urlparse(url or "").netloc.lower()
    except ValueError:
        return False


def _result_published_date(result: dict):
    """Parse Tavily's `published_date` (RFC-1123-ish, e.g. 'Tue, 15 Sep 2026
    17:00:00 GMT') into a date, or None if missing/unparseable."""
    raw = result.get("published_date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError):
        return None


def _is_stale_result(result: dict, cutoff_iso: str) -> bool:
    """Code-level (not LLM-trusted) staleness check on a single search
    result, independent of whatever release_date the model later claims.
    Primary signal: Tavily's own `published_date` field (confirmed reliable
    for instagram.com results as of 2026-09-23 testing). Fallback: if that's
    missing, a crude scan for an explicit pre-2026 year token in the URL or
    text with no 2026 mention alongside it — deliberately conservative
    (only flags an *unambiguous* old-year signal) since it has no publish
    date to go on.
    """
    cutoff = datetime.strptime(cutoff_iso, "%Y-%m-%d").date()
    pub_date = _result_published_date(result)
    if pub_date is not None:
        return pub_date < cutoff

    text = f"{result.get('url', '')} {result.get('title', '')} {result.get('content', '')}"
    years = set(_YEAR_RE.findall(text))
    if years and "2026" not in years:
        return True
    return False


def _passes_grounding(source_url: str, brewery_name: str, results_by_url: dict) -> bool:
    """Hard gate: the brewery's name must appear in the ACTUAL fetched
    source content for source_url — not in the model's self-reported
    evidence_snippet, which is exactly what let a fabricated citation slip
    through before (a hallucinated quote can "mention" the brewery even
    when the real article never does). If source_url doesn't match any
    result we actually fetched, that's an automatic fail — an
    unverifiable citation is treated the same as a wrong one.
    """
    result = results_by_url.get(_normalize_url(source_url))
    if result is None:
        return False
    return _brewery_mentioned(result.get("content", ""), brewery_name)


EXTRACTION_TOOL = {
    "name": "record_fresh_hop_releases",
    "description": (
        "Record every distinct fresh hop beer release ANNOUNCEMENT that is genuinely, verbatim about "
        "the given brewery. Only include releases dated on or after the data start date. If the "
        "brewery's name does not literally appear anywhere in the search results, return an empty list "
        "— never infer or borrow a release from a different brewery mentioned nearby."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "releases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "beer_name": {"type": "string", "description": "Name of the fresh hop beer"},
                        "release_date": {
                            "type": "string",
                            "description": (
                                "ISO date (YYYY-MM-DD) the beer is/was announced to go on draft. Resolve "
                                "relative or vague phrasing (e.g. 'this Saturday', 'dropping Friday', "
                                "'next week') into an actual date using today's date as the anchor. If "
                                "only a month is known, use the 1st of that month. If genuinely no date "
                                "signal exists at all, omit the release."
                            ),
                        },
                        "source_url": {"type": "string", "description": "URL of the search result this came from"},
                        "evidence_snippet": {
                            "type": "string",
                            "description": (
                                "A short verbatim quote (copy exact text, do not paraphrase) from that "
                                "source's CONTENT that mentions this brewery's name together with the "
                                "beer/date info. This is what proves the release isn't made up — if you "
                                "can't quote such a passage, don't include the release at all."
                            ),
                        },
                    },
                    "required": ["beer_name", "release_date", "evidence_snippet"],
                },
            }
        },
        "required": ["releases"],
    },
}


class RefreshError(Exception):
    pass


def _check_keys():
    missing = []
    if not config.TAVILY_API_KEY:
        missing.append("TAVILY_API_KEY")
    if not config.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        raise RefreshError(
            "Missing API key(s): " + ", ".join(missing) + ". Set them in your .env file before refreshing."
        )


def _tavily_search(query: str, include_domains=None):
    payload = {
        "api_key": config.TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": 8,
        "include_answer": False,
        "start_date": config.IG_SEARCH_START_DATE,
        "filter_by_published_date": True,
        "include_published_date": True,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    resp = requests.post(TAVILY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("results", [])


def _search_brewery_instagram(brewery_name: str):
    """Exactly one Tavily call per brewery, restricted to Instagram content
    published on/after IG_SEARCH_START_DATE — Track B is for upcoming
    announcements only, not general web coverage (that's what made it
    expensive and prone to misattribution before).

    Two things are re-checked in code here rather than just trusted from
    the API call, because both were verified (2026-09-23) to not be fully
    reliable on their own: `include_domains` still occasionally lets
    non-instagram.com results through, and `filter_by_published_date`
    relies on Tavily's own crawl finding a date — belt-and-suspenders with
    an explicit code-level check on `published_date` per result.
    """
    try:
        raw_results = _tavily_search(f"{brewery_name} fresh hop", include_domains=["instagram.com"])
    except requests.RequestException as e:
        raise RefreshError(f"search failed ({e})") from e

    on_domain = [r for r in raw_results if _is_instagram_url(r.get("url", ""))]
    fresh = [r for r in on_domain if not _is_stale_result(r, config.IG_SEARCH_START_DATE)]
    return fresh, len(raw_results) - len(fresh)


def _extract_with_claude(client, brewery_name: str, search_results: list):
    snippets = []
    for r in search_results:
        snippets.append(
            f"URL: {r.get('url')}\nTITLE: {r.get('title')}\nCONTENT: {r.get('content', '')[:1200]}"
        )
    joined = "\n\n---\n\n".join(snippets)

    prompt = (
        f"Brewery you are researching: {brewery_name}\n"
        f"Today's date: {date.today().isoformat()}\n"
        f"Only count releases dated on or after {config.IG_SEARCH_START_DATE}. If a result is clearly "
        f"about an older season (e.g. mentions 2022-2025 dates), ignore it entirely rather than "
        f"extracting it.\n\n"
        "Below are Instagram-related web search results (titles + snippets), gathered for the brewery "
        "named above, about possible fresh hop beer release announcements. IMPORTANT: these search "
        "results can contain information about OTHER breweries too (e.g. a roundup article covering "
        "several breweries). Only extract a release if the literal text below contains this brewery's "
        f'name — "{brewery_name}" — directly connected to the beer name and date. If a result talks '
        "about a different brewery, or doesn't mention this brewery's name at all, ignore that result "
        "completely. Do not fill in gaps by inference or by analogy with other breweries in the results. "
        "For every release you do extract, copy a short verbatim quote into evidence_snippet that "
        "contains this brewery's name — if you cannot find such a quote, do not extract that release. "
        "Include releases that are only planned/teased and haven't happened yet, resolving relative "
        "dates (e.g. 'this Saturday') into an actual YYYY-MM-DD using today's date as the anchor.\n\n"
        f"{joined}"
    )

    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2048,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "record_fresh_hop_releases"},
        messages=[{"role": "user", "content": prompt}],
    )

    for block in message.content:
        if block.type == "tool_use" and block.name == "record_fresh_hop_releases":
            releases = _normalize_releases(block.input.get("releases", []))
            if releases is None:
                raise ValueError("model returned releases in an unexpected shape")
            return releases
    return []


def _normalize_releases(raw):
    """Claude occasionally stringifies the whole releases array instead of
    returning it as a real JSON array in the tool input (e.g. {"releases":
    "[{...}]"} or even double-encoded). Unwrap up to a couple of levels of
    JSON-string nesting before giving up. Returns a list, or None if the
    shape can't be recovered.
    """
    depth = 0
    while isinstance(raw, str) and depth < 3:
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        depth += 1
        if isinstance(raw, dict) and "releases" in raw:
            raw = raw["releases"]
    return raw if isinstance(raw, list) else None


def _run_untappd_track(brewery, summary):
    name = brewery["name"]
    url = brewery.get("untappd_url")
    if not url:
        summary["details"].append(
            {
                "brewery": name,
                "track": "untappd",
                "status": "no_url",
                "message": "no Untappd URL configured — add one in the Breweries tab",
            }
        )
        return

    summary["untappd_checked"] += 1
    try:
        status, message, releases = untappd_check_brewery(url)
    except UntappdFetchError as e:
        summary["details"].append({"brewery": name, "track": "untappd", "status": "error", "message": str(e)})
        return
    except Exception as e:
        summary["details"].append(
            {"brewery": name, "track": "untappd", "status": "error", "message": f"unexpected error: {e}"}
        )
        return
    finally:
        db.touch_brewery_untappd_checked(brewery["id"])

    for rel in releases:
        try:
            kind, _ = db.upsert_fresh_hop_release(
                name, rel["beer_name"], rel["release_date"], rel["source_url"], rel["evidence_snippet"], "untappd"
            )
            if kind == "inserted":
                summary["inserted"] += 1
            elif kind == "merged":
                summary["merged"] += 1
            elif kind == "updated":
                summary["updated"] += 1
        except Exception as e:
            summary["details"].append(
                {"brewery": name, "track": "untappd", "status": "error", "message": f"{rel['beer_name']}: {e}"}
            )

    summary["details"].append({"brewery": name, "track": "untappd", "status": status, "message": message})


def _run_instagram_track(client, brewery, summary, force, skip_recent_days, now):
    name = brewery["name"]

    if not force and brewery.get("last_refreshed_at"):
        try:
            last = datetime.fromisoformat(brewery["last_refreshed_at"])
            age_days = (now - last).days
        except ValueError:
            age_days = skip_recent_days + 1
        if age_days < skip_recent_days:
            summary["ig_skipped"] += 1
            summary["details"].append(
                {"brewery": name, "track": "instagram", "status": "skipped", "message": f"confirmed {age_days} day(s) ago"}
            )
            return

    summary["ig_searched"] += 1
    try:
        results, stale_filtered = _search_brewery_instagram(name)
    except RefreshError as e:
        summary["details"].append({"brewery": name, "track": "instagram", "status": "error", "message": str(e)})
        return
    except Exception as e:
        summary["details"].append(
            {"brewery": name, "track": "instagram", "status": "error", "message": f"search failed: {e}"}
        )
        return

    summary["discarded_stale_source"] += stale_filtered

    if not results:
        summary["details"].append(
            {
                "brewery": name,
                "track": "instagram",
                "status": "no_results",
                "message": f"no web results found (filtered {stale_filtered} off-domain/stale result(s))"
                if stale_filtered
                else "no web results found",
            }
        )
        db.touch_brewery_refreshed(brewery["id"])
        return

    results_by_url = {_normalize_url(r.get("url")): r for r in results if r.get("url")}

    try:
        releases = _extract_with_claude(client, name, results)
    except Exception as e:
        summary["details"].append(
            {"brewery": name, "track": "instagram", "status": "error", "message": f"extraction failed: {e}"}
        )
        return

    found_count = 0
    for rel in releases:
        if not isinstance(rel, dict):
            summary["details"].append(
                {
                    "brewery": name,
                    "track": "instagram",
                    "status": "error",
                    "message": f"malformed extraction item (expected object): {rel!r}",
                }
            )
            continue

        beer_name = (rel.get("beer_name") or "").strip()
        release_date = (rel.get("release_date") or "").strip()
        source_url = (rel.get("source_url") or "").strip() or None
        evidence_snippet = (rel.get("evidence_snippet") or "").strip() or None
        if not beer_name or not release_date:
            continue
        if release_date < config.IG_SEARCH_START_DATE:
            continue

        # Grounding check: verified against the ACTUAL fetched source text
        # for source_url, never against the model's self-reported
        # evidence_snippet (which can be a fabricated quote that happens to
        # name the brewery even when the real source never does — that
        # loophole is exactly how a past hallucination got through).
        if not _passes_grounding(source_url, name, results_by_url):
            summary["discarded_ungrounded"] += 1
            summary["details"].append(
                {
                    "brewery": name,
                    "track": "instagram",
                    "status": "error",
                    "message": f"discarded '{beer_name}' — brewery name not found in the actual fetched source text for {source_url!r} (likely misattributed)",
                }
            )
            continue

        try:
            kind, _ = db.upsert_fresh_hop_release(
                name, beer_name, release_date, source_url, evidence_snippet, "instagram"
            )
            if kind == "inserted":
                summary["inserted"] += 1
                found_count += 1
            elif kind in ("updated", "merged"):
                summary["updated"] += 1
                found_count += 1
            # 'kept_higher_priority' means an Untappd-confirmed record already
            # covers this beer; intentionally not counted as a new find.
        except Exception as e:
            summary["details"].append(
                {"brewery": name, "track": "instagram", "status": "error", "message": f"{beer_name}: {e}"}
            )

    db.touch_brewery_refreshed(brewery["id"])

    if found_count == 0:
        summary["details"].append(
            {
                "brewery": name,
                "track": "instagram",
                "status": "no_matches",
                "message": f"{len(results)} web result(s), but none looked like a genuine fresh hop announcement",
            }
        )
    else:
        summary["details"].append(
            {"brewery": name, "track": "instagram", "status": "found", "message": f"{found_count} release(s)"}
        )


def run_refresh(force=False):
    _check_keys()
    import anthropic  # imported lazily so the app can start without the package during setup issues

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    breweries = db.list_breweries(active_only=True)
    skip_recent_days = db.get_skip_recent_days()
    now = datetime.utcnow()

    summary = {
        "breweries_total": len(breweries),
        "untappd_checked": 0,
        "ig_searched": 0,
        "ig_skipped": 0,
        "inserted": 0,
        "updated": 0,
        "merged": 0,
        "discarded_ungrounded": 0,
        "discarded_stale_source": 0,
        "errors": [],  # populated at the end: every 'error'-status detail
        # One entry per brewery per track: {brewery, track, status, message}
        "details": [],
    }

    first = True
    for brewery in breweries:
        if brewery.get("untappd_url"):
            if not first:
                time.sleep(config.UNTAPPD_REQUEST_DELAY_SECONDS)
            first = False
            _run_untappd_track(brewery, summary)

        _run_instagram_track(client, brewery, summary, force, skip_recent_days, now)

    db.recompute_all_statuses()
    db.set_last_refresh_completed_at()
    summary["errors"] = [d for d in summary["details"] if d["status"] == "error"]
    return summary
