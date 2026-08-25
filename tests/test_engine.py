import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from engine import (
    CombinedMatch,
    add_strategy_analysis,
    canonical_epl_club,
    is_current_epl_fixture,
    normalised_polymarket_probabilities,
    _is_epl_polymarket_event,
)


class EngineTests(unittest.TestCase):
    def test_current_epl_filter(self):
        self.assertTrue(is_current_epl_fixture("Crystal Palace FC", "Manchester City FC"))
        self.assertTrue(is_current_epl_fixture("Coventry City", "Hull City"))
        self.assertFalse(is_current_epl_fixture("Alfreton Town", "Hyde"))
        self.assertEqual(canonical_epl_club("Man City FC"), "Manchester City")

    def test_polymarket_derivative_rejected(self):
        self.assertFalse(_is_epl_polymarket_event({
            "title": "Crystal Palace FC v Manchester City FC - Halftime Result",
            "slug": "epl-test",
        }))
        self.assertTrue(_is_epl_polymarket_event({
            "title": "Crystal Palace FC v Manchester City FC",
            "slug": "epl-test",
        }))

    def test_probabilities_normalise(self):
        fair = normalised_polymarket_probabilities(5.56, 4.35, 1.64)
        self.assertIsNotNone(fair)
        self.assertAlmostEqual(sum(fair), 1.0, places=10)

    def test_palace_city_snapshot_passes(self):
        row = CombinedMatch(
            kickoff=datetime.now(timezone.utc),
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
        add_strategy_analysis(row, 4.0)
        self.assertEqual(row.strategy_flag, "PASS")
        self.assertLess(row.ev_away_pct, 0)

    def test_away_favourite_value_flag(self):
        row = CombinedMatch(
            kickoff=datetime.now(timezone.utc),
            home_team="Aston Villa",
            away_team="Arsenal",
            sb_home=4.50,
            sb_draw=4.00,
            sb_away=1.90,
            pm_home=4.50,
            pm_draw=4.00,
            pm_away=1.75,
            away_favourite="YES",
            match_status="Matched",
        )
        add_strategy_analysis(row, 4.0)
        self.assertEqual(row.best_selection, "AWAY")
        self.assertEqual(row.strategy_flag, "AWAY-FAV VALUE")


if __name__ == "__main__":
    unittest.main()
