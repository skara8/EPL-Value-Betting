from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, Optional

from config import DB_FILE
from engine import CombinedMatch
from independent_model_v24 import canonical_history_team, resolve_league_source
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

CREATE TABLE IF NOT EXISTS v3_events (
 canonical_event_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 competition_key TEXT NOT NULL, season_key TEXT NOT NULL, kickoff TEXT NOT NULL,
 home_team_canonical TEXT NOT NULL, away_team_canonical TEXT NOT NULL,
 home_team_display TEXT, away_team_display TEXT, league_display TEXT
);
CREATE INDEX IF NOT EXISTS idx_v3_events_fixture ON v3_events(competition_key,season_key,home_team_canonical,away_team_canonical);

CREATE TABLE IF NOT EXISTS v3_provider_event_map (
 id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_event_id TEXT NOT NULL, provider TEXT NOT NULL, provider_event_id TEXT NOT NULL,
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, match_confidence REAL,
 UNIQUE(canonical_event_id,provider,provider_event_id),
 FOREIGN KEY(canonical_event_id) REFERENCES v3_events(canonical_event_id)
);

CREATE TABLE IF NOT EXISTS v3_data_provenance (
 id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, stage TEXT NOT NULL, source TEXT NOT NULL,
 record_count INTEGER, payload_hash TEXT, metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_v3_provenance_stage ON v3_data_provenance(stage,captured_at DESC);

CREATE TABLE IF NOT EXISTS v3_experiments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, experiment_name TEXT NOT NULL,
 control_model TEXT NOT NULL, challenger_model TEXT NOT NULL, feature_set_json TEXT NOT NULL,
 train_start TEXT, train_end TEXT, test_start TEXT, test_end TEXT, primary_metric TEXT NOT NULL,
 multiple_testing_family TEXT, status TEXT NOT NULL DEFAULT 'REGISTERED', notes TEXT,
 UNIQUE(experiment_name,created_at)
);

CREATE TABLE IF NOT EXISTS v3_fills (
 id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_event_id TEXT NOT NULL, side TEXT NOT NULL, source TEXT NOT NULL,
 requested_odds REAL, filled_odds REAL NOT NULL, stake REAL NOT NULL, fees REAL NOT NULL DEFAULT 0,
 requested_at TEXT, filled_at TEXT NOT NULL, status TEXT NOT NULL, external_reference TEXT,
 FOREIGN KEY(canonical_event_id) REFERENCES v3_events(canonical_event_id)
);
CREATE INDEX IF NOT EXISTS idx_v3_fills_event ON v3_fills(canonical_event_id,filled_at DESC);

