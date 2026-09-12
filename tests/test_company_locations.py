import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import sys


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import brave_search  # noqa: E402
import collect_company_locations  # noqa: E402
import collect_jobs  # noqa: E402


class CompanyLocationTest(unittest.TestCase):
    def test_extracts_hamburg_address_without_snippet_prefix(self):
        text = (
            "Office address Nect GmbH Großer Burstah 21 20457 Hamburg Germany"
        )
        self.assertEqual(
            collect_company_locations.extract_hamburg_address(text),
            "Großer Burstah 21, 20457 Hamburg, Germany",
        )

    def test_selects_only_hamburg_result_inside_configured_bounds(self):
        results = [
            {"lat": "52.52", "lon": "13.40", "display_name": "Berlin"},
            {
                "lat": "53.55",
                "lon": "9.99",
                "display_name": "Office, Hamburg, Deutschland",
            },
        ]
        result = collect_company_locations.select_hamburg_result(
            results, [9.65, 53.75, 10.35, 53.35]
        )
        self.assertEqual(result["display_name"], "Office, Hamburg, Deutschland")

    def test_verified_employer_office_replaces_city_jitter(self):
        office = {
            "Example": {
                "address": "Domstraße 10, 20095 Hamburg, Germany",
                "latitude": 53.55,
                "longitude": 10.0,
                "source_url": "https://example.com/legal",
                "osm_url": "https://www.openstreetmap.org/way/1",
            }
        }
        mapped = collect_jobs.map_position(
            {"company": "Example", "title": "Data Scientist", "location": "Hamburg"},
            office,
        )
        self.assertEqual(mapped["precision"], "verified employer office")
        self.assertEqual(mapped["address"], office["Example"]["address"])
        self.assertEqual(mapped["latitude"], 53.55)

    def test_unchanged_geocode_is_reused_without_network(self):
        config = {
            "user_agent": "test",
            "nominatim": {
                "policy_url": "https://example.com/policy",
                "minimum_interval_seconds": 1.1,
                "viewbox": [9.65, 53.75, 10.35, 53.35],
            },
            "brave": {"max_queries_per_run": 1, "retry_days": 30},
            "companies": [
                {
                    "company": "Example",
                    "address": "Domstraße 10, 20095 Hamburg, Germany",
                    "source_url": "https://example.com/legal",
                }
            ],
        }
        previous = {
            "locations": {
                "Example": {
                    "address": "Domstraße 10, 20095 Hamburg, Germany",
                    "latitude": 53.55,
                    "longitude": 10.0,
                    "source_url": "https://example.com/old",
                    "source_type": "Official company page",
                }
            }
        }
        with patch.object(
            collect_company_locations, "geocode_address"
        ) as geocode:
            result = collect_company_locations.collect_locations(
                config,
                {"jobs": [{"company": "Example", "map": {"latitude": 53.55}}]},
                previous,
                discover_with_brave=False,
                offline=False,
                target_date=date(2026, 9, 12),
            )
        geocode.assert_not_called()
        self.assertEqual(result["meta"]["location_count"], 1)
        self.assertEqual(
            result["locations"]["Example"]["source_url"],
            "https://example.com/legal",
        )

    def test_address_discovery_cannot_exceed_shared_brave_budget(self):
        discovery = {"daily_request_limit": 10}
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            brave_search.write_state(
                {"request_date_utc": "2026-09-12", "request_count": 10},
                state_path,
            )
            reserved, used, budget = (
                collect_company_locations.reserve_brave_request(
                    "address query",
                    discovery,
                    date(2026, 9, 12),
                    state_path,
                )
            )
        self.assertFalse(reserved)
        self.assertEqual((used, budget), (10, 10))


if __name__ == "__main__":
    unittest.main()
