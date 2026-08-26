import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import engine
import independent_model_v24 as v24
import independent_model_v3 as v3
import price_shop_v3
from strategy_v3 import decision_for_side
from v3_walkforward import evaluate_league


class V3ScientificTests(unittest.TestCase):
    @staticmethod
    def source():
        return next(s for s in v24.LEAGUE_SOURCES if s.key == "ENG-PL")

    @staticmethod
    def history(count=220):
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        teams = ("alpha", "beta", "gamma", "delta")
        out = []
        for i in range(count):
            home = teams[i % 4]
            away = teams[(i + 1 + (i // 4) % 2) % 4]
            if home == away:
                away = teams[(i + 2) % 4]
            hg = 2 if home == "alpha" else (i % 3)
            ag = 2 if away == "alpha" else ((i + 1) % 2)
            out.append(v24.HistoricalMatch(start + timedelta(days=i * 2), "2324", "ENG-PL", home, away, hg, ag))
        return out

    def states(self, cutoff):
        history = self.history()
        return {half: v3._build_clean_state(self.source(), history, cutoff, half) for half in (90.0, 180.0, 360.0)}

    def row(self, home_odds=2.0):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Alpha",
            away_team="Beta",
            sb_home=home_odds,
            sb_draw=3.5,
            sb_away=4.0,
        )
        row.league = "England Premier League"
        return row

    def test_current_bookmaker_odds_do_not_change_v3_probability(self):
        cutoff = datetime(2026, 8, 28, tzinfo=timezone.utc)
        states = self.states(cutoff)
        a = v3._forecast_clean(self.row(1.4), self.source(), states, dynamic=None)
        b = v3._forecast_clean(self.row(5.0), self.source(), states, dynamic=None)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertAlmostEqual(a.home_probability, b.home_probability, places=12)
        self.assertAlmostEqual(a.draw_probability, b.draw_probability, places=12)
        self.assertAlmostEqual(a.away_probability, b.away_probability, places=12)

    def test_missing_decay_variant_does_not_become_duplicate_vote(self):
        cutoff = datetime(2026, 8, 28, tzinfo=timezone.utc)
        states = self.states(cutoff)
        original = v3._team_lambdas

        def selective(state, home, away, prior_matches=7.0):
            if state.half_life_days in (90.0, 360.0):
                return None
            return original(state, home, away, prior_matches)

        with patch("independent_model_v3._team_lambdas", side_effect=selective):
            forecast = v3._forecast_clean(self.row(), self.source(), states, dynamic=None)
        self.assertIsNotNone(forecast)
        self.assertEqual(forecast.components, ("DIXON-COLES", "ELO"))
        self.assertIsNone(forecast.short_home)
        self.assertIsNone(forecast.long_home)

    def test_unvalidated_ev_is_labelled_research_only(self):
        row = self.row(home_odds=3.0)
        class Edge:
            model_probability = 0.50
            conservative_probability = 0.45
            source_count = 3
            external_disagreement_pp = 2.0
            confidence = "HIGH"
        row.edge_outcomes = {"HOME": Edge()}
        item = decision_for_side(row, "HOME", 4.0, validation_grade="FORECAST_VALIDATED")
        self.assertIsNotNone(item)
        self.assertEqual(item.status, "RESEARCH +EV — UNVALIDATED")

    def test_strict_match_rejects_large_kickoff_difference(self):
        row = self.row()
        event = {
            "startTime": (row.kickoff.astimezone(timezone.utc) + timedelta(hours=2)).isoformat(),
            "home": "Alpha",
            "away": "Beta",
            "markets": [],
        }
        self.assertIsNone(price_shop_v3._event_candidate(event, row))

    def test_strict_match_rejects_youth_vs_senior(self):
        row = self.row()
        event = {
            "startTime": row.kickoff.astimezone(timezone.utc).isoformat(),
            "home": "Alpha U21",
            "away": "Beta U21",
            "markets": [],
        }
        self.assertIsNone(price_shop_v3._event_candidate(event, row))

    def test_quote_matrix_targets_all_independently_priced_rows(self):
        rows = [self.row(1.20), self.row(8.00)]
        rows[1].home_team = "Gamma"
        rows[1].away_team = "Delta"
        for row in rows:
            row.model_fair_home = 0.40
            row.model_fair_draw = 0.30
            row.model_fair_away = 0.30
        with patch.object(price_shop_v3.legacy, "BOOKMAKERS", ()):
            result = price_shop_v3.fetch_best_prices_v3("unused", rows)
        self.assertEqual(result.target_matches, 2)
        self.assertEqual(set(result.matches), {r.match_name for r in rows})

    def test_walkforward_withholds_same_day_results(self):
        history = self.history(200)
        day = history[-1].kickoff + timedelta(days=3)
        # Same identical fixture, same timestamp/date, deliberately opposite
        # outcomes. Both predictions must be made before either result updates
        # the model, so their probabilities are identical.
        history.append(v24.HistoricalMatch(day, "2526", "ENG-PL", "alpha", "beta", 4, 0))
        history.append(v24.HistoricalMatch(day, "2526", "ENG-PL", "alpha", "beta", 0, 4))
        records = evaluate_league(self.source(), history, min_train_matches=180, max_predictions=2)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].baseline, records[1].baseline)
        self.assertEqual(records[0].challenger, records[1].challenger)


if __name__ == "__main__":
    unittest.main()
