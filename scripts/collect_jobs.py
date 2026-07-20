#!/usr/bin/env python3
"""Collect, normalize, score, and salary-estimate Hamburg job postings.

The collector deliberately avoids scraping Google result pages. It visits a
small allowlist of official company career pages at low frequency, respects
robots.txt, and falls back to the dated prototype snapshot when necessary.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ba_jobs import search_once_daily as search_ba_jobs
from brave_search import bootstrap_queries, contains_term, search_once_daily
from experimentation_jobs import search_once_daily as search_experimentation_jobs


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "profile.json"
SOURCES_PATH = ROOT / "config" / "sources.json"
DISCOVERY_PATH = ROOT / "config" / "discovery.json"
SEED_PATH = ROOT / "data" / "seed_jobs.json"
OUTPUT_PATH = ROOT / "data" / "jobs.json"

SALARY_BENCHMARKS = {
    "data_science": 74000,
    "data_analytics": 77364,
    "analytics_engineering": 79000,
    "data_engineering": 82000,
    "product_analytics": 78000,
    "business_analytics": 72000,
    "other": 70000,
}

LEVEL_FACTORS = {
    "student": 0.45,
    "intern": 0.42,
    "junior": 0.76,
    "experienced": 1.00,
    "mid": 1.00,
    "senior": 1.12,
    "staff": 1.24,
    "lead": 1.20,
    "manager": 1.18,
}

GAP_KEYWORDS = {
    "AWS": ["aws", "amazon web services"],
    "dbt": ["dbt"],
    "Dagster": ["dagster"],
    "Airflow": ["airflow"],
    "Terraform": ["terraform"],
    "Kubernetes": ["kubernetes"],
    "PyTorch": ["pytorch"],
    "Spark": ["spark", "pyspark"],
    "GA4": ["ga4", "google analytics 4"],
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.json_ld: list[str] = []
        self.text_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.title_parts: list[str] = []
        self._json_depth = 0
        self._ignored_depth = 0
        self._h1_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        onclick = values.get("onclick") or ""
        match = re.search(
            r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)", onclick
        )
        if match:
            self.links.append(match.group(1))
        if tag == "script":
            if "ld+json" in (values.get("type") or ""):
                self._json_depth += 1
            else:
                self._ignored_depth += 1
        elif tag in {"style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag == "h1":
            self._h1_depth += 1
        if tag == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            if self._json_depth:
                self._json_depth -= 1
            elif self._ignored_depth:
                self._ignored_depth -= 1
        elif tag in {"style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "h1" and self._h1_depth:
            self._h1_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._json_depth:
            self.json_ld.append(data)
            return
        if self._ignored_depth:
            return
        if data.strip():
            self.text_parts.append(data.strip())
            if self._h1_depth:
                self.h1_parts.append(data.strip())
            if self._title_depth:
                self.title_parts.append(data.strip())

    @property
    def page_text(self) -> str:
        return clean_text(" ".join(self.text_parts))


@dataclass
class Fetcher:
    user_agent: str
    delay: float

    def __post_init__(self) -> None:
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request = 0.0

    def _robot_parser(self, url: str) -> urllib.robotparser.RobotFileParser:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser(
                urllib.parse.urljoin(origin, "/robots.txt")
            )
            try:
                parser.read()
            except Exception:
                parser = urllib.robotparser.RobotFileParser()
                parser.set_url(urllib.parse.urljoin(origin, "/robots.txt"))
                parser.parse(["User-agent: *", "Disallow: /"])
            self._robots[origin] = parser
        return self._robots[origin]

    def get(self, url: str) -> str:
        if not self._robot_parser(url).can_fetch(self.user_agent, url):
            raise PermissionError(f"robots.txt does not allow collection: {url}")
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = response.read(2_500_000)
            charset = response.headers.get_content_charset() or "utf-8"
        self._last_request = time.monotonic()
        return payload.decode(charset, errors="replace")


def find_job_posting(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        kind = value.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            return value
        for child in value.values():
            found = find_job_posting(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_job_posting(child)
            if found:
                return found
    return None


def location_from_json_ld(value: Any) -> str:
    if isinstance(value, list):
        locations = [location_from_json_ld(item) for item in value]
        return " / ".join(item for item in locations if item)
    if not isinstance(value, dict):
        return ""
    address = value.get("address", value)
    if not isinstance(address, dict):
        return clean_text(str(address))
    parts = [
        address.get("addressLocality"),
        address.get("addressRegion"),
        address.get("addressCountry"),
    ]
    return ", ".join(
        str(part)
        for part in parts
        if part and str(part).strip().lower() not in {"unavailable", "none", "null"}
    )


def salary_from_json_ld(value: dict[str, Any]) -> tuple[int | None, int | None]:
    salary = value.get("baseSalary")
    if not isinstance(salary, dict):
        return None, None
    amount = salary.get("value", salary)
    if isinstance(amount, dict):
        lower = amount.get("minValue")
        upper = amount.get("maxValue")
        unit = str(amount.get("unitText", salary.get("unitText", "YEAR"))).upper()
    else:
        lower = upper = amount
        unit = str(salary.get("unitText", "YEAR")).upper()
    try:
        factor = 12 if "MONTH" in unit else 1
        return int(float(lower) * factor), int(float(upper) * factor)
    except (TypeError, ValueError):
        return None, None


def parse_advertised_salary(text: str) -> tuple[int | None, int | None]:
    patterns = [
        r"(?i)(\d{2,3})\s*k\s*(?:€|eur)?\s*[-–—]\s*(\d{2,3})\s*k",
        r"(?i)(\d{2,3})[.,]000\s*(?:€|eur)?\s*[-–—]\s*(\d{2,3})[.,]000",
        r"(?i)salary\s+range\D{0,20}(\d{2,3})\s*k\s*[-–—]\s*(\d{2,3})\s*k",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1)) * 1000, int(match.group(2)) * 1000
    return None, None


def infer_title(parser: PageParser, url: str) -> str:
    if parser.h1_parts:
        title = clean_text(" ".join(parser.h1_parts))
        title = re.sub(r"(?i)^care changes everything[.!]?\s*", "", title)
        repeated = re.fullmatch(r"(.+?)\s+\1", title)
        return repeated.group(1) if repeated else title
    if parser.title_parts:
        return clean_text(" ".join(parser.title_parts).split("|")[0])
    slug = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").title()


def focused_page_description(page_text: str, title: str) -> str:
    """Start near the role heading instead of navigation repeated before it."""
    positions = [
        match.start()
        for match in re.finditer(re.escape(title), page_text, flags=re.IGNORECASE)
    ]
    start = positions[1] if len(positions) > 1 else positions[0] if positions else 0
    focused = page_text[start : start + 6000]
    repeated_prefix = f"{title} {title}".lower()
    while focused.lower().startswith(repeated_prefix):
        focused = focused[len(title) :].lstrip()
    return focused


def normalize_job(
    url: str, page: str, company: str, discovery_source: str
) -> dict[str, Any]:
    parser = PageParser()
    parser.feed(page)
    posting: dict[str, Any] | None = None
    for raw in parser.json_ld:
        try:
            posting = find_job_posting(json.loads(raw))
        except json.JSONDecodeError:
            continue
        if posting:
            break

    if posting:
        salary_min, salary_max = salary_from_json_ld(posting)
        job = {
            "title": clean_text(str(posting.get("title", ""))),
            "company": company,
            "location": location_from_json_ld(posting.get("jobLocation")),
            "work_mode": "Remote" if posting.get("jobLocationType") else "",
            "level": "",
            "url": clean_text(str(posting.get("url", url))),
            "date_posted": clean_text(str(posting.get("datePosted", ""))),
            "description": clean_text(str(posting.get("description", ""))),
            "discovery_source": discovery_source,
        }
        if salary_min and salary_max:
            job.update(
                {
                    "salary_min": salary_min,
                    "salary_max": salary_max,
                    "salary_currency": "EUR",
                    "salary_source": "Employer advertised",
                }
            )
    else:
        title = infer_title(parser, url)
        job = {
            "title": title,
            "company": company,
            "location": "Hamburg, Germany"
            if "hamburg" in parser.page_text.lower()
            else "",
            "work_mode": "Hybrid" if "hybrid" in parser.page_text.lower() else "",
            "level": "",
            "url": url,
            "date_posted": "",
            "description": focused_page_description(parser.page_text, title),
            "discovery_source": discovery_source,
        }

    if not job.get("work_mode"):
        page_text = parser.page_text.lower()
        if any(term in page_text for term in ["fully remote", "100% remote"]):
            job["work_mode"] = "Remote"
        elif any(
            term in page_text
            for term in [
                "hybrid",
                "hybrides arbeiten",
                "mobiles arbeiten",
                "mobile working",
                "homeoffice",
                "home-office",
            ]
        ):
            job["work_mode"] = "Hybrid / mobile work"
        elif any(term in page_text for term in ["on-site", "onsite"]):
            job["work_mode"] = "On-site"

    if not job.get("salary_min"):
        lower, upper = parse_advertised_salary(
            f"{job.get('description', '')} {parser.page_text}"
        )
        if lower and upper:
            job.update(
                {
                    "salary_min": lower,
                    "salary_max": upper,
                    "salary_currency": "EUR",
                    "salary_source": "Employer advertised",
                }
            )
    return job


def discover_jobs(
    source: dict[str, Any], fetcher: Fetcher, max_details: int
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    priority_links = [str(url) for url in source.get("detail_urls", [])]
    links: set[str] = set(priority_links)
    for discovery_url in source["discovery_urls"]:
        try:
            page = fetcher.get(discovery_url)
        except Exception as exc:
            errors.append(f"{source['company']}: {type(exc).__name__}: {exc}")
            continue
        parser = PageParser()
        parser.feed(page)
        for href in parser.links:
            url = urllib.parse.urljoin(discovery_url, href).split("#")[0]
            parsed = urllib.parse.urlparse(url)
            if parsed.hostname not in source["allowed_hosts"]:
                continue
            if source["link_pattern"] not in url:
                continue
            link_text = url.lower()
            filter_by_url = source.get("filter_links_by_url", True)
            if not filter_by_url or any(
                term in link_text for term in source["include_terms"]
            ):
                links.add(url)

    jobs: list[dict[str, Any]] = []
    ordered_links = priority_links + sorted(links - set(priority_links))
    for url in ordered_links[:max_details]:
        try:
            page = fetcher.get(url)
            job = normalize_job(url, page, source["company"], "Official career page")
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        detail_haystack = " ".join(
            [
                str(job.get("title", "")),
                str(job.get("description", "")),
            ]
        ).lower()
        relevance_haystack = (
            str(job.get("title", "")).lower()
            if source.get("filter_details_by_title")
            else detail_haystack
        )
        if not any(term in relevance_haystack for term in source["include_terms"]):
            continue
        location_haystack = (f"{detail_haystack} {job.get('location', '')}").lower()
        if source.get("location_terms") and not any(
            term in location_haystack for term in source["location_terms"]
        ):
            continue
        job["freshness"] = "live check"
        jobs.append(job)
    return jobs, errors


def retain_recent_brave_jobs(
    output_path: Path, retention_days: int, today: date | None = None
) -> list[dict[str, Any]]:
    if not output_path.exists():
        return []
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=retention_days)
    try:
        previous = read_json(output_path)
    except (json.JSONDecodeError, OSError):
        return []
    retained = []
    for job in previous.get("jobs", []):
        if not str(job.get("discovery_source", "")).startswith("Brave Search"):
            continue
        try:
            query_date = date.fromisoformat(str(job["brave_query_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if query_date >= cutoff:
            job["discovery_source"] = "Brave Search API — daily request budget"
            retained.append(job)
    return retained


def job_matches_preferences(job: dict[str, Any], discovery: dict[str, Any]) -> bool:
    text = " ".join(
        str(job.get(field, ""))
        for field in ["title", "company", "description", "location", "url"]
    )
    if contains_term(text, discovery["blocked_terms"]):
        return False
    title = str(job.get("title", ""))
    blocked_title_terms = discovery.get("blocked_title_terms", [])
    if any(
        re.search(rf"\b{re.escape(str(term))}\b", title, flags=re.IGNORECASE)
        for term in blocked_title_terms
    ):
        return False
    if re.search(r"\bscientists?\b", title, flags=re.IGNORECASE) and not contains_term(
        title, discovery.get("scientist_title_qualifiers", [])
    ):
        return False
    if contains_term(str(job.get("company", "")), discovery["blocked_companies"]):
        return False
    if str(job.get("discovery_source", "")).startswith(
        "Brave Search"
    ) and discovery.get("require_watchlist_company", False):
        watched_companies = {
            str(company["name"]) for company in discovery["company_watchlist"]
        }
        if str(job.get("company", "")) not in watched_companies:
            return False

    return True


def assess_work_mode_fit(
    job: dict[str, Any], discovery: dict[str, Any]
) -> dict[str, str]:
    """Label work-mode evidence without hiding otherwise relevant roles."""
    title = str(job.get("title", "")).lower()
    work_mode = str(job.get("work_mode", "")).strip().lower()
    location = str(job.get("location", "")).lower()
    description = str(job.get("description", "")).lower()
    text = f"{title} {work_mode} {location} {description}"
    in_hamburg_area = contains_term(text, discovery["hamburg_area_terms"])

    if any(term in f"{title} {work_mode}" for term in ["on-site", "onsite", "on site"]):
        return {
            "work_mode_fit": "mismatch",
            "work_mode_note": "Appears to require regular on-site work",
        }

    if work_mode.startswith("hybrid"):
        if in_hamburg_area:
            return {
                "work_mode_fit": "fit",
                "work_mode_note": "Hybrid work in the Hamburg area is stated",
            }
        return {
            "work_mode_fit": "mismatch",
            "work_mode_note": "Hybrid role, but not clearly based near Hamburg",
        }

    if work_mode.startswith("remote"):
        ineligible_regions = [
            "usa",
            "united states",
            "canada",
            "united kingdom",
            "uk only",
            "india",
            "australia",
            "brazil",
        ]
        if contains_term(text, ineligible_regions) and not contains_term(
            text,
            ["germany", "deutschland", "europe", "eu", "emea", "global", "worldwide"],
        ):
            return {
                "work_mode_fit": "mismatch",
                "work_mode_note": "Remote region does not appear to include Germany",
            }
        if in_hamburg_area or contains_term(
            text,
            ["germany", "deutschland", "europe", "eu", "emea", "global", "worldwide"],
        ):
            return {
                "work_mode_fit": "fit",
                "work_mode_note": "Remote eligibility appears compatible with Hamburg",
            }
        return {
            "work_mode_fit": "unclear",
            "work_mode_note": "Remote role, but regional eligibility needs checking",
        }

    return {
        "work_mode_fit": "unclear",
        "work_mode_note": "Hybrid or remote policy is not stated; verify on the job page",
    }


def infer_level(job: dict[str, Any]) -> str:
    current = str(job.get("level", "")).lower()
    if current in LEVEL_FACTORS:
        return current
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    checks = [
        ("student", ["working student", "werkstudent"]),
        ("intern", ["intern ", "internship", "praktikum"]),
        ("staff", ["staff ", "principal"]),
        ("lead", ["lead ", "head of"]),
        ("manager", ["manager"]),
        ("senior", ["senior", "expert"]),
        ("junior", ["junior", "entry level", "graduate"]),
    ]
    for level, terms in checks:
        if any(term in text for term in terms):
            return level
    return "experienced"


def role_family(title: str) -> str:
    title = title.lower()
    if "analytics engineer" in title:
        return "analytics_engineering"
    if "data engineer" in title:
        return "data_engineering"
    if "product" in title and ("analyst" in title or "data" in title):
        return "product_analytics"
    if "data scientist" in title or "scientist" in title:
        return "data_science"
    if "data analyst" in title or "insight" in title:
        return "data_analytics"
    if (
        "business analyst" in title
        or "business intelligence" in title
        or "bi " in title.lower()
    ):
        return "business_analytics"
    return "other"


def title_fit(title: str, profile: dict[str, Any]) -> tuple[float, str]:
    title_low = title.lower()
    best_score = 0.0
    best_title = ""
    title_tokens = set(re.findall(r"[a-z]+", title_low))
    for target in profile["target_roles"]:
        target_low = target["title"].lower()
        target_tokens = set(re.findall(r"[a-z]+", target_low))
        overlap = len(title_tokens & target_tokens) / max(len(target_tokens), 1)
        if target_low in title_low:
            overlap = 1.0
        score = overlap * float(target["weight"])
        if score > best_score:
            best_score = score
            best_title = target["title"]
    return min(best_score, 1.0), best_title


def matched_skills(
    job: dict[str, Any], profile: dict[str, Any]
) -> tuple[list[str], float]:
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    matches: list[tuple[str, int]] = []
    for skill in profile["skills"]:
        if any(keyword in text for keyword in skill["keywords"]):
            matches.append((skill["name"], int(skill["weight"])))
    matches.sort(key=lambda item: (-item[1], item[0]))
    weight = sum(item[1] for item in matches)
    return [item[0] for item in matches], min(weight / 30, 1.0)


def job_gaps(job: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    profile_text = json.dumps(profile).lower()
    gaps = []
    for label, keywords in GAP_KEYWORDS.items():
        if any(keyword in text for keyword in keywords) and not any(
            keyword in profile_text for keyword in keywords
        ):
            gaps.append(label)
    return gaps[:4]


def score_job(job: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    title_score, target_title = title_fit(str(job["title"]), profile)
    skills, skill_score = matched_skills(job, profile)
    level = infer_level(job)
    level_score = {
        "staff": 0.75,
        "lead": 0.8,
        "manager": 0.78,
        "senior": 1.0,
        "experienced": 0.92,
        "mid": 0.9,
        "junior": 0.35,
        "student": 0.05,
        "intern": 0.05,
    }.get(level, 0.7)
    text = f"{job.get('location', '')} {job.get('work_mode', '')}".lower()
    work_mode_fit = str(job.get("work_mode_fit", "unclear"))
    location_score = {"fit": 1.0, "unclear": 0.65, "mismatch": 0.2}.get(
        work_mode_fit, 0.65
    )
    domain_text = f"{job.get('description', '')} {job.get('company', '')}".lower()
    domain_score = (
        1.0
        if any(
            term in domain_text
            for term in ["e-commerce", "ecommerce", "retail", "product"]
        )
        else 0.55
    )
    score = round(
        30 * title_score
        + 40 * skill_score
        + 15 * level_score
        + 10 * location_score
        + 5 * domain_score
    )
    reasons = []
    if target_title:
        reasons.append(f"Role alignment: {target_title}")
    if skills:
        reasons.append("Skill overlap: " + ", ".join(skills[:4]))
    if "hamburg" in text:
        reasons.append("Hamburg location")
    if any(term in domain_text for term in ["e-commerce", "ecommerce", "retail"]):
        reasons.append("Relevant e-commerce / retail domain")
    job["level"] = level
    job["match_score"] = min(score, 99)
    job["matched_skills"] = skills[:7]
    job["fit_reasons"] = reasons[:4]
    job["skill_gaps"] = job_gaps(job, profile)
    return job


def estimate_salary(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("salary_min") and job.get("salary_max"):
        job["salary_mid"] = round((int(job["salary_min"]) + int(job["salary_max"])) / 2)
        job["salary_confidence"] = "high"
        job.setdefault("salary_source", "Employer advertised")
        return job

    family = role_family(str(job["title"]))
    level = infer_level(job)
    base = SALARY_BENCHMARKS[family] * LEVEL_FACTORS[level]
    fit_adjustment = 1 + max(0, int(job.get("match_score", 50)) - 70) * 0.0015
    midpoint = round(base * fit_adjustment / 1000) * 1000
    job.update(
        {
            "salary_min": round(midpoint * 0.90 / 1000) * 1000,
            "salary_mid": midpoint,
            "salary_max": round(midpoint * 1.10 / 1000) * 1000,
            "salary_currency": "EUR",
            "salary_source": "Estimate: BA Entgeltatlas 2024 + role/level/fit adjustment",
            "salary_confidence": "medium" if family != "other" else "low",
        }
    )
    return job


def deduplicate(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def quality(job: dict[str, Any]) -> int:
        source = str(job.get("discovery_source", "")).lower()
        score = len(str(job.get("description", "")))
        if "official career" in source:
            score += 5000
        if str(job.get("salary_source", "")).lower().startswith("employer"):
            score += 10000
        return score

    unique: dict[str, dict[str, Any]] = {}
    for job in jobs:
        title = str(job.get("title", "")).lower()
        title = re.sub(
            r"\((?:(?:[mfwxd]\s*[|/]\s*)+[mfwxd]|all genders)[^)]*\)",
            " ",
            title,
        )
        title = re.sub(
            r"\b(?:onsite|on-site|hybrid|remote)(?:\s+(?:in|or|within))?.*$",
            " ",
            title,
        )
        title = re.sub(r"\W+", " ", title).strip()
        company = re.sub(r"\W+", " ", str(job.get("company", "")).lower()).strip()
        key = f"{company}::{title}"
        if key not in unique:
            unique[key] = dict(job)
            continue
        existing = unique[key]
        winner, other = (
            (dict(job), existing)
            if quality(job) > quality(existing)
            else (existing, job)
        )
        if str(other.get("discovery_source", "")).startswith("Brave Search"):
            winner["freshness"] = str(
                other.get("freshness", winner.get("freshness", ""))
            )
            winner["brave_query_date"] = other.get("brave_query_date")
            winner["confirmed_by"] = "Brave Search API"
        for field in ["work_mode", "location", "date_posted"]:
            if (
                not str(winner.get(field, "")).strip()
                and str(other.get(field, "")).strip()
            ):
                winner[field] = other[field]
        unique[key] = winner
    return list(unique.values())


def stable_id(job: dict[str, Any]) -> str:
    value = f"{job.get('company')}|{job.get('title')}|{job.get('url')}"
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def canonical_company_name(value: str) -> str:
    lowered = value.lower()
    aliases = [
        ("heinemann", "Gebr. Heinemann"),
        ("freenet", "Freenet Group"),
        ("edeka", "EDEKA"),
        ("fielmann", "Fielmann"),
        ("maxingvest", "Maxingvest"),
        ("tchibo", "Tchibo"),
        ("helm", "HELM AG"),
        ("publicis", "Publicis Groupe"),
        ("about you", "ABOUT YOU"),
    ]
    for alias, canonical in aliases:
        if alias in lowered:
            return canonical
    return value


def build_output(
    jobs: list[dict[str, Any]],
    profile: dict[str, Any],
    mode: str,
    errors: list[str],
    discovery: dict[str, Any] | None = None,
    excluded_count: int = 0,
    search_status: str = "",
    experimentation_status: str = "",
    ba_status: str = "",
) -> dict[str, Any]:
    discovery = discovery or read_json(DISCOVERY_PATH)
    canonicalized = []
    for raw in jobs:
        job = dict(raw)
        job["company"] = canonical_company_name(str(job.get("company", "")))
        canonicalized.append(job)
    normalized = []
    for raw in deduplicate(canonicalized):
        job = dict(raw)
        job["id"] = stable_id(job)
        job["description"] = clean_text(str(job.get("description", "")))
        job.update(assess_work_mode_fit(job, discovery))
        job = estimate_salary(score_job(job, profile))
        job["description"] = job["description"][:900]
        normalized.append(job)
    normalized.sort(
        key=lambda item: (-int(item["match_score"]), -int(item["salary_mid"]))
    )
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "meta": {
            "generated_at": now,
            "mode": mode,
            "job_count": len(normalized),
            "location": profile["location"],
            "salary_benchmark": "Bundesagentur für Arbeit Entgeltatlas 2024",
            "salary_benchmark_url": "https://web.arbeitsagentur.de/entgeltatlas/beruf/129987",
            "preference_excluded_count": excluded_count,
            "brave_search_status": search_status,
            "experimentation_jobs_status": experimentation_status,
            "ba_jobs_status": ba_status,
            "errors": errors,
        },
        "jobs": normalized,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--offline", action="store_true", help="Use the dated seed snapshot only"
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=24,
        help="Maximum detail pages fetched per company",
    )
    parser.add_argument(
        "--skip-brave",
        action="store_true",
        help="Skip the Brave daily query while still refreshing official pages",
    )
    parser.add_argument(
        "--initialize-brave",
        action="store_true",
        help="One-time bootstrap of every query rotation; never used by schedule",
    )
    parser.add_argument(
        "--skip-experimentation-jobs",
        action="store_true",
        help="Skip Experimentation Jobs while refreshing other live sources",
    )
    parser.add_argument(
        "--skip-ba",
        action="store_true",
        help="Skip the BA Jobsuche API while refreshing other live sources",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    profile = read_json(PROFILE_PATH)
    sources = read_json(SOURCES_PATH)
    discovery = read_json(DISCOVERY_PATH)
    seed = read_json(SEED_PATH)
    seed_jobs = []
    for raw_job in seed["jobs"]:
        seed_job = dict(raw_job)
        seed_job["freshness"] = f"snapshot {seed['snapshot_date']}"
        seed_jobs.append(seed_job)
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []
    mode = "offline snapshot"
    search_status = "Brave disabled for offline run"
    experimentation_status = "Experimentation Jobs disabled for offline run"
    ba_status = "BA API disabled for offline run"

    if not args.offline:
        fetcher = Fetcher(
            sources["user_agent"], float(sources["request_delay_seconds"])
        )
        for source in sources["sources"]:
            if not source.get("enabled", True):
                continue
            found, source_errors = discover_jobs(source, fetcher, args.max_details)
            jobs.extend(found)
            errors.extend(source_errors)
        experimentation_config = sources.get("experimentation_jobs", {})
        if args.skip_experimentation_jobs or not experimentation_config.get(
            "enabled", False
        ):
            experimentation_status = "Experimentation Jobs skipped"
        else:
            experimentation_found, experimentation_status = search_experimentation_jobs(
                experimentation_config,
                discovery,
                sources["user_agent"],
            )
            jobs.extend(experimentation_found)
        ba_config = sources.get("ba_jobs", {})
        if args.skip_ba or not ba_config.get("enabled", False):
            ba_status = "BA API skipped"
        else:
            ba_found, ba_status = search_ba_jobs(
                ba_config,
                sources["user_agent"],
            )
            jobs.extend(ba_found)
        jobs.extend(
            retain_recent_brave_jobs(args.output, int(discovery["retention_days"]))
        )
        if args.skip_brave:
            search_status = "Brave skipped by command option"
        elif args.initialize_brave:
            brave_jobs, search_status = bootstrap_queries(discovery)
            jobs.extend(brave_jobs)
        else:
            brave_jobs, search_status = search_once_daily(discovery)
            jobs.extend(brave_jobs)
        mode = "live official career pages"

    if args.offline:
        jobs.extend(seed_jobs)
    else:
        live_count = len(jobs)
        jobs.extend(seed_jobs)
        mode = (
            "live official pages + dated snapshot coverage"
            if live_count
            else "live attempt + dated snapshot fallback"
        )

    eligible_jobs = [job for job in jobs if job_matches_preferences(job, discovery)]
    excluded_count = len(jobs) - len(eligible_jobs)
    output = build_output(
        eligible_jobs,
        profile,
        mode,
        errors,
        discovery=discovery,
        excluded_count=excluded_count,
        search_status=search_status,
        experimentation_status=experimentation_status,
        ba_status=ba_status,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(output['jobs'])} jobs to {args.output} ({output['meta']['mode']})."
    )
    if errors:
        print(f"Collector recorded {len(errors)} source warning(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
