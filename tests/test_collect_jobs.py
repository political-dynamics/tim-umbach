import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import sys


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import collect_jobs  # noqa: E402
import ba_jobs  # noqa: E402
import brave_search  # noqa: E402
import experimentation_jobs  # noqa: E402


class CollectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = collect_jobs.read_json(collect_jobs.PROFILE_PATH)
        cls.discovery = collect_jobs.read_json(collect_jobs.DISCOVERY_PATH)

    def test_parses_advertised_salary(self):
        self.assertEqual(
            collect_jobs.parse_advertised_salary("Salary range 70k - 85k €"),
            (70000, 85000),
        )

    def test_strong_profile_role_scores_higher_than_student_role(self):
        strong = {
            "title": "Senior Product Data Scientist",
            "company": "Example",
            "location": "Hamburg",
            "work_mode": "Hybrid",
            "description": "Experimentation, causal inference, SQL, BigQuery, Python, statistics, product metrics and stakeholder communication in e-commerce.",
        }
        weak = {
            "title": "Working Student Marketing",
            "company": "Example",
            "location": "Hamburg",
            "work_mode": "On-site",
            "description": "Support social media content and event planning.",
        }
        self.assertGreater(
            collect_jobs.score_job(strong, self.profile)["match_score"],
            collect_jobs.score_job(weak, self.profile)["match_score"],
        )

    def test_advertised_salary_is_not_overwritten(self):
        job = {
            "title": "Data Analyst",
            "salary_min": 60000,
            "salary_max": 70000,
            "salary_source": "Employer advertised",
        }
        result = collect_jobs.estimate_salary(job)
        self.assertEqual(result["salary_mid"], 65000)
        self.assertEqual(result["salary_confidence"], "high")

    def test_multilingual_duplicates_are_collapsed(self):
        jobs = [
            {
                "title": "Senior Data Scientist — Search Engine",
                "company": "ABOUT YOU",
                "url": "https://example.com/en/job",
                "description": "Longer canonical description",
                "discovery_source": "Official career page",
            },
            {
                "title": "Senior Data Scientist (m/w/d) - Search Engine - onsite in Hamburg or Berlin",
                "company": "ABOUT YOU",
                "url": "https://example.com/de/job",
                "description": "Duplicate",
                "discovery_source": "Brave Search API — one daily query",
                "freshness": "Brave query 2026-07-20",
            },
        ]
        result = collect_jobs.deduplicate(jobs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://example.com/en/job")
        self.assertEqual(result[0]["freshness"], "Brave query 2026-07-20")

    def test_duplicate_merge_preserves_work_mode_evidence(self):
        jobs = [
            {
                "title": "Data Analyst",
                "company": "Example",
                "url": "https://example.com/job",
                "description": "Long live description " * 20,
                "work_mode": "",
                "discovery_source": "Official career page",
            },
            {
                "title": "Data Analyst",
                "company": "Example",
                "url": "https://example.com/job",
                "description": "Snapshot",
                "work_mode": "Hybrid",
                "discovery_source": "Official career page",
            },
        ]
        self.assertEqual(collect_jobs.deduplicate(jobs)[0]["work_mode"], "Hybrid")

    def test_shared_tchibo_board_uses_company_alias_before_domain(self):
        company = brave_search.infer_company(
            "Corporate Development Manager — Maxingvest",
            "Maxingvest role",
            "https://www.tchibo-karriere.de/jobs/123",
            self.discovery,
        )
        self.assertEqual(company, "Maxingvest")

    def test_offline_output_has_unique_ids_and_required_fields(self):
        seed = collect_jobs.read_json(collect_jobs.SEED_PATH)
        output = collect_jobs.build_output(seed["jobs"], self.profile, "test", [])
        ids = [job["id"] for job in output["jobs"]]
        self.assertEqual(len(ids), len(set(ids)))
        for job in output["jobs"]:
            for field in [
                "title",
                "company",
                "url",
                "match_score",
                "salary_min",
                "salary_mid",
                "salary_max",
                "work_mode_fit",
                "work_mode_note",
            ]:
                self.assertIn(field, job)

    def test_preferences_retain_on_site_roles_but_block_industries(self):
        base = {
            "title": "Senior Data Scientist",
            "company": "Airbus Commercial",
            "description": "Product analytics for commercial aircraft",
            "location": "Hamburg",
            "url": "https://example.com/job",
        }
        self.assertTrue(
            collect_jobs.job_matches_preferences(
                {**base, "work_mode": "Hybrid"}, self.discovery
            )
        )
        self.assertTrue(
            collect_jobs.job_matches_preferences(
                {**base, "work_mode": "On-site"}, self.discovery
            )
        )
        self.assertFalse(
            collect_jobs.job_matches_preferences(
                {
                    **base,
                    "company": "Airbus Defence",
                    "work_mode": "Hybrid",
                },
                self.discovery,
            )
        )
        self.assertFalse(
            collect_jobs.job_matches_preferences(
                {
                    **base,
                    "company": "Bauer Media Group",
                    "work_mode": "Hybrid",
                },
                self.discovery,
            )
        )

    def test_preferences_exclude_engineer_job_titles(self):
        base = {
            "company": "Example",
            "description": "Product analytics with an engineering team",
            "location": "Hamburg",
            "url": "https://example.com/job",
        }
        self.assertFalse(
            collect_jobs.job_matches_preferences(
                {**base, "title": "Senior Analytics Engineer"}, self.discovery
            )
        )
        self.assertTrue(
            collect_jobs.job_matches_preferences(
                {**base, "title": "Senior Product Analyst"}, self.discovery
            )
        )

    def test_preferences_require_relevant_scientist_title_qualifier(self):
        base = {
            "company": "Example",
            "description": "Research role using Python and stakeholder management",
            "location": "Hamburg",
            "url": "https://example.com/job",
        }
        self.assertFalse(
            collect_jobs.job_matches_preferences(
                {**base, "title": "Research Scientist Skin Microbiome"},
                self.discovery,
            )
        )
        for title in [
            "Senior Data Scientist",
            "Experimentation Scientist",
            "Research Scientist — Causal Inference",
        ]:
            with self.subTest(title=title):
                self.assertTrue(
                    collect_jobs.job_matches_preferences(
                        {**base, "title": title}, self.discovery
                    )
                )

    def test_work_mode_fit_is_labelled_without_filtering(self):
        base = {
            "title": "Senior Data Scientist",
            "company": "Example",
            "description": "Product analytics",
            "location": "Hamburg",
        }
        hybrid = collect_jobs.assess_work_mode_fit(
            {**base, "work_mode": "Hybrid"}, self.discovery
        )
        unknown = collect_jobs.assess_work_mode_fit(
            {**base, "work_mode": ""}, self.discovery
        )
        onsite = collect_jobs.assess_work_mode_fit(
            {**base, "work_mode": "On-site"}, self.discovery
        )
        self.assertEqual(hybrid["work_mode_fit"], "fit")
        self.assertEqual(unknown["work_mode_fit"], "unclear")
        self.assertEqual(onsite["work_mode_fit"], "mismatch")

    def test_page_parser_extracts_javascript_location_links(self):
        parser = collect_jobs.PageParser()
        parser.feed(
            "<tr onclick=\"window.location.href='/?page=home&amp;action=view&amp;id=1'\">"
        )
        self.assertEqual(parser.links, ["/?page=home&action=view&id=1"])

    def test_page_parser_ignores_css_and_regular_scripts(self):
        parser = collect_jobs.PageParser()
        parser.feed(
            "<style>.job { color: red }</style><script>tracking()</script>"
            "<h1>Data Analyst</h1><p>SQL and Python</p>"
        )
        self.assertEqual(parser.page_text, "Data Analyst SQL and Python")

    def test_description_starts_at_second_repeated_role_heading(self):
        page = "Data Analyst Navigation Data Analyst SQL Python experimentation"
        self.assertEqual(
            collect_jobs.focused_page_description(page, "Data Analyst"),
            "Data Analyst SQL Python experimentation",
        )

    def test_brave_daily_guard_skips_after_budget_is_used(self):
        today = date(2026, 7, 20)
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            brave_search.write_state(
                {
                    "request_date_utc": today.isoformat(),
                    "request_count": self.discovery["daily_request_limit"],
                    "daily_attempted_queries": self.discovery["daily_queries"],
                },
                state_path,
            )
            jobs, status = brave_search.search_once_daily(
                self.discovery,
                state_path=state_path,
                target_date=today,
            )
        self.assertEqual(jobs, [])
        self.assertIn("budget already used", status)

    def test_brave_uses_only_remaining_daily_budget(self):
        today = date(2026, 7, 20)
        queries = self.discovery["daily_queries"]
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            brave_search.write_state(
                {
                    "request_date_utc": today.isoformat(),
                    "request_count": 1,
                    "query": queries[0],
                    "bootstrap_completed_at": "2026-07-20T12:00:00+00:00",
                    "bootstrap_request_count": 6,
                    "bootstrap_attempted_queries": queries[1:7],
                },
                state_path,
            )
            with (
                patch.object(brave_search, "read_api_key", return_value="test"),
                patch.object(brave_search, "request_query", return_value=[]) as request,
            ):
                brave_search.search_once_daily(
                    self.discovery,
                    state_path=state_path,
                    target_date=today,
                )
            state = brave_search.read_state(state_path)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(state["request_count"], 4)

    def test_brave_bootstrap_cannot_run_twice(self):
        today = date(2026, 7, 20)
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            brave_search.write_state(
                {"bootstrap_completed_at": "2026-07-20T12:00:00+00:00"},
                state_path,
            )
            jobs, status = brave_search.bootstrap_queries(
                self.discovery,
                state_path=state_path,
                target_date=today,
            )
        self.assertEqual(jobs, [])
        self.assertIn("already completed", status)

    def test_experimentation_jobs_parser_and_region_filter(self):
        payload = json.dumps(
            {
                "html": """
                <li class="job_listing job-type-remote">
                  <a href="https://experimentationjobs.com/job/example/">
                    <div class="position"><h3>Senior Product Data Analyst</h3>
                    <div class="company"><strong>Example GmbH</strong></div></div>
                    <div class="location">Germany (Remote)</div>
                    <ul class="meta"><li class="job-type remote">Remote</li>
                    <li class="date"><time datetime="2026-07-20">Today</time></li></ul>
                  </a>
                </li>
                """
            }
        )
        listings = experimentation_jobs.parse_listing_payload(payload)
        config = collect_jobs.read_json(collect_jobs.SOURCES_PATH)[
            "experimentation_jobs"
        ]
        jobs = experimentation_jobs.normalize_listings(
            listings, config, self.discovery, "2026-07-20"
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Example GmbH")
        self.assertEqual(jobs[0]["work_mode"], "Remote")

    def test_experimentation_jobs_retains_region_locked_remote_for_labelling(self):
        config = collect_jobs.read_json(collect_jobs.SOURCES_PATH)[
            "experimentation_jobs"
        ]
        listing = {
            "title": "Senior Data Analyst",
            "company": "Example",
            "location": "USA (Remote)",
            "work_mode": "Remote",
            "url": "https://experimentationjobs.com/job/example/",
            "date_posted": "2026-07-20",
        }
        jobs = experimentation_jobs.normalize_listings(
            [listing], config, self.discovery, "2026-07-20"
        )
        self.assertEqual(len(jobs), 1)
        listing["location"] = "Remote"
        jobs = experimentation_jobs.normalize_listings(
            [listing], config, self.discovery, "2026-07-20"
        )
        self.assertEqual(len(jobs), 1)

    def test_ba_detail_normalization_requires_home_work_signal(self):
        detail = {
            "stellenangebotsTitel": "Senior Data Analyst",
            "arbeitgeber": "Example GmbH",
            "arbeitsorte": [{"ort": "Hamburg", "land": "Deutschland"}],
            "arbeitszeitmodelle": ["VOLLZEIT", "HEIM_TELEARBEIT"],
            "stellenangebotsBeschreibung": "SQL und Experimente",
            "referenznummer": "10000-123-S",
        }
        job = ba_jobs.normalize_detail(detail, {}, "2026-07-20")
        self.assertEqual(job["work_mode"], "Hybrid")
        self.assertIn("Hamburg", job["location"])
        self.assertIn("10000-123-S", job["url"])


if __name__ == "__main__":
    unittest.main()
