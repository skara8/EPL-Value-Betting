import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import engine
from edge_parallel_v22 import enrich_edge_model_parallel, recommended_workers
from main_v17 import BLUE_BG, BLUE_DARK
import main_v19

main_v19.BLUE_BG = BLUE_BG
main_v19.BLUE_DARK = BLUE_DARK

from main_v22 import V22App  # noqa: E402
from research_models_v22 import (  # noqa: E402
    HistoricalMatch,
    ResearchMatchFeatures,
    build_research_features,
    elo_ratings_before,
    lineup_continuity,
    time_decayed_poisson_before,
)
from v22_research_storage import research_summary, save_research_features  # noqa: E402


class CpuAccelerationTests(unittest.TestCase):
    def test_auto_worker_count_uses_most_but_not_all_cpu(self):
        self.assertEqual(recommended_workers(2), 1)
        self.assertEqual(recommended_workers(4), 3)
        self.assertEqual(recommended_workers(12), 9)
        self.assertEqual(recommended_workers(32), 10)

    def test_serial_fallback_path_preserves_edge_calculation(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Arsenal",
            away_team="Chelsea",
            sb_home=2.0,
            sb_draw=3.4,
            sb_away=3.8,
            pm_fair_home=0.52,
            pm_fair_draw=0.25,
            pm_fair_away=0.23,
        )
        row.pin_home = 1.95
        row.pin_draw = 3.55
        row.pin_away = 4.10
        rows, stats = enrich_edge_model_parallel([row], 4.0, workers=1)
        self.assertFalse(stats.parallel)
        self.assertEqual(stats.completed, 1)
        self.assertTrue(hasattr(rows[0], "edge_outcomes"))
        self.assertIsNotNone(rows[0].edge_outcomes["HOME"].model_probability)


class HistoricalResearchModelTests(unittest.TestCase):
    def _history(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        matches = []
        # Arsenal is deliberately dominant at home; Chelsea is weak away.
        for i in range(18):
            matches.append(HistoricalMatch(
                start + timedelta(days=i * 7),
                "2526",
                "Arsenal",
                "Chelsea",
                3 if i % 3 else 2,
                0 if i % 4 else 1,
            ))
            matches.append(HistoricalMatch(
                start + timedelta(days=i * 7 + 2),
                "2526",
                "Liverpool",
                "Arsenal",
                1,
                2,
            ))
            matches.append(HistoricalMatch(
                start + timedelta(days=i * 7 + 4),
                "2526",
                "Chelsea",
                "Liverpool",
                0,
                2,
            ))
        return sorted(matches, key=lambda m: m.kickoff)

    def test_elo_detects_stronger_team(self):
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ratings = elo_ratings_before(self._history(), cutoff)
        self.assertGreater(ratings["Arsenal"], ratings["Chelsea"])

    def test_time_decayed_poisson_detects_home_strength(self):
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        fit = time_decayed_poisson_before(self._history(), "Arsenal", "Chelsea", cutoff)
        self.assertIsNotNone(fit)
        home, draw, away, lh, la, _ = fit
        self.assertGreater(home, away)
        self.assertGreater(lh, la)
        self.assertAlmostEqual(home + draw + away, 1.0, places=5)

    def test_lineup_continuity_rewards_regular_expected_starters(self):
        stable = SimpleNamespace(expected_xi=[
            SimpleNamespace(strength=1.0, recent_starts=5, start_probability=0.95)
            for _ in range(11)
        ])
        rotated = SimpleNamespace(expected_xi=[
            SimpleNamespace(strength=1.0, recent_starts=2, start_probability=0.70)
            for _ in range(11)
        ])
        self.assertGreater(lineup_continuity(stable), lineup_continuity(rotated))

    def test_research_models_do_not_overwrite_market_probability(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 1, 5, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Arsenal",
            away_team="Chelsea",
            sb_home=1.90,
            sb_draw=3.6,
            sb_away=4.2,
        )
        row.league = "Premier League"
        row.model_fair_home = 0.55
        row.model_fair_draw = 0.25
        row.model_fair_away = 0.20

        player = lambda starts: SimpleNamespace(strength=1.0, recent_starts=starts, start_probability=0.9)
        home_intel = SimpleNamespace(expected_xi=[player(5) for _ in range(11)], recent_matches=[])
        away_intel = SimpleNamespace(expected_xi=[player(3) for _ in range(11)], recent_matches=[])
        intel = SimpleNamespace(home=home_intel, away=away_intel, data_quality="HIGH")
        bundle = SimpleNamespace(matches={row.match_name: intel})

        before = (row.model_fair_home, row.model_fair_draw, row.model_fair_away)
        features = build_research_features([row], bundle, self._history())
        after = (row.model_fair_home, row.model_fair_draw, row.model_fair_away)
        self.assertEqual(before, after)
        self.assertIn(row.match_name, features)
        self.assertIsNotNone(features[row.match_name].elo_home)


class ResearchStorageTests(unittest.TestCase):
    def test_research_snapshot_schema_and_upsert(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Arsenal",
            away_team="Chelsea",
        )
        row.league = "Premier League"
        feature = ResearchMatchFeatures(
            match_name=row.match_name,
            market_home=0.55, market_draw=0.25, market_away=0.20,
            elo_home_rating=1600.0, elo_away_rating=1500.0,
            elo_home=0.58, elo_draw=0.25, elo_away=0.17,
            poisson_home=0.57, poisson_draw=0.24, poisson_away=0.19,
            poisson_lambda_home=1.85, poisson_lambda_away=0.90,
            lineup_home=0.90, lineup_away=0.78, lineup_diff_pp=12.0,
            home_recent_net_xg=0.75, away_recent_net_xg=-0.10,
            home_recent_opponent_elo=1540.0, away_recent_opponent_elo=1490.0,
            market_research_disagreement_pp=3.0,
            consensus="3/3 agree · Home",
            history_matches=80,
            data_quality="HIGH",
        )
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "research.db"
            with patch("v22_research_storage.DB_FILE", db):
                changed = save_research_features([row], {row.match_name: feature})
                self.assertEqual(changed, 1)
                summary = research_summary()
                self.assertEqual(summary.snapshots, 1)
                self.assertEqual(summary.high_quality, 1)
                self.assertEqual(summary.with_elo, 1)
                self.assertEqual(summary.with_poisson, 1)
                self.assertEqual(summary.with_lineup, 1)
                # Same event should update rather than create a duplicate.
                save_research_features([row], {row.match_name: feature})
                self.assertEqual(research_summary().snapshots, 1)


class V22GuiSmokeTests(unittest.TestCase):
    def test_v22_gui_adds_research_models_without_expanding_top_level_nav(self):
        try:
            app = V22App()
        except Exception as exc:
            self.fail(f"V2.2 GUI failed to initialise: {exc}")
        try:
            self.assertEqual(len(app.notebook.tabs()), 6)
            self.assertEqual(len(app.research_book.tabs()), 4)
            self.assertTrue(hasattr(app, "v22_research_tree"))
            self.assertIn("workers", app.v22_cpu_var.get())
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
