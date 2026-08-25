import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import v22_import_compat  # noqa: F401
import v23_matching_patch  # noqa: F401
import v23_edge_patch  # noqa: F401
import v23_strategy_patch  # noqa: F401

import edge_model
import engine
from reference_consensus_v23 import apply_consensus_quotes


class V23ReferenceCoverageTests(unittest.TestCase):
    def test_long_and_short_global_club_names_match(self):
        score = engine.team_similarity("Club 2 de Mayo de Pedro Juan Cab", "2 de Mayo")
        self.assertGreaterEqual(score, 0.90)

    def test_two_books_create_consensus(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Home",
            away_team="Away",
        )
        ok = apply_consensus_quotes(
            row,
            {
                "Bet365": (2.30, 3.20, 3.10),
                "TAB": (2.35, 3.15, 3.05),
            },
        )
        self.assertTrue(ok)
        self.assertEqual(row.consensus_book_count, 2)
        self.assertAlmostEqual(
            row.consensus_fair_home + row.consensus_fair_draw + row.consensus_fair_away,
            1.0,
            places=7,
        )

    def test_consensus_can_fill_missing_primary_model(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Home",
            away_team="Away",
            sb_home=2.50,
            sb_draw=3.10,
            sb_away=2.90,
        )
        apply_consensus_quotes(
            row,
            {
                "Bet365": (2.25, 3.20, 3.20),
                "TAB": (2.30, 3.15, 3.15),
                "Ladbrokes": (2.28, 3.18, 3.18),
            },
        )
        outcomes = edge_model.calculate_match_edge(row, 4.0)
        self.assertEqual(row.reference_tier, "TIER 2 — BOOKMAKER CONSENSUS")
        self.assertIsNotNone(outcomes["HOME"].model_probability)
        self.assertGreaterEqual(outcomes["HOME"].source_count, 2)
        self.assertIn("Bet365", row.reference_execution_exclusions)


if __name__ == "__main__":
    unittest.main()
