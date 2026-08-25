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
from context_model import (
    ContextInputs,
    FPLTeamContext,
    apply_context_tilt,
    context_adjustment_for_match,
    weighted_context_score,
)
from dutch_calc import (
    DutchSelection,
    calculate_dutch,
    polymarket_effective_decimal_odds,
)


class ContextModelTests(unittest.TestCase):
    def test_context_tilt_preserves_probability_mass_and_cap(self):
        base = (0.50, 0.25, 0.25)
        adjusted = apply_context_tilt(base, 3.0, max_shift_pp=1.5)
        self.assertAlmostEqual(sum(adjusted), 1.0, places=10)
        self.assertGreater(adjusted[0], base[0])
        self.assertLess(adjusted[2], base[2])
        self.assertLessEqual(max(abs(adjusted[i] - base[i]) for i in range(3)), 0.01501)

    def test_transfer_spend_alone_does_not_create_probability_direction(self):
        inputs = ContextInputs(home_transfer_spend_m=300, away_transfer_spend_m=20)
        score, breakdown = weighted_context_score(inputs, 0.0)
        self.assertAlmostEqual(score, 0.0)
        self.assertAlmostEqual(breakdown["transfer_squad"], 0.0)

    def test_availability_difference_can_tilt_context(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 5, 0, tzinfo=engine.BRISBANE),
            home_team="Crystal Palace",
            away_team="Manchester City",
            sb_home=5.30,
            sb_draw=4.10,
            sb_away=1.61,
        )
        row.model_fair_home = 0.18
        row.model_fair_draw = 0.22
        row.model_fair_away = 0.60
        contexts = {
            "Crystal Palace": FPLTeamContext(team="Crystal Palace", availability_penalty=0.0),
            "Manchester City": FPLTeamContext(team="Manchester City", availability_penalty=1.2),
        }
        adjustment = context_adjustment_for_match(row, ContextInputs(), contexts, max_shift_pp=1.5)
        self.assertIsNotNone(adjustment)
        self.assertGreater(adjustment.weighted_score, 0)
        self.assertGreater(adjustment.home_probability, 0.18)
        self.assertLess(adjustment.away_probability, 0.60)


class DutchTests(unittest.TestCase):
    def test_classic_four_selection_dutch_matches_article_example(self):
        selections = [
            DutchSelection("A", "Other", decimal_odds=8.0),
            DutchSelection("B", "Other", decimal_odds=9.0),
            DutchSelection("C", "Other", decimal_odds=15.0),
            DutchSelection("D", "Other", decimal_odds=16.0),
        ]
        result = calculate_dutch(selections, 40.0, complete_market=False)
        self.assertAlmostEqual(result.combined_decimal_odds, 2.7376, places=3)
        self.assertAlmostEqual(result.equal_profit, 69.50, delta=0.15)
        for row in result.rows:
            self.assertAlmostEqual(row.gross_return, result.equal_return, places=8)

    def test_arbitrage_detection_requires_complete_market(self):
        selections = [
            DutchSelection("Home", "Other", decimal_odds=2.20),
            DutchSelection("Draw", "Other", decimal_odds=3.60),
            DutchSelection("Away", "Other", decimal_odds=4.00),
        ]
        complete = calculate_dutch(selections, 100.0, complete_market=True)
        partial = calculate_dutch(selections, 100.0, complete_market=False)
        self.assertTrue(complete.arbitrage)
        self.assertGreater(complete.equal_profit, 0)
        self.assertFalse(partial.arbitrage)

    def test_overround_prices_lock_in_negative_dutch(self):
        selections = [
            DutchSelection("Home", "Sportsbet", decimal_odds=1.91),
            DutchSelection("Draw", "Sportsbet", decimal_odds=3.75),
            DutchSelection("Away", "Sportsbet", decimal_odds=3.80),
        ]
        result = calculate_dutch(selections, 100.0, complete_market=True)
        self.assertFalse(result.arbitrage)
        self.assertLess(result.equal_profit, 0)

    def test_polymarket_taker_fee_reduces_effective_odds(self):
        p = 0.60
        raw = 1.0 / p
        taker = polymarket_effective_decimal_odds(p, fee_rate=0.05, maker=False)
        maker = polymarket_effective_decimal_odds(p, fee_rate=0.05, maker=True)
        self.assertLess(taker, raw)
        self.assertAlmostEqual(maker, raw, places=10)


if __name__ == "__main__":
    unittest.main()
