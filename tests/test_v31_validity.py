import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import edge_model
import engine
import v3_storage
from execution_v3 import V3MatchPriceShop, V3PriceQuote, best_executable_quote, is_executable_quote
from independent_model_v24 import HistoricalMatch, LEAGUE_SOURCES
from model_v3 import apply_v3_forecasts, build_v3_forecasts
from storage import event_key
from strategy_v3 import decision_for_side
from validation_v3 import _fold_plan, walk_forward_validate


class V31ValidityTests(unittest.TestCase):
    @staticmethod
    def source():
        return next(source for source in LEAGUE_SOURCES if source.key == "ENG-PL")

    @staticmethod
    def history(days=70):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        teams = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
        output = []
        for day in range(days):
            at = start + timedelta(days=day * 3)
            for slot in range(2):
                i = day * 2 + slot
                home = teams[i % len(teams)]
                away = teams[(i * 3 + 1) % len(teams)]
                if home == away:
                    away = teams[(i + 2) % len(teams)]
                hs = len(teams) - teams.index(home)
                as_ = len(teams) - teams.index(away)
                hg = 1 + int(hs > as_) + int(i % 5 == 0)
                ag = int(as_ >= hs) + int(i % 7 == 0)
                output.append(HistoricalMatch(at, "2526", "ENG-PL", home, away, hg, ag))
        return output

    @staticmethod
    def row(kickoff=None):
        row = engine.CombinedMatch(
            kickoff=kickoff or datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Alpha",
            away_team="Beta",
            sb_home=2.10,
            sb_draw=3.50,
            sb_away=3.80,
            pm_fair_home=.48,
            pm_fair_draw=.27,
            pm_fair_away=.25,
        )
        row.league = "England Premier League"
        return row

    def test_fold_boundaries_never_split_a_calendar_day(self):
        history = self.history(80)
        plans = _fold_plan(history, min_train_matches=80, fold_size=25, max_folds=3)
        self.assertGreater(len(plans), 0)
        for training, test in plans:
            self.assertLess(max(m.kickoff.date() for m in training), min(m.kickoff.date() for m in test))
            self.assertTrue(set(m.kickoff.date() for m in training).isdisjoint({m.kickoff.date() for m in test}))

    def test_walk_forward_reports_simple_chronological_baselines(self):
        report = walk_forward_validate(self.source(), self.history(90), min_train_matches=100, fold_size=30, max_folds=2)
        self.assertGreater(report.predictions, 0)
        self.assertIsNotNone(report.league_frequency_log_loss)
        self.assertIsNotNone(report.elo_only_log_loss)
        self.assertIsNotNone(report.dynamic_only_log_loss)
        self.assertIsNotNone(report.delta_log_loss_vs_best_baseline)
        self.assertIsNotNone(report.rps)

    def test_stale_best_price_cannot_beat_fresh_executable_price(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stale = V3PriceQuote("BookA", "HOME", 2.50, "2.50", now, age_seconds=601.0, match_confidence=.99)
        fresh = V3PriceQuote("BookB", "HOME", 2.20, "2.20", now, age_seconds=15.0, match_confidence=.99)
        self.assertFalse(is_executable_quote(stale))
        self.assertTrue(is_executable_quote(fresh))
        self.assertIs(best_executable_quote([stale, fresh]), fresh)

    def test_canonical_event_id_survives_same_season_reschedule(self):
        first = self.row(datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE))
        moved = self.row(datetime(2026, 9, 5, 20, 0, tzinfo=engine.BRISBANE))
        self.assertEqual(
            v3_storage.canonical_event_id_for_row(first, "ENG-PL"),
            v3_storage.canonical_event_id_for_row(moved, "ENG-PL"),
        )


