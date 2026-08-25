import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import engine
from advanced_market import (
    extract_1x2,
    extract_main_asian_handicap,
    extract_main_total,
    no_vig_1x2,
    outcome_analysis,
    plain_english_summary,
)


SAMPLE_EVENT = {
    "home": "Crystal Palace",
    "away": "Manchester City",
    "startTime": "2026-08-28T19:00:00Z",
    "markets": [
        {
            "canonicalMarket": "MATCH_RESULT",
            "period": "FULL_TIME",
            "isActive": True,
            "selections": [
                {"canonicalOutcome": "HOME", "name": "Crystal Palace", "decimal": 5.30},
                {"canonicalOutcome": "DRAW", "name": "Draw", "decimal": 4.10},
                {"canonicalOutcome": "AWAY", "name": "Manchester City", "decimal": 1.61},
            ],
        },
        {
            "canonicalMarket": "ASIAN_HANDICAP",
            "period": "FULL_TIME",
            "line": 1.0,
            "isActive": True,
            "selections": [
                {"canonicalOutcome": "HOME", "name": "Crystal Palace +1", "decimal": 1.96, "line": 1.0},
                {"canonicalOutcome": "AWAY", "name": "Manchester City -1", "decimal": 1.94, "line": -1.0},
            ],
        },
        {
            "canonicalMarket": "ASIAN_HANDICAP",
            "period": "FULL_TIME",
            "line": 0.75,
            "isActive": True,
            "selections": [
                {"canonicalOutcome": "HOME", "name": "Crystal Palace +0.75", "decimal": 1.74, "line": 0.75},
                {"canonicalOutcome": "AWAY", "name": "Manchester City -0.75", "decimal": 2.20, "line": -0.75},
            ],
        },
        {
            "canonicalMarket": "OVER_UNDER",
            "period": "FULL_TIME",
            "line": 3.0,
            "isActive": True,
            "selections": [
                {"canonicalOutcome": "OVER", "name": "Over 3.0", "decimal": 1.93, "line": 3.0},
                {"canonicalOutcome": "UNDER", "name": "Under 3.0", "decimal": 1.97, "line": 3.0},
            ],
        },
    ],
}


class MarketParserTests(unittest.TestCase):
    def test_extracts_1x2(self):
        self.assertEqual(extract_1x2(SAMPLE_EVENT), (5.30, 4.10, 1.61))

    def test_extracts_main_asian_handicap(self):
        h_line, h_odds, a_line, a_odds = extract_main_asian_handicap(SAMPLE_EVENT)
        self.assertEqual(h_line, 1.0)
        self.assertEqual(a_line, -1.0)
        self.assertAlmostEqual(h_odds, 1.96)
        self.assertAlmostEqual(a_odds, 1.94)

    def test_extracts_main_total(self):
        line, over, under = extract_main_total(SAMPLE_EVENT)
        self.assertEqual(line, 3.0)
        self.assertAlmostEqual(over, 1.93)
        self.assertAlmostEqual(under, 1.97)

    def test_no_vig_probabilities_sum_to_one(self):
        fair = no_vig_1x2(5.56, 4.35, 1.64)
        self.assertIsNotNone(fair)
        self.assertAlmostEqual(sum(fair), 1.0, places=10)


class ExplanationTests(unittest.TestCase):
    def _row(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 5, 0, tzinfo=engine.BRISBANE),
            home_team="Crystal Palace",
            away_team="Manchester City",
            sb_home=5.30,
            sb_draw=4.10,
            sb_away=1.61,
            pm_home=5.56,
            pm_draw=4.35,
            pm_away=1.64,
            away_favourite="YES",
            match_status="Matched",
        )
        engine.add_strategy_analysis(row, 4.0)
        row.pin_fair_home = 0.18
        row.pin_fair_draw = 0.225
        row.pin_fair_away = 0.595
        row.pin_ev_home_pct = engine.expected_value_pct(row.pin_fair_home, row.sb_home)
        row.pin_ev_draw_pct = engine.expected_value_pct(row.pin_fair_draw, row.sb_draw)
        row.pin_ev_away_pct = engine.expected_value_pct(row.pin_fair_away, row.sb_away)
        row.consensus_home = (row.pm_fair_home + row.pin_fair_home) / 2
        row.consensus_draw = (row.pm_fair_draw + row.pin_fair_draw) / 2
        row.consensus_away = (row.pm_fair_away + row.pin_fair_away) / 2
        row.consensus_ev_home_pct = engine.expected_value_pct(row.consensus_home, row.sb_home)
        row.consensus_ev_draw_pct = engine.expected_value_pct(row.consensus_draw, row.sb_draw)
        row.consensus_ev_away_pct = engine.expected_value_pct(row.consensus_away, row.sb_away)
        row.reference_max_diff_pp = 0.8
        row.reference_quality = "STRONG AGREEMENT"
        row.sharp_check = "PINNACLE DISAGREES"
        return row

    def test_outcome_analysis_has_required_odds(self):
        rows = outcome_analysis(self._row(), 4.0)
        self.assertEqual(len(rows), 3)
        away = next(x for x in rows if x["side"] == "AWAY")
        self.assertGreater(away["threshold_odds"], 1.6)
        self.assertIsNotNone(away["break_even"])

    def test_plain_english_summary_explains_formula(self):
        text = plain_english_summary(self._row(), 4.0)
        self.assertIn("break even", text)
        self.assertIn("EV =", text)
        self.assertIn("Sportsbet would need to offer", text)
        self.assertIn("away-favourite status alone does not create a bet", text)


if __name__ == "__main__":
    unittest.main()
