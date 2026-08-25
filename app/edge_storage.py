from __future__ import annotations

from datetime import datetime
from typing import Iterable

from config import DB_FILE
from engine import CombinedMatch
from storage import event_key


SCHEMA = """
CREATE TABLE IF NOT EXISTS edge_model_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    event_key TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    side TEXT NOT NULL,
    selection_name TEXT NOT NULL,
    sb_odds REAL,
    break_even_probability REAL,
    sb_raw_probability REAL,
    sb_devig_probability REAL,
    pm_probability REAL,
    pinnacle_probability REAL,
    ah_probability REAL,
    model_probability REAL,
    conservative_probability REAL,
    model_fair_odds REAL,
    price_edge_pp REAL,
    sportsbet_residual_pp REAL,
    model_ev_pct REAL,
    conservative_ev_pct REAL,
    required_odds REAL,
    external_disagreement_pp REAL,
    bias_tags TEXT,
    confidence TEXT,
    signal TEXT,
    source_count INTEGER,
    source_names TEXT,
    ah_model_source TEXT,
    ah_model_fit_error_pct REAL,
    ah_lambda_home REAL,
    ah_lambda_away REAL
);
CREATE INDEX IF NOT EXISTS idx_edge_event ON edge_model_snapshots(event_key, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_edge_signal ON edge_model_snapshots(signal, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_edge_side ON edge_model_snapshots(side, captured_at DESC);
"""


def _connect():
    import sqlite3
    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    return con


def save_edge_snapshot(rows: Iterable[CombinedMatch]) -> int:
    captured = datetime.now().astimezone().isoformat(timespec="seconds")
    values = []
    for row in rows:
        outcomes = getattr(row, "edge_outcomes", {})
        for side, edge in outcomes.items():
            values.append((
                captured,
                event_key(row),
                row.kickoff.isoformat(),
                row.home_team,
                row.away_team,
                side,
                edge.name,
                edge.sportsbet_odds,
                edge.break_even_probability,
                edge.sportsbet_raw_probability,
                edge.sportsbet_devig_probability,
                edge.polymarket_probability,
                edge.pinnacle_probability,
                edge.ah_probability,
                edge.model_probability,
                edge.conservative_probability,
                edge.model_fair_odds,
                edge.price_edge_pp,
                edge.sportsbet_residual_pp,
                edge.model_ev_pct,
                edge.conservative_ev_pct,
                edge.required_odds_for_threshold,
                edge.external_disagreement_pp,
                " | ".join(edge.bias_tags),
                edge.confidence,
                edge.signal,
                edge.source_count,
                " | ".join(getattr(row, "edge_source_names", ())),
                getattr(row, "ah_model_source", None),
                getattr(row, "ah_model_fit_error_pct", None),
                getattr(row, "ah_model_lambda_home", None),
                getattr(row, "ah_model_lambda_away", None),
            ))

    if not values:
        return 0

    placeholders = ",".join("?" for _ in range(32))
    columns = """
        captured_at,event_key,kickoff,home_team,away_team,side,selection_name,
        sb_odds,break_even_probability,sb_raw_probability,sb_devig_probability,
        pm_probability,pinnacle_probability,ah_probability,model_probability,
        conservative_probability,model_fair_odds,price_edge_pp,sportsbet_residual_pp,
        model_ev_pct,conservative_ev_pct,required_odds,external_disagreement_pp,
        bias_tags,confidence,signal,source_count,source_names,ah_model_source,
        ah_model_fit_error_pct,ah_lambda_home,ah_lambda_away
    """.replace("\n", "").replace(" ", "")

    with _connect() as con:
        con.executemany(
            f"INSERT INTO edge_model_snapshots ({columns}) VALUES ({placeholders})",
            values,
        )
        con.commit()
    return len(values)


def edge_snapshot_count() -> int:
    with _connect() as con:
        return int(con.execute("SELECT COUNT(*) FROM edge_model_snapshots").fetchone()[0])
