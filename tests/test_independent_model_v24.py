import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import edge_model
import engine
from independent_model_v24 import (
    HistoricalMatch,
    LEAGUE_SOURCES,
    _build_state,
    _forecast_from_states,
    apply_independent_forecasts,
    dixon_coles_probabilities,
    resolve_league_source,
)
from strategy_v24 import decision_for_side


class LeagueCoverageTests(unittest.TestCase):
    def test_major_league_aliases(self):
        cases = {
            "England Premier League": "ENG-PL",
            "Spain La Liga": "ESP-LL",
            "Germany Bundesliga": "GER-B1",
            "Italy Serie A": "ITA-A",
            "France Ligue 1": "FRA-L1",
            "Brazil Serie A": "BRA-A",
            "Argentina Primera Division": "ARG-PR",
            "Major League Soccer": "USA-MLS",
            "Japan J1 League": "JPN-J1",
            "Mexico Liga MX": "MEX-LMX",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                source = resolve_league_source(label)
                self.assertIsNotNone(source)
                self.assertEqual(source.key, expected)

    def test_unknown_league_is_not_forced_into_model(self):
        self.assertIsNone(resolve_league_source("Imaginary Islands Regional Division 7"))


class IndependentProbabilityTests(unittest.TestCase):
    @staticmethod
    def source():
        return next(s for s in LEAGUE_SOURCES if s.key == "ENG-PL")

    @staticmethod
    def history():
        start = datetime(2024, 7, 1, tzinfo=timezone.utc)
        teams = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
        matches = []
        for i in range(180):
            home = teams[i % len(teams)]
            away = teams[(i * 3 + 1) % len(teams)]
            if home == away:
                away = teams[(i + 2) % len(teams)]
            # Stable synthetic strengths: alpha strongest, beta second.
            h_strength = len(teams) - teams.index(home)
            a_strength = len(teams) - teams.index(away)
            hg = max(0, 1 + int(h_strength > a_strength) + (i % 3 == 0))
            ag = max(0, int(a_strength >= h_strength) + (i % 5 == 0))
            matches.append(
                HistoricalMatch(
                    start + timedelta(days=i * 3),
                    "2425" if i < 90 else "2526",
                    "ENG-PL",
                    home,
                    away,
                    hg,
                    ag,
                )
            )
        return matches

    def states(self):
        cutoff = datetime(2026, 8, 28, tzinfo=timezone.utc)
        return {
            half: _build_state(self.source(), self.history(), cutoff, half)
            for half in (90.0, 180.0, 360.0)
        }

    @staticmethod
    def row(sb_home, sb_draw, sb_away, pm_triplet):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
            home_team="Alpha",
            away_team="Beta",
            sb_home=sb_home,
            sb_draw=sb_draw,
            sb_away=sb_away,
            pm_fair_home=pm_triplet[0],
            pm_fair_draw=pm_triplet[1],
            pm_fair_away=pm_triplet[2],
        )
        row.league = "England Premier League"
        return row

    def test_dixon_coles_probabilities_sum_to_one(self):
        p = dixon_coles_probabilities(1.65, 1.05)
        self.assertAlmostEqual(sum(p), 1.0, places=10)
        self.assertTrue(all(0 < x < 1 for x in p))

    def test_current_odds_do_not_change_independent_probability(self):
        states = self.states()
        a = self.row(1.45, 4.80, 8.50, (0.72, 0.18, 0.10))
        b = self.row(3.60, 2.20, 2.05, (0.25, 0.30, 0.45))
        fa = _forecast_from_states(a, self.source(), states)
        fb = _forecast_from_states(b, self.source(), states)
        self.assertIsNotNone(fa)
        self.assertIsNotNone(fb)
        self.assertAlmostEqual(fa.home_probability, fb.home_probability, places=12)
        self.assertAlmostEqual(fa.draw_probability, fb.draw_probability, places=12)
        self.assertAlmostEqual(fa.away_probability, fb.away_probability, places=12)

    def test_market_probability_is_preserved_only_as_diagnostic(self):
        states = self.states()
        row = self.row(1.95, 3.60, 4.00, (0.55, 0.25, 0.20))
        edge_model.calculate_match_edge(row, 4.0)
        market_before = row.model_fair_home
        forecast = _forecast_from_states(row, self.source(), states)
        self.assertIsNotNone(forecast)
        apply_independent_forecasts([row], {row.match_name: forecast}, 4.0)
        self.assertAlmostEqual(row.market_reference_home, market_before, places=12)
        self.assertAlmostEqual(row.model_fair_home, forecast.home_probability, places=12)
        self.assertAlmostEqual(row.edge_outcomes["HOME"].model_probability, forecast.home_probability, places=12)

    def test_ev_changes_with_price_while_probability_stays_frozen(self):
        states = self.states()
        row = self.row(1.90, 3.60, 4.10, (0.55, 0.25, 0.20))
        edge_model.calculate_match_edge(row, 4.0)
        forecast = _forecast_from_states(row, self.source(), states)
        self.assertIsNotNone(forecast)
        apply_independent_forecasts([row], {row.match_name: forecast}, 4.0)
        first_p = row.edge_outcomes["HOME"].model_probability
        d1 = decision_for_side(row, "HOME", 4.0)
        self.assertIsNotNone(d1)
        row.sb_home = 2.25
        d2 = decision_for_side(row, "HOME", 4.0)
        self.assertIsNotNone(d2)
        self.assertAlmostEqual(d1.model_probability, d2.model_probability, places=12)
        self.assertAlmostEqual(first_p, d2.model_probability, places=12)
        self.assertGreater(d2.model_ev_pct, d1.model_ev_pct)

    def test_unsupported_forecast_removes_headline_ev(self):
        row = self.row(2.00, 3.40, 3.70, (0.50, 0.27, 0.23))
        edge_model.calculate_match_edge(row, 4.0)
        apply_independent_forecasts([row], {}, 4.0)
        self.assertIsNone(row.model_fair_home)
        self.assertIsNone(row.edge_outcomes["HOME"].model_ev_pct)
        self.assertEqual(row.edge_signal, "NO INDEPENDENT MODEL")

    def test_conservative_component_is_never_above_central_for_same_side(self):
        states = self.states()
        row = self.row(2.00, 3.40, 3.70, (0.50, 0.27, 0.23))
        forecast = _forecast_from_states(row, self.source(), states)
        self.assertIsNotNone(forecast)
        self.assertLessEqual(forecast.conservative_home, forecast.home_probability + 1e-12)
        self.assertLessEqual(forecast.conservative_draw, forecast.draw_probability + 1e-12)
        self.assertLessEqual(forecast.conservative_away, forecast.away_probability + 1e-12)


if __name__ == "__main__":
    unittest.main()