CREATE TABLE IF NOT EXISTS v3_economic_evidence (
 decision_id INTEGER PRIMARY KEY, canonical_event_id TEXT NOT NULL, computed_at TEXT NOT NULL,
 close_source TEXT, close_odds REAL, close_probability REAL,
 price_clv_pct REAL, log_odds_clv REAL, model_vs_close_pp REAL,
 settled INTEGER NOT NULL DEFAULT 0, unit_return REAL, evidence_type TEXT NOT NULL,
 FOREIGN KEY(decision_id) REFERENCES v3_decisions(id),
 FOREIGN KEY(canonical_event_id) REFERENCES v3_events(canonical_event_id)
);
CREATE INDEX IF NOT EXISTS idx_v3_economic_event ON v3_economic_evidence(canonical_event_id);
"""


MIGRATIONS = {
    "v3_forecasts": {
        "canonical_event_id": "TEXT",
    },
    "v3_quotes": {
        "canonical_event_id": "TEXT",
        "executable": "INTEGER NOT NULL DEFAULT 1",
        "execution_reason": "TEXT",
    },
    "v3_decisions": {
        "canonical_event_id": "TEXT",
        "quote_received_at": "TEXT",
        "quote_market_timestamp": "TEXT",
        "quote_age_seconds": "REAL",
        "quote_liquidity": "REAL",
        "quote_available_size": "REAL",
        "quote_provider_event_id": "TEXT",
    },
    "v3_outcomes": {
        "canonical_event_id": "TEXT",
    },
    "v3_sharp_lines": {
        "canonical_event_id": "TEXT",
        "minutes_to_kickoff": "REAL",
        "de_vig_method": "TEXT",
    },
    "v3_validation_runs": {
        "rps": "REAL",
        "league_frequency_log_loss": "REAL",
        "elo_only_log_loss": "REAL",
        "dynamic_only_log_loss": "REAL",
        "delta_log_loss_vs_best_baseline": "REAL",
    },
    "v3_validation_predictions": {
        "baseline_home": "REAL", "baseline_draw": "REAL", "baseline_away": "REAL",
        "elo_only_home": "REAL", "elo_only_draw": "REAL", "elo_only_away": "REAL",
        "dynamic_only_home": "REAL", "dynamic_only_draw": "REAL", "dynamic_only_away": "REAL",
    },
}


def _ensure_columns(con: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        existing = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    _ensure_columns(con)
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


def _season_key(kickoff: datetime) -> str:
    at = kickoff.astimezone(timezone.utc)
    if at.month >= 7:
        return f"{at.year}-{str(at.year + 1)[-2:]}"
    return f"{at.year - 1}-{str(at.year)[-2:]}"


def canonical_event_id_for_row(row: CombinedMatch, league_key: Optional[str] = None) -> str:
    source = resolve_league_source(str(getattr(row, "league", "") or ""))
    competition = league_key or (source.key if source is not None else str(getattr(row, "league", "") or "UNKNOWN"))
    payload = "|".join((
        competition,
        _season_key(row.kickoff),
        canonical_history_team(row.home_team),
        canonical_history_team(row.away_team),
    ))
    return "v3evt_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _upsert_event(con: sqlite3.Connection, row: CombinedMatch, league_key: str, captured: str) -> str:
    canonical_id = canonical_event_id_for_row(row, league_key)
    con.execute(
        """INSERT INTO v3_events(canonical_event_id,created_at,updated_at,competition_key,season_key,kickoff,home_team_canonical,away_team_canonical,home_team_display,away_team_display,league_display)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(canonical_event_id) DO UPDATE SET updated_at=excluded.updated_at,kickoff=excluded.kickoff,home_team_display=excluded.home_team_display,away_team_display=excluded.away_team_display,league_display=excluded.league_display""",
        (
            canonical_id, captured, captured, league_key, _season_key(row.kickoff), row.kickoff.isoformat(),
            canonical_history_team(row.home_team), canonical_history_team(row.away_team),
            row.home_team, row.away_team, str(getattr(row, "league", "") or ""),
        ),
    )
    return canonical_id


def _record_provider_map(con: sqlite3.Connection, canonical_id: str, provider: str, provider_event_id: Optional[str], captured: str, confidence: Optional[float]) -> None:
    if not provider_event_id:
        return
    con.execute(
        """INSERT INTO v3_provider_event_map(canonical_event_id,provider,provider_event_id,first_seen_at,last_seen_at,match_confidence)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(canonical_event_id,provider,provider_event_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,match_confidence=excluded.match_confidence""",
        (canonical_id, provider, str(provider_event_id), captured, captured, confidence),
    )


def save_v3_snapshot(rows: Iterable[CombinedMatch], decisions: Iterable[V3Decision]) -> tuple[int, int, int]:
    rows, decisions = list(rows), list(decisions)
    captured = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row_map = {r.match_name: r for r in rows}
    canonical_ids: dict[str, str] = {}
    forecasts, quotes, decision_rows = [], [], []

    with _db() as con:
        for row in rows:
            f: Optional[V3Forecast] = getattr(row, "independent_v3", None)
            if f is None:
                continue
            key = event_key(row)
            canonical_id = _upsert_event(con, row, f.league_key, captured)
            canonical_ids[row.match_name] = canonical_id
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
                canonical_id,
            ))
            shop = getattr(row, "price_shop", None)
            if shop is not None:
                for side, items in shop.quotes.items():
                    best = shop.best.get(side)
                    for q in items:
                        executable = int(bool(getattr(q, "executable", True)))
                        execution_reason = str(getattr(q, "execution_reason", "") or "") or None
                        quotes.append((
                            captured, key, row.kickoff.isoformat(), str(getattr(row, "league", "") or ""), side,
                            str(q.source), float(q.decimal_odds), getattr(q, "received_at", None), getattr(q, "market_timestamp", None),
                            getattr(q, "age_seconds", None), getattr(q, "liquidity", None), getattr(q, "available_size", None),
                            getattr(q, "commission", None), getattr(q, "line", None), getattr(q, "event_id", None),
                            getattr(q, "match_confidence", None), int(best is q), canonical_id, executable, execution_reason,
                        ))
                        _record_provider_map(con, canonical_id, str(q.source), getattr(q, "event_id", None), captured, getattr(q, "match_confidence", None))

        for d in decisions:
            row = row_map.get(d.match_name)
            if row is None:
                continue
            canonical_id = canonical_ids.get(d.match_name)
            if canonical_id is None:
                source = resolve_league_source(d.league)
                canonical_id = _upsert_event(con, row, source.key if source else "UNKNOWN", captured)
            decision_rows.append((
                captured, event_key(row), row.kickoff.isoformat(), d.league, row.home_team, row.away_team,
                d.side, d.selection, d.quote_source, d.quote_odds, d.quote_match_confidence,
                d.model_probability, d.lower_probability, d.probability_stdev, d.fair_odds,
                d.model_ev_pct, d.lower_ev_pct, d.probability_ev_positive, d.component_spread_pp,
                d.component_count, d.confidence, d.status, d.market_probability, d.market_gap_pp,
                canonical_id, d.quote_received_at, d.quote_market_timestamp, d.quote_age_seconds,
                d.quote_liquidity, d.quote_available_size, d.quote_event_id,
            ))
            _record_provider_map(con, canonical_id, d.quote_source, d.quote_event_id, captured, d.quote_match_confidence)

        if forecasts:
            con.executemany(
                """INSERT INTO v3_forecasts(captured_at,decision_time,event_key,kickoff,league,league_key,home_team,away_team,model_version,git_commit,feature_schema_version,feature_snapshot_hash,p_home,p_draw,p_away,p05_home,p05_draw,p05_away,p95_home,p95_draw,p95_away,sd_home,sd_draw,sd_away,dynamic_home,dynamic_draw,dynamic_away,elo_home,elo_draw,elo_away,lambda_home,lambda_away,stack_weight_dynamic,calibration_temperature,component_spread_pp,bootstrap_samples,history_matches,home_history_matches,away_history_matches,confidence,promotion_prior_home,promotion_prior_away,market_reference_home,market_reference_draw,market_reference_away,canonical_event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                forecasts,
            )
        if quotes:
            con.executemany(
                "INSERT INTO v3_quotes(captured_at,event_key,kickoff,league,side,source,decimal_odds,received_at,market_timestamp,age_seconds,liquidity,available_size,commission,line,provider_event_id,match_confidence,is_best,canonical_event_id,executable,execution_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                quotes,
            )
        if decision_rows:
            con.executemany(
                "INSERT INTO v3_decisions(captured_at,event_key,kickoff,league,home_team,away_team,side,selection_name,quote_source,quote_odds,quote_match_confidence,model_probability,lower_probability,probability_stdev,fair_odds,model_ev_pct,lower_ev_pct,probability_ev_positive,component_spread_pp,component_count,confidence,status,market_probability,market_gap_pp,canonical_event_id,quote_received_at,quote_market_timestamp,quote_age_seconds,quote_liquidity,quote_available_size,quote_provider_event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                decision_rows,
            )
    return len(forecasts), len(quotes), len(decision_rows)


def save_validation_report(report: WalkForwardReport) -> int:
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db() as con:
        cur = con.execute(
            """INSERT INTO v3_validation_runs(created_at,model_version,git_commit,league_key,predictions,folds,log_loss,brier_score,calibration_error,home_log_loss,draw_log_loss,away_log_loss,notes,rps,league_frequency_log_loss,elo_only_log_loss,dynamic_only_log_loss,delta_log_loss_vs_best_baseline)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                created, MODEL_VERSION, _git_commit(), report.league_key, report.predictions, report.folds, report.log_loss,
                report.brier_score, report.calibration_error, report.home_log_loss, report.draw_log_loss, report.away_log_loss,
                " | ".join(report.notes), report.rps, report.league_frequency_log_loss, report.elo_only_log_loss,
                report.dynamic_only_log_loss, report.delta_log_loss_vs_best_baseline,
            ),
        )
        run_id = int(cur.lastrowid)
        if report.records:
            con.executemany(
                """INSERT INTO v3_validation_predictions(run_id,kickoff,league_key,home_team,away_team,p_home,p_draw,p_away,outcome,fold,training_matches,dynamic_weight,calibration_temperature,baseline_home,baseline_draw,baseline_away,elo_only_home,elo_only_draw,elo_only_away,dynamic_only_home,dynamic_only_draw,dynamic_only_away)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(
                    run_id, r.kickoff.isoformat(), r.league_key, r.home_team, r.away_team, r.p_home, r.p_draw, r.p_away,
                    r.outcome, r.fold, r.training_matches, r.dynamic_weight, r.calibration_temperature,
                    r.baseline_home, r.baseline_draw, r.baseline_away,
                    r.elo_only_home, r.elo_only_draw, r.elo_only_away,
                    r.dynamic_only_home, r.dynamic_only_draw, r.dynamic_only_away,
                ) for r in report.records],
            )
        return run_id


