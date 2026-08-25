from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import engine
from config import DATA_DIR
from context_model import ContextInputs, FPLTeamContext, context_adjustment_for_match
from engine import CombinedMatch


FOTMOB_LEAGUE_ID = 47
FOTMOB_BASE = "https://www.fotmob.com/api"
FPL_BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
CACHE_DIR = DATA_DIR / "cache" / "football_intelligence"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

LEAGUE_CACHE_SECONDS = 30 * 60
FPL_CACHE_SECONDS = 30 * 60
FINISHED_MATCH_CACHE_SECONDS = 7 * 24 * 60 * 60
MAX_DETAIL_REQUESTS_PER_REFRESH = 42
REQUEST_PAUSE_SECONDS = 0.15
RECENT_MATCH_LIMIT = 5


@dataclass
class LeagueMatchRef:
    match_id: int
    kickoff: Optional[datetime]
    home_team: str
    away_team: str
    home_id: Optional[int] = None
    away_id: Optional[int] = None
    finished: bool = False
    home_score: Optional[int] = None
    away_score: Optional[int] = None

    @property
    def match_name(self) -> str:
        return f"{self.home_team} v {self.away_team}"


@dataclass
class FPLPlayerProfile:
    name: str
    team: str
    position: str
    chance: float
    status: str
    news: str
    price: float
    minutes: float
    starts: float
    points_per_game: float
    xgi90: float
    saves: float
    clean_sheets: float
    goals: float
    assists: float


@dataclass
class PlayerAppearance:
    name: str
    position: str
    starter: bool
    rating: Optional[float] = None
    minutes: Optional[float] = None


@dataclass
class MatchPerformance:
    match_id: int
    kickoff: Optional[datetime]
    opponent: str
    venue: str
    goals_for: float
    goals_against: float
    xg_for: Optional[float] = None
    xg_against: Optional[float] = None
    shots_for: Optional[float] = None
    shots_against: Optional[float] = None
    shots_on_target_for: Optional[float] = None
    shots_on_target_against: Optional[float] = None
    big_chances_for: Optional[float] = None
    big_chances_against: Optional[float] = None
    possession_for: Optional[float] = None
    possession_against: Optional[float] = None
    corners_for: Optional[float] = None
    corners_against: Optional[float] = None
    accurate_passes_for: Optional[float] = None
    accurate_passes_against: Optional[float] = None
    touches_box_for: Optional[float] = None
    touches_box_against: Optional[float] = None
    formation: str = ""
    players: list[PlayerAppearance] = field(default_factory=list)


@dataclass
class ExpectedXIPlayer:
    name: str
    position: str
    start_probability: float
    strength: float
    availability: float
    recent_starts: int
    average_rating: Optional[float]
    note: str = ""


@dataclass
class TacticalProfile:
    labels: tuple[str, ...] = ()
    possession: Optional[float] = None
    shots: Optional[float] = None
    shots_on_target: Optional[float] = None
    xg: Optional[float] = None
    xga: Optional[float] = None
    xg_per_shot: Optional[float] = None
    corners: Optional[float] = None
    corners_against: Optional[float] = None
    touches_box: Optional[float] = None
    defensive_suppression: Optional[float] = None


@dataclass
class TeamIntelligence:
    team: str
    expected_xi: list[ExpectedXIPlayer] = field(default_factory=list)
    xi_strength: Optional[float] = None
    position_strength: dict[str, float] = field(default_factory=dict)
    recent_matches: list[MatchPerformance] = field(default_factory=list)
    form_score: float = 0.0
    tactical: TacticalProfile = field(default_factory=TacticalProfile)
    latest_formation: str = ""
    rest_days: Optional[float] = None
    data_quality: str = "LOW"


@dataclass
class MatchIntelligence:
    match_name: str
    home_team: str
    away_team: str
    home: Optional[TeamIntelligence]
    away: Optional[TeamIntelligence]
    xi_rating: float = 0.0
    recent_form_rating: float = 0.0
    tactical_rating: float = 0.0
    rest_rating: float = 0.0
    overall_rating: float = 0.0
    data_quality: str = "LOW"
    reasons: tuple[str, ...] = ()


@dataclass
class IntelligenceBundle:
    teams: dict[str, TeamIntelligence]
    matches: dict[str, MatchIntelligence]
    league_matches: list[LeagueMatchRef]
    source_notes: tuple[str, ...] = ()
    refreshed_at: str = ""


# ---------------------------------------------------------------------------
# Cache / HTTP helpers
# ---------------------------------------------------------------------------


