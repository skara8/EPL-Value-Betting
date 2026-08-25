from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from config import DB_FILE
from engine import CombinedMatch
from research_models_v22 import ResearchMatchFeatures
from storage import event_key


SCHEMA = """
CREATE TABLE IF NOT EXISTS v22_research_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    kickoff TEXT NOT NULL,
    league TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    first_captured_at TEXT NOT NULL,
    last_captured_at TEXT NOT NULL,
    market_home REAL,
    market_draw REAL,
    market_away REAL,
    elo_home_rating REAL,
    elo_away_rating REAL,
    elo_home REAL,
    elo_draw REAL,
    elo_away REAL,
    poisson_home REAL,
    poisson_draw REAL,
    poisson_away REAL,
    poisson_lambda_home REAL,
    poisson_lambda_away REAL,
    lineup_home REAL,
    lineup_away REAL,
    lineup_diff_pp REAL,
    home_recent_net_xg REAL,
    away_recent_net_xg REAL,
    home_recent_opponent_elo REAL,
    away_recent_opponent_elo REAL,
    market_research_disagreement_pp REAL,
    consensus TEXT,
    history_matches INTEGER,
    data_quality TEXT
);
CREATE INDEX IF NOT EXISTS idx_v22_research_kickoff ON v22_research_features(kickoff DESC);
CREATE INDEX IF NOT EXISTS idx_v22_research_quality ON v22_research_features(data_quality, kickoff DESC);
"""


@dataclass
class V22ResearchSummary:
    snapshots: int = 0
    high_quality: int = 0
    with_elo: int = 0
    with_poisson: int = 0
    with_lineup: int = 0


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    return con


def save_research_features(
    rows: Iterable[CombinedMatch],
    features: dict[str, ResearchMatchFeatures],
) -> int:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    changed = 0
    with _connect() as con:
        for row in rows:
            f = features.get(row.match_name)
            if f is None:
                continue
            values = (
                event_key(row), row.kickoff.isoformat(), str(getattr(row, "league", "") or ""),
                row.home_team, row.away_team, now, now,
                f.market_home, f.market_draw, f.market_away,
                f.elo_home_rating, f.elo_away_rating, f.elo_home, f.elo_draw, f.elo_away,
                f.poisson_home, f.poisson_draw, f.poisson_away,
                f.poisson_lambda_home, f.poisson_lambda_away,
                f.lineup_home, f.lineup_away, f.lineup_diff_pp,
                f.home_recent_net_xg, f.away_recent_net_xg,
                f.home_recent_opponent_elo, f.away_recent_opponent_elo,
                f.market_research_disagreement_pp, f.consensus, f.history_matches, f.data_quality,
            )
            before = con.total_changes
            con.execute(
                """
                INSERT INTO v22_research_features (
                    event_key,kickoff,league,home_team,away_team,first_captured_at,last_captured_at,
                    market_home,market_draw,market_away,elo_home_rating,elo_away_rating,
                    elo_home,elo_draw,elo_away,poisson_home,poisson_draw,poisson_away,
                    poisson_lambda_home,poisson_lambda_away,lineup_home,lineup_away,lineup_diff_pp,
                    home_recent_net_xg,away_recent_net_xg,home_recent_opponent_elo,away_recent_opponent_elo,
                    market_research_disagreement_pp,consensus,history_matches,data_quality
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key) DO UPDATE SET
                    last_captured_at=excluded.last_captured_at,
                    market_home=excluded.market_home,
                    market_draw=excluded.market_draw,
                    market_away=excluded.market_away,
                    elo_home_rating=excluded.elo_home_rating,
                    elo_away_rating=excluded.elo_away_rating,
                    elo_home=excluded.elo_home,
                    elo_draw=excluded.elo_draw,
                    elo_away=excluded.elo_away,
                    poisson_home=excluded.poisson_home,
                    poisson_draw=excluded.poisson_draw,
                    poisson_away=excluded.poisson_away,
                    poisson_lambda_home=excluded.poisson_lambda_home,
                    poisson_lambda_away=excluded.poisson_lambda_away,
                    lineup_home=excluded.lineup_home,
                    lineup_away=excluded.lineup_away,
                    lineup_diff_pp=excluded.lineup_diff_pp,
                    home_recent_net_xg=excluded.home_recent_net_xg,
                    away_recent_net_xg=excluded.away_recent_net_xg,
                    home_recent_opponent_elo=excluded.home_recent_opponent_elo,
                    away_recent_opponent_elo=excluded.away_recent_opponent_elo,
                    market_research_disagreement_pp=excluded.market_research_disagreement_pp,
                    consensus=excluded.consensus,
                    history_matches=excluded.history_matches,
                    data_quality=excluded.data_quality
                """,
                values,
            )
            if con.total_changes > before:
                changed += 1
        con.commit()
    return changed


def research_summary() -> V22ResearchSummary:
    with _connect() as con:
        row = con.execute(
            """
            SELECT
                COUNT(*) AS snapshots,
                SUM(CASE WHEN data_quality='HIGH' THEN 1 ELSE 0 END) AS high_quality,
                SUM(CASE WHEN elo_home IS NOT NULL THEN 1 ELSE 0 END) AS with_elo,
                SUM(CASE WHEN poisson_home IS NOT NULL THEN 1 ELSE 0 END) AS with_poisson,
                SUM(CASE WHEN lineup_home IS NOT NULL AND lineup_away IS NOT NULL THEN 1 ELSE 0 END) AS with_lineup
            FROM v22_research_features
            """
        ).fetchone()
    return V22ResearchSummary(
        snapshots=int(row[0] or 0),
        high_quality=int(row[1] or 0),
        with_elo=int(row[2] or 0),
        with_poisson=int(row[3] or 0),
        with_lineup=int(row[4] or 0),
    )