class V31EconomicEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = v3_storage.DB_FILE
        v3_storage.DB_FILE = Path(self.tmp.name) / "v31-test.db"

    def tearDown(self):
        v3_storage.DB_FILE = self.old_db
        self.tmp.cleanup()

    @staticmethod
    def history(count=125):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        teams = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
        output = []
        for i in range(count):
            home = teams[i % len(teams)]
            away = teams[(i * 3 + 1) % len(teams)]
            if home == away:
                away = teams[(i + 2) % len(teams)]
            hg = 1 + int(teams.index(home) < teams.index(away))
            ag = int(teams.index(away) <= teams.index(home))
            output.append(HistoricalMatch(start + timedelta(days=i * 3), "2526", "ENG-PL", home, away, hg, ag))
        return output

    def _row_and_decision(self):
        row = V31ValidityTests.row()
        edge_model.calculate_match_edge(row, 4.0)
        forecast = build_v3_forecasts([row], {"ENG-PL": self.history()}, bootstrap_samples=3)[row.match_name]
        apply_v3_forecasts([row], {row.match_name: forecast}, 4.0)
        shop = V3MatchPriceShop(row.match_name, row.league)
        shop.model_probability = {"HOME": forecast.home_probability, "DRAW": forecast.draw_probability, "AWAY": forecast.away_probability}
        quote = V3PriceQuote(
            "UnitBook", "HOME", 2.25, "2.25", datetime.now(timezone.utc).isoformat(timespec="seconds"),
            age_seconds=5.0, available_size=100.0, match_confidence=.99, event_id="unit-event-1",
        )
        shop.quotes["HOME"].append(quote)
        shop.best["HOME"] = quote
        shop.best_ev_pct["HOME"] = (forecast.home_probability * 2.25 - 1) * 100
        row.price_shop = shop
        decision = decision_for_side(row, "HOME", 4.0)
        self.assertIsNotNone(decision)
        return row, decision

    def test_final_close_and_outcome_create_separate_economic_evidence(self):
        row, decision = self._row_and_decision()
        v3_storage.save_v3_snapshot([row], [decision])
        canonical_id = v3_storage.canonical_event_id_for_row(row, "ENG-PL")
        v3_storage.record_sharp_line(
            event_key(row),
            "Pinnacle",
            horizon_label="T-15m",
            probabilities=(.49, .27, .24),
            odds=(2.02, 3.60, 4.10),
            final_pre_kickoff=True,
            canonical_event_id=canonical_id,
            minutes_to_kickoff=10.0,
            de_vig_method="MULTIPLICATIVE_INVERSE_ODDS",
        )
        v3_storage.record_outcome(event_key(row), row.kickoff.isoformat(), 2, 1, canonical_event_id=canonical_id)
        updated = v3_storage.refresh_economic_evidence()
        summary = v3_storage.economic_summary()
        self.assertEqual(updated, 1)
        self.assertEqual(summary["decisions_with_final_close"], 1)
        self.assertEqual(summary["settled_proxy_decisions"], 1)
        self.assertGreater(summary["average_price_clv_pct"], 0)
        self.assertEqual(summary["actual_fills"], 0)

    def test_provenance_experiment_and_fill_ledgers_are_available(self):
        row, decision = self._row_and_decision()
        v3_storage.save_v3_snapshot([row], [decision])
        canonical_id = v3_storage.canonical_event_id_for_row(row, "ENG-PL")
        provenance_id = v3_storage.record_provenance("unit-test", "synthetic", record_count=1, payload={"x": 1})
        experiment_id = v3_storage.register_experiment(
            "unit challenger",
            control_model="dynamic+elo",
            challenger_model="candidate-xg",
            feature_set=("xg_for", "xg_against"),
            primary_metric="multiclass_log_loss",
        )
        fill_id = v3_storage.record_fill(canonical_id, "HOME", "UnitBook", filled_odds=2.20, stake=1.0)
        self.assertGreater(provenance_id, 0)
        self.assertGreater(experiment_id, 0)
        self.assertGreater(fill_id, 0)
        self.assertEqual(v3_storage.economic_summary()["actual_fills"], 1)


if __name__ == "__main__":
    unittest.main()
