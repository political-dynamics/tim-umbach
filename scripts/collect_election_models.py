#!/usr/bin/env python3
"""Build the static Election Lab snapshot from DAWUM's open polling API.

The frontend is deliberately static. This script performs the network request,
reduces DAWUM's database to the elections used by the page, and writes a small
JSON artifact that can be served by GitHub Pages.

The probability model is a portfolio demonstration, not an election forecast:

* poll-only logit: recency/sample weighted polls on the log-odds scale;
* economy probit: the same polling signal plus a small, pre-declared economic
  vote adjustment using real-GDP growth and the change in unemployment;
* coalition probabilities: simulated seat majorities multiplied by transparent
  formation priors. The priors encode current political compatibility and are
  intentionally exposed in the output.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import urllib.request
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

from election_candidates import (
    CACHE_PATH as CANDIDATE_CACHE_PATH,
    candidate_evidence,
    discover_candidates,
    read_cache as read_candidate_cache,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "election_models.json"
DAWUM_API = "https://api.dawum.de/"
TODAY = date.today()

PARTY_ORDER = [
    "CDU/CSU",
    "CDU",
    "CSU",
    "SPD",
    "Grüne",
    "FDP",
    "AfD",
    "Linke",
    "BSW",
    "Freie Wähler",
    "SSW",
    "Sonstige",
]

PARTY_COLORS = {
    "CDU/CSU": "#282828",
    "CDU": "#282828",
    "CSU": "#282828",
    "SPD": "#d64b4b",
    "Grüne": "#62a654",
    "FDP": "#e9bf35",
    "AfD": "#4d91c6",
    "Linke": "#a44d8c",
    "BSW": "#7d365f",
    "Freie Wähler": "#e58a3a",
    "SSW": "#3f74a8",
    "Sonstige": "#aaa19b",
}

# A shared macro signal keeps the state/federal comparison coherent. The
# current values are the German government's 2026 projection published in the
# Jahreswirtschaftsbericht: real GDP +1.0%; BA unemployment 6.2% after 6.3%.
CURRENT_ECONOMY = {
    "gdp_change": 1.0,
    "unemployment_change": -0.1,
    "unemployment_rate": 6.2,
    "period": "2026 government projection",
    "source_note": "Real GDP year-on-year; BA unemployment rate change in percentage points.",
}


TARGETS: dict[str, dict[str, Any]] = {
    "bundestag": {
        "parliament_id": "0",
        "label": "Bundestag",
        "election_label": "Federal election",
        "election_date": "2029",
        "head_title": "Chancellor",
        "seats": 630,
        "incumbent_parties": ["CDU/CSU", "SPD"],
        "candidates": {
            "CDU/CSU": "Friedrich Merz",
            "SPD": "Lars Klingbeil",
            "AfD": "Alice Weidel",
            "Grüne": "Franziska Brantner",
            "Linke": "Heidi Reichinnek",
        },
        "coalitions": [
            {"name": "Black–red", "parties": ["CDU/CSU", "SPD"], "prior": 0.92},
            {"name": "Kenya", "parties": ["CDU/CSU", "SPD", "Grüne"], "prior": 0.68},
            {"name": "Black–green", "parties": ["CDU/CSU", "Grüne"], "prior": 0.55},
            {"name": "Traffic light", "parties": ["SPD", "Grüne", "FDP"], "prior": 0.24},
            {"name": "Red–red–green", "parties": ["SPD", "Linke", "Grüne"], "prior": 0.28},
        ],
    },
    "berlin": {
        "parliament_id": "3",
        "label": "Berlin",
        "election_label": "Abgeordnetenhaus election",
        "election_date": "20 Sep 2026",
        "head_title": "Governing Mayor",
        "seats": 130,
        "incumbent_parties": ["CDU", "SPD"],
        "candidates": {
            "CDU": "Stefan Evers",
            "SPD": "Steffen Krach",
            "Linke": "Tobias Schulze",
            "Grüne": "Bettina Jarasch",
            "AfD": "Kristin Brinker",
        },
        "coalitions": [
            {"name": "Black–red", "parties": ["CDU", "SPD"], "prior": 0.75},
            {"name": "Red–red–green", "parties": ["SPD", "Linke", "Grüne"], "prior": 0.78},
            {"name": "Black–red–green", "parties": ["CDU", "SPD", "Grüne"], "prior": 0.63},
            {"name": "Black–green", "parties": ["CDU", "Grüne"], "prior": 0.43},
            {"name": "Black–green–red", "parties": ["CDU", "Grüne", "Linke"], "prior": 0.16},
        ],
    },
    "mecklenburg-vorpommern": {
        "parliament_id": "8",
        "label": "Mecklenburg-Vorpommern",
        "election_label": "Landtag election",
        "election_date": "20 Sep 2026",
        "head_title": "Minister-President",
        "seats": 79,
        "incumbent_parties": ["SPD", "Linke"],
        "candidates": {
            "SPD": "Manuela Schwesig",
            "CDU": "Daniel Peters",
            "AfD": "Leif-Erik Holm",
            "Linke": "Jeannine Rösler",
        },
        "coalitions": [
            {"name": "Red–red", "parties": ["SPD", "Linke"], "prior": 0.84},
            {"name": "Red–black", "parties": ["SPD", "CDU"], "prior": 0.70},
            {"name": "Red–black–green", "parties": ["SPD", "CDU", "Grüne"], "prior": 0.54},
            {"name": "Red–red–green", "parties": ["SPD", "Linke", "Grüne"], "prior": 0.73},
            {"name": "Black–red–green", "parties": ["CDU", "SPD", "Grüne"], "prior": 0.42},
        ],
    },
    "sachsen-anhalt": {
        "parliament_id": "14",
        "label": "Saxony-Anhalt",
        "election_label": "Landtag election",
        "election_date": "6 Sep 2026",
        "head_title": "Minister-President",
        "seats": 97,
        "incumbent_parties": ["CDU", "SPD", "FDP"],
        "candidates": {
            "CDU": "Sven Schulze",
            "AfD": "Ulrich Siegmund",
            "SPD": "Armin Willingmann",
            "Linke": "Eva von Angern",
        },
        "coalitions": [
            {"name": "Black–red–yellow", "parties": ["CDU", "SPD", "FDP"], "prior": 0.74},
            {"name": "Black–red–green", "parties": ["CDU", "SPD", "Grüne"], "prior": 0.62},
            {"name": "Black–red–left", "parties": ["CDU", "SPD", "Linke"], "prior": 0.34},
            {"name": "Black–left–green", "parties": ["CDU", "Linke", "Grüne"], "prior": 0.18},
            {"name": "Blue minority", "parties": ["AfD"], "prior": 0.22},
        ],
    },
    "nordrhein-westfalen": {
        "parliament_id": "10",
        "label": "North Rhine-Westphalia",
        "election_label": "Landtag election",
        "election_date": "Spring 2027",
        "head_title": "Minister-President",
        "seats": 195,
        "incumbent_parties": ["CDU", "Grüne"],
        "candidates": {
            "CDU": "Hendrik Wüst",
            "SPD": "Jochen Ott",
            "Grüne": "Mona Neubaur",
            "AfD": "Martin Vincentz",
        },
        "coalitions": [
            {"name": "Black–green", "parties": ["CDU", "Grüne"], "prior": 0.90},
            {"name": "Grand coalition", "parties": ["CDU", "SPD"], "prior": 0.72},
            {"name": "Black–red–green", "parties": ["CDU", "SPD", "Grüne"], "prior": 0.48},
            {"name": "Traffic light", "parties": ["SPD", "Grüne", "FDP"], "prior": 0.40},
            {"name": "Red–green–left", "parties": ["SPD", "Grüne", "Linke"], "prior": 0.26},
        ],
    },
    "schleswig-holstein": {
        "parliament_id": "15",
        "label": "Schleswig-Holstein",
        "election_label": "Landtag election",
        "election_date": "2027",
        "head_title": "Minister-President",
        "seats": 69,
        "incumbent_parties": ["CDU", "Grüne"],
        "threshold_exempt": ["SSW"],
        "candidates": {
            "CDU": "Daniel Günther",
            "SPD": "Ulf Kämpfer",
            "Grüne": "Lasse Petersdotter",
        },
        "coalitions": [
            {"name": "Black–green", "parties": ["CDU", "Grüne"], "prior": 0.91},
            {"name": "Black–red", "parties": ["CDU", "SPD"], "prior": 0.67},
            {"name": "Jamaica", "parties": ["CDU", "Grüne", "FDP"], "prior": 0.58},
            {"name": "Black–red–yellow", "parties": ["CDU", "SPD", "FDP"], "prior": 0.43},
            {"name": "Traffic light + SSW", "parties": ["SPD", "Grüne", "FDP", "SSW"], "prior": 0.30},
        ],
    },
    "hamburg": {
        "parliament_id": "6",
        "label": "Hamburg",
        "election_label": "Bürgerschaft election",
        "election_date": "2030",
        "head_title": "First Mayor",
        "seats": 121,
        "incumbent_parties": ["SPD", "Grüne"],
        "candidates": {
            "SPD": "Peter Tschentscher",
            "CDU": "Dennis Thering",
            "Grüne": "Katharina Fegebank",
            "Linke": "Cansu Özdemir",
        },
        "coalitions": [
            {"name": "Red–green", "parties": ["SPD", "Grüne"], "prior": 0.94},
            {"name": "Red–black", "parties": ["SPD", "CDU"], "prior": 0.65},
            {"name": "Red–red–green", "parties": ["SPD", "Linke", "Grüne"], "prior": 0.72},
            {"name": "Black–green–left", "parties": ["CDU", "Grüne", "Linke"], "prior": 0.15},
            {"name": "Red–green–yellow", "parties": ["SPD", "Grüne", "FDP"], "prior": 0.42},
        ],
    },
}


BACKTESTS = [
    {
        "target": "Bundestag 2017",
        "parliament_id": "0",
        "date": "2017-09-24",
        "incumbent": ["CDU/CSU", "SPD"],
        "gdp_change": 3.0,
        "unemployment_change": -0.4,
        "actual": {"CDU/CSU": 32.9, "SPD": 20.5, "AfD": 12.6, "FDP": 10.7, "Linke": 9.2, "Grüne": 8.9},
    },
    {
        "target": "Bundestag 2021",
        "parliament_id": "0",
        "date": "2021-09-26",
        "incumbent": ["CDU/CSU", "SPD"],
        "gdp_change": 3.2,
        "unemployment_change": -0.2,
        "actual": {"SPD": 25.7, "CDU/CSU": 24.1, "Grüne": 14.7, "FDP": 11.4, "AfD": 10.4, "Linke": 4.9},
    },
    {
        "target": "Berlin 2023",
        "parliament_id": "3",
        "date": "2023-02-12",
        "incumbent": ["SPD", "Grüne", "Linke"],
        "gdp_change": -0.3,
        "unemployment_change": 0.4,
        "actual": {"CDU": 28.2, "SPD": 18.4, "Grüne": 18.4, "Linke": 12.2, "AfD": 9.1, "FDP": 4.6},
    },
    {
        "target": "Schleswig-Holstein 2022",
        "parliament_id": "15",
        "date": "2022-05-08",
        "incumbent": ["CDU", "Grüne", "FDP"],
        "gdp_change": 1.8,
        "unemployment_change": -0.7,
        "actual": {"CDU": 43.4, "Grüne": 18.3, "SPD": 16.0, "FDP": 6.4, "SSW": 5.7, "AfD": 4.4},
    },
    {
        "target": "North Rhine-Westphalia 2022",
        "parliament_id": "10",
        "date": "2022-05-15",
        "incumbent": ["CDU", "FDP"],
        "gdp_change": 1.8,
        "unemployment_change": -0.7,
        "actual": {"CDU": 35.7, "SPD": 26.7, "Grüne": 18.2, "FDP": 5.9, "AfD": 5.4, "Linke": 2.1},
    },
    {
        "target": "Mecklenburg-Vorpommern 2021",
        "parliament_id": "8",
        "date": "2021-09-26",
        "incumbent": ["SPD", "CDU"],
        "gdp_change": 3.2,
        "unemployment_change": -0.2,
        "actual": {"SPD": 39.6, "AfD": 16.7, "CDU": 13.3, "Linke": 9.9, "Grüne": 6.3, "FDP": 5.8},
    },
    {
        "target": "Saxony-Anhalt 2021",
        "parliament_id": "14",
        "date": "2021-06-06",
        "incumbent": ["CDU", "SPD", "Grüne"],
        "gdp_change": 3.2,
        "unemployment_change": -0.2,
        "actual": {"CDU": 37.1, "AfD": 20.8, "Linke": 11.0, "SPD": 8.4, "FDP": 6.4, "Grüne": 5.9},
    },
    {
        "target": "Hamburg 2025",
        "parliament_id": "6",
        "date": "2025-03-02",
        "incumbent": ["SPD", "Grüne"],
        "gdp_change": 0.2,
        "unemployment_change": 0.3,
        "actual": {"SPD": 33.5, "CDU": 19.8, "Grüne": 18.5, "Linke": 11.2, "AfD": 7.5, "FDP": 2.3},
    },
    {
        "target": "Bundestag 2025",
        "parliament_id": "0",
        "date": "2025-02-23",
        "incumbent": ["SPD", "Grüne", "FDP"],
        "gdp_change": -0.2,
        "unemployment_change": 0.3,
        "actual": {"CDU/CSU": 28.5, "AfD": 20.8, "SPD": 16.4, "Grüne": 11.6, "Linke": 8.8, "BSW": 5.0, "FDP": 4.3},
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Use a downloaded DAWUM JSON file instead of the live API.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--discover-candidates",
        action="store_true",
        help="Use the shared weekly Brave budget to verify candidate names and attach sources.",
    )
    parser.add_argument(
        "--candidate-cache",
        type=Path,
        default=CANDIDATE_CACHE_PATH,
        help="Candidate-evidence cache used by the static snapshot.",
    )
    return parser.parse_args()


def load_dawum(path: Path | None) -> dict[str, Any]:
    if path:
        return json.loads(path.read_text(encoding="utf-8"))
    request = urllib.request.Request(DAWUM_API, headers={"User-Agent": "Tim-Umbach-Election-Lab/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def party_maps(payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    id_to_party = {str(key): value["Shortcut"] for key, value in payload["Parties"].items()}
    party_to_id = {value: key for key, value in id_to_party.items()}
    return id_to_party, party_to_id


def surveys_for(
    payload: dict[str, Any],
    parliament_id: str,
    as_of: date,
    window_days: int,
    limit: int,
) -> list[dict[str, Any]]:
    surveys = [
        survey
        for survey in payload["Surveys"].values()
        if str(survey["Parliament_ID"]) == parliament_id
        and date.fromisoformat(survey["Date"]) <= as_of
        and (as_of - date.fromisoformat(survey["Date"])).days <= window_days
    ]
    surveys.sort(key=lambda item: item["Date"], reverse=True)
    return surveys[:limit]


def weighted_trend(
    surveys: list[dict[str, Any]],
    id_to_party: dict[str, str],
    as_of: date,
) -> tuple[dict[str, float], dict[str, float]]:
    totals: defaultdict[str, float] = defaultdict(float)
    weights: defaultdict[str, float] = defaultdict(float)
    observations: defaultdict[str, list[tuple[float, float]]] = defaultdict(list)
    for survey in surveys:
        age = (as_of - date.fromisoformat(survey["Date"])).days
        sample = max(int(survey.get("Surveyed_Persons") or 1000), 250)
        weight = math.exp(-age / 28) * math.sqrt(sample / 1000)
        for party_id, result in survey["Results"].items():
            party = id_to_party.get(str(party_id), f"Party {party_id}")
            value = float(result)
            totals[party] += value * weight
            weights[party] += weight
            observations[party].append((value, weight))
    trend = {party: totals[party] / weights[party] for party in totals if weights[party]}
    uncertainty: dict[str, float] = {}
    for party, values in observations.items():
        mean = trend[party]
        total_weight = sum(weight for _, weight in values)
        variance = sum(weight * (value - mean) ** 2 for value, weight in values) / max(total_weight, 1)
        uncertainty[party] = max(1.15, min(3.2, math.sqrt(variance + 1.0)))
    return trend, uncertainty


def economic_adjustment(
    shares: dict[str, float],
    incumbent_parties: list[str],
    gdp_change: float,
    unemployment_change: float,
) -> dict[str, float]:
    """Apply a deliberately small economic-vote correction in percentage points."""
    result = dict(shares)
    incumbent = [party for party in incumbent_parties if party in result]
    opposition = [party for party in result if party not in incumbent and party != "Sonstige"]
    if not incumbent or not opposition:
        return result
    # Better growth and a falling unemployment rate help governing parties.
    total_shift = max(-1.8, min(1.8, 0.32 * gdp_change - 0.55 * unemployment_change))
    incumbent_total = sum(max(result[party], 0.1) for party in incumbent)
    opposition_total = sum(max(result[party], 0.1) for party in opposition)
    for party in incumbent:
        result[party] += total_shift * result[party] / incumbent_total
    for party in opposition:
        result[party] -= total_shift * result[party] / opposition_total
        result[party] = max(0.1, result[party])
    scale = sum(shares.values()) / sum(result.values())
    return {party: value * scale for party, value in result.items()}


def seat_share(draw: dict[str, float], threshold_exempt: list[str]) -> dict[str, float]:
    eligible = {
        party: value
        for party, value in draw.items()
        if party != "Sonstige" and (value >= 5.0 or party in threshold_exempt)
    }
    total = sum(eligible.values()) or 1.0
    return {party: value / total for party, value in eligible.items()}


def simulate_model(
    means: dict[str, float],
    uncertainty: dict[str, float],
    target: dict[str, Any],
    link: str,
    seed: int,
    draws_count: int = 5000,
) -> dict[str, Any]:
    rng = random.Random(seed)
    parties = sorted(means, key=lambda party: PARTY_ORDER.index(party) if party in PARTY_ORDER else 99)
    threshold_exempt = target.get("threshold_exempt", [])
    draws: list[dict[str, float]] = []
    seat_draws: list[dict[str, float]] = []
    for _ in range(draws_count):
        sampled: dict[str, float] = {}
        common_shock = rng.gauss(0, 0.45)
        for party in parties:
            mean = max(0.2, min(70.0, means[party]))
            sd = uncertainty.get(party, 1.5)
            if link == "logit":
                latent = math.log(mean / (100 - mean))
                shock = rng.gauss(0, sd / max(mean * (1 - mean / 100), 2.0))
                sampled[party] = 100 / (1 + math.exp(-(latent + shock + common_shock / 30)))
            else:
                sampled[party] = max(0.05, mean + rng.gauss(0, sd) + common_shock)
        scale = 100 / sum(sampled.values())
        sampled = {party: value * scale for party, value in sampled.items()}
        draws.append(sampled)
        seat_draws.append(seat_share(sampled, threshold_exempt))

    party_rows = []
    mean_seat_share: dict[str, float] = {}
    for party in parties:
        values = sorted(draw[party] for draw in draws)
        seats = [seat.get(party, 0) for seat in seat_draws]
        avg_seat = statistics.fmean(seats)
        mean_seat_share[party] = avg_seat
        party_rows.append(
            {
                "party": party,
                "color": PARTY_COLORS.get(party, "#aaa19b"),
                "vote": round(statistics.fmean(values), 1),
                "low": round(values[int(draws_count * 0.1)], 1),
                "high": round(values[int(draws_count * 0.9)], 1),
                "seats": round(avg_seat * target["seats"]),
                "threshold_probability": round(sum(value >= 5 for value in values) / draws_count * 100),
            }
        )
    party_rows.sort(key=lambda item: item["vote"], reverse=True)

    coalition_rows = []
    raw_formation = []
    for coalition in target["coalitions"]:
        support = [
            sum(seat.get(party, 0) for party in coalition["parties"])
            for seat in seat_draws
        ]
        majority_probability = sum(value > 0.5 for value in support) / draws_count
        average_support = statistics.fmean(support)
        # A majority is necessary for regular coalitions. Minority options keep
        # a small pathway through their declared prior.
        if "minority" in coalition["name"].lower():
            viability = max(0.05, sum(value >= 0.43 for value in support) / draws_count)
        else:
            viability = majority_probability
        formation_weight = viability * coalition["prior"] * (0.90 ** max(0, len(coalition["parties"]) - 2))
        raw_formation.append(formation_weight)
        coalition_rows.append(
            {
                "name": coalition["name"],
                "parties": coalition["parties"],
                "majority_probability": round(majority_probability * 100),
                "seat_share": round(average_support * 100, 1),
                "formation_prior": round(coalition["prior"] * 100),
            }
        )
    unresolved_weight = 0.32
    total_weight = sum(raw_formation) + unresolved_weight
    for row, weight in zip(coalition_rows, raw_formation):
        row["formation_probability"] = round(weight / total_weight * 100)
        row["party_colors"] = [PARTY_COLORS.get(party, "#aaa19b") for party in row["parties"]]
    coalition_rows.sort(key=lambda item: item["formation_probability"], reverse=True)

    candidate_probabilities: defaultdict[str, float] = defaultdict(float)
    candidate_parties: dict[str, str] = {}
    for coalition in coalition_rows:
        eligible = [party for party in coalition["parties"] if party in target["candidates"]]
        if not eligible:
            continue
        lead_party = max(eligible, key=lambda party: mean_seat_share.get(party, 0))
        candidate = target["candidates"][lead_party]
        candidate_probabilities[candidate] += coalition["formation_probability"]
        candidate_parties[candidate] = lead_party
    candidate_rows = [
        {
            "name": candidate,
            "party": candidate_parties[candidate],
            "probability": round(probability),
            "color": PARTY_COLORS.get(candidate_parties[candidate], "#aaa19b"),
            "source_status": target.get("candidate_evidence", {}).get(candidate_parties[candidate], {}).get("status", "curated"),
            "source_url": target.get("candidate_evidence", {}).get(candidate_parties[candidate], {}).get("source_url", ""),
            "source_title": target.get("candidate_evidence", {}).get(candidate_parties[candidate], {}).get("source_title", ""),
            "source_checked_at": target.get("candidate_evidence", {}).get(candidate_parties[candidate], {}).get("checked_at", ""),
        }
        for candidate, probability in candidate_probabilities.items()
    ]
    candidate_rows.sort(key=lambda item: item["probability"], reverse=True)
    if not candidate_rows:
        leading = party_rows[0]["party"]
        candidate_rows = [
            {
                "name": target["candidates"].get(leading, "Unresolved"),
                "party": leading,
                "probability": 0,
                "color": PARTY_COLORS.get(leading, "#aaa19b"),
                "source_status": target.get("candidate_evidence", {}).get(leading, {}).get("status", "curated"),
                "source_url": target.get("candidate_evidence", {}).get(leading, {}).get("source_url", ""),
                "source_title": target.get("candidate_evidence", {}).get(leading, {}).get("source_title", ""),
                "source_checked_at": target.get("candidate_evidence", {}).get(leading, {}).get("checked_at", ""),
            }
        ]

    return {
        "parties": party_rows,
        "coalitions": coalition_rows,
        "candidates": candidate_rows,
        "unresolved_probability": max(0, 100 - sum(row["formation_probability"] for row in coalition_rows)),
    }


def build_backtests(payload: dict[str, Any], id_to_party: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for item in BACKTESTS:
        election_day = date.fromisoformat(item["date"])
        surveys = surveys_for(payload, item["parliament_id"], election_day, window_days=40, limit=10)
        if not surveys:
            continue
        trend, _ = weighted_trend(surveys, id_to_party, election_day)
        enriched = economic_adjustment(
            trend,
            item["incumbent"],
            item["gdp_change"],
            item["unemployment_change"],
        )
        common = [party for party in item["actual"] if party in trend]
        if not common:
            continue
        logit_mae = statistics.fmean(abs(trend[party] - item["actual"][party]) for party in common)
        probit_mae = statistics.fmean(abs(enriched[party] - item["actual"][party]) for party in common)
        actual_winner = max(item["actual"], key=item["actual"].get)
        poll_winner = max(common, key=trend.get)
        economy_winner = max(common, key=enriched.get)
        rows.append(
            {
                "target": item["target"],
                "date": item["date"],
                "poll_count": len(surveys),
                "logit_mae": round(logit_mae, 2),
                "probit_mae": round(probit_mae, 2),
                "actual_winner": actual_winner,
                "logit_winner_correct": poll_winner == actual_winner,
                "probit_winner_correct": economy_winner == actual_winner,
            }
        )
    return rows


def build_snapshot(
    payload: dict[str, Any],
    candidate_cache: dict[str, Any] | None = None,
    candidate_search_status: str = "Candidate evidence cache loaded",
) -> dict[str, Any]:
    id_to_party, _ = party_maps(payload)
    database_update = payload["Database"]["Last_Update"]
    snapshot_day = datetime.fromisoformat(database_update).date()
    elections: dict[str, Any] = {}
    candidate_cache = candidate_cache or {"meta": {}, "elections": {}}
    for index, (slug, base_target) in enumerate(TARGETS.items()):
        target = deepcopy(base_target)
        target["candidate_evidence"] = candidate_evidence(
            candidate_cache,
            slug,
            target["candidates"],
        )
        surveys = surveys_for(payload, target["parliament_id"], snapshot_day, window_days=120, limit=12)
        if not surveys:
            continue
        trend, uncertainty = weighted_trend(surveys, id_to_party, snapshot_day)
        economic_trend = economic_adjustment(
            trend,
            target["incumbent_parties"],
            CURRENT_ECONOMY["gdp_change"],
            CURRENT_ECONOMY["unemployment_change"],
        )
        elections[slug] = {
            "slug": slug,
            "label": target["label"],
            "election_label": target["election_label"],
            "election_date": target["election_date"],
            "head_title": target["head_title"],
            "seat_count": target["seats"],
            "poll_count": len(surveys),
            "latest_poll": max(survey["Date"] for survey in surveys),
            "poll_only": simulate_model(trend, uncertainty, target, "logit", seed=4100 + index),
            "economy": simulate_model(economic_trend, uncertainty, target, "probit", seed=9100 + index),
        }

    backtests = build_backtests(payload, id_to_party)
    return {
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "dawum_updated_at": database_update,
            "method_version": "1.0",
            "license": payload["Database"]["License"],
            "economy": CURRENT_ECONOMY,
            "candidate_search": {
                **candidate_cache.get("meta", {}),
                "message": candidate_search_status,
            },
            "sources": [
                {"label": "DAWUM open polling API", "url": "https://dawum.de/API/"},
                {
                    "label": "Brave Search API candidate evidence",
                    "url": "https://brave.com/search/api/",
                },
                {
                    "label": "German government 2026 economic projection",
                    "url": "https://www.bundeswirtschaftsministerium.de/Redaktion/DE/Artikel/Wirtschaft/Projektionen-der-Bundesregierung/projektionen-der-bundesregierung-jahresprojektion-2026.html",
                },
                {
                    "label": "Destatis / BA labour-market indicator",
                    "url": "https://www.destatis.de/DE/Themen/Wirtschaft/Konjunkturindikatoren/Arbeitsmarkt/arb210a.html",
                },
                {
                    "label": "Official federal election results",
                    "url": "https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse/bund-99.html",
                },
            ],
        },
        "elections": elections,
        "backtests": backtests,
        "backtest_summary": {
            "logit_mae": round(statistics.fmean(row["logit_mae"] for row in backtests), 2),
            "probit_mae": round(statistics.fmean(row["probit_mae"] for row in backtests), 2),
            "logit_winners": sum(row["logit_winner_correct"] for row in backtests),
            "probit_winners": sum(row["probit_winner_correct"] for row in backtests),
            "count": len(backtests),
        },
    }


def main() -> None:
    args = parse_args()
    if args.discover_candidates:
        candidate_cache, candidate_status = discover_candidates(
            TARGETS,
            cache_path=args.candidate_cache,
        )
    else:
        candidate_cache = read_candidate_cache(args.candidate_cache)
        candidate_status = "Brave candidate search not requested; cached evidence used"
    payload = load_dawum(args.input)
    snapshot = build_snapshot(payload, candidate_cache, candidate_status)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} with {len(snapshot['elections'])} election models.")


if __name__ == "__main__":
    main()