def _cache_path(name: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
    return CACHE_DIR / f"{safe}.json"


def _load_cache(name: str, max_age_seconds: int) -> Optional[Any]:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > max_age_seconds:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(name: str, payload: Any) -> None:
    try:
        _cache_path(name).write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _cached_json(name: str, url: str, *, params: Optional[dict] = None, ttl: int, provider: str) -> Any:
    cached = _load_cache(name, ttl)
    if cached is not None:
        return cached
    payload = engine.get_json(
        url,
        params=params,
        headers={
            "Referer": "https://www.fotmob.com/" if "fotmob.com" in url else "https://fantasy.premierleague.com/",
            "Accept-Language": "en-AU,en;q=0.9",
        },
        provider_name=provider,
    )
    _save_cache(name, payload)
    return payload


def fetch_fotmob_league() -> Any:
    return _cached_json(
        "fotmob_epl_league",
        f"{FOTMOB_BASE}/leagues",
        params={"id": FOTMOB_LEAGUE_ID},
        ttl=LEAGUE_CACHE_SECONDS,
        provider="FotMob",
    )


def fetch_fotmob_match_details(match_id: int) -> Any:
    return _cached_json(
        f"fotmob_match_{int(match_id)}",
        f"{FOTMOB_BASE}/matchDetails",
        params={"matchId": int(match_id)},
        ttl=FINISHED_MATCH_CACHE_SECONDS,
        provider="FotMob",
    )


def fetch_fpl_bootstrap() -> Any:
    return _cached_json(
        "fpl_bootstrap",
        FPL_BOOTSTRAP,
        ttl=FPL_CACHE_SECONDS,
        provider="Fantasy Premier League",
    )


# ---------------------------------------------------------------------------
# Generic payload extraction
# ---------------------------------------------------------------------------


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _int(value: Any) -> Optional[int]:
    n = _num(value)
    return int(n) if n is not None else None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    dt = engine.parse_datetime(value)
    if dt is not None:
        return dt
    try:
        ms = float(value)
        if ms > 10_000_000_000:
            ms /= 1000.0
        return datetime.fromtimestamp(ms, tz=timezone.utc)
    except Exception:
        return None


def _canonical(name: str) -> Optional[str]:
    return engine.canonical_epl_club(name or "")


def _walk(obj: Any) -> Iterable[Any]:
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _team_obj_name(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("name") or obj.get("teamName") or "")
    return ""


def _extract_team_pair(node: dict[str, Any]) -> Optional[tuple[dict, dict]]:
    home = node.get("home") or node.get("homeTeam")
    away = node.get("away") or node.get("awayTeam")
    if isinstance(home, dict) and isinstance(away, dict):
        return home, away
    return None


def extract_league_match_refs(payload: Any) -> list[LeagueMatchRef]:
    """Find EPL match records without depending on one fragile FotMob nesting path."""
    output: dict[int, LeagueMatchRef] = {}
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue
        raw_id = node.get("id") or node.get("matchId")
        pair = _extract_team_pair(node)
        if raw_id is None or pair is None:
            continue
        home_obj, away_obj = pair
        home = _canonical(_team_obj_name(home_obj))
        away = _canonical(_team_obj_name(away_obj))
        if not home or not away or home == away:
            continue
        try:
            match_id = int(raw_id)
        except Exception:
            continue

        status = node.get("status") if isinstance(node.get("status"), dict) else {}
        kickoff = (
            _parse_dt(status.get("utcTime"))
            or _parse_dt(status.get("matchTimeUTCDate"))
            or _parse_dt(node.get("utcTime"))
            or _parse_dt(node.get("timeTS"))
        )
        home_score = _int(home_obj.get("score"))
        away_score = _int(away_obj.get("score"))
        if home_score is None:
            home_score = _int(status.get("scoreStr", "").split("-")[0]) if "-" in str(status.get("scoreStr", "")) else None
        if away_score is None:
            parts = str(status.get("scoreStr", "")).split("-")
            away_score = _int(parts[1]) if len(parts) == 2 else None
        finished = bool(status.get("finished")) or str(status.get("reason", "")).lower() in {"finished", "ft"}
        if not finished and kickoff and kickoff < datetime.now(timezone.utc) and home_score is not None and away_score is not None:
            finished = True

        candidate = LeagueMatchRef(
            match_id=match_id,
            kickoff=kickoff,
            home_team=home,
            away_team=away,
            home_id=_int(home_obj.get("id")),
            away_id=_int(away_obj.get("id")),
            finished=finished,
            home_score=home_score,
            away_score=away_score,
        )
        current = output.get(match_id)
        if current is None or (candidate.kickoff is not None and current.kickoff is None):
            output[match_id] = candidate
    return sorted(output.values(), key=lambda x: x.kickoff or datetime.min.replace(tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# FPL player profiles
# ---------------------------------------------------------------------------


def _position_name(element_type: Any) -> str:
    return {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}.get(_int(element_type), "UNK")


def _player_chance(player: dict[str, Any]) -> float:
    status = str(player.get("status") or "a").lower()
    if status == "a":
        return 1.0
    raw = player.get("chance_of_playing_next_round")
    if raw is None:
        if status in {"i", "u", "s"}:
            return 0.0
        if status == "d":
            return 0.75
        return 0.5
    value = _num(raw)
    return max(0.0, min(1.0, (value or 0.0) / 100.0))


def _departure_news(news: str) -> bool:
    text = (news or "").lower()
    patterns = (
        "joined ", "has joined ", "signed for ", "transferred to ", "moved to ",
        "on loan to ", "loaned to ", "loan for the rest", "returned to ",
        "has returned to ", "permanently", "left the club", "departed",
    )
    return any(p in text for p in patterns)


def parse_fpl_players(payload: Any) -> dict[str, list[FPLPlayerProfile]]:
    if not isinstance(payload, dict):
        return {}
    teams = payload.get("teams")
    players = payload.get("elements")
    if not isinstance(teams, list) or not isinstance(players, list):
        return {}
    team_map: dict[int, str] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        canonical = _canonical(str(team.get("name") or team.get("short_name") or ""))
        if canonical and _int(team.get("id")) is not None:
            team_map[int(team["id"])] = canonical

    output: dict[str, list[FPLPlayerProfile]] = {team: [] for team in team_map.values()}
    for player in players:
        if not isinstance(player, dict):
            continue
        team = team_map.get(_int(player.get("team")) or -1)
        if not team:
            continue
        news = str(player.get("news") or "").strip()
        if _departure_news(news):
            continue
        output.setdefault(team, []).append(
            FPLPlayerProfile(
                name=str(player.get("web_name") or player.get("second_name") or player.get("first_name") or "Unknown"),
                team=team,
                position=_position_name(player.get("element_type")),
                chance=_player_chance(player),
                status=str(player.get("status") or "a"),
                news=news,
                price=(_num(player.get("now_cost")) or 0.0) / 10.0,
                minutes=_num(player.get("minutes")) or 0.0,
                starts=_num(player.get("starts")) or 0.0,
                points_per_game=_num(player.get("points_per_game")) or 0.0,
                xgi90=_num(player.get("expected_goal_involvements_per_90")) or 0.0,
                saves=_num(player.get("saves")) or 0.0,
                clean_sheets=_num(player.get("clean_sheets")) or 0.0,
                goals=_num(player.get("goals_scored")) or 0.0,
                assists=_num(player.get("assists")) or 0.0,
            )
        )
    return output


# ---------------------------------------------------------------------------
# FotMob match details -> recent-performance records
# ---------------------------------------------------------------------------


def _stat_pairs(payload: Any) -> dict[str, tuple[Optional[float], Optional[float]]]:
    pairs: dict[str, tuple[Optional[float], Optional[float]]] = {}
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue
        title = str(node.get("title") or node.get("name") or "").strip()
        values = node.get("stats") or node.get("values")
        if not title or not isinstance(values, list) or len(values) < 2:
            continue
        left, right = _num(values[0]), _num(values[1])
        if left is None and right is None:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        if key and key not in pairs:
            pairs[key] = (left, right)
    return pairs


def _pick_stat(pairs: dict[str, tuple[Optional[float], Optional[float]]], *needles: str) -> tuple[Optional[float], Optional[float]]:
    normalised = [re.sub(r"[^a-z0-9]+", " ", n.lower()).strip() for n in needles]
    for needle in normalised:
        if needle in pairs:
            return pairs[needle]
    for key, value in pairs.items():
        if any(needle in key for needle in normalised):
            return value
    return None, None


def _extract_formation_and_players(payload: Any) -> dict[str, tuple[str, list[PlayerAppearance]]]:
    result: dict[str, tuple[str, list[PlayerAppearance]]] = {}
    for node in _walk(payload):
        if not isinstance(node, dict):
            continue
        team_name = _canonical(str(node.get("teamName") or node.get("name") or ""))
        players = node.get("players")
        if not team_name or not isinstance(players, list):
            continue
        formation = str(node.get("formation") or "")
        parsed: list[PlayerAppearance] = []
        for item in players:
            if not isinstance(item, dict):
                continue
            player = item.get("player") if isinstance(item.get("player"), dict) else item
            name = str(player.get("name") or player.get("shortName") or player.get("lastName") or "").strip()
            if not name:
                continue
            pos_raw = str(
                item.get("position") or player.get("position") or item.get("positionStringShort")
                or player.get("positionStringShort") or item.get("role") or ""
            ).upper()
            if "GK" in pos_raw or "KEEP" in pos_raw:
                pos = "GKP"
            elif any(x in pos_raw for x in ("CB", "LB", "RB", "DEF", "BACK")):
                pos = "DEF"
            elif any(x in pos_raw for x in ("FW", "ST", "ATT", "FORW")):
                pos = "FWD"
            else:
                pos = "MID"
            starter = bool(item.get("isStarter", item.get("starter", True)))
            rating = _num(item.get("rating") or player.get("rating"))
            minutes = _num(item.get("minutes") or player.get("minutes"))
            parsed.append(PlayerAppearance(name=name, position=pos, starter=starter, rating=rating, minutes=minutes))
        if parsed:
            result[team_name] = (formation, parsed)
    return result


def parse_match_detail(ref: LeagueMatchRef, payload: Any) -> tuple[Optional[MatchPerformance], Optional[MatchPerformance]]:
    pairs = _stat_pairs(payload)
    xg = _pick_stat(pairs, "expected goals xg", "expected goals")
    shots = _pick_stat(pairs, "total shots", "shots")
    sot = _pick_stat(pairs, "shots on target")
    big = _pick_stat(pairs, "big chances")
    possession = _pick_stat(pairs, "ball possession", "possession")
    corners = _pick_stat(pairs, "corners")
    passes = _pick_stat(pairs, "accurate passes")
    touches = _pick_stat(pairs, "touches in opposition box", "touches in opponent box")
    formations = _extract_formation_and_players(payload)

    home_form, home_players = formations.get(ref.home_team, ("", []))
    away_form, away_players = formations.get(ref.away_team, ("", []))
    hs = float(ref.home_score or 0)
    aas = float(ref.away_score or 0)
    home = MatchPerformance(
        match_id=ref.match_id,
        kickoff=ref.kickoff,
        opponent=ref.away_team,
        venue="HOME",
        goals_for=hs,
        goals_against=aas,
        xg_for=xg[0], xg_against=xg[1],
        shots_for=shots[0], shots_against=shots[1],
        shots_on_target_for=sot[0], shots_on_target_against=sot[1],
        big_chances_for=big[0], big_chances_against=big[1],
        possession_for=possession[0], possession_against=possession[1],
        corners_for=corners[0], corners_against=corners[1],
        accurate_passes_for=passes[0], accurate_passes_against=passes[1],
        touches_box_for=touches[0], touches_box_against=touches[1],
        formation=home_form,
        players=home_players,
    )
    away = MatchPerformance(
        match_id=ref.match_id,
        kickoff=ref.kickoff,
        opponent=ref.home_team,
        venue="AWAY",
        goals_for=aas,
        goals_against=hs,
        xg_for=xg[1], xg_against=xg[0],
        shots_for=shots[1], shots_against=shots[0],
        shots_on_target_for=sot[1], shots_on_target_against=sot[0],
        big_chances_for=big[1], big_chances_against=big[0],
        possession_for=possession[1], possession_against=possession[0],
        corners_for=corners[1], corners_against=corners[0],
        accurate_passes_for=passes[1], accurate_passes_against=passes[0],
        touches_box_for=touches[1], touches_box_against=touches[0],
        formation=away_form,
        players=away_players,
    )
    return home, away


# ---------------------------------------------------------------------------
# Expected XI and player-strength model
# ---------------------------------------------------------------------------


def _normalise_name(name: str) -> str:
    return engine.normalise_text(name or "")


def _fpl_strength(player: FPLPlayerProfile) -> float:
    # 0..100 proxy. It deliberately mixes expected role, market price and
    # performance rather than treating fantasy points as football truth.
    position_price_anchor = {"GKP": 4.5, "DEF": 4.5, "MID": 5.5, "FWD": 6.0}.get(player.position, 5.0)
    price = max(0.0, min(1.0, (player.price / position_price_anchor - 0.75) / 1.25))
    ppg = max(0.0, min(1.0, player.points_per_game / 7.0))
    xgi = max(0.0, min(1.0, player.xgi90 / 0.85)) if player.position in {"MID", "FWD"} else 0.35
    role = max(0.0, min(1.0, max(player.starts / 5.0, player.minutes / 450.0)))
    if player.position == "GKP":
        specialist = max(0.0, min(1.0, (player.saves / max(1.0, player.minutes / 90.0)) / 5.0))
    elif player.position == "DEF":
        specialist = max(0.0, min(1.0, player.clean_sheets / 6.0))
    else:
        specialist = max(0.0, min(1.0, (player.goals + player.assists) / 6.0))
    return 100.0 * (0.30 * role + 0.25 * price + 0.20 * ppg + 0.15 * xgi + 0.10 * specialist)


def _recent_player_evidence(matches: list[MatchPerformance]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for recency, match in enumerate(matches):
        weight = max(0.35, 1.0 - recency * 0.14)
        for appearance in match.players:
            key = _normalise_name(appearance.name)
            if not key:
                continue
            item = evidence.setdefault(key, {"name": appearance.name, "position": appearance.position, "starts": 0, "weighted_starts": 0.0, "ratings": [], "minutes": 0.0})
            if appearance.starter:
                item["starts"] += 1
                item["weighted_starts"] += weight
            if appearance.rating is not None:
                item["ratings"].append(float(appearance.rating))
            if appearance.minutes is not None:
                item["minutes"] += float(appearance.minutes)
    return evidence


def build_expected_xi(team: str, recent_matches: list[MatchPerformance], fpl_players: list[FPLPlayerProfile]) -> list[ExpectedXIPlayer]:
    recent = _recent_player_evidence(recent_matches)
    candidates: list[ExpectedXIPlayer] = []

    for player in fpl_players:
        key = _normalise_name(player.name)
        evidence = recent.get(key)
        # Name matching fallback: FotMob and FPL sometimes use first/last-name variants.
        if evidence is None:
            for rkey, value in recent.items():
                if key and rkey and (key in rkey or rkey in key):
                    evidence = value
                    break
        recent_starts = int(evidence.get("starts", 0)) if evidence else 0
        weighted_starts = float(evidence.get("weighted_starts", 0.0)) if evidence else 0.0
        ratings = list(evidence.get("ratings", [])) if evidence else []
        avg_rating = sum(ratings) / len(ratings) if ratings else None
        sample = max(1, min(RECENT_MATCH_LIMIT, len(recent_matches)))
        start_freq = min(1.0, weighted_starts / max(1.0, sum(max(0.35, 1.0 - i * 0.14) for i in range(sample))))
        historical_role = min(1.0, max(player.starts / max(1.0, sample), player.minutes / max(90.0, sample * 90.0)))
        start_probability = (0.62 * start_freq + 0.38 * historical_role) * (0.20 + 0.80 * player.chance)
        if not recent_matches:
            start_probability = historical_role * (0.20 + 0.80 * player.chance)
        rating_component = 50.0 if avg_rating is None else max(0.0, min(100.0, (avg_rating - 5.5) / 2.5 * 100.0))
        strength = 0.74 * _fpl_strength(player) + 0.26 * rating_component
        strength *= 0.35 + 0.65 * player.chance
        note = ""
        if player.chance < 0.99:
            note = f"{int(round(player.chance * 100))}% availability"
            if player.news:
                note += f" — {player.news}"
        candidates.append(
            ExpectedXIPlayer(
                name=player.name,
                position=player.position,
                start_probability=max(0.0, min(1.0, start_probability)),
                strength=max(0.0, min(100.0, strength)),
                availability=player.chance,
                recent_starts=recent_starts,
                average_rating=avg_rating,
                note=note,
            )
        )

    # A stable football-shaped XI: one keeper, four defenders, four midfielders,
    # two forwards. FPL MID contains many wide attackers, so 4-4-2 here is a
    # selection constraint rather than a predicted tactical formation.
    slots = {"GKP": 1, "DEF": 4, "MID": 4, "FWD": 2}
    selected: list[ExpectedXIPlayer] = []
    for position, count in slots.items():
        pool = [p for p in candidates if p.position == position]
        pool.sort(key=lambda p: (p.start_probability, p.strength), reverse=True)
        selected.extend(pool[:count])

    if len(selected) < 11:
        used = {_normalise_name(p.name) for p in selected}
        rest = [p for p in candidates if _normalise_name(p.name) not in used]
        rest.sort(key=lambda p: (p.start_probability, p.strength), reverse=True)
        selected.extend(rest[: 11 - len(selected)])
    return selected[:11]


def _position_strength(xi: list[ExpectedXIPlayer]) -> dict[str, float]:
    result: dict[str, float] = {}
    for pos in ("GKP", "DEF", "MID", "FWD"):
        players = [p for p in xi if p.position == pos]
        if players:
            result[pos] = sum(p.strength for p in players) / len(players)
    return result


# ---------------------------------------------------------------------------
# Recent underlying performance and tactical profiles
# ---------------------------------------------------------------------------


def _weighted_average(matches: list[MatchPerformance], attr: str) -> Optional[float]:
    total = 0.0
    denom = 0.0
    for i, match in enumerate(matches[:RECENT_MATCH_LIMIT]):
        value = getattr(match, attr)
        if value is None:
            continue
        weight = 0.82 ** i
        total += float(value) * weight
        denom += weight
    return total / denom if denom else None


def _weighted_diff_score(matches: list[MatchPerformance]) -> float:
    if not matches:
        return 0.0
    total = 0.0
    denom = 0.0
    for i, match in enumerate(matches[:RECENT_MATCH_LIMIT]):
        weight = 0.82 ** i
        xg_diff = ((match.xg_for or match.goals_for) - (match.xg_against or match.goals_against)) / 1.5
        sot_diff = ((match.shots_on_target_for or 0.0) - (match.shots_on_target_against or 0.0)) / 4.0
        big_diff = ((match.big_chances_for or 0.0) - (match.big_chances_against or 0.0)) / 3.0
        goal_diff = (match.goals_for - match.goals_against) / 2.0
        possession_diff = ((match.possession_for or 50.0) - (match.possession_against or 50.0)) / 20.0
        value = 0.48 * xg_diff + 0.20 * sot_diff + 0.12 * big_diff + 0.10 * goal_diff + 0.10 * possession_diff
        total += max(-1.75, min(1.75, value)) * weight
        denom += weight
    return total / denom if denom else 0.0


def build_tactical_profile(matches: list[MatchPerformance]) -> TacticalProfile:
    possession = _weighted_average(matches, "possession_for")
    shots = _weighted_average(matches, "shots_for")
    sot = _weighted_average(matches, "shots_on_target_for")
    xg = _weighted_average(matches, "xg_for")
    xga = _weighted_average(matches, "xg_against")
    corners = _weighted_average(matches, "corners_for")
    corners_against = _weighted_average(matches, "corners_against")
    touches = _weighted_average(matches, "touches_box_for")
    xg_per_shot = xg / shots if xg is not None and shots and shots > 0 else None
    opp_sot = _weighted_average(matches, "shots_on_target_against")
    suppression = None if xga is None and opp_sot is None else 0.0
    if suppression is not None:
        if xga is not None:
            suppression += max(-1.0, min(1.0, (1.25 - xga) / 0.75)) * 0.65
        if opp_sot is not None:
            suppression += max(-1.0, min(1.0, (4.0 - opp_sot) / 3.0)) * 0.35

    labels: list[str] = []
    if possession is not None:
        if possession >= 56:
            labels.append("possession control")
        elif possession <= 44:
            labels.append("lower-possession / transition")
    if shots is not None and shots >= 13.5:
        labels.append("high shot volume")
    if xg_per_shot is not None and xg_per_shot >= 0.12:
        labels.append("high chance quality")
    if corners is not None and corners >= 5.8:
        labels.append("set-piece / territory threat")
    if suppression is not None and suppression >= 0.35:
        labels.append("strong defensive suppression")
    if xg is not None and xga is not None and xg + xga >= 3.0:
        labels.append("open-game profile")
    if not labels:
        labels.append("balanced recent profile")

    return TacticalProfile(
        labels=tuple(labels[:4]),
        possession=possession,
        shots=shots,
        shots_on_target=sot,
        xg=xg,
        xga=xga,
        xg_per_shot=xg_per_shot,
        corners=corners,
        corners_against=corners_against,
        touches_box=touches,
        defensive_suppression=suppression,
    )


def _quality(recent_count: int, xi_count: int, xg_count: int) -> str:
    score = 0
    score += 1 if recent_count >= 2 else 0
    score += 1 if recent_count >= 4 else 0
    score += 1 if xi_count >= 9 else 0
    score += 1 if xg_count >= 2 else 0
    if score >= 4:
        return "HIGH"
    if score >= 2:
        return "MEDIUM"
    return "LOW"


def build_team_intelligence(team: str, matches: list[MatchPerformance], fpl_players: list[FPLPlayerProfile]) -> TeamIntelligence:
    matches = sorted(matches, key=lambda x: x.kickoff or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:RECENT_MATCH_LIMIT]
    xi = build_expected_xi(team, matches, fpl_players)
    xi_strength = sum(p.strength for p in xi) / len(xi) if xi else None
    position_strength = _position_strength(xi)
    tactical = build_tactical_profile(matches)
    latest_formation = next((m.formation for m in matches if m.formation), "")
    xg_count = sum(1 for m in matches if m.xg_for is not None and m.xg_against is not None)
    return TeamIntelligence(
        team=team,
        expected_xi=xi,
        xi_strength=xi_strength,
        position_strength=position_strength,
        recent_matches=matches,
        form_score=_weighted_diff_score(matches),
        tactical=tactical,
        latest_formation=latest_formation,
        data_quality=_quality(len(matches), len(xi), xg_count),
    )


# ---------------------------------------------------------------------------
# Match-level automatic ratings
# ---------------------------------------------------------------------------


def _clamp3(value: float) -> float:
    return max(-3.0, min(3.0, float(value)))


def _fmt_metric(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _match_intelligence(row: CombinedMatch, home: Optional[TeamIntelligence], away: Optional[TeamIntelligence]) -> MatchIntelligence:
    if home is None or away is None:
        return MatchIntelligence(
            match_name=row.match_name,
            home_team=row.home_team,
            away_team=row.away_team,
            home=home,
            away=away,
            data_quality="LOW",
            reasons=("Automatic football data is incomplete for one or both teams.",),
        )

    reasons: list[str] = []
    xi_rating = 0.0
    if home.xi_strength is not None and away.xi_strength is not None:
        diff = home.xi_strength - away.xi_strength
        xi_rating = _clamp3(diff / 7.5)
        if abs(xi_rating) >= 0.35:
            better = row.home_team if xi_rating > 0 else row.away_team
            reasons.append(f"The estimated starting XI rates stronger for {better}.")

    form_rating = _clamp3((home.form_score - away.form_score) * 1.45)
    hxg, axg = home.tactical.xg, away.tactical.xg
    hxga, axga = home.tactical.xga, away.tactical.xga
    if abs(form_rating) >= 0.35:
        better = row.home_team if form_rating > 0 else row.away_team
        reasons.append(
            f"Recent underlying performance leans {better} "
            f"({row.home_team} xG {_fmt_metric(hxg)} / xGA {_fmt_metric(hxga)}; "
            f"{row.away_team} xG {_fmt_metric(axg)} / xGA {_fmt_metric(axga)})."
        )

    # Tactical matchup: compare attacking creation to the opponent's recent
    # defensive suppression, with smaller set-piece and chance-quality terms.
    h_attack = (hxg if hxg is not None else 1.25) - (axga if axga is not None else 1.25)
    a_attack = (axg if axg is not None else 1.25) - (hxga if hxga is not None else 1.25)
    tactical_raw = (h_attack - a_attack) / 0.85
    if home.tactical.xg_per_shot is not None and away.tactical.xg_per_shot is not None:
        tactical_raw += (home.tactical.xg_per_shot - away.tactical.xg_per_shot) / 0.08 * 0.35
    if home.tactical.corners is not None and away.tactical.corners is not None:
        h_set = home.tactical.corners - (away.tactical.corners_against or 5.0)
        a_set = away.tactical.corners - (home.tactical.corners_against or 5.0)
        tactical_raw += (h_set - a_set) / 4.0 * 0.25
    tactical_rating = _clamp3(tactical_raw)
    if abs(tactical_rating) >= 0.45:
        better = row.home_team if tactical_rating > 0 else row.away_team
        profile = home.tactical if tactical_rating > 0 else away.tactical
        reasons.append(f"The recent style matchup slightly favours {better}: {', '.join(profile.labels[:2])}.")

    rest_rating = 0.0
    if row.kickoff and home.recent_matches and away.recent_matches:
        hlast = home.recent_matches[0].kickoff
        alast = away.recent_matches[0].kickoff
        if hlast and alast:
            hrest = max(0.0, (row.kickoff.astimezone(timezone.utc) - hlast).total_seconds() / 86400.0)
            arest = max(0.0, (row.kickoff.astimezone(timezone.utc) - alast).total_seconds() / 86400.0)
            home.rest_days, away.rest_days = hrest, arest
            rest_rating = _clamp3((hrest - arest) / 2.5)
            if abs(rest_rating) >= 0.55:
                better = row.home_team if rest_rating > 0 else row.away_team
                reasons.append(f"Rest between matches gives a small scheduling edge to {better}.")

    overall = _clamp3(0.34 * xi_rating + 0.34 * form_rating + 0.24 * tactical_rating + 0.08 * rest_rating)
    if not reasons:
        reasons.append("Expected XI, recent performance and style are close enough that the football layer adds little extra lean.")
    qualities = {home.data_quality, away.data_quality}
    if qualities == {"HIGH"}:
        quality = "HIGH"
    elif "LOW" in qualities:
        quality = "LOW"
    else:
        quality = "MEDIUM"
    return MatchIntelligence(
        match_name=row.match_name,
        home_team=row.home_team,
        away_team=row.away_team,
        home=home,
        away=away,
        xi_rating=xi_rating,
        recent_form_rating=form_rating,
        tactical_rating=tactical_rating,
        rest_rating=rest_rating,
        overall_rating=overall,
        data_quality=quality,
        reasons=tuple(reasons[:4]),
    )


def merge_context_inputs(manual: ContextInputs, intelligence: Optional[MatchIntelligence]) -> ContextInputs:
    """Add V1.8 automatic football signals to the existing small context layer.

    Existing FPL availability is still added separately by context_model. The
    expected-XI rating therefore gets a reduced coefficient to avoid counting
    availability twice.
    """
    if intelligence is None:
        return manual
    return ContextInputs(
        player_lineup=float(manual.player_lineup) + 0.55 * intelligence.xi_rating,
        recent_performance=float(manual.recent_performance) + intelligence.recent_form_rating,
        tactical_matchup=float(manual.tactical_matchup) + intelligence.tactical_rating,
        manager_coaching=float(manual.manager_coaching),
        transfer_squad=float(manual.transfer_squad),
        schedule_rest=float(manual.schedule_rest) + 0.65 * intelligence.rest_rating,
        home_transfer_spend_m=manual.home_transfer_spend_m,
        away_transfer_spend_m=manual.away_transfer_spend_m,
        home_manager=manual.home_manager,
        away_manager=manual.away_manager,
        notes=manual.notes,
    )


def context_adjustment_v18(
    row: CombinedMatch,
    manual: ContextInputs,
    fpl_context: Optional[dict[str, FPLTeamContext]],
    intelligence: Optional[MatchIntelligence],
    max_shift_pp: float = 1.50,
):
    return context_adjustment_for_match(
        row,
        merge_context_inputs(manual, intelligence),
        fpl_context,
        max_shift_pp=max_shift_pp,
    )


def intelligence_plain_summary(intel: Optional[MatchIntelligence]) -> str:
    if intel is None:
        return "Automatic football intelligence is not available for this match yet."
    direction = "neutral"
    if intel.overall_rating > 0.30:
        direction = f"leans {intel.home_team}"
    elif intel.overall_rating < -0.30:
        direction = f"leans {intel.away_team}"
    reasons = " ".join(intel.reasons[:3])
    return f"Football data {direction}. Data quality: {intel.data_quality.title()}. {reasons}"


# ---------------------------------------------------------------------------
# Full refresh orchestration
# ---------------------------------------------------------------------------


def _recent_refs_by_team(refs: list[LeagueMatchRef], limit: int = RECENT_MATCH_LIMIT) -> dict[str, list[LeagueMatchRef]]:
    finished = [r for r in refs if r.finished]
    finished.sort(key=lambda r: r.kickoff or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    result: dict[str, list[LeagueMatchRef]] = {}
    for ref in finished:
        for team in (ref.home_team, ref.away_team):
            bucket = result.setdefault(team, [])
            if len(bucket) < limit:
                bucket.append(ref)
    return result


def refresh_football_intelligence(rows: list[CombinedMatch]) -> IntelligenceBundle:
    notes: list[str] = []
    league_payload = None
    fpl_payload = None
    refs: list[LeagueMatchRef] = []
    try:
        league_payload = fetch_fotmob_league()
        refs = extract_league_match_refs(league_payload)
    except Exception as exc:
        notes.append(f"FotMob unavailable: {exc}")
    try:
        fpl_payload = fetch_fpl_bootstrap()
    except Exception as exc:
        notes.append(f"FPL player data unavailable: {exc}")

    fpl_players = parse_fpl_players(fpl_payload) if fpl_payload is not None else {}
    recent_refs = _recent_refs_by_team(refs)
    needed_ids: list[int] = []
    for row in rows:
        for team in (row.home_team, row.away_team):
            for ref in recent_refs.get(team, []):
                if ref.match_id not in needed_ids:
                    needed_ids.append(ref.match_id)
    needed_ids = needed_ids[:MAX_DETAIL_REQUESTS_PER_REFRESH]

    ref_map = {ref.match_id: ref for ref in refs}
    team_matches: dict[str, list[MatchPerformance]] = {}
    uncached_requests = 0
    for match_id in needed_ids:
        ref = ref_map.get(match_id)
        if ref is None:
            continue
        cache_name = f"fotmob_match_{match_id}"
        was_cached = _load_cache(cache_name, FINISHED_MATCH_CACHE_SECONDS) is not None
        try:
            detail = fetch_fotmob_match_details(match_id)
            home_perf, away_perf = parse_match_detail(ref, detail)
            if home_perf:
                team_matches.setdefault(ref.home_team, []).append(home_perf)
            if away_perf:
                team_matches.setdefault(ref.away_team, []).append(away_perf)
            if not was_cached:
                uncached_requests += 1
                time.sleep(REQUEST_PAUSE_SECONDS)
        except Exception as exc:
            notes.append(f"FotMob match {match_id} skipped: {exc}")
            if len(notes) > 6:
                break

    team_names = {team for row in rows for team in (row.home_team, row.away_team)}
    teams: dict[str, TeamIntelligence] = {}
    for team in team_names:
        teams[team] = build_team_intelligence(
            team,
            team_matches.get(team, []),
            fpl_players.get(team, []),
        )

    matches = {
        row.match_name: _match_intelligence(row, teams.get(row.home_team), teams.get(row.away_team))
        for row in rows
    }
    if refs:
        notes.insert(0, f"FotMob: {len(refs)} EPL fixtures/results indexed; {uncached_requests} new detailed match request(s).")
    if fpl_players:
        notes.insert(0, f"FPL: player profiles loaded for {len(fpl_players)} EPL teams.")
    return IntelligenceBundle(
        teams=teams,
        matches=matches,
        league_matches=refs,
        source_notes=tuple(notes[:8]),
        refreshed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
