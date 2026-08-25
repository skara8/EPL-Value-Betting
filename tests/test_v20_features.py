import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import engine
import main_v19
from main_v17 import BLUE_BG, BLUE_DARK

main_v19.BLUE_BG = BLUE_BG
main_v19.BLUE_DARK = BLUE_DARK

from main_v20 import V20App  # noqa: E402
from main_v20_final import V20FinalApp  # noqa: E402
from price_shop import MatchPriceShop, PriceQuote, _match_league, _top_rows  # noqa: E402


class PriceShopTests(unittest.TestCase):
    def test_league_matching_handles_country_prefix(self):
        offered = ["England - Premier League", "Spain - La Liga", "Germany Bundesliga"]
        self.assertEqual(_match_league("Premier League", offered), "England - Premier League")

    def test_top_rows_are_ranked_by_existing_model_ev(self):
        def row(name, league, ev):
            item = engine.CombinedMatch(
                kickoff=datetime(2026, 8, 25, 20, 0, tzinfo=engine.BRISBANE),
                home_team=name,
                away_team="Opponent",
            )
            setattr(item, "league", league)
            item.edge_outcomes = {"HOME": SimpleNamespace(model_ev_pct=ev)}
            return item

        rows = [row("A", "L1", 1.0), row("B", "L2", 7.0), row("C", "L1", 4.0)]
        chosen = _top_rows(rows, max_matches=2, max_leagues=2)
        self.assertEqual([r.home_team for r in chosen], ["B", "C"])

    def test_best_quote_is_highest_decimal_price(self):
        shop = MatchPriceShop("A v B", "League")
        shop.quotes["HOME"] = [
            PriceQuote("Sportsbet", "HOME", 2.10),
            PriceQuote("TAB", "HOME", 2.20),
            PriceQuote("Ladbrokes", "HOME", 2.15),
        ]
        shop.best["HOME"] = max(shop.quotes["HOME"], key=lambda q: q.decimal_odds)
        self.assertEqual(shop.best["HOME"].source, "TAB")
        self.assertAlmostEqual(shop.best["HOME"].decimal_odds, 2.20)


class V20CopyTests(unittest.TestCase):
    def test_old_version_language_is_removed(self):
        text = "V1.6 keeps the V1.5 external-market fair probability as the baseline. Manual in V1.6."
        cleaned = V20App._clean_copy(text)
        self.assertNotIn("V1.", cleaned)
        self.assertIn("independent external-market fair probability", cleaned)

    def test_selection_side_mapping(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 25, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Home",
            away_team="Away",
        )
        self.assertEqual(V20App._side_from_selection(row, "Home"), "HOME")
        self.assertEqual(V20App._side_from_selection(row, "Draw"), "DRAW")
        self.assertEqual(V20App._side_from_selection(row, "Away"), "AWAY")


class V20GuiSmokeTests(unittest.TestCase):
    def test_condensed_navigation_constructs(self):
        try:
            app = V20FinalApp()
        except Exception as exc:
            self.fail(f"V2 GUI failed to initialise: {exc}")
        try:
            self.assertEqual(len(app.notebook.tabs()), 6)
            self.assertEqual(len(app.markets_book.tabs()), 3)
            self.assertEqual(len(app.analysis_book.tabs()), 4)
            self.assertEqual(len(app.tools_book.tabs()), 1)
            self.assertEqual(len(app.research_book.tabs()), 3)
            self.assertTrue(hasattr(app, "analysis_progress"))
            self.assertTrue(hasattr(app, "price_shop_tree"))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
