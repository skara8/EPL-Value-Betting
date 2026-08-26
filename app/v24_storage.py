from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable

from config import DB_FILE
from engine import CombinedMatch
from storage import event_key
from strategy_v24 import V24Decision


SCHEMA = """
CREATE TABLE IF NOT EXISTS v24_independent_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    event_key TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    league TEXT,
    league_key TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    p_home REAL,
    p_draw REAL,
    p_away REAL,
    conservative_home REAL,
    conservative_draw REAL,
    conservative_away REAL,
    dc_home REAL,
    dc_draw REAL,
    dc_away REAL,
    elo_home REAL,
    elo_draw REAL,
    elo_away REAL,
    short_home REAL,
    short_draw REAL,
    short_away REAL,
    long_home REAL,
    long_draw REAL,
    long_away REAL,
    lambda_home REAL,
    lambda_away REAL,
    model_spread_pp REAL,
    history_matches INTEGER,
    home_history_matches INTEGER,
    away_history_matches INTEGER,
    confidence TEXT,
    market_reference_home REAL,
    market_reference_draw REAL,
    market_reference_away REAL
);
CREATE INDEX IF NOT EXISTS idx_v24_snapshot_event ON v24_independent_snapshots(event_key, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_v24_snapshot_league ON v24_independent_snapshots(league_key, captured_at DESC);

CREATE TABLE IF NOT EXISTS v24_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    event_key TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    league TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    side TEXT NOT NULL,
    selection_name TEXT NOT NULL,
    quote_source TEXT NOT NULL,
    quote_odds REAL NOT NULL,
    model_probability REAL NOT NULL,
    conservative_probability REAL NOT NULL,
    fair_odds REAL NOT NULL,
    model_ev_pct REAL NOT NULL,
    robust_ev_pct REAL NOT NULL,
    model_spread_pp REAL,
    component_count INTEGER NOT NULL,
    confidence TEXT,
    status TEXT,
    market_probability REAL,
    market_gap_pp REAL
);
CREATE INDEX IF NOT EXISTS idx_v24_decision_kickoff ON v24_decisions(kickoff DESC);
CREATE INDEX IF NOT EXISTS idx_v24_decision_status ON v24_decisions(status, captured_at DESC);
"""


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    return con


def save_v24_snapshots(rows: Iterable[CombinedMatch], decisions: Iterable[V24Decision]) -> tuple[int, int]:
    rows = list(rows)
    decisions = list(decisions)
    captured = datetime.now().astimezone().isoformat(timespec="seconds")
    row_map = {row.match_name: row for row in rows}
    snapshot_values = []
    for row in rows:
        f = getattr(row, "independent_v24", None)
        if f is None:
            continue
        snapshot_values.append((
            captured, event_key(row), row.kickoff.isoformat(), str(getattr(row, "league", "") or ""),
            f.league_key, row.home_team, row.away_team,
            f.home_probability, f.draw_probability, f.away_probability,
            f.conservative_home, f.conservative_draw, f.conservative_away,
            f.dc_home, f.dc_draw, f.dc_away,
            f.elo_home, f.elo_draw, f.elo_away,
            f.short_home, f.short_draw, f.short_away,
            f.long_home, f.long_draw, f.long_away,
            f.lambda_home, f.lambda_away, f.model_spread_pp,
            f.history_matches, f.home_history_matches, f.away_history_matches, f.confidence,
            getattr(row, "market_reference_home", None), getattr(row, "market_reference_draw", None), getattr(row, "market_reference_away", None),
        ))

    decision_values = []
    for d in decisions:
        row = row_map.get(d.match_name)
        if row is None:
            continue
        decision_values.append((
            captured, event_key(row), row.kickoff.isoformat(), d.league,
            row.home_team, row.away_team, d.side, d.selection,
            d.quote_source, d.quote_odds, d.model_probability, d.conservative_probability,
            d.fair_odds, d.model_ev_pct, d.robust_ev_pct, d.model_spread_pp,
            d.component_count, d.confidence, d.status, d.market_probability, d.market_gap_pp,
        ))

    with _connect() as con:
        if snapshot_values:
            con.executemany(
                """
                INSERT INTO v24_independent_snapshots (
                    captured_at,event_key,kickoff,league,league_key,home_team,away_team,
                    p_home,p_draw,p_away,conservative_home,conservative_draw,conservative_away,
                    dc_home,dc_draw,dc_away,elo_home,elo_draw,elo_away,
                    short_home,short_draw,short_away,long_home,long_draw,long_away,
                    lambda_home,lambda_away,model_spread_pp,history_matches,home_history_matches,
                    away_history_matches,confidence,market_reference_home,market_reference_draw,market_reference_away
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                snapshot_values,
            )
        if decision_values:
            con.executemany(
                """
                INSERT INTO v24_decisions (
                    captured_at,event_key,kickoff,league,home_team,away_team,side,selection_name,
                    quote_source,quote_odds,model_probability,conservative_probability,fair_odds,
                    model_ev_pct,robust_ev_pct,model_spread_pp,component_count,confidence,status,
                    market_probability,market_gap_pp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                decision_values,
            )
        con.commit()
    return len(snapshot_values), len(decision_values)


def v24_counts() -> tuple[int, int]:
    with _connect() as con:
        snapshots = int(con.execute("SELECT COUNT(*) FROM v24_independent_snapshots").fetchone()[0])
        decisions = int(con.execute("SELECT COUNT(*) FROM v24_decisions").fetchone()[0])
    return snapshots, decisions
