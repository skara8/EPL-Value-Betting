import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import engine
from edge_progress_v22 import enrich_edge_model_parallel, recommended_worker_count
from research_models_v22 import HistoricalMatch, elo_probabilities, parse_history_csv, time_decayed_poisson


class CpuWorkerTests(unittest.TestCase):
    def test_worker_count_uses_most_but_not_all_cores(self):
        with patch("edge_progress_v22.os.cpu_count", return_value=12):
            self.assertEqual(recommended_worker_count(), 9)
        with patch("edge_progress_v22.os.cpu_count", return_value=4):
            self.assertEqual(recommended_worker_count(), 3)
        with patch("edge_progress_v22.os.cpu_count", return_value=2):
            self.assertEqual(recommended_worker_count(), 1)

    def test_parallel_api_has_safe_single_worker_fallback(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 12, 0, tzinfo=engine.BRISBANE),
            home_team="Home",
            away_team="Away",
            sb_home=2.0,
            sb_draw=3.5,
            sb_away=4.0,
            pm_fair_home=0.50,
            pm_fair_draw=0.28,
            pm_fair_away=0.22,
        )
        result = enrich_edge_model_parallel([row], workers=1)
        self.assertEqual(len(result), 1)
        self.assertTrue(hasattr(result[0], "edge_outcomes"))


class HistoricalResearchTests(unittest.TestCase):
    def test_csv_parser_uses_market_prices_when_present(self):
        text = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,PSH,PSD,PSA\n"
            "01/08/2025,Arsenal,Chelsea,2,1,1.80,3.80,4.70\n"
        )
        rows = parse_history_csv(text)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].market_home + rows[0].market_draw + rows[0].market_away, 1.0, places=6)

    @staticmethod
    def _history(n=120):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        teams = ["Alpha", "Bravo", "Charlie", "Delta"]
        rows = []
        for i in range(n):
            home = teams[i % 4]
            away = teams[(i + 1 + (i // 4) % 2) % 4]
            if home == away:
                away = teams[(i + 2) % 4]
            hg = 2 if home == "Alpha" else 1
            ag = 0 if away == "Delta" else 1
            rows.append(HistoricalMatch(start + timedelta(days=i * 2), home, away, hg, ag))
        return rows

    def test_elo_and_poisson_only_use_pre_cutoff_history(self):
        history = self._history()
        cutoff = history[-1].kickoff - timedelta(days=10)
        future = HistoricalMatch(cutoff + timedelta(days=1), "Alpha", "Bravo", 20, 0)
        base_elo = elo_probabilities(history, "Alpha", "Bravo", cutoff)
        base_dc = time_decayed_poisson(history, "Alpha", "Bravo", cutoff)
        with_future_elo = elo_probabilities(history + [future], "Alpha", "Bravo", cutoff)
        with_future_dc = time_decayed_poisson(history + [future], "Alpha", "Bravo", cutoff)
        self.assertEqual(base_elo, with_future_elo)
        self.assertEqual(base_dc, with_future_dc)
        self.assertIsNotNone(base_elo)
        self.assertIsNotNone(base_dc)


class V22GuiSmokeTests(unittest.TestCase):
    def test_v22_gui_constructs_research_tab(self):
        from main_v22 import V22App
        try:
            app = V22App()
        except Exception as exc:
            self.fail(f"V2.2 GUI failed to initialise: {exc}")
        try:
            self.assertTrue(hasattr(app, "research_models_tab"))
            self.assertEqual(len(app.analysis_book.tabs()), 5)
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
