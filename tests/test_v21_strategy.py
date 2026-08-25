import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import engine
from market_context_v21 import build_context_index, find_indexed_context
from strategy_v21 import best_available_v21, decision_for_side, primary_v21_decisions


class V21DecisionTests(unittest.TestCase):
    def _row(self, model_p=0.52, conservative_p=0.48, odds=2.20, source_count=2, disagreement=2.0, confidence="MEDIUM"):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Home FC",
            away_team="Away FC",
            sb_home=odds,
            sb_draw=3.30,
            sb_away=3.50,
        )
        row.league = "Test League"
        row.edge_outcomes = {
            "HOME": SimpleNamespace(
                model_probability=model_p,
                conservative_probability=conservative_p,
                source_count=source_count,
                external_disagreement_pp=disagreement,
                confidence=confidence,
            )
        }
        return row

    def test_robust_edge_requires_conservative_ev_to_clear_threshold(self):
        row = self._row(model_p=0.52, conservative_p=0.48, odds=2.20)
        decision = decision_for_side(row, "HOME", min_ev_pct=4.0)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.status, "ROBUST +EV")
        self.assertAlmostEqual(decision.robust_ev_pct, 5.6, places=5)

    def test_average_edge_with_negative_conservative_ev_is_not_primary(self):
        row = self._row(model_p=0.55, conservative_p=0.45, odds=2.00)
        decision = decision_for_side(row, "HOME", min_ev_pct=4.0)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.status, "WATCH — MARKET DISAGREEMENT")
        self.assertEqual(primary_v21_decisions([row], 4.0), [])

    def test_single_external_reference_cannot_be_primary(self):
        row = self._row(model_p=0.60, conservative_p=0.60, odds=2.00, source_count=1, disagreement=None, confidence="HIGH")
        decision = decision_for_side(row, "HOME", min_ev_pct=4.0)
        self.assertIsNotNone(decision)
        self.assertNotEqual(decision.status, "ROBUST +EV")

    def test_price_shopping_can_improve_execution_without_changing_probability(self):
        row = self._row(model_p=0.52, conservative_p=0.48, odds=2.00)
        row.price_shop = SimpleNamespace(
            best={
                "HOME": SimpleNamespace(source="TAB", decimal_odds=2.20),
                "DRAW": None,
                "AWAY": None,
            }
        )
        decision = decision_for_side(row, "HOME", min_ev_pct=4.0)
        self.assertEqual(decision.quote_source, "TAB")
        self.assertAlmostEqual(decision.model_probability, 0.52)
        self.assertEqual(decision.status, "ROBUST +EV")

    def test_no_robust_edge_still_returns_highest_ev_for_dashboard_comparison(self):
        a = self._row(model_p=0.45, conservative_p=0.44, odds=2.00)
        b = self._row(model_p=0.49, conservative_p=0.46, odds=2.00)
        b.home_team = "Second Home"
        choice = best_available_v21([a, b], min_ev_pct=4.0)
        self.assertIsNotNone(choice)
        self.assertEqual(choice.match_name, b.match_name)
        self.assertLess(choice.model_ev_pct, 0.0)


class V21ContextIndexTests(unittest.TestCase):
    def test_raw_provider_event_is_parsed_once_when_index_is_built(self):
        calls = {"count": 0}
        kickoff = datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE)

        def fake_context(event):
            calls["count"] += 1
            i = int(event["i"])
            return {
                "kickoff": kickoff,
                "home": f"Home {i}",
                "away": f"Away {i}",
                "league": "Test League",
                "h": 2.0, "d": 3.2, "a": 3.8,
                "ah_h_line": 0.0, "ah_h": 1.95,
                "ah_a_line": 0.0, "ah_a": 1.95,
                "total_line": 2.5, "total_over": 1.95, "total_under": 1.95,
            }

        raw = [{"i": i} for i in range(100)]
        with patch("market_context_v21._context_event", side_effect=fake_context):
            index = build_context_index(raw)
            self.assertEqual(calls["count"], 100)

            # Repeated fixture lookups use the index and must not reparse the
            # original provider catalogue.
            for i in range(30):
                row = engine.CombinedMatch(
                    kickoff=kickoff,
                    home_team=f"Home {i}",
                    away_team=f"Away {i}",
                )
                row.league = "Test League"
                self.assertIsNotNone(find_indexed_context(row, index))
            self.assertEqual(calls["count"], 100)


if __name__ == "__main__":
    unittest.main()
