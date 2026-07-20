"""Low-frequency client for the community-documented BA Jobsuche endpoint."""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "ba_jobs_state.json"
CACHE_PATH = ROOT / "data" / "ba_jobs_cache.json"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def api_get(url: str, client_id: str, user_agent: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
            "X-API-Key": client_id,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read(2_500_000).decode("utf-8"))


def search_url(config: dict[str, Any], query: str) -> str:
    values = {
        "wo": config.get("location", "Hamburg"),
        "umkreis": int(config.get("radius_km", 50)),
        "angebotsart": 1,
        "zeitarbeit": "false",
        "pav": "false",
        "berufsfeld": config.get("professional_field", "Informatik"),
        "veroeffentlichtseit": int(config.get("published_within_days", 30)),
        "page": 1,
        "size": min(int(config.get("size", 50)), 100),
    }
    if query:
        values["was"] = query
    parameters = urllib.parse.urlencode(values)
    return (
        f"{config['base_url']}{config.get('search_path', '/pc/v4/jobs')}?{parameters}"
    )


def detail_url(config: dict[str, Any], reference: str) -> str:
    encoded = base64.b64encode(reference.encode("utf-8")).decode("ascii")
    return (
        f"{config['base_url']}/pc/v4/jobdetails/{urllib.parse.quote(encoded, safe='')}"
    )


def location_text(detail: dict[str, Any], fallback: dict[str, Any]) -> str:
    locations = detail.get("arbeitsorte") or []
    if not locations and fallback.get("arbeitsort"):
        locations = [fallback["arbeitsort"]]
    rendered = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        parts = [location.get("ort"), location.get("region"), location.get("land")]
        text = ", ".join(str(part) for part in parts if part)
        if text and text not in rendered:
            rendered.append(text)
    return " / ".join(rendered)


def work_mode(detail: dict[str, Any]) -> str:
    models = {str(model).upper() for model in detail.get("arbeitszeitmodelle", [])}
    description = " ".join(
        str(detail.get(field, ""))
        for field in ["stellenangebotsBeschreibung", "stellenbeschreibung"]
    ).lower()
    if re.search(r"\b(?:100\s*%|fully|full)\s*remote\b", description):
        return "Remote"
    if (
        any("HEIM" in model or "TELEARBEIT" in model for model in models)
        or any(
            phrase in description
            for phrase in [
                "homeoffice",
                "home office",
                "mobiles arbeiten",
                "mobile arbeit",
                "hybrid",
                "remote working",
            ]
        )
        or re.search(r"\b\d{1,3}\s*%\s*remote\b", description)
    ):
        return "Hybrid"
    return ""


def normalize_detail(
    detail: dict[str, Any],
    summary: dict[str, Any],
    query_date: str,
) -> dict[str, Any]:
    reference = str(
        detail.get("referenznummer")
        or detail.get("refnr")
        or summary.get("referenznummer")
        or summary.get("refnr")
        or ""
    )
    description = str(
        detail.get("stellenangebotsBeschreibung")
        or detail.get("stellenbeschreibung")
        or ""
    )
    compensation = str(detail.get("verguetung") or "")
    if compensation:
        description = f"{description} Vergütung: {compensation}"
    return {
        "title": str(
            detail.get("stellenangebotsTitel")
            or detail.get("titel")
            or summary.get("beruf")
            or ""
        ),
        "company": str(detail.get("arbeitgeber") or summary.get("arbeitgeber") or ""),
        "location": location_text(detail, summary),
        "work_mode": work_mode(detail),
        "level": "",
        "url": (
            str(summary.get("externeUrl"))
            if summary.get("externeUrl")
            else f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{reference}"
        ),
        "date_posted": str(
            detail.get("aktuelleVeroeffentlichungsdatum")
            or summary.get("aktuelleVeroeffentlichungsdatum")
            or ""
        ),
        "description": description,
        "discovery_source": "BA Jobsuche endpoint — bund.dev documentation",
        "freshness": f"BA API refresh {query_date}",
        "ba_query_date": query_date,
        "ba_reference": reference,
    }


def retained_cache(
    cache_path: Path, retention_days: int, target_date: date
) -> list[dict[str, Any]]:
    cutoff = target_date - timedelta(days=retention_days)
    retained = []
    for job in read_json(cache_path).get("jobs", []):
        try:
            seen = date.fromisoformat(str(job["ba_query_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if seen >= cutoff:
            if not job.get("work_mode"):
                job["work_mode"] = work_mode(
                    {"stellenangebotsBeschreibung": job.get("description", "")}
                )
            retained.append(job)
    return retained


def merge_jobs(
    previous: list[dict[str, Any]], current: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_reference = {
        str(job.get("ba_reference") or job.get("url")): dict(job) for job in previous
    }
    for job in current:
        by_reference[str(job.get("ba_reference") or job.get("url"))] = dict(job)
    return list(by_reference.values())


def search_once_daily(
    config: dict[str, Any],
    user_agent: str,
    state_path: Path = STATE_PATH,
    cache_path: Path = CACHE_PATH,
    target_date: date | None = None,
) -> tuple[list[dict[str, Any]], str]:
    target_date = target_date or datetime.now(timezone.utc).date()
    date_text = target_date.isoformat()
    state = read_json(state_path)
    cached = retained_cache(
        cache_path, int(config.get("retention_days", 21)), target_date
    )
    if state.get("request_date_utc") == date_text:
        return cached, "BA API daily search already used; cached"

    queries = [str(query) for query in config["daily_queries"]]
    query = queries[target_date.toordinal() % len(queries)]
    state = {
        "request_date_utc": date_text,
        "request_count": 1,
        "detail_request_count": 0,
        "query": query or str(config.get("professional_field", "Informatik")),
        "status": "started",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    write_json(state_path, state)
    current = []
    errors = []
    try:
        response = api_get(
            search_url(config, query), str(config["client_id"]), user_agent
        )
        summaries = response.get("stellenangebote", [])
        role_pattern = re.compile(str(config["detail_candidate_pattern"]), re.I)
        candidates = [
            summary
            for summary in summaries
            if role_pattern.search(str(summary.get("beruf", "")))
        ][: int(config.get("max_details", 10))]
        for summary in candidates:
            reference = str(summary.get("referenznummer") or summary.get("refnr") or "")
            if not reference:
                continue
            state["detail_request_count"] += 1
            write_json(state_path, state)
            try:
                detail = api_get(
                    detail_url(config, reference),
                    str(config["client_id"]),
                    user_agent,
                )
                current.append(normalize_detail(detail, summary, date_text))
            except Exception as exc:
                errors.append(type(exc).__name__)
        cached = merge_jobs(cached, current)
        write_json(
            cache_path,
            {
                "updated_at": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat(),
                "jobs": cached,
            },
        )
        state["status"] = "partial" if errors else "success"
        state["search_result_count"] = len(summaries)
        state["normalized_results"] = len(current)
        state["detail_error_types"] = errors
    except Exception as exc:
        state["status"] = "error"
        state["error_type"] = type(exc).__name__
    state["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    write_json(state_path, state)
    return cached, (
        f"BA API refresh: {state['status']} "
        f"({state.get('normalized_results', 0)} detailed)"
    )
