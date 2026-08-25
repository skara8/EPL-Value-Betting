import os
import sys
import unittest
from datetime import date, datetime
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v14_runtime_hook  # noqa: F401
import engine
import multileague_data as multi
from edge_model import enrich_edge_model


class LeagueDiscoveryTests(unittest.TestCase):
    def setUp(self):
        for slot in multi._cache.values():
            slot["time"] = 0.0
            slot["value"] = None

    def test_sportsbet_league_endpoint_is_authoritative_gate(self):
        responses = [
            {
                "hasNextPage": False,
                "leagues": [
                    {"league": "Premier League", "eventCount": 10},
                    {"league": "La Liga", "eventCount": 10},
                ],
            },
            {
                "hasNextPage": False,
                "events": [
                    self._event("Premier League", "Arsenal", "Chelsea", "2026-08-29T14:00:00Z"),
                    self._event("La Liga", "Real Madrid", "Barcelona", "2026-08-29T18:00:00Z"),
                    # Must be rejected because it was not in Sportsbet's league catalogue.
                    self._event("Imaginary League", "Alpha", "Beta", "2026-08-29T19:00:00Z"),
                ],
            },
            {"hasNextPage": False, "events": []},
            {"hasNextPage": False, "events": []},
        ]

        with patch.object(engine, "get_json", side_effect=responses):
            source = multi.fetch_multileague_sources(
                "test-key", date(2026, 8, 29), date(2026, 8, 30)
            )

        leagues = {x.name for x in source["leagues"]}
        self.assertEqual(leagues, {"Premier League", "La Liga"})
        matches = source["sportsbet"].matches
        self.assertEqual(len(matches), 2)
        self.assertEqual({getattr(x, "league") for x in matches}, {"Premier League", "La Liga"})

    def test_polymarket_only_fixture_never_enters_analysis(self):
        ko = datetime(2026, 8, 29, 14, 0, tzinfo=engine.UTC)
        sb = engine.ProviderMatch("Sportsbet", ko, "Arsenal", "Chelsea", 2.20, 3.50, 3.10)
        setattr(sb, "league", "Premier League")
        pm_match = engine.ProviderMatch("Polymarket", ko, "Arsenal", "Chelsea", 2.30, 3.55, 3.20)
        setattr(pm_match, "league", "Premier League")
        pm_only = engine.ProviderMatch("Polymarket", ko, "Other", "Fixture", 2.0, 3.0, 4.0)
        setattr(pm_only, "league", "Other League")

        rows = multi.combine_sportsbet_catalogue([sb], [pm_match, pm_only], 4.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].home_team, "Arsenal")
        self.assertEqual(getattr(rows[0], "league"), "Premier League")

    def test_negative_ev_can_still_be_identified_as_highest_available(self):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 14, 0, tzinfo=engine.UTC),
            home_team="Arsenal",
            away_team="Chelsea",
            sb_home=1.80,
            sb_draw=3.80,
            sb_away=5.00,
            pm_home=1.72,
            pm_draw=4.00,
            pm_away=5.40,
            match_status="Matched",
        )
        engine.add_strategy_analysis(row, 4.0)
        enrich_edge_model([row], 4.0)
        values = [edge.model_ev_pct for edge in row.edge_outcomes.values() if edge.model_ev_pct is not None]
        self.assertTrue(values)
        self.assertLess(max(values), 4.0)
        # The dashboard fallback is allowed to display max(values), but it is
        # not reclassified as a recommendation.
        self.assertIsInstance(max(values), float)

    @staticmethod
    def _event(league, home, away, start):
        return {
            "sport": "soccer",
            "league": league,
            "country": "Test",
            "home": home,
            "away": away,
            "live": False,
            "startTime": start,
            "markets": [
                {
                    "canonicalMarket": "MATCH_RESULT",
                    "period": "FULL_TIME",
                    "isActive": True,
                    "selections": [
                        {"canonicalOutcome": "HOME", "name": home, "decimal": 2.10},
                        {"canonicalOutcome": "DRAW", "name": "Draw", "decimal": 3.40},
                        {"canonicalOutcome": "AWAY", "name": away, "decimal": 3.50},
                    ],
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