def record_provenance(stage: str, source: str, *, record_count: Optional[int] = None, payload: object = None, metadata: Optional[dict] = None) -> int:
    captured = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload_hash = None
    if payload is not None:
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        payload_hash = hashlib.sha256(encoded).hexdigest()
    metadata_json = json.dumps(metadata or {}, sort_keys=True, default=str, separators=(",", ":"))
    with _db() as con:
        cur = con.execute(
            "INSERT INTO v3_data_provenance(captured_at,stage,source,record_count,payload_hash,metadata_json) VALUES (?,?,?,?,?,?)",
            (captured, stage, source, record_count, payload_hash, metadata_json),
        )
        return int(cur.lastrowid)


def register_experiment(
    experiment_name: str,
    *,
    control_model: str,
    challenger_model: str,
    feature_set: Iterable[str],
    primary_metric: str = "multiclass_log_loss",
    multiple_testing_family: str = "v3.1-football-models",
    train_start: Optional[str] = None,
    train_end: Optional[str] = None,
    test_start: Optional[str] = None,
    test_end: Optional[str] = None,
    notes: str = "",
) -> int:
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db() as con:
        cur = con.execute(
            """INSERT INTO v3_experiments(created_at,experiment_name,control_model,challenger_model,feature_set_json,train_start,train_end,test_start,test_end,primary_metric,multiple_testing_family,status,notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                created, experiment_name, control_model, challenger_model,
                json.dumps(sorted(set(feature_set)), separators=(",", ":")),
                train_start, train_end, test_start, test_end, primary_metric, multiple_testing_family, "REGISTERED", notes,
            ),
        )
        return int(cur.lastrowid)


def record_fill(
    canonical_event_id: str,
    side: str,
    source: str,
    *,
    filled_odds: float,
    stake: float,
    requested_odds: Optional[float] = None,
    fees: float = 0.0,
    requested_at: Optional[str] = None,
    filled_at: Optional[str] = None,
    status: str = "FILLED",
    external_reference: Optional[str] = None,
) -> int:
    at = filled_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db() as con:
        cur = con.execute(
            """INSERT INTO v3_fills(canonical_event_id,side,source,requested_odds,filled_odds,stake,fees,requested_at,filled_at,status,external_reference)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (canonical_event_id, side, source, requested_odds, filled_odds, stake, fees, requested_at, at, status, external_reference),
        )
        return int(cur.lastrowid)


