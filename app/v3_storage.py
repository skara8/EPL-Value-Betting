from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, Optional

from config import DB_FILE
from engine import CombinedMatch
from model_v3 import FEATURE_SCHEMA_VERSION, MODEL_VERSION, V3Forecast
from storage import event_key
from strategy_v3 import V3Decision
from validation_v3 import WalkForwardReport

SCHEMA = """
CREATE TABLE IF NOT EXISTS v3_forecasts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, decision_time TEXT NOT NULL,
 event_key TEXT NOT NULL, kickoff TEXT NOT NULL, league TEXT, league_key TEXT, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
 model_version TEXT NOT NULL, git_commit TEXT, feature_schema_version TEXT NOT NULL, feature_snapshot_hash TEXT NOT NULL,
 p_home REAL NOT NULL, p_draw REAL NOT NULL, p_away REAL NOT NULL,
 p05_home REAL NOT NULL, p05_draw REAL NOT NULL, p05_away REAL NOT NULL,
 p95_home REAL NOT NULL, p95_draw REAL NOT NULL, p95_away REAL NOT NULL,
 sd_home REAL NOT NULL, sd_draw REAL NOT NULL, sd_away REAL NOT NULL,
 dynamic_home REAL NOT NULL, dynamic_draw REAL NOT NULL, dynamic_away REAL NOT NULL,
 elo_home REAL NOT NULL, elo_draw REAL NOT NULL, elo_away REAL NOT NULL,
 lambda_home REAL NOT NULL, lambda_away REAL NOT NULL, stack_weight_dynamic REAL NOT NULL, calibration_temperature REAL NOT NULL,
 component_spread_pp REAL NOT NULL, bootstrap_samples INTEGER NOT NULL, history_matches INTEGER NOT NULL,
 home_history_matches INTEGER NOT NULL, away_history_matches INTEGER NOT NULL, confidence TEXT,
 promotion_prior_home INTEGER NOT NULL DEFAULT 0, promotion_prior_away INTEGER NOT NULL DEFAULT 0,
 market_reference_home REAL, market_reference_draw REAL, market_reference_away REAL
);
CREATE INDEX IF NOT EXISTS idx_v3_forecasts_event ON v3_forecasts(event_key,captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_v3_forecasts_kickoff ON v3_forecasts(kickoff DESC);

CREATE TABLE IF NOT EXISTS v3_quotes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, event_key TEXT NOT NULL, kickoff TEXT NOT NULL, league TEXT,
 side TEXT NOT NULL, source TEXT NOT NULL, decimal_odds REAL NOT NULL, received_at TEXT, market_timestamp TEXT, age_seconds REAL,
 liquidity REAL, available_size REAL, commission REAL, line REAL, provider_event_id TEXT, match_confidence REAL, is_best INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_v3_quotes_event ON v3_quotes(event_key,captured_at DESC,side,source);

CREATE TABLE IF NOT EXISTS v3_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, event_key TEXT NOT NULL, kickoff TEXT NOT NULL, league TEXT,
 home_team TEXT NOT NULL, away_team TEXT NOT NULL, side TEXT NOT NULL, selection_name TEXT NOT NULL,
 quote_source TEXT NOT NULL, quote_odds REAL NOT NULL, quote_match_confidence REAL NOT NULL,
 model_probability REAL NOT NULL, lower_probability REAL NOT NULL, probability_stdev REAL NOT NULL, fair_odds REAL NOT NULL,
 model_ev_pct REAL NOT NULL, lower_ev_pct REAL NOT NULL, probability_ev_positive REAL NOT NULL,
 component_spread_pp REAL NOT NULL, component_count INTEGER NOT NULL, confidence TEXT, status TEXT, market_probability REAL, market_gap_pp REAL
);
CREATE INDEX IF NOT EXISTS idx_v3_decisions_event ON v3_decisions(event_key,captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_v3_decisions_status ON v3_decisions(status,captured_at DESC);

CREATE TABLE IF NOT EXISTS v3_outcomes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, event_key TEXT NOT NULL, kickoff TEXT,
 home_goals INTEGER NOT NULL, away_goals INTEGER NOT NULL, outcome TEXT NOT NULL,
 UNIQUE(event_key,home_goals,away_goals)
);
CREATE TABLE IF NOT EXISTS v3_sharp_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, event_key TEXT NOT NULL, source TEXT NOT NULL, horizon_label TEXT,
 p_home REAL, p_draw REAL, p_away REAL, odds_home REAL, odds_draw REAL, odds_away REAL, is_final_pre_kickoff INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_v3_sharp_event ON v3_sharp_lines(event_key,captured_at DESC);

CREATE TABLE IF NOT EXISTS v3_validation_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, model_version TEXT NOT NULL, git_commit TEXT, league_key TEXT NOT NULL,
 predictions INTEGER NOT NULL, folds INTEGER NOT NULL, log_loss REAL, brier_score REAL, calibration_error REAL,
 home_log_loss REAL, draw_log_loss REAL, away_log_loss REAL, notes TEXT
);
CREATE TABLE IF NOT EXISTS v3_validation_predictions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, kickoff TEXT NOT NULL, league_key TEXT NOT NULL,
 home_team TEXT NOT NULL, away_team TEXT NOT NULL, p_home REAL NOT NULL, p_draw REAL NOT NULL, p_away REAL NOT NULL,
 outcome INTEGER NOT NULL, fold INTEGER NOT NULL, training_matches INTEGER NOT NULL, dynamic_weight REAL NOT NULL,
 calibration_temperature REAL NOT NULL, FOREIGN KEY(run_id) REFERENCES v3_validation_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_v3_validation_kickoff ON v3_validation_predictions(kickoff);
"""


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


