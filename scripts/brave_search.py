"""One-request-per-UTC-day Brave Search discovery for the job radar."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEY_PATH = ROOT.parent / "docu" / "brave_search_api_key"
STATE_PATH = ROOT / "data" / "search_state.json"
API_URL = "https://api.search.brave.com/res/v1/web/search"


def read_api_key() -> str | None:
    from_environment = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if from_environment:
        return from_environment
    key_path = Path(os.environ.get("BRAVE_SEARCH_API_KEY_FILE", str(DEFAULT_KEY_PATH)))
    if not key_path.exists():
        return None
    value = key_path.read_text(encoding="utf-8").strip()
    return value or None


def read_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def daily_query(config: dict[str, Any], target_date: date) -> str:
    queries = config["daily_queries"]
    return str(queries[target_date.toordinal() % len(queries)])


def contains_term(text: str, terms: list[str]) -> bool:
    text = text.lower()
    return any(
        re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", text) for term in terms
    )


def infer_company(
    title: str,
    description: str,
    url: str,
    config: dict[str, Any],
) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    text = f"{title} {description}".lower()
    for company in config["company_watchlist"]:
        if any(
            re.search(rf"(?<!\w){re.escape(alias.lower())}(?!\w)", text)
            for alias in company["aliases"]
        ):
            return str(company["name"])
    for company in config["company_watchlist"]:
        if any(domain in host for domain in company["domains"]):
            return str(company["name"])
    if config.get("require_watchlist_company", False):
        return ""
    host = host.removeprefix("www.")
    return host.split(".")[0].replace("-", " ").title() or "Unknown employer"


def infer_work_mode(text: str, config: dict[str, Any]) -> str:
    text = text.lower()
    if any(term in text for term in config["remote_terms"]):
        return "Remote"
    if any(term in text for term in config["hybrid_terms"]):
        return "Hybrid"
    return ""


def clean_result_title(title: str, company: str) -> str:
    title = re.sub(r"(?i)^job\s*[:\-–—]\s*", "", title).strip()
    for separator in [" | ", " – ", " — ", " - "]:
        parts = title.split(separator)
        if len(parts) > 1 and company.lower() in parts[-1].lower():
            title = separator.join(parts[:-1]).strip()
            break
    return title


def normalize_results(
    response: dict[str, Any],
    config: dict[str, Any],
    query_date: str,
) -> list[dict[str, Any]]:
    normalized = []
    for result in response.get("web", {}).get("results", []):
        raw_title = str(result.get("title", ""))
        description = re.sub(r"<[^>]+>", " ", str(result.get("description", "")))
        description = re.sub(r"\s+", " ", description).strip()
        url = str(result.get("url", ""))
        company = infer_company(raw_title, description, url, config)
        if not company:
            continue
        title = clean_result_title(raw_title, company)
        text = f"{title} {description} {company} {url}"

        if not contains_term(text, config["role_terms"]):
            continue
        if contains_term(text, config["blocked_terms"]) or contains_term(
            text, config["blocked_companies"]
        ):
            continue
        work_mode = infer_work_mode(text, config)
        in_hamburg_area = contains_term(text, config["hamburg_area_terms"])
        if not in_hamburg_area and work_mode not in {"Hybrid", "Remote"}:
            continue

        if in_hamburg_area:
            location = "Hamburg area"
        elif work_mode == "Remote":
            location = "Remote — region stated on source page"
        else:
            location = "Outside Hamburg area"

        normalized.append(
            {
                "title": title,
                "company": company,
                "location": location,
                "work_mode": work_mode,
                "level": "",
                "url": url,
                "date_posted": str(result.get("age", "")),
                "description": description,
                "discovery_source": "Brave Search API — daily request budget",
                "freshness": f"Brave query {query_date}",
                "brave_query_date": query_date,
            }
        )
    return normalized


def request_query(
    config: dict[str, Any],
    query: str,
    api_key: str,
    query_date: str,
) -> list[dict[str, Any]]:
    parameters = urllib.parse.urlencode(
        {
            "q": query,
            "count": min(int(config.get("result_count", 20)), 20),
            "country": config.get("country", "de"),
            "search_lang": config.get("search_lang", "de"),
            "safesearch": "strict",
        }
    )
    request = urllib.request.Request(
        f"{API_URL}?{parameters}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
            "User-Agent": "HamburgJobRadar/0.2",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return normalize_results(payload, config, query_date)


def search_once_daily(
    config: dict[str, Any],
    state_path: Path = STATE_PATH,
    target_date: date | None = None,
) -> tuple[list[dict[str, Any]], str]:
    target_date = target_date or datetime.now(timezone.utc).date()
    date_text = target_date.isoformat()
    state = read_state(state_path)
    budget = max(1, int(config.get("daily_request_limit", 1)))
    same_day = state.get("request_date_utc") == date_text
    regular_count = int(state.get("request_count", 0)) if same_day else 0
    attempted = list(state.get("daily_attempted_queries", [])) if same_day else []
    if same_day and not attempted and state.get("query"):
        attempted.append(str(state["query"]))
    accepted_count = int(state.get("accepted_results", 0)) if same_day else 0

    bootstrap_count = 0
    if str(state.get("bootstrap_completed_at", "")).startswith(date_text):
        bootstrap_count = int(state.get("bootstrap_request_count", 0))
        attempted.extend(
            str(query) for query in state.get("bootstrap_attempted_queries", [])
        )
    used = regular_count + bootstrap_count
    remaining = max(0, budget - used)
    queries = [
        str(query) for query in config["daily_queries"] if str(query) not in attempted
    ][:remaining]
    if not queries:
        return [], f"Brave daily budget already used ({used}/{budget}); skipped"

    api_key = read_api_key()
    if not api_key:
        return [], "Brave API key unavailable; skipped"

    bootstrap_fields = {
        key: value for key, value in state.items() if key.startswith("bootstrap_")
    }
    if not same_day:
        state = bootstrap_fields
        attempted = []
        accepted_count = 0
        regular_count = 0

    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    for query in queries:
        regular_count += 1
        attempted.append(query)
        state.update(
            {
                "request_date_utc": date_text,
                "request_count": regular_count,
                "daily_attempted_queries": attempted,
                "query": query,
                "status": "running",
                "updated_at": (
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                ),
            }
        )
        write_state(state, state_path)
        try:
            jobs.extend(request_query(config, query, api_key, date_text))
        except Exception as exc:
            errors.append(type(exc).__name__)

    accepted_count += len(jobs)
    state["status"] = "partial" if errors else "success"
    state["accepted_results"] = accepted_count
    state["daily_error_types"] = errors
    state["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    write_state(state, state_path)
    total_used = regular_count + bootstrap_count
    return (
        jobs,
        f"Brave daily budget: {state['status']} "
        f"({total_used}/{budget} requests used, {len(jobs)} accepted)",
    )


def bootstrap_queries(
    config: dict[str, Any],
    state_path: Path = STATE_PATH,
    target_date: date | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Run each configured query once as an explicit, permanent bootstrap."""
    target_date = target_date or datetime.now(timezone.utc).date()
    date_text = target_date.isoformat()
    state = read_state(state_path)
    if state.get("bootstrap_completed_at"):
        return [], "Brave bootstrap already completed; skipped"

    api_key = read_api_key()
    if not api_key:
        return [], "Brave API key unavailable; bootstrap skipped"

    prior_query = (
        str(state.get("query", ""))
        if state.get("request_date_utc") == date_text
        else ""
    )
    queries = [
        str(query) for query in config["daily_queries"] if str(query) != prior_query
    ]
    accepted_jobs: list[dict[str, Any]] = []
    attempted_queries: list[str] = []
    errors: list[str] = []

    for query in queries:
        attempted_queries.append(query)
        state.update(
            {
                "bootstrap_status": "running",
                "bootstrap_request_count": len(attempted_queries),
                "bootstrap_attempted_queries": attempted_queries,
                "updated_at": (
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                ),
            }
        )
        write_state(state, state_path)
        try:
            accepted_jobs.extend(request_query(config, query, api_key, date_text))
        except Exception as exc:
            errors.append(type(exc).__name__)

    state.update(
        {
            "bootstrap_completed_at": (
                datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            ),
            "bootstrap_status": "partial" if errors else "success",
            "bootstrap_request_count": len(attempted_queries),
            "bootstrap_attempted_queries": attempted_queries,
            "bootstrap_accepted_results": len(accepted_jobs),
            "bootstrap_error_types": errors,
            "updated_at": (
                datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            ),
        }
    )
    write_state(state, state_path)
    status = state["bootstrap_status"]
    return (
        accepted_jobs,
        f"Brave one-time bootstrap: {status} "
        f"({len(attempted_queries)} requests, {len(accepted_jobs)} accepted)",
    )
