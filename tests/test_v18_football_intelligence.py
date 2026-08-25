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
from context_model import ContextInputs
from decision_model_v18 import build_bet_ideas_v18
from edge_model import calculate_match_edge
from football_intelligence import (
    ExpectedXIPlayer,
    LeagueMatchRef,
    MatchIntelligence,
    MatchPerformance,
    TacticalProfile,
    TeamIntelligence,
    build_expected_xi,
    build_tactical_profile,
    context_adjustment_v18,
    extract_league_match_refs,
    parse_fpl_players,
    parse_match_detail,
)


class FotMobParsingTests(unittest.TestCase):
    def test_extract_league_match_refs(self):
        payload = {
            "matches": {
                "allMatches": [
                    {
                        "id": 123,
                        "home": {"id": 1, "name": "Arsenal", "score": 2},
                        "away": {"id": 2, "name": "Chelsea", "score": 1},
                        "status": {"finished": True, "utcTime": "2026-08-20T19:00:00Z"},
                    }
                ]
            }
        }
        refs = extract_league_match_refs(payload)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].home_team, "Arsenal")
        self.assertEqual(refs[0].away_team, "Chelsea")
        self.assertTrue(refs[0].finished)
        self.assertEqual(refs[0].home_score, 2)

    def test_parse_match_detail_stats_and_lineup(self):
        ref = LeagueMatchRef(
            match_id=123,
            kickoff=engine.parse_datetime("2026-08-20T19:00:00Z"),
            home_team="Arsenal",
            away_team="Chelsea",
            finished=True,
            home_score=2,
            away_score=1,
        )
        payload = {
            "content": {
                "stats": {
                    "Periods": {
                        "All": {
                            "stats": [
                                {"title": "Expected goals (xG)", "stats": [1.85, 0.72]},
                                {"title": "Total shots", "stats": [16, 8]},
                                {"title": "Shots on target", "stats": [6, 3]},
                                {"title": "Big chances", "stats": [4, 1]},
                                {"title": "Ball possession", "stats": [61, 39]},
                                {"title": "Corners", "stats": [7, 2]},
                            ]
                        }
                    }
                },
                "lineup": {
                    "lineups": [
                        {
                            "teamName": "Arsenal",
                            "formation": "4-3-3",
                            "players": [
                                {"player": {"name": "Example Keeper"}, "position": "GK", "isStarter": True, "rating": 7.1}
                            ],
                        },
                        {
                            "teamName": "Chelsea",
                            "formation": "4-2-3-1",
                            "players": [
                                {"player": {"name": "Example Striker"}, "position": "ST", "isStarter": True, "rating": 6.4}
                            ],
                        },
                    ]
                },
            }
        }
        home, away = parse_match_detail(ref, payload)
        self.assertAlmostEqual(home.xg_for, 1.85)
        self.assertAlmostEqual(away.xg_for, 0.72)
        self.assertEqual(home.formation, "4-3-3")
        self.assertEqual(home.players[0].position, "GKP")
        self.assertEqual(away.players[0].position, "FWD")


class FPLExpectedXITests(unittest.TestCase):
    def _bootstrap(self):
        players = []
        for idx in range(15):
            if idx == 0:
                pos = 1
            elif idx < 6:
                pos = 2
            elif idx < 12:
                pos = 3
            else:
                pos = 4
            players.append(
                {
                    "team": 1,
                    "web_name": f"Player{idx}",
                    "element_type": pos,
                    "status": "a",
                    "chance_of_playing_next_round": 100,
                    "now_cost": 50 + idx,
                    "minutes": 450,
                    "starts": 5,
                    "points_per_game": "4.5",
                    "expected_goal_involvements_per_90": "0.30",
                    "clean_sheets": 2,
                    "saves": 10,
                    "goals_scored": 1,
                    "assists": 1,
                    "news": "",
                }
            )
        players.append(
            {
                "team": 1,
                "web_name": "Departed",
                "element_type": 3,
                "status": "u",
                "chance_of_playing_next_round": 0,
                "now_cost": 100,
                "minutes": 450,
                "starts": 5,
                "points_per_game": "7.0",
                "news": "Has joined Barcelona permanently",
            }
        )
        return {"teams": [{"id": 1, "name": "Arsenal"}], "elements": players}

    def test_departed_player_not_in_current_pool(self):
        parsed = parse_fpl_players(self._bootstrap())
        names = {p.name for p in parsed["Arsenal"]}
        self.assertNotIn("Departed", names)

    def test_expected_xi_has_eleven_players(self):
        parsed = parse_fpl_players(self._bootstrap())
        xi = build_expected_xi("Arsenal", [], parsed["Arsenal"])
        self.assertEqual(len(xi), 11)
        self.assertEqual(sum(1 for p in xi if p.position == "GKP"), 1)


class TacticalProfileTests(unittest.TestCase):
    def test_control_profile_detected(self):
        matches = [
            MatchPerformance(
                match_id=i,
                kickoff=None,
                opponent="Chelsea",
                venue="HOME",
                goals_for=2,
                goals_against=0,
                xg_for=2.0,
                xg_against=0.7,
                shots_for=16,
                shots_against=7,
                shots_on_target_for=6,
                shots_on_target_against=2,
                possession_for=62,
                possession_against=38,
                corners_for=7,
                corners_against=2,
            )
            for i in range(4)
        ]
        profile = build_tactical_profile(matches)
        self.assertIn("possession control", profile.labels)
        self.assertIn("high shot volume", profile.labels)
        self.assertGreater(profile.xg_per_shot, 0.1)


class V18DecisionTests(unittest.TestCase):
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

    def _intel(self, row, rating=3.0):
        team = TeamIntelligence(team=row.home_team, expected_xi=[], xi_strength=60, tactical=TacticalProfile(), data_quality="HIGH")
        away = TeamIntelligence(team=row.away_team, expected_xi=[], xi_strength=60, tactical=TacticalProfile(), data_quality="HIGH")
        return MatchIntelligence(
            match_name=row.match_name,
            home_team=row.home_team,
            away_team=row.away_team,
            home=team,
            away=away,
            xi_rating=rating,
            recent_form_rating=rating,
            tactical_rating=rating,
            rest_rating=rating,
            overall_rating=rating,
            data_quality="HIGH",
            reasons=("Artificial test lean",),
        )

    def test_v18_context_remains_capped(self):
        row = self._row()
        adjustment = context_adjustment_v18(row, ContextInputs(), {}, self._intel(row), max_shift_pp=1.5)
        self.assertIsNotNone(adjustment)
        largest = max(abs(adjustment.home_shift_pp), abs(adjustment.draw_shift_pp), abs(adjustment.away_shift_pp))
        self.assertLessEqual(largest, 1.50001)

    def test_football_data_cannot_rescue_negative_base_ev(self):
        row = self._row()
        row.edge_outcomes["HOME"].model_ev_pct = -1.0
        row.model_fair_home = 0.19
        ideas = build_bet_ideas_v18(
            [row],
            intelligence_by_match={row.match_name: self._intel(row, 3.0)},
            max_context_shift_pp=3.0,
            min_ev_pct=0.1,
        )
        self.assertFalse(any(i.side == "HOME" for i in ideas))


if __name__ == "__main__":
    unittest.main()
