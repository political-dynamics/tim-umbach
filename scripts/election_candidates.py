"""Weekly Brave Search verification for Election Lab candidate names.

The election model keeps a curated candidate mapping because search snippets
alone are not a safe source for inventing names. Brave Search is used to verify
those names against current public pages and attach a dated evidence link. If a
name cannot be verified, the curated value remains visible and is explicitly
marked as such in the frontend.

Candidate queries share the Job Radar's ten-request daily budget and state
file. The weekly workflow runs this module first with a maximum of seven
requests, leaving at least three Brave requests for job discovery that day.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from brave_search import API_URL, STATE_PATH, read_api_key, read_state, write_state


ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "election_candidates.json"
DAILY_LIMIT = 10
WEEKLY_CANDIDATE_LIMIT = 7

PARTY_SEARCH_ALIASES = {
    "CDU/CSU": ["CDU", "CSU", "Union"],
    "CDU": ["CDU"],
    "CSU": ["CSU"],
    "SPD": ["SPD"],
    "Grüne": ["Grüne", "Bündnis 90"],
    "FDP": ["FDP"],
    "AfD": ["AfD"],
    "Linke": ["Linke", "Die Linke"],
    "BSW": ["BSW"],
    "SSW": ["SSW"],
}


def read_cache(path: Path = CACHE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"meta": {}, "elections": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"meta": {}, "elections": {}}
    payload.setdefault("meta", {})
    payload.setdefault("elections", {})
    return payload


def write_cache(cache: dict[str, Any], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def candidate_query(target: dict[str, Any]) -> str:
    party_list = " ".join(target["candidates"])
    return (
        f'{target["election_label"]} {target["label"]} '
        f'{target["election_date"]} Spitzenkandidaten Kandidaten '
        f"Ministerpräsident Bürgermeister Kanzler {party_list}"
    )


def request_search(query: str, api_key: str) -> dict[str, Any]:
    parameters = urllib.parse.urlencode(
        {
            "q": query,
            "count": 20,
            "country": "de",
            "search_lang": "de",
            "safesearch": "strict",
        }
    )
    request = urllib.request.Request(
        f"{API_URL}?{parameters}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
            "User-Agent": "Tim-Umbach-Election-Lab/1.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", value).strip()


def result_candidates(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for result in payload.get("web", {}).get("results", []):
        title = clean_text(str(result.get("title", "")))
        description = clean_text(str(result.get("description", "")))
        url = str(result.get("url", ""))
        if not title or not url.startswith(("https://", "http://")):
            continue
        rows.append(
            {
                "title": title,
                "description": description,
                "url": url,
                "text": f"{title} {description}".casefold(),
            }
        )
    return rows


def verify_candidates(
    payload: dict[str, Any],
    candidates: dict[str, str],
    checked_at: str,
) -> dict[str, dict[str, Any]]:
    results = result_candidates(payload)
    verified: dict[str, dict[str, Any]] = {}
    for party, name in candidates.items():
        name_key = name.casefold()
        aliases = PARTY_SEARCH_ALIASES.get(party, [party])
        best = None
        for result in results:
            if name_key not in result["text"]:
                continue
            party_match = any(alias.casefold() in result["text"] for alias in aliases)
            candidate_match = any(
                word in result["text"]
                for word in (
                    "spitzenkandidat",
                    "kandidat",
                    "ministerpräsident",
                    "bürgermeister",
                    "kanzler",
                    "parteivorsitz",
                )
            )
            score = int(party_match) + int(candidate_match)
            if best is None or score > best[0]:
                best = (score, result)
        if best is None:
            continue
        result = best[1]
        verified[party] = {
            "name": name,
            "status": "brave_verified",
            "source_url": result["url"],
            "source_title": result["title"],
            "checked_at": checked_at,
        }
    return verified


def _reset_daily_state(state: dict[str, Any], date_text: str) -> dict[str, Any]:
    if state.get("request_date_utc") == date_text:
        return state
    preserved = {
        key: value for key, value in state.items() if key.startswith("bootstrap_")
    }
    preserved.update(
        {
            "request_date_utc": date_text,
            "request_count": 0,
            "daily_attempted_queries": [],
            "accepted_results": 0,
        }
    )
    return preserved


def discover_candidates(
    targets: dict[str, dict[str, Any]],
    cache_path: Path = CACHE_PATH,
    state_path: Path = STATE_PATH,
    target_date: date | None = None,
    weekly_limit: int = WEEKLY_CANDIDATE_LIMIT,
) -> tuple[dict[str, Any], str]:
    target_date = target_date or datetime.now(timezone.utc).date()
    date_text = target_date.isoformat()
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cache = read_cache(cache_path)
    api_key = read_api_key()
    if not api_key:
        cache["meta"].update(
            {
                "status": "key_unavailable",
                "last_attempted_at": checked_at,
                "requests_used": 0,
            }
        )
        write_cache(cache, cache_path)
        return cache, "Brave candidate search: API key unavailable; curated names retained"

    state = _reset_daily_state(read_state(state_path), date_text)
    attempted = list(state.get("daily_attempted_queries", []))
    used = int(state.get("request_count", 0))
    available = max(0, min(weekly_limit, DAILY_LIMIT - used))
    errors: list[str] = []
    requests_used = 0
    verified_count = 0

    pending: list[tuple[str, dict[str, Any], str, str]] = []
    for slug, target in targets.items():
        query = candidate_query(target)
        query_id = f"candidate:{slug}:{query}"
        if query_id not in attempted:
            pending.append((slug, target, query, query_id))

    for slug, target, query, query_id in pending[:available]:
        requests_used += 1
        used += 1
        attempted.append(query_id)
        state.update(
            {
                "request_date_utc": date_text,
                "request_count": used,
                "daily_attempted_queries": attempted,
                "query": query_id,
                "candidate_search_status": "running",
                "candidate_search_updated_at": checked_at,
            }
        )
        # Reserve the request before making it so interruptions cannot exceed
        # the shared daily budget.
        write_state(state, state_path)
        try:
            response = request_search(query, api_key)
            evidence = verify_candidates(response, target["candidates"], checked_at)
            prior = cache["elections"].get(slug, {}).get("candidates", {})
            combined = dict(prior)
            combined.update(evidence)
            cache["elections"][slug] = {
                "query": query,
                "checked_at": checked_at,
                "status": "success",
                "candidates": combined,
            }
            verified_count += len(evidence)
        except Exception as exc:
            errors.append(type(exc).__name__)
            prior = cache["elections"].get(slug, {})
            cache["elections"][slug] = {
                **prior,
                "query": query,
                "checked_at": checked_at,
                "status": f"error:{type(exc).__name__}",
            }
        write_cache(cache, cache_path)

    status = "partial" if errors or len(pending) > available else "success"
    if available == 0:
        status = "budget_exhausted"
    cache["meta"].update(
        {
            "status": status,
            "last_attempted_at": checked_at,
            "last_successful_at": checked_at if requests_used and not errors else cache["meta"].get("last_successful_at"),
            "requests_used": requests_used,
            "verified_candidates": verified_count,
            "shared_daily_limit": DAILY_LIMIT,
        }
    )
    write_cache(cache, cache_path)
    state.update(
        {
            "candidate_search_status": status,
            "candidate_search_requests": requests_used,
            "candidate_search_verified": verified_count,
            "candidate_search_updated_at": checked_at,
        }
    )
    write_state(state, state_path)
    return (
        cache,
        f"Brave candidate search: {status} "
        f"({requests_used}/{weekly_limit} weekly requests, {verified_count} names verified)",
    )


def candidate_evidence(
    cache: dict[str, Any],
    slug: str,
    candidates: dict[str, str],
) -> dict[str, dict[str, Any]]:
    cached = cache.get("elections", {}).get(slug, {}).get("candidates", {})
    evidence: dict[str, dict[str, Any]] = {}
    for party, name in candidates.items():
        row = cached.get(party, {})
        if row.get("name") == name and row.get("source_url"):
            evidence[party] = row
        else:
            evidence[party] = {
                "name": name,
                "status": "curated",
                "source_url": "",
                "source_title": "",
                "checked_at": "",
            }
    return evidence
