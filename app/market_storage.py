from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable

from config import DB_FILE
from engine import CombinedMatch
from storage import event_key


SCHEMA = """
CREATE TABLE IF NOT EXISTS market_context_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    event_key TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    sb_ah_home_line REAL,
    sb_ah_home_odds REAL,
    sb_ah_away_line REAL,
    sb_ah_away_odds REAL,
    sb_total_line REAL,
    sb_total_over REAL,
    sb_total_under REAL,
    pin_home REAL,
    pin_draw REAL,
    pin_away REAL,
    pin_fair_home REAL,
    pin_fair_draw REAL,
    pin_fair_away REAL,
    pin_ev_home_pct REAL,
    pin_ev_draw_pct REAL,
    pin_ev_away_pct REAL,
    pin_ah_home_line REAL,
    pin_ah_home_odds REAL,
    pin_ah_away_line REAL,
    pin_ah_away_odds REAL,
    pin_total_line REAL,
    pin_total_over REAL,
    pin_total_under REAL,
    consensus_home REAL,
    consensus_draw REAL,
    consensus_away REAL,
    consensus_ev_home_pct REAL,
    consensus_ev_draw_pct REAL,
    consensus_ev_away_pct REAL,
    reference_max_diff_pp REAL,
    reference_quality TEXT,
    sharp_check TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_context_event
    ON market_context_snapshots(event_key, captured_at DESC);
"""

FIELDS = [
    "captured_at", "event_key", "kickoff", "home_team", "away_team",
    "sb_ah_home_line", "sb_ah_home_odds", "sb_ah_away_line", "sb_ah_away_odds",
    "sb_total_line", "sb_total_over", "sb_total_under",
    "pin_home", "pin_draw", "pin_away",
    "pin_fair_home", "pin_fair_draw", "pin_fair_away",
    "pin_ev_home_pct", "pin_ev_draw_pct", "pin_ev_away_pct",
    "pin_ah_home_line", "pin_ah_home_odds", "pin_ah_away_line", "pin_ah_away_odds",
    "pin_total_line", "pin_total_over", "pin_total_under",
    "consensus_home", "consensus_draw", "consensus_away",
    "consensus_ev_home_pct", "consensus_ev_draw_pct", "consensus_ev_away_pct",
    "reference_max_diff_pp", "reference_quality", "sharp_check",
]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    return con


def save_market_context(rows: Iterable[CombinedMatch]) -> int:
    captured = datetime.now().astimezone().isoformat(timespec="seconds")
    values = []
    for r in rows:
        values.append((
            captured,
            event_key(r),
            r.kickoff.isoformat(),
            r.home_team,
            r.away_team,
            getattr(r, "sb_ah_home_line", None),
            getattr(r, "sb_ah_home_odds", None),
            getattr(r, "sb_ah_away_line", None),
            getattr(r, "sb_ah_away_odds", None),
            getattr(r, "sb_total_line", None),
            getattr(r, "sb_total_over", None),
            getattr(r, "sb_total_under", None),
            getattr(r, "pin_home", None),
            getattr(r, "pin_draw", None),
            getattr(r, "pin_away", None),
            getattr(r, "pin_fair_home", None),
            getattr(r, "pin_fair_draw", None),
            getattr(r, "pin_fair_away", None),
            getattr(r, "pin_ev_home_pct", None),
            getattr(r, "pin_ev_draw_pct", None),
            getattr(r, "pin_ev_away_pct", None),
            getattr(r, "pin_ah_home_line", None),
            getattr(r, "pin_ah_home_odds", None),
            getattr(r, "pin_ah_away_line", None),
            getattr(r, "pin_ah_away_odds", None),
            getattr(r, "pin_total_line", None),
            getattr(r, "pin_total_over", None),
            getattr(r, "pin_total_under", None),
            getattr(r, "consensus_home", None),
            getattr(r, "consensus_draw", None),
            getattr(r, "consensus_away", None),
            getattr(r, "consensus_ev_home_pct", None),
            getattr(r, "consensus_ev_draw_pct", None),
            getattr(r, "consensus_ev_away_pct", None),
            getattr(r, "reference_max_diff_pp", None),
            getattr(r, "reference_quality", None),
            getattr(r, "sharp_check", None),
        ))
    if not values:
        return 0
    placeholders = ",".join("?" for _ in FIELDS)
    with connect() as con:
        con.executemany(
            f"INSERT INTO market_context_snapshots ({','.join(FIELDS)}) VALUES ({placeholders})",
            values,
        )
        con.commit()
    return len(values)


def context_snapshot_count() -> int:
    with connect() as con:
        row = con.execute("SELECT COUNT(*) FROM market_context_snapshots").fetchone()
    return int(row[0] if row else 0)
