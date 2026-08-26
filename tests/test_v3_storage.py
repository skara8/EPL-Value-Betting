import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import engine
import v3_storage
from strategy_v3 import V3Decision


class V3StorageTests(unittest.TestCase):
    def test_live_snapshot_round_trip_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db = v3_storage.DB_FILE
            v3_storage.DB_FILE = Path(tmp) / "research.db"
            try:
                row = engine.CombinedMatch(
                    kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
                    home_team="Alpha",
                    away_team="Beta",
                    sb_home=2.0,
                    sb_draw=3.5,
                    sb_away=4.0,
                )
                row.league = "England Premier League"
                row.market_reference_home = 0.49
                row.market_reference_draw = 0.27
                row.market_reference_away = 0.24
                row.independent_v3 = SimpleNamespace(
                    league_key="ENG-PL",
                    home_probability=0.52, draw_probability=0.26, away_probability=0.22,
                    stress_home=0.50, stress_draw=0.25, stress_away=0.20,
                    dc_home=0.53, dc_draw=0.25, dc_away=0.22,
                    elo_home=0.50, elo_draw=0.28, elo_away=0.22,
                    short_home=0.54, short_draw=0.25, short_away=0.21,
                    long_home=0.51, long_draw=0.26, long_away=0.23,
                    components=("DIXON-COLES", "ELO", "SHORT-DECAY", "LONG-DECAY"),
                    lambda_home=1.6, lambda_away=1.1, model_spread_pp=4.0,
                    confidence="MEDIUM", history_matches=800,
                    home_history_matches=70, away_history_matches=68,
                    challenger_home=0.525, challenger_draw=0.255, challenger_away=0.220,
                    challenger_eta=0.025, challenger_annual_shrink=0.90,
                    challenger_validation_logloss=1.01, challenger_gap_pp=0.5,
                )
                quote = SimpleNamespace(
                    source="Sportsbet", decimal_odds=2.0,
                    received_at="2026-08-26T00:00:00+00:00",
                    market_timestamp="2026-08-25T23:59:00+00:00",
                    event_id="abc", match_confidence=1.0,
                )
                row.price_shop = SimpleNamespace(
                    event_fingerprint="eng|alpha|beta",
                    quotes={"HOME": [quote], "DRAW": [], "AWAY": []},
                )
                decision = V3Decision(
                    match_name=row.match_name, league=row.league, side="HOME", selection="Alpha",
                    quote_source="Sportsbet", quote_odds=2.0, model_probability=0.52,
                    fair_odds=1 / 0.52, model_ev_pct=4.0, stress_probability=0.50,
                    stress_ev_pct=0.0, component_count=4, model_spread_pp=4.0,
                    confidence="MEDIUM", status="RESEARCH +EV — UNVALIDATED",
                    reason="test", validation_grade="UNVALIDATED",
                    market_probability=0.49, market_gap_pp=3.0,
                    challenger_probability=0.525, challenger_gap_pp=0.5,
                )

                saved = v3_storage.save_v3_live_snapshot([row], [decision])
                self.assertEqual(saved, (1, 1, 1))
                self.assertEqual(v3_storage.v3_counts(), (1, 1, 1, 0))
            finally:
                v3_storage.DB_FILE = old_db


if __name__ == "__main__":
    unittest.main()