def record_outcome(event: str, kickoff: str, home_goals: int, away_goals: int, canonical_event_id: Optional[str] = None) -> None:
    outcome = "HOME" if home_goals > away_goals else "DRAW" if home_goals == away_goals else "AWAY"
    with _db() as con:
        con.execute(
            "INSERT OR IGNORE INTO v3_outcomes(recorded_at,event_key,kickoff,home_goals,away_goals,outcome,canonical_event_id) VALUES (?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), event, kickoff, int(home_goals), int(away_goals), outcome, canonical_event_id),
        )


def record_sharp_line(
    event: str,
    source: str,
    *,
    horizon_label: Optional[str] = None,
    probabilities=(None, None, None),
    odds=(None, None, None),
    final_pre_kickoff: bool = False,
    canonical_event_id: Optional[str] = None,
    minutes_to_kickoff: Optional[float] = None,
    de_vig_method: Optional[str] = None,
) -> None:
    with _db() as con:
        con.execute(
            """INSERT INTO v3_sharp_lines(captured_at,event_key,source,horizon_label,p_home,p_draw,p_away,odds_home,odds_draw,odds_away,is_final_pre_kickoff,canonical_event_id,minutes_to_kickoff,de_vig_method)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"), event, source, horizon_label,
                probabilities[0], probabilities[1], probabilities[2], odds[0], odds[1], odds[2], int(final_pre_kickoff),
                canonical_event_id, minutes_to_kickoff, de_vig_method,
            ),
        )


def _devig_inverse_odds(odds: tuple[float, float, float]) -> tuple[float, float, float]:
    raw = [1.0 / float(value) for value in odds]
    total = sum(raw)
    return raw[0] / total, raw[1] / total, raw[2] / total


def _horizon(minutes: float) -> str:
    if minutes <= 15:
        return "T-15m"
    if minutes <= 60:
        return "T-1h"
    if minutes <= 360:
        return "T-6h"
    if minutes <= 1440:
        return "T-24h"
    return "EARLY"


def record_sharp_snapshots(rows: Iterable[CombinedMatch], source: str = "Pinnacle") -> int:
    now = datetime.now(timezone.utc)
    stored = 0
    with _db() as con:
        for row in rows:
            odds = (getattr(row, "pin_home", None), getattr(row, "pin_draw", None), getattr(row, "pin_away", None))
            if any(value is None or float(value) <= 1.0 for value in odds):
                continue
            minutes = (row.kickoff.astimezone(timezone.utc) - now).total_seconds() / 60.0
            if minutes < 0:
                continue
            forecast: Optional[V3Forecast] = getattr(row, "independent_v3", None)
            league_key = forecast.league_key if forecast is not None else (resolve_league_source(str(getattr(row, "league", "") or "")).key if resolve_league_source(str(getattr(row, "league", "") or "")) else "UNKNOWN")
            canonical_id = _upsert_event(con, row, league_key, now.isoformat(timespec="seconds"))
            probabilities = _devig_inverse_odds((float(odds[0]), float(odds[1]), float(odds[2])))
            con.execute(
                """INSERT INTO v3_sharp_lines(captured_at,event_key,source,horizon_label,p_home,p_draw,p_away,odds_home,odds_draw,odds_away,is_final_pre_kickoff,canonical_event_id,minutes_to_kickoff,de_vig_method)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now.isoformat(timespec="seconds"), event_key(row), source, _horizon(minutes),
                    probabilities[0], probabilities[1], probabilities[2], float(odds[0]), float(odds[1]), float(odds[2]),
                    int(0 <= minutes <= 15), canonical_id, minutes, "MULTIPLICATIVE_INVERSE_ODDS",
                ),
            )
            _record_provider_map(con, canonical_id, source, getattr(row, "pinnacle_event_id", None), now.isoformat(timespec="seconds"), 1.0)
            stored += 1
    return stored


