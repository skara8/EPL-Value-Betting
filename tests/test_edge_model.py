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
from edge_model import (
    asian_handicap_profit,
    calculate_match_edge,
    fit_ah_total_model,
    power_devig,
    proportional_devig,
    total_over_profit,
)


class DevigTests(unittest.TestCase):
    def test_power_devig_sums_to_one(self):
        fair = power_devig(1.61, 4.10, 5.30)
        self.assertIsNotNone(fair)
        self.assertAlmostEqual(sum(fair), 1.0, places=10)

    def test_power_method_reduces_longshot_more_than_proportional(self):
        prop = proportional_devig(1.61, 4.10, 5.30)
        power = power_devig(1.61, 4.10, 5.30)
        self.assertIsNotNone(prop)
        self.assertIsNotNone(power)
        # For an overround market, the power method normally transfers some
        # probability mass from longshots toward the favourite.
        self.assertGreater(power[0], prop[0])
        self.assertLess(power[2], prop[2])


class AsianSettlementTests(unittest.TestCase):
    def test_minus_quarter_splits_draw_into_half_loss(self):
        # Home -0.25 on a draw: half stake pushes at 0, half loses at -0.5.
        self.assertAlmostEqual(asian_handicap_profit(0, -0.25, 2.0), -0.5)

    def test_plus_quarter_splits_draw_into_half_win(self):
        # Home +0.25 on a draw: half pushes at 0, half wins at +0.5.
        self.assertAlmostEqual(asian_handicap_profit(0, 0.25, 2.0), 0.5)

    def test_over_275_with_three_goals_is_half_win(self):
        # O2.75 = half O2.5 (win) + half O3.0 (push).
        self.assertAlmostEqual(total_over_profit(3, 2.75, 2.0), 0.5)


class EdgeModelTests(unittest.TestCase):
    def _palace_city(self):
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
            sportsbet_favourite="Manchester City",
            away_favourite="YES",
            match_status="Matched",
        )
        engine.add_strategy_analysis(row, 4.0)
        return row

    def test_sportsbet_is_not_used_as_fair_baseline(self):
        row = self._palace_city()
        calculate_match_edge(row, 4.0)
        away = row.edge_outcomes["AWAY"]
        # With only Polymarket present, model probability must equal PM exactly,
        # not Sportsbet's own de-vig probability.
        self.assertAlmostEqual(away.model_probability, row.pm_fair_away, places=10)
        self.assertNotAlmostEqual(away.model_probability, away.sportsbet_devig_probability, places=5)

    def test_two_external_providers_are_averaged_once_each(self):
        row = self._palace_city()
        # Add a Pinnacle 1X2 market.  No AH market in this test.
        row.pin_home = 5.40
        row.pin_draw = 4.20
        row.pin_away = 1.66
        calculate_match_edge(row, 4.0)
        away = row.edge_outcomes["AWAY"]
        self.assertEqual(away.source_count, 2)
        self.assertGreater(away.model_probability, min(away.polymarket_probability, away.pinnacle_probability) - 1e-12)
        self.assertLess(away.model_probability, max(away.polymarket_probability, away.pinnacle_probability) + 1e-12)

    def test_away_favourite_is_tag_not_probability_bonus(self):
        row = self._palace_city()
        calculate_match_edge(row, 4.0)
        away = row.edge_outcomes["AWAY"]
        self.assertIn("AWAY-FAVOURITE", away.bias_tags)
        self.assertAlmostEqual(away.model_probability, row.pm_fair_away, places=10)

    def test_pinnacle_ah_total_fit_returns_valid_probabilities(self):
        row = self._palace_city()
        row.pin_ah_home_line = 1.0
        row.pin_ah_home_odds = 1.96
        row.pin_ah_away_line = -1.0
        row.pin_ah_away_odds = 1.94
        row.pin_total_line = 3.0
        row.pin_total_over = 1.93
        row.pin_total_under = 1.97
        fit = fit_ah_total_model(row)
        self.assertIsNotNone(fit)
        self.assertEqual(fit.source, "PINNACLE")
        total = fit.home_probability + fit.draw_probability + fit.away_probability
        self.assertAlmostEqual(total, 1.0, places=7)
        self.assertGreater(fit.lambda_home, 0)
        self.assertGreater(fit.lambda_away, 0)


if __name__ == "__main__":
    unittest.main()