@contextmanager
def _db():
    """Transactional connection that always releases the SQLite file handle."""
    con = _connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _git_commit() -> Optional[str]:
    value = os.environ.get("GITHUB_SHA") or os.environ.get("APP_GIT_COMMIT")
    return value.strip() if value else None


def _feature_hash(f: V3Forecast) -> str:
    payload = {
        "league": f.league_key,
        "teams": [f.home_team_history, f.away_team_history],
        "dynamic": [f.dynamic_home, f.dynamic_draw, f.dynamic_away],
        "elo": [f.elo_home, f.elo_draw, f.elo_away],
        "lambda": [f.lambda_home, f.lambda_away],
        "weight": f.stack_weight_dynamic,
        "temperature": f.calibration_temperature,
        "history": [f.history_matches, f.home_history_matches, f.away_history_matches],
        "promotion": [f.promotion_prior_home, f.promotion_prior_away],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save_v3_snapshot(rows: Iterable[CombinedMatch], decisions: Iterable[V3Decision]) -> tuple[int, int, int]:
    rows, decisions = list(rows), list(decisions)
    captured = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row_map = {r.match_name: r for r in rows}
    forecasts, quotes, decision_rows = [], [], []

    for row in rows:
        f: Optional[V3Forecast] = getattr(row, "independent_v3", None)
        if f is None:
            continue
        key = event_key(row)
        forecasts.append((
            captured, captured, key, row.kickoff.isoformat(), str(getattr(row, "league", "") or ""), f.league_key,
            row.home_team, row.away_team, MODEL_VERSION, _git_commit(), FEATURE_SCHEMA_VERSION, _feature_hash(f),
            f.home_probability, f.draw_probability, f.away_probability,
            f.lower_home, f.lower_draw, f.lower_away, f.upper_home, f.upper_draw, f.upper_away,
            f.sd_home, f.sd_draw, f.sd_away,
            f.dynamic_home, f.dynamic_draw, f.dynamic_away, f.elo_home, f.elo_draw, f.elo_away,
            f.lambda_home, f.lambda_away, f.stack_weight_dynamic, f.calibration_temperature,
            f.component_spread_pp, f.bootstrap_samples, f.history_matches, f.home_history_matches, f.away_history_matches,
            f.confidence, int(f.promotion_prior_home), int(f.promotion_prior_away),
            getattr(row, "market_reference_home", None), getattr(row, "market_reference_draw", None), getattr(row, "market_reference_away", None),
        ))
        shop = getattr(row, "price_shop", None)
        if shop is not None:
            for side, items in shop.quotes.items():
                best = shop.best.get(side)
                for q in items:
                    quotes.append((
                        captured, key, row.kickoff.isoformat(), str(getattr(row, "league", "") or ""), side,
                        str(q.source), float(q.decimal_odds), getattr(q, "received_at", None), getattr(q, "market_timestamp", None),
                        getattr(q, "age_seconds", None), getattr(q, "liquidity", None), getattr(q, "available_size", None),
                        getattr(q, "commission", None), getattr(q, "line", None), getattr(q, "event_id", None),
                        getattr(q, "match_confidence", None), int(best is q),
                    ))

    for d in decisions:
        row = row_map.get(d.match_name)
        if row is None:
            continue
        decision_rows.append((
            captured, event_key(row), row.kickoff.isoformat(), d.league, row.home_team, row.away_team,
            d.side, d.selection, d.quote_source, d.quote_odds, d.quote_match_confidence,
            d.model_probability, d.lower_probability, d.probability_stdev, d.fair_odds,
            d.model_ev_pct, d.lower_ev_pct, d.probability_ev_positive, d.component_spread_pp,
            d.component_count, d.confidence, d.status, d.market_probability, d.market_gap_pp,
        ))

    with _db() as con:
        if forecasts:
            con.executemany(
                """INSERT INTO v3_forecasts(captured_at,decision_time,event_key,kickoff,league,league_key,home_team,away_team,model_version,git_commit,feature_schema_version,feature_snapshot_hash,p_home,p_draw,p_away,p05_home,p05_draw,p05_away,p95_home,p95_draw,p95_away,sd_home,sd_draw,sd_away,dynamic_home,dynamic_draw,dynamic_away,elo_home,elo_draw,elo_away,lambda_home,lambda_away,stack_weight_dynamic,calibration_temperature,component_spread_pp,bootstrap_samples,history_matches,home_history_matches,away_history_matches,confidence,promotion_prior_home,promotion_prior_away,market_reference_home,market_reference_draw,market_reference_away) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                forecasts,
            )
        if quotes:
            con.executemany(
                "INSERT INTO v3_quotes(captured_at,event_key,kickoff,league,side,source,decimal_odds,received_at,market_timestamp,age_seconds,liquidity,available_size,commission,line,provider_event_id,match_confidence,is_best) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                quotes,
            )
        if decision_rows:
            con.executemany(
                "INSERT INTO v3_decisions(captured_at,event_key,kickoff,league,home_team,away_team,side,selection_name,quote_source,quote_odds,quote_match_confidence,model_probability,lower_probability,probability_stdev,fair_odds,model_ev_pct,lower_ev_pct,probability_ev_positive,component_spread_pp,component_count,confidence,status,market_probability,market_gap_pp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                decision_rows,
            )
    return len(forecasts), len(quotes), len(decision_rows)


def save_validation_report(report: WalkForwardReport) -> int:
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db() as con:
        cur = con.execute(
            "INSERT INTO v3_validation_runs(created_at,model_version,git_commit,league_key,predictions,folds,log_loss,brier_score,calibration_error,home_log_loss,draw_log_loss,away_log_loss,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (created, MODEL_VERSION, _git_commit(), report.league_key, report.predictions, report.folds, report.log_loss,
             report.brier_score, report.calibration_error, report.home_log_loss, report.draw_log_loss, report.away_log_loss,
             " | ".join(report.notes)),
        )
        run_id = int(cur.lastrowid)
        if report.records:
            con.executemany(
                "INSERT INTO v3_validation_predictions(run_id,kickoff,league_key,home_team,away_team,p_home,p_draw,p_away,outcome,fold,training_matches,dynamic_weight,calibration_temperature) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, r.kickoff.isoformat(), r.league_key, r.home_team, r.away_team, r.p_home, r.p_draw, r.p_away,
                  r.outcome, r.fold, r.training_matches, r.dynamic_weight, r.calibration_temperature) for r in report.records],
            )
        return run_id


def record_outcome(event: str, kickoff: str, home_goals: int, away_goals: int) -> None:
    outcome = "HOME" if home_goals > away_goals else "DRAW" if home_goals == away_goals else "AWAY"
    with _db() as con:
        con.execute(
            "INSERT OR IGNORE INTO v3_outcomes(recorded_at,event_key,kickoff,home_goals,away_goals,outcome) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), event, kickoff, int(home_goals), int(away_goals), outcome),
        )


def record_sharp_line(event: str, source: str, *, horizon_label: Optional[str] = None, probabilities=(None, None, None), odds=(None, None, None), final_pre_kickoff: bool = False) -> None:
    with _db() as con:
        con.execute(
            "INSERT INTO v3_sharp_lines(captured_at,event_key,source,horizon_label,p_home,p_draw,p_away,odds_home,odds_draw,odds_away,is_final_pre_kickoff) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), event, source, horizon_label,
             probabilities[0], probabilities[1], probabilities[2], odds[0], odds[1], odds[2], int(final_pre_kickoff)),
        )


def v3_counts() -> tuple[int, int, int, int]:
    with _db() as con:
        return tuple(int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in ("v3_forecasts", "v3_quotes", "v3_decisions", "v3_validation_runs"))
