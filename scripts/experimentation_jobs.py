"""Low-frequency Experimentation Jobs discovery via its public listing route."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "experimentation_jobs_state.json"
CACHE_PATH = ROOT / "data" / "experimentation_jobs_cache.json"


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


class ListingParser(HTMLParser):
    """Parse the compact list-item markup returned by WP Job Manager."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jobs: list[dict[str, str]] = []
        self._job: dict[str, str] | None = None
        self._listing_depth = 0
        self._capture: str | None = None
        self._capture_tag = ""
        self._capture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "li" and "job_listing" in classes:
            self._job = {
                "title": "",
                "company": "",
                "location": "",
                "work_mode": "",
                "url": "",
                "date_posted": "",
            }
            self._listing_depth = 1
            return
        if self._job is None:
            return

        self._listing_depth += 1
        if tag == "a" and not self._job["url"]:
            self._job["url"] = values.get("href") or ""
        if tag == "time" and values.get("datetime"):
            self._job["date_posted"] = values["datetime"] or ""

        capture = None
        if tag == "h3":
            capture = "title"
        elif tag == "div" and "company" in classes:
            capture = "company"
        elif tag == "div" and "location" in classes:
            capture = "location"
        elif tag == "li" and "job-type" in classes:
            capture = "work_mode"
        if capture:
            self._capture = capture
            self._capture_tag = tag
            self._capture_depth = self._listing_depth

    def handle_endtag(self, tag: str) -> None:
        if self._job is None:
            return
        if (
            self._capture
            and tag == self._capture_tag
            and self._listing_depth == self._capture_depth
        ):
            self._capture = None
            self._capture_tag = ""
            self._capture_depth = 0
        self._listing_depth -= 1
        if self._listing_depth == 0:
            for key, value in self._job.items():
                self._job[key] = re.sub(r"\s+", " ", value).strip()
            self.jobs.append(self._job)
            self._job = None

    def handle_data(self, data: str) -> None:
        if self._job is not None and self._capture and data.strip():
            self._job[self._capture] += f" {data.strip()}"


def parse_listing_payload(payload: str) -> list[dict[str, str]]:
    response = json.loads(payload)
    parser = ListingParser()
    parser.feed(str(response.get("html", "")))
    return parser.jobs


def contains_phrase(text: str, phrases: list[str]) -> bool:
    text = text.lower()
    return any(
        re.search(rf"(?<!\w){re.escape(phrase.lower())}(?!\w)", text)
        for phrase in phrases
    )


def remote_location_is_eligible(location: str, config: dict[str, Any]) -> bool:
    location = location.strip().lower()
    if location in {"", "remote"}:
        return False
    if location in {"worldwide", "global", "anywhere"}:
        return True
    if contains_phrase(location, config["eligible_remote_regions"]):
        return True
    return not contains_phrase(location, config["ineligible_remote_regions"])


def normalize_listings(
    listings: list[dict[str, str]],
    config: dict[str, Any],
    discovery: dict[str, Any],
    query_date: str,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    role_terms = list(discovery["role_terms"]) + list(config["extra_role_terms"])
    for listing in listings:
        title = listing["title"]
        company = listing["company"]
        location = listing["location"]
        work_mode = listing["work_mode"].title()
        text = f"{title} {company} {location}"
        if not contains_phrase(title, role_terms):
            continue
        if contains_phrase(text, discovery["blocked_terms"]):
            continue
        if contains_phrase(company, discovery["blocked_companies"]):
            continue
        in_hamburg_area = contains_phrase(location, discovery["hamburg_area_terms"])
        if work_mode != "Remote" and not in_hamburg_area:
            continue

        jobs.append(
            {
                "title": title,
                "company": company,
                "location": location or "Remote — region not stated",
                "work_mode": work_mode,
                "level": "",
                "url": listing["url"],
                "date_posted": listing["date_posted"],
                "description": (
                    "Experimentation or product-analytics role discovered on "
                    "Experimentation Jobs. Open the original listing to validate "
                    "the full requirements and remote-work eligibility."
                ),
                "discovery_source": "Experimentation Jobs",
                "freshness": f"Experimentation Jobs refresh {query_date}",
                "experimentation_query_date": query_date,
            }
        )
    return jobs


def retained_cache(
    cache_path: Path, retention_days: int, target_date: date
) -> list[dict[str, Any]]:
    cutoff = target_date - timedelta(days=retention_days)
    retained = []
    for job in read_json(cache_path).get("jobs", []):
        try:
            seen = date.fromisoformat(str(job["experimentation_query_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if seen >= cutoff:
            retained.append(job)
    return retained


def merge_jobs(
    previous: list[dict[str, Any]], current: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_url = {str(job.get("url", "")): dict(job) for job in previous}
    for job in current:
        by_url[str(job.get("url", ""))] = dict(job)
    return list(by_url.values())


def request_listings(config: dict[str, Any], user_agent: str, query: str) -> str:
    form_data = urllib.parse.urlencode(
        {
            "search_keywords": query,
            "search_location": config.get("location", "Germany"),
        }
    )
    body = urllib.parse.urlencode(
        {
            "search_keywords": query,
            "search_location": config.get("location", "Germany"),
            "per_page": min(int(config.get("per_page", 50)), 50),
            "orderby": "date",
            "order": "DESC",
            "page": 1,
            "show_pagination": "false",
            "featured_first": "false",
            "form_data": form_data,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        str(config["endpoint"]),
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": user_agent,
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read(1_500_000).decode("utf-8", errors="replace")


def search_once_daily(
    config: dict[str, Any],
    discovery: dict[str, Any],
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
        return cached, "Experimentation Jobs daily refresh already used; cached"

    queries = [str(query) for query in config["daily_queries"]]
    query = queries[target_date.toordinal() % len(queries)]
    state = {
        "request_date_utc": date_text,
        "request_count": 1,
        "query": query,
        "status": "started",
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    write_json(state_path, state)
    try:
        payload = request_listings(config, user_agent, query)
        listings = parse_listing_payload(payload)
        current = normalize_listings(listings, config, discovery, date_text)
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
        state["status"] = "success"
        state["listing_count"] = len(listings)
        state["accepted_results"] = len(current)
    except Exception as exc:
        state["status"] = "error"
        state["error_type"] = type(exc).__name__
    state["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    write_json(state_path, state)
    return cached, (
        f"Experimentation Jobs refresh: {state['status']} "
        f"({state.get('accepted_results', 0)} accepted)"
    )
