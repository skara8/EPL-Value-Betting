from __future__ import annotations

from datetime import datetime
from typing import Optional

from config import DB_FILE
from context_model import ContextAdjustment, ContextInputs
from engine import CombinedMatch
from storage import event_key


SCHEMA = """
CREATE TABLE IF NOT EXISTS context_research_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    event_key TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_transfer_spend_m REAL,
    away_transfer_spend_m REAL,
    home_manager TEXT,
    away_manager TEXT,
    player_lineup_rating REAL,
    recent_performance_rating REAL,
    tactical_matchup_rating REAL,
    manager_coaching_rating REAL,
    transfer_squad_rating REAL,
    schedule_rest_rating REAL,
    auto_availability_rating REAL,
    weighted_context_score REAL,
    max_shift_pp REAL,
    base_home REAL,
    base_draw REAL,
    base_away REAL,
    adjusted_home REAL,
    adjusted_draw REAL,
    adjusted_away REAL,
    adjusted_ev_home REAL,
    adjusted_ev_draw REAL,
    adjusted_ev_away REAL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_context_event ON context_research_snapshots(event_key, captured_at DESC);
"""


def _connect():
    import sqlite3
    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    return con


def save_context_snapshot(
    row: CombinedMatch,
    inputs: ContextInputs,
    adjustment: ContextAdjustment,
    adjusted_evs: dict[str, Optional[float]],
) -> int:
    captured = datetime.now().astimezone().isoformat(timespec="seconds")
    with _connect() as con:
        con.execute(
            """
            INSERT INTO context_research_snapshots (
                captured_at,event_key,kickoff,home_team,away_team,
                home_transfer_spend_m,away_transfer_spend_m,home_manager,away_manager,
                player_lineup_rating,recent_performance_rating,tactical_matchup_rating,
                manager_coaching_rating,transfer_squad_rating,schedule_rest_rating,
                auto_availability_rating,weighted_context_score,max_shift_pp,
                base_home,base_draw,base_away,adjusted_home,adjusted_draw,adjusted_away,
                adjusted_ev_home,adjusted_ev_draw,adjusted_ev_away,notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                captured,
                event_key(row),
                row.kickoff.isoformat(),
                row.home_team,
                row.away_team,
                inputs.home_transfer_spend_m,
                inputs.away_transfer_spend_m,
                inputs.home_manager,
                inputs.away_manager,
                inputs.player_lineup,
                inputs.recent_performance,
                inputs.tactical_matchup,
                inputs.manager_coaching,
                inputs.transfer_squad,
                inputs.schedule_rest,
                adjustment.auto_availability_rating,
                adjustment.weighted_score,
                adjustment.max_shift_pp,
                getattr(row, "model_fair_home", None),
                getattr(row, "model_fair_draw", None),
                getattr(row, "model_fair_away", None),
                adjustment.home_probability,
                adjustment.draw_probability,
                adjustment.away_probability,
                adjusted_evs.get("HOME"),
                adjusted_evs.get("DRAW"),
                adjusted_evs.get("AWAY"),
                inputs.notes,
            ),
        )
        con.commit()
    return 1


def context_snapshot_count() -> int:
    with _connect() as con:
        return int(con.execute("SELECT COUNT(*) FROM context_research_snapshots").fetchone()[0])