def reconcile_outcomes_from_histories(histories: dict[str, list]) -> int:
    """Settle previously stored V3 events only on exact teams and near dates.

    Historical feeds are sometimes date-level. We therefore permit a one-day
    timestamp discrepancy, but never fuzzy team matching and never an ambiguous
    candidate.
    """
    now = datetime.now(timezone.utc)
    indexes: dict[str, list] = {}
    for league_key, matches in histories.items():
        indexes[league_key] = list(matches)

    settled = 0
    with _db() as con:
        pending = con.execute(
            """SELECT e.canonical_event_id,e.competition_key,e.kickoff,e.home_team_canonical,e.away_team_canonical,
                      COALESCE((SELECT f.event_key FROM v3_forecasts f WHERE f.canonical_event_id=e.canonical_event_id ORDER BY f.captured_at DESC LIMIT 1),'')
               FROM v3_events e
               WHERE datetime(e.kickoff) < datetime(?)
                 AND NOT EXISTS (SELECT 1 FROM v3_outcomes o WHERE o.canonical_event_id=e.canonical_event_id)""",
            (now.isoformat(timespec="seconds"),),
        ).fetchall()
        for canonical_id, league_key, kickoff_text, home, away, legacy_key in pending:
            kickoff = datetime.fromisoformat(str(kickoff_text))
            candidates = []
            for match in indexes.get(str(league_key), ()):
                if canonical_history_team(match.home_team) != home or canonical_history_team(match.away_team) != away:
                    continue
                day_gap = abs((match.kickoff.date() - kickoff.date()).days)
                if day_gap <= 1:
                    candidates.append((day_gap, match))
            candidates.sort(key=lambda item: item[0])
            if not candidates:
                continue
            if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                continue
            match = candidates[0][1]
            outcome = "HOME" if match.home_goals > match.away_goals else "DRAW" if match.home_goals == match.away_goals else "AWAY"
            con.execute(
                "INSERT OR IGNORE INTO v3_outcomes(recorded_at,event_key,kickoff,home_goals,away_goals,outcome,canonical_event_id) VALUES (?,?,?,?,?,?,?)",
                (now.isoformat(timespec="seconds"), legacy_key, kickoff_text, int(match.home_goals), int(match.away_goals), outcome, canonical_id),
            )
            settled += 1
    return settled


