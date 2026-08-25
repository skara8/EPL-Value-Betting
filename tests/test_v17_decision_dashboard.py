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
    _is_departed_player_news,
    apply_context_tilt,
    biggest_position_gap,
)
from decision_model import build_bet_ideas, find_dutch_ideas
from edge_model import calculate_match_edge


class FPLCleaningTests(unittest.TestCase):
    def test_departed_players_are_recognised(self):
        self.assertTrue(_is_departed_player_news("Has joined Barcelona permanently"))
        self.assertTrue(_is_departed_player_news("Has joined Bristol City on loan for the rest of the season"))
        self.assertTrue(_is_departed_player_news("Has returned to Getafe CF"))
        self.assertFalse(_is_departed_player_news("Calf injury - Expected back 5 Sep"))
        self.assertFalse(_is_departed_player_news("Groin injury - 75% chance of playing"))

    def test_position_gap(self):
        home = FPLTeamContext(team="Home")
        away = FPLTeamContext(team="Away")
        away.position_penalty["MID"] = 2.0
        position, gap = biggest_position_gap(home, away)
        self.assertEqual(position, "MID")
        self.assertGreater(gap, 0)


class SimpleDashboardTests(unittest.TestCase):
    def _row(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 5, 0, tzinfo=engine.BRISBANE),
            home_team="Crystal Palace",
            away_team="Manchester City",
            sb_home=5.30,
            sb_draw=4.10,
            sb_away=1.90,
            pm_home=5.56,
            pm_draw=4.35,
            pm_away=1.72,
            sportsbet_favourite="Manchester City",
            away_favourite="YES",
            match_status="Matched",
        )
        engine.add_strategy_analysis(row, 4.0)
        calculate_match_edge(row, 4.0)
        return row

    def test_dashboard_does_not_use_context_to_rescue_negative_base_ev(self):
        row = self._row()
        # Force a negative base HOME edge but a strong home context tilt.
        edge = row.edge_outcomes["HOME"]
        edge.model_ev_pct = -2.0
        row.model_fair_home = 0.19
        ideas = build_bet_ideas(
            [row],
            context_inputs_by_key={
                f"{row.kickoff.isoformat()}|{row.home_team}|{row.away_team}": ContextInputs(player_lineup=3.0)
            },
            max_context_shift_pp=3.0,
            min_ev_pct=0.1,
        )
        self.assertFalse(any(i.side == "HOME" for i in ideas))

    def test_context_probability_still_sums_to_one(self):
        adjusted = apply_context_tilt((0.50, 0.25, 0.25), 2.0, 1.5)
        self.assertAlmostEqual(sum(adjusted), 1.0, places=10)

    def test_full_market_dutch_arbitrage_is_surfaced(self):
        row = self._row()
        # Artificial prices solely for calculator regression: inverse sum < 1.
        row.sb_home = 3.60
        row.sb_draw = 3.60
        row.sb_away = 3.60
        dutch = find_dutch_ideas([row])
        self.assertTrue(any(d.arbitrage and d.complete_market for d in dutch))


if __name__ == "__main__":
    unittest.main()
