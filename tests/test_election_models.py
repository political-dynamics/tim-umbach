import json
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import election_candidates  # noqa: E402


class ElectionModelSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads((ROOT / "data" / "election_models.json").read_text(encoding="utf-8"))

    def test_contains_federal_and_state_models(self):
        elections = self.payload["elections"]
        self.assertIn("bundestag", elections)
        self.assertIn("berlin", elections)
        self.assertGreaterEqual(len(elections), 7)

    def test_model_outputs_are_bounded_and_complete(self):
        for election in self.payload["elections"].values():
            for model_name in ("poll_only", "economy"):
                with self.subTest(election=election["slug"], model=model_name):
                    model = election[model_name]
                    self.assertTrue(model["parties"])
                    self.assertTrue(model["coalitions"])
                    self.assertTrue(model["candidates"])
                    self.assertIn("source_status", model["candidates"][0])
                    self.assertAlmostEqual(sum(row["vote"] for row in model["parties"]), 100, delta=0.8)
                    for coalition in model["coalitions"]:
                        self.assertGreaterEqual(coalition["formation_probability"], 0)
                        self.assertLessEqual(coalition["formation_probability"], 100)
                        self.assertGreaterEqual(coalition["majority_probability"], 0)
                        self.assertLessEqual(coalition["majority_probability"], 100)

    def test_backtests_report_both_specifications(self):
        summary = self.payload["backtest_summary"]
        self.assertGreaterEqual(summary["count"], 8)
        self.assertGreater(summary["logit_mae"], 0)
        self.assertGreater(summary["probit_mae"], 0)
        self.assertEqual(summary["count"], len(self.payload["backtests"]))

    def test_brave_result_verifies_only_known_candidate_name(self):
        payload = {
            "web": {
                "results": [
                    {
                        "title": "Steffen Krach ist SPD-Spitzenkandidat für Berlin",
                        "description": "Kandidat für das Amt des Regierenden Bürgermeisters",
                        "url": "https://example.org/krach",
                    },
                    {
                        "title": "An unrelated candidate",
                        "description": "No match here",
                        "url": "https://example.org/unrelated",
                    },
                ]
            }
        }
        evidence = election_candidates.verify_candidates(
            payload,
            {"SPD": "Steffen Krach", "CDU": "Stefan Evers"},
            "2026-07-27T10:00:00+00:00",
        )
        self.assertEqual(evidence["SPD"]["status"], "brave_verified")
        self.assertEqual(evidence["SPD"]["source_url"], "https://example.org/krach")
        self.assertNotIn("CDU", evidence)

    def test_candidate_search_respects_shared_daily_budget(self):
        targets = {
            "one": {
                "label": "One",
                "election_label": "Election one",
                "election_date": "2027",
                "candidates": {"SPD": "Example One"},
            },
            "two": {
                "label": "Two",
                "election_label": "Election two",
                "election_date": "2027",
                "candidates": {"CDU": "Example Two"},
            },
            "three": {
                "label": "Three",
                "election_label": "Election three",
                "election_date": "2027",
                "candidates": {"Grüne": "Example Three"},
            },
        }
        response = {
            "web": {
                "results": [
                    {
                        "title": "Example One SPD Kandidat Example Two CDU Kandidat Example Three Grüne Kandidat",
                        "description": "Spitzenkandidaten",
                        "url": "https://example.org/candidates",
                    }
                ]
            }
        }
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "candidates.json"
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "request_date_utc": "2026-07-27",
                        "request_count": 8,
                        "daily_attempted_queries": ["job:1", "job:2"],
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(election_candidates, "read_api_key", return_value="test-key"),
                patch.object(election_candidates, "request_search", return_value=response),
            ):
                cache, _ = election_candidates.discover_candidates(
                    targets,
                    cache_path=cache_path,
                    state_path=state_path,
                    target_date=date(2026, 7, 27),
                )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["request_count"], 10)
            self.assertEqual(cache["meta"]["requests_used"], 2)
            self.assertEqual(len(cache["elections"]), 2)


if __name__ == "__main__":
    unittest.main()
