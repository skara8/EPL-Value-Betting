import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import v3_clv


class V3CLVTests(unittest.TestCase):
    def test_uses_only_near_close_and_devigs_pinnacle(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db = v3_clv.DB_FILE
            v3_clv.DB_FILE = Path(tmp) / "research.db"
            try:
                kickoff = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
                decision_at = kickoff - timedelta(hours=6)
                near_close = kickoff - timedelta(minutes=20)
                old_close = kickoff - timedelta(hours=3)

                con = sqlite3.connect(v3_clv.DB_FILE)
                con.executescript(v3_clv.SCHEMA)
                con.execute(
                    """CREATE TABLE v3_decision_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        captured_at TEXT,event_key TEXT,kickoff_utc TEXT,side TEXT,
                        quote_source TEXT,quote_odds REAL,model_probability REAL,status TEXT
                    )"""
                )
                con.execute(
                    """CREATE TABLE market_context_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        captured_at TEXT,event_key TEXT,pin_home REAL,pin_draw REAL,pin_away REAL
                    )"""
                )
                con.execute(
                    "INSERT INTO v3_decision_snapshots (captured_at,event_key,kickoff_utc,side,quote_source,quote_odds,model_probability,status) VALUES (?,?,?,?,?,?,?,?)",
                    (decision_at.isoformat(), "event-1", kickoff.isoformat(), "HOME", "TAB", 2.10, 0.50, "RESEARCH +EV — UNVALIDATED"),
                )
                # An older price exists but must be ignored because it is more
                # than 60 minutes before kickoff.
                con.execute(
                    "INSERT INTO market_context_snapshots (captured_at,event_key,pin_home,pin_draw,pin_away) VALUES (?,?,?,?,?)",
                    (old_close.isoformat(), "event-1", 1.80, 4.00, 5.00),
                )
                # This is the legitimate near-close reference.
                con.execute(
                    "INSERT INTO market_context_snapshots (captured_at,event_key,pin_home,pin_draw,pin_away) VALUES (?,?,?,?,?)",
                    (near_close.isoformat(), "event-1", 2.00, 3.50, 4.20),
                )
                con.commit()
                con.close()

                self.assertEqual(v3_clv.refresh_v3_clv_evaluations(), 1)
                summary = v3_clv.v3_clv_summary()
                self.assertEqual(summary.samples, 1)
                self.assertIsNotNone(summary.average_price_clv_pct)
                self.assertEqual(summary.edge_grade, "INSUFFICIENT_CLV")

                con = sqlite3.connect(v3_clv.DB_FILE)
                minutes = con.execute("SELECT close_minutes_before_kickoff FROM v3_clv_evaluations").fetchone()[0]
                con.close()
                self.assertAlmostEqual(minutes, 20.0, places=6)
            finally:
                v3_clv.DB_FILE = old_db


if __name__ == "__main__":
    unittest.main()
