from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from config import DB_FILE, EXPORT_DIR
from engine import CombinedMatch


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    event_key TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    sb_home REAL,
    sb_draw REAL,
    sb_away REAL,
    pm_home REAL,
    pm_draw REAL,
    pm_away REAL,
    pm_fair_home REAL,
    pm_fair_draw REAL,
    pm_fair_away REAL,
    ev_home_pct REAL,
    ev_draw_pct REAL,
    ev_away_pct REAL,
    best_selection TEXT,
    best_ev_pct REAL,
    strategy_flag TEXT,
    away_favourite TEXT,
    sportsbet_overround_pct REAL,
    pm_ask_overround_pct REAL,
    pm_volume REAL,
    pm_liquidity REAL,
    sportsbet_updated TEXT,
    polymarket_updated TEXT,
    match_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_captured ON snapshots(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_event ON snapshots(event_key, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_signal ON snapshots(strategy_flag, captured_at DESC);
"""

FIELDS = [
    "captured_at", "kickoff", "event_key", "home_team", "away_team",
    "sb_home", "sb_draw", "sb_away", "pm_home", "pm_draw", "pm_away",
    "pm_fair_home", "pm_fair_draw", "pm_fair_away",
    "ev_home_pct", "ev_draw_pct", "ev_away_pct",
    "best_selection", "best_ev_pct", "strategy_flag", "away_favourite",
    "sportsbet_overround_pct", "pm_ask_overround_pct", "pm_volume",
    "pm_liquidity", "sportsbet_updated", "polymarket_updated", "match_status",
]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def event_key(row: CombinedMatch) -> str:
    return f"{row.kickoff.isoformat()}|{row.home_team.strip().lower()}|{row.away_team.strip().lower()}"


def save_snapshot(rows: Iterable[CombinedMatch]) -> int:
    captured = datetime.now().astimezone().isoformat(timespec="seconds")
    values = []
    for r in rows:
        values.append((
            captured, r.kickoff.isoformat(), event_key(r), r.home_team, r.away_team,
            r.sb_home, r.sb_draw, r.sb_away, r.pm_home, r.pm_draw, r.pm_away,
            r.pm_fair_home, r.pm_fair_draw, r.pm_fair_away,
            r.ev_home_pct, r.ev_draw_pct, r.ev_away_pct,
            r.best_selection, r.best_ev_pct, r.strategy_flag, r.away_favourite,
            r.sportsbet_overround_pct, r.polymarket_sum_minus_100_pct,
            r.polymarket_volume, r.polymarket_liquidity,
            r.sportsbet_updated, r.polymarket_updated, r.match_status,
        ))
    if not values:
        return 0
    placeholders = ",".join("?" for _ in FIELDS)
    with connect() as con:
        con.executemany(
            f"INSERT INTO snapshots ({','.join(FIELDS)}) VALUES ({placeholders})",
            values,
        )
        con.commit()
    return len(values)


def recent_snapshots(limit: int = 500) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()


def latest_event_snapshots(limit: int = 200) -> list[sqlite3.Row]:
    sql = """
    SELECT s.*
    FROM snapshots s
    JOIN (
        SELECT event_key, MAX(id) AS max_id
        FROM snapshots
        GROUP BY event_key
    ) x ON x.max_id = s.id
    ORDER BY s.kickoff ASC
    LIMIT ?
    """
    with connect() as con:
        return con.execute(sql, (int(limit),)).fetchall()


def history_summary() -> dict[str, int]:
    with connect() as con:
        total = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        events = con.execute("SELECT COUNT(DISTINCT event_key) FROM snapshots").fetchone()[0]
        candidates = con.execute(
            "SELECT COUNT(*) FROM snapshots WHERE strategy_flag IN ('VALUE','AWAY-FAV VALUE')"
        ).fetchone()[0]
        away = con.execute(
            "SELECT COUNT(*) FROM snapshots WHERE strategy_flag='AWAY-FAV VALUE'"
        ).fetchone()[0]
    return {
        "snapshots": int(total),
        "events": int(events),
        "candidates": int(candidates),
        "away_candidates": int(away),
    }


def export_history(path: Optional[Path] = None) -> Path:
    if path is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = EXPORT_DIR / f"epl-odds-history-{stamp}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = recent_snapshots(limit=1_000_000)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id"] + FIELDS)
        for row in reversed(rows):
            writer.writerow([row["id"]] + [row[name] for name in FIELDS])
    return path


def clear_history() -> int:
    with connect() as con:
        count = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        con.execute("DELETE FROM snapshots")
        con.commit()
    return int(count)


def first_last_price(event_key_value: str, selection: str) -> tuple[Optional[float], Optional[float]]:
    column = {"HOME": "sb_home", "DRAW": "sb_draw", "AWAY": "sb_away"}.get(selection.upper())
    if not column:
        return None, None
    with connect() as con:
        first = con.execute(
            f"SELECT {column} FROM snapshots WHERE event_key=? AND {column} IS NOT NULL ORDER BY id ASC LIMIT 1",
            (event_key_value,),
        ).fetchone()
        last = con.execute(
            f"SELECT {column} FROM snapshots WHERE event_key=? AND {column} IS NOT NULL ORDER BY id DESC LIMIT 1",
            (event_key_value,),
        ).fetchone()
    return (
        float(first[0]) if first and first[0] is not None else None,
        float(last[0]) if last and last[0] is not None else None,
    )