def refresh_economic_evidence() -> int:
    """Join decisions to genuine final-pre-kickoff sharp lines and outcomes.

    Decision quotes are labelled OBSERVED_QUOTE_PROXY, not actual fills. Actual
    strategy economics belong in v3_fills and are intentionally kept separate.
    """
    computed = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated = 0
    with _db() as con:
        decisions = con.execute(
            "SELECT id,canonical_event_id,side,quote_odds,model_probability FROM v3_decisions WHERE canonical_event_id IS NOT NULL"
        ).fetchall()
        for decision_id, canonical_id, side, decision_odds, model_probability in decisions:
            sharp = con.execute(
                """SELECT source,p_home,p_draw,p_away,odds_home,odds_draw,odds_away
                   FROM v3_sharp_lines
                   WHERE canonical_event_id=? AND is_final_pre_kickoff=1
                   ORDER BY captured_at DESC LIMIT 1""",
                (canonical_id,),
            ).fetchone()
            if sharp is None:
                continue
            index = {"HOME": 0, "DRAW": 1, "AWAY": 2}.get(str(side))
            if index is None:
                continue
            close_probability = sharp[1 + index]
            close_odds = sharp[4 + index]
            if close_odds is None or float(close_odds) <= 1.0:
                continue
            price_clv = (float(decision_odds) / float(close_odds) - 1.0) * 100.0
            log_clv = math.log(float(decision_odds) / float(close_odds))
            model_vs_close = None if close_probability is None else (float(model_probability) - float(close_probability)) * 100.0
            outcome_row = con.execute(
                "SELECT outcome FROM v3_outcomes WHERE canonical_event_id=? ORDER BY recorded_at DESC LIMIT 1",
                (canonical_id,),
            ).fetchone()
            settled = int(outcome_row is not None)
            unit_return = None
            if outcome_row is not None:
                unit_return = float(decision_odds) - 1.0 if str(outcome_row[0]) == str(side) else -1.0
            con.execute(
                """INSERT INTO v3_economic_evidence(decision_id,canonical_event_id,computed_at,close_source,close_odds,close_probability,price_clv_pct,log_odds_clv,model_vs_close_pp,settled,unit_return,evidence_type)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(decision_id) DO UPDATE SET computed_at=excluded.computed_at,close_source=excluded.close_source,close_odds=excluded.close_odds,close_probability=excluded.close_probability,price_clv_pct=excluded.price_clv_pct,log_odds_clv=excluded.log_odds_clv,model_vs_close_pp=excluded.model_vs_close_pp,settled=excluded.settled,unit_return=excluded.unit_return,evidence_type=excluded.evidence_type""",
                (decision_id, canonical_id, computed, sharp[0], close_odds, close_probability, price_clv, log_clv, model_vs_close, settled, unit_return, "OBSERVED_QUOTE_PROXY"),
            )
            updated += 1
    return updated


def economic_summary() -> dict[str, Optional[float]]:
    with _db() as con:
        row = con.execute(
            """SELECT COUNT(*),AVG(price_clv_pct),AVG(CASE WHEN price_clv_pct>0 THEN 1.0 ELSE 0.0 END),
                      SUM(settled),AVG(CASE WHEN settled=1 THEN unit_return END)
               FROM v3_economic_evidence"""
        ).fetchone()
        fills = con.execute("SELECT COUNT(*) FROM v3_fills WHERE status='FILLED'").fetchone()[0]
    return {
        "decisions_with_final_close": int(row[0] or 0),
        "average_price_clv_pct": None if row[1] is None else float(row[1]),
        "positive_clv_rate": None if row[2] is None else float(row[2]),
        "settled_proxy_decisions": int(row[3] or 0),
        "unit_stake_roi": None if row[4] is None else float(row[4]),
        "actual_fills": int(fills or 0),
    }


def v3_counts() -> tuple[int, int, int, int]:
    with _db() as con:
        return tuple(int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in ("v3_forecasts", "v3_quotes", "v3_decisions", "v3_validation_runs"))
