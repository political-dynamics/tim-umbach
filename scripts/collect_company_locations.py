#!/usr/bin/env python3
"""Cache evidence-backed Hamburg employer offices and their OSM coordinates.

Official addresses are curated in config/company_locations.json. For employers
without a source, an optional Brave query can discover an official page within
the shared daily request budget. Only new or changed addresses are sent to
Nominatim, sequentially and no faster than its public usage policy permits.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from brave_search import API_URL, read_api_key, read_state, write_state


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "company_locations.json"
DISCOVERY_PATH = ROOT / "config" / "discovery.json"
JOBS_PATH = ROOT / "data" / "jobs.json"
OUTPUT_PATH = ROOT / "data" / "company_locations.json"
BRAVE_STATE_PATH = ROOT / "data" / "search_state.json"


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg"}:
            self.ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


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


def extract_hamburg_address(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()
    street = (
        r"(?:An\s+der\s+Alster|Großer\s+Burstah|Neumühlen|"
        r"[\wÄÖÜäöüß'.-]*(?:straße|strasse|str\.|weg|allee|platz|ring|kai|"
        r"stieg|damm|chaussee|ufer|markt|wall|reihe|kamp))"
    )
    patterns = [
        rf"(?P<street>{street}\s+\d{{1,4}}(?:\s*[-–]\s*\d{{1,4}})?[a-zA-Z]?)"
        rf"\s*[,;|]?\s*(?:D[- ]?)?(?P<postal>\d{{5}})\s+(?P<city>Hamburg)\b"
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            street = re.sub(r"\s+", " ", match.group("street")).strip(" ,;|")
            return f"{street}, {match.group('postal')} Hamburg, Germany"
    return ""


def company_watchlist_entry(company: str, discovery: dict[str, Any]) -> dict[str, Any] | None:
    lowered = company.lower()
    for entry in discovery.get("company_watchlist", []):
        names = [str(entry.get("name", "")), *map(str, entry.get("aliases", []))]
        if any(name and (name.lower() in lowered or lowered in name.lower()) for name in names):
            return entry
    return None


def official_result_url(url: str, entry: dict[str, Any]) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(
        host == str(domain).lower() or host.endswith(f".{str(domain).lower()}")
        for domain in entry.get("domains", [])
    )


def fetch_official_text(url: str, user_agent: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    robots_url = f"{parsed.scheme}://{parsed.hostname}/robots.txt"
    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(robots_url)
    try:
        robots.read()
    except Exception:
        return ""
    if not robots.can_fetch(user_agent, url):
        return ""
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/html", "User-Agent": user_agent},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        if "text/html" not in response.headers.get_content_type():
            return ""
        parser = TextParser()
        parser.feed(response.read(1_500_000).decode("utf-8", errors="ignore"))
        return parser.text


def reserve_brave_request(
    query: str,
    discovery: dict[str, Any],
    target_date: date,
    state_path: Path = BRAVE_STATE_PATH,
) -> tuple[bool, int, int]:
    state = read_state(state_path)
    date_text = target_date.isoformat()
    same_day = state.get("request_date_utc") == date_text
    regular = int(state.get("request_count", 0)) if same_day else 0
    bootstrap = (
        int(state.get("bootstrap_request_count", 0))
        if str(state.get("bootstrap_completed_at", "")).startswith(date_text)
        else 0
    )
    budget = max(1, int(discovery.get("daily_request_limit", 1)))
    if regular + bootstrap >= budget:
        return False, regular + bootstrap, budget
    if not same_day:
        state = {
            key: value for key, value in state.items() if key.startswith("bootstrap_")
        }
        regular = 0
    attempted = list(state.get("daily_attempted_queries", []))
    regular += 1
    attempted.append(query)
    state.update(
        {
            "request_date_utc": date_text,
            "request_count": regular,
            "daily_attempted_queries": attempted,
            "query": query,
            "status": "address discovery running",
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
    )
    write_state(state, state_path)
    return True, regular + bootstrap, budget


def brave_discover_official_address(
    company: str,
    entry: dict[str, Any],
    discovery: dict[str, Any],
    user_agent: str,
    target_date: date,
    state_path: Path = BRAVE_STATE_PATH,
) -> tuple[dict[str, str] | None, str, bool]:
    domains = [str(domain) for domain in entry.get("domains", [])]
    domain_filter = " OR ".join(f"site:{domain}" for domain in domains)
    query = f'"{company}" Hamburg Adresse Standort ({domain_filter})'
    api_key = read_api_key()
    if not api_key:
        return None, "Brave address discovery skipped; API key unavailable", False
    reserved, used, budget = reserve_brave_request(
        query, discovery, target_date, state_path
    )
    if not reserved:
        return (
            None,
            f"Brave address discovery skipped ({used}/{budget} requests used)",
            False,
        )
    parameters = urllib.parse.urlencode(
        {
            "q": query,
            "count": 8,
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
            "User-Agent": user_agent,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return None, f"Brave address discovery error: {type(exc).__name__}", True
    for result in payload.get("web", {}).get("results", []):
        url = str(result.get("url", ""))
        if not official_result_url(url, entry):
            continue
        snippet = re.sub(r"<[^>]+>", " ", str(result.get("description", "")))
        address = extract_hamburg_address(snippet)
        if not address:
            try:
                address = extract_hamburg_address(fetch_official_text(url, user_agent))
            except Exception:
                address = ""
        if address:
            return (
                {
                    "company": company,
                    "address": address,
                    "source_url": url,
                    "source_type": "Brave-discovered official company page",
                },
                f"Brave address discovery found an official source for {company}",
                True,
            )
    return (
        None,
        f"Brave address discovery found no verified Hamburg address for {company}",
        True,
    )


def select_hamburg_result(
    results: list[dict[str, Any]], viewbox: list[float]
) -> dict[str, Any] | None:
    west, north, east, south = map(float, viewbox)
    for result in results:
        try:
            latitude = float(result["lat"])
            longitude = float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        rendered = str(result.get("display_name", "")).lower()
        if (
            "hamburg" in rendered
            and south <= latitude <= north
            and west <= longitude <= east
        ):
            return result
    return None


def geocode_address(
    address: str, config: dict[str, Any], user_agent: str
) -> dict[str, Any] | None:
    nominatim = config["nominatim"]
    west, north, east, south = map(float, nominatim["viewbox"])
    parameters = urllib.parse.urlencode(
        {
            "q": address,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 3,
            "countrycodes": "de",
            "viewbox": f"{west},{north},{east},{south}",
            "bounded": 1,
        }
    )
    request = urllib.request.Request(
        f"{nominatim['search_url']}?{parameters}",
        headers={"Accept": "application/json", "User-Agent": user_agent},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        results = json.loads(response.read().decode("utf-8"))
    selected = select_hamburg_result(results, nominatim["viewbox"])
    if not selected:
        return None
    osm_type = str(selected.get("osm_type", ""))
    osm_id = str(selected.get("osm_id", ""))
    type_path = {"node": "node", "way": "way", "relation": "relation"}.get(osm_type)
    return {
        "latitude": round(float(selected["lat"]), 6),
        "longitude": round(float(selected["lon"]), 6),
        "geocoder_label": str(selected.get("display_name", "")),
        "osm_url": (
            f"https://www.openstreetmap.org/{type_path}/{osm_id}"
            if type_path and osm_id
            else "https://www.openstreetmap.org/"
        ),
    }


def unresolved_is_recent(
    unresolved: dict[str, Any], company: str, target_date: date, retry_days: int
) -> bool:
    try:
        attempted = date.fromisoformat(str(unresolved[company]["last_attempted_at"])[:10])
    except (KeyError, TypeError, ValueError):
        return False
    return attempted >= target_date - timedelta(days=retry_days)


def collect_locations(
    config: dict[str, Any],
    jobs_payload: dict[str, Any],
    previous: dict[str, Any],
    discover_with_brave: bool,
    offline: bool,
    target_date: date | None = None,
    brave_state_path: Path = BRAVE_STATE_PATH,
) -> dict[str, Any]:
    target_date = target_date or datetime.now(timezone.utc).date()
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    user_agent = str(config["user_agent"])
    discovery = read_json(DISCOVERY_PATH)
    sources = {
        str(item["company"]): {
            **item,
            "source_type": item.get("source_type", "Official company page"),
        }
        for item in config.get("companies", [])
    }
    previous_locations = previous.get("locations", {})
    # A skipped lookup did not reach Brave and must not suppress the next real
    # attempt for 30 days. Older versions could persist such markers.
    unresolved = {
        company: item
        for company, item in previous.get("unresolved", {}).items()
        if "skipped" not in str(item.get("status", "")).lower()
    }
    companies = sorted(
        {
            str(job.get("company", ""))
            for job in jobs_payload.get("jobs", [])
            if job.get("map") and job.get("company")
        }
    )
    brave_messages: list[str] = []
    max_brave = int(config.get("brave", {}).get("max_queries_per_run", 1))
    retry_days = int(config.get("brave", {}).get("retry_days", 30))
    brave_used = 0
    if discover_with_brave and not offline:
        for company in companies:
            if company in sources or company in previous_locations:
                continue
            if unresolved_is_recent(unresolved, company, target_date, retry_days):
                continue
            entry = company_watchlist_entry(company, discovery)
            if not entry or brave_used >= max_brave:
                continue
            found, status, attempted = brave_discover_official_address(
                company,
                entry,
                discovery,
                user_agent,
                target_date,
                brave_state_path,
            )
            brave_messages.append(status)
            if not attempted:
                break
            brave_used += 1
            if found:
                sources[company] = found
                unresolved.pop(company, None)
            else:
                unresolved[company] = {
                    "last_attempted_at": checked_at,
                    "status": status,
                }

    locations: dict[str, Any] = {}
    errors: list[str] = []
    last_request = 0.0
    for company, source in sorted(sources.items()):
        address = str(source.get("address", "")).strip()
        cached = previous_locations.get(company, {})
        if (
            address
            and str(cached.get("address", "")) == address
            and cached.get("latitude") is not None
            and cached.get("longitude") is not None
        ):
            locations[company] = {**cached, **source}
            continue
        if offline or not address:
            if cached:
                locations[company] = cached
            continue
        elapsed = time.monotonic() - last_request
        minimum = float(config["nominatim"].get("minimum_interval_seconds", 1.1))
        if elapsed < minimum:
            time.sleep(minimum - elapsed)
        try:
            coordinates = geocode_address(address, config, user_agent)
            last_request = time.monotonic()
        except Exception as exc:
            coordinates = None
            last_request = time.monotonic()
            errors.append(f"{company}: {type(exc).__name__}")
        if not coordinates:
            errors.append(f"{company}: no Hamburg geocode for {address}")
            if cached:
                locations[company] = cached
            continue
        locations[company] = {
            **source,
            **coordinates,
            "geocoded_at": checked_at,
        }

    return {
        "meta": {
            "updated_at": checked_at,
            "location_count": len(locations),
            "official_source_count": sum(
                str(item.get("source_type", "")).lower().startswith("official")
                for item in locations.values()
            ),
            "brave_discovered_count": sum(
                str(item.get("source_type", "")).lower().startswith("brave")
                for item in locations.values()
            ),
            "brave_status": brave_messages or [
                "Brave address discovery not requested" if not discover_with_brave
                else "No eligible unresolved employer used the Brave fallback"
            ],
            "nominatim_policy_url": config["nominatim"]["policy_url"],
            "errors": errors,
        },
        "locations": locations,
        "unresolved": unresolved,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--discover-with-brave",
        action="store_true",
        help="Use at most one remaining shared Brave request for an unresolved employer",
    )
    parser.add_argument(
        "--offline", action="store_true", help="Reuse the cache without network calls"
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--jobs", type=Path, default=JOBS_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    output = collect_locations(
        read_json(args.config),
        read_json(args.jobs),
        read_json(args.output),
        discover_with_brave=args.discover_with_brave,
        offline=args.offline,
    )
    write_json(args.output, output)
    print(
        f"Cached {output['meta']['location_count']} employer locations in "
        f"{args.output}; {len(output['meta']['errors'])} warning(s)."
    )
    for message in output["meta"]["brave_status"]:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
