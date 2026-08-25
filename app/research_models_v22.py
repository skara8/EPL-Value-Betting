from __future__ import annotations

import csv
import io
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

import engine
from config import DATA_DIR
from engine import CombinedMatch
from football_intelligence import IntelligenceBundle, TeamIntelligence


FOOTBALL_DATA_BASE = "https://www.football-data.co.uk/mmz4281"
HISTORY_CACHE = DATA_DIR / "cache" / "historical_models_v22"
HISTORY_CACHE.mkdir(parents=True, exist_ok=True)
HISTORICAL_SEASONS = 5
CURRENT_TTL_SECONDS = 6 * 60 * 60
ARCHIVE_TTL_SECONDS = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class HistoricalMatch:
    kickoff: datetime
    season: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int


@dataclass
class ResearchMatchFeatures:
    match_name: str
    market_home: Optional[float]
    market_draw: Optional[float]
    market_away: Optional[float]
    elo_home_rating: Optional[float]
    elo_away_rating: Optional[float]
    elo_home: Optional[float]
    elo_draw: Optional[float]
    elo_away: Optional[float]
    poisson_home: Optional[float]
    poisson_draw: Optional[float]
    poisson_away: Optional[float]
    poisson_lambda_home: Optional[float]
    poisson_lambda_away: Optional[float]
    lineup_home: Optional[float]
    lineup_away: Optional[float]
    lineup_diff_pp: Optional[float]
    home_recent_net_xg: Optional[float]
    away_recent_net_xg: Optional[float]
    home_recent_opponent_elo: Optional[float]
    away_recent_opponent_elo: Optional[float]
    market_research_disagreement_pp: Optional[float]
    consensus: str
    history_matches: int
    data_quality: str


TEAM_ALIASES = {
    "man united": "Manchester United",
    "man utd": "Manchester United",
    "man city": "Manchester City",
    "nottm forest": "Nottingham Forest",
    "nott m forest": "Nottingham Forest",
    "brighton": "Brighton & Hove Albion",
    "tottenham": "Tottenham Hotspur",
    "wolves": "Wolverhampton Wanderers",
    "west ham": "West Ham United",
    "newcastle": "Newcastle United",
    "leicester": "Leicester City",
    "sheffield united": "Sheffield United",
    "sheffield utd": "Sheffield United",
    "luton": "Luton Town",
    "burnley": "Burnley",
    "southampton": "Southampton",
}


def canonical_team(value: str) -> str:
    current = engine.canonical_epl_club(value)
    if current:
        return current
    key = engine.normalise_text(value)
    if key in TEAM_ALIASES:
        return TEAM_ALIASES[key]
    return " ".join(part.capitalize() for part in key.split())


def _current_season_start_year(now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def season_code(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def recent_season_codes(count: int = HISTORICAL_SEASONS, now: Optional[datetime] = None) -> list[str]:
    current = _current_season_start_year(now)
    first = current - max(1, int(count)) + 1
    return [season_code(year) for year in range(first, current + 1)]


def _cache_file(code: str) -> Path:
    return HISTORY_CACHE / f"football_data_E0_{code}.csv"


def _read_or_download(code: str, current_code: str) -> tuple[Optional[str], str]:
    path = _cache_file(code)
    ttl = CURRENT_TTL_SECONDS if code == current_code else ARCHIVE_TTL_SECONDS
    if path.exists():
        try:
            if time.time() - path.stat().st_mtime <= ttl:
                return path.read_text(encoding="utf-8-sig"), "cache"
        except Exception:
            pass

    url = f"{FOOTBALL_DATA_BASE}/{code}/E0.csv"
    try:
        response = requests.get(
            url,
            timeout=18,
            headers={"User-Agent": "Football-Value-Betting/2.2", "Accept": "text/csv,*/*;q=0.8"},
        )
        if response.status_code >= 400:
            # A brand-new season file may not exist yet. Keep any stale cache.
            if path.exists():
                return path.read_text(encoding="utf-8-sig"), f"stale-cache HTTP {response.status_code}"
            return None, f"HTTP {response.status_code}"
        text = response.content.decode("utf-8-sig", errors="replace")
        if "HomeTeam" not in text or "AwayTeam" not in text:
            return None, "unexpected CSV"
        path.write_text(text, encoding="utf-8")
        return text, "download"
    except Exception as exc:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8-sig"), f"stale-cache {type(exc).__name__}"
            except Exception:
                pass
        return None, f"{type(exc).__name__}: {exc}"


def _parse_date(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def load_epl_history(count: int = HISTORICAL_SEASONS) -> tuple[list[HistoricalMatch], tuple[str, ...]]:
    codes = recent_season_codes(count)
    current = codes[-1]
    matches: list[HistoricalMatch] = []
    notes: list[str] = []
    for code in codes:
        text, source = _read_or_download(code, current)
        if not text:
            notes.append(f"EPL {code}: unavailable ({source})")
            continue
        parsed = 0
        for row in csv.DictReader(io.StringIO(text)):
            dt = _parse_date(row.get("Date", ""))
            try:
                hg = int(float(row.get("FTHG", "")))
                ag = int(float(row.get("FTAG", "")))
            except (TypeError, ValueError):
                continue
            home = canonical_team(row.get("HomeTeam", ""))
            away = canonical_team(row.get("AwayTeam", ""))
            if not dt or not home or not away:
                continue
            matches.append(HistoricalMatch(dt, code, home, away, hg, ag))
            parsed += 1
        notes.append(f"EPL {code}: {parsed} results ({source})")
    matches.sort(key=lambda m: m.kickoff)
    return matches, tuple(notes)


def elo_ratings_before(
    matches: Iterable[HistoricalMatch],
    cutoff: datetime,
    k: float = 24.0,
    home_advantage: float = 65.0,
    season_regression: float = 0.18,
) -> dict[str, float]:
    ratings: dict[str, float] = defaultdict(lambda: 1500.0)
    last_season: Optional[str] = None
    for match in matches:
        if match.kickoff >= cutoff:
            break
        if last_season is not None and match.season != last_season:
            # Regress slightly toward the league mean between seasons. This is
            # deliberately conservative and reduces stale multi-season strength.
            for team in list(ratings):
                ratings[team] = 1500.0 + (ratings[team] - 1500.0) * (1.0 - season_regression)
        last_season = match.season
        rh = ratings[match.home_team]
        ra = ratings[match.away_team]
        expected = 1.0 / (1.0 + 10.0 ** ((ra - (rh + home_advantage)) / 400.0))
        score = 1.0 if match.home_goals > match.away_goals else 0.0 if match.home_goals < match.away_goals else 0.5
        delta = k * (score - expected)
        ratings[match.home_team] = rh + delta
        ratings[match.away_team] = ra - delta
    return dict(ratings)


def elo_three_way(
    home_rating: float,
    away_rating: float,
    draw_anchor: float,
    home_advantage: float = 65.0,
) -> tuple[float, float, float]:
    draw = max(0.12, min(0.40, float(draw_anchor)))
    decisive_home = 1.0 / (1.0 + 10.0 ** ((away_rating - (home_rating + home_advantage)) / 400.0))
    home = (1.0 - draw) * decisive_home
    away = (1.0 - draw) * (1.0 - decisive_home)
    return home, draw, away


def _poisson_probs(lambda_home: float, lambda_away: float, max_goals: int = 10) -> tuple[float, float, float]:
    home = [math.exp(-lambda_home)]
    away = [math.exp(-lambda_away)]
    for i in range(1, max_goals + 1):
        home.append(home[-1] * lambda_home / i)
        away.append(away[-1] * lambda_away / i)
    mass = sum(home) * sum(away)
    ph = pd = pa = 0.0
    for h, hp in enumerate(home):
        for a, ap in enumerate(away):
            p = hp * ap / mass
            if h > a:
                ph += p
            elif h == a:
                pd += p
            else:
                pa += p
    return ph, pd, pa


def time_decayed_poisson_before(
    matches: Iterable[HistoricalMatch],
    home_team: str,
    away_team: str,
    cutoff: datetime,
    half_life_days: float = 180.0,
    max_age_days: int = 1000,
    prior_matches: float = 5.0,
) -> Optional[tuple[float, float, float, float, float, int]]:
    relevant = []
    counts: Counter[str] = Counter()
    for match in matches:
        if match.kickoff >= cutoff:
            break
        age = (cutoff - match.kickoff).total_seconds() / 86400.0
        if age < 0 or age > max_age_days:
            continue
        weight = 0.5 ** (age / max(1.0, half_life_days))
        relevant.append((match, weight))
        counts[match.home_team] += 1
        counts[match.away_team] += 1

    if counts[home_team] < 4 or counts[away_team] < 4 or not relevant:
        return None

    total_weight = sum(w for _, w in relevant)
    if total_weight <= 0:
        return None
    league_home = sum(m.home_goals * w for m, w in relevant) / total_weight
    league_away = sum(m.away_goals * w for m, w in relevant) / total_weight
    if league_home <= 0.2 or league_away <= 0.2:
        return None

    def venue_average(team: str, venue: str, goals_for: bool) -> tuple[float, float]:
        weighted = weight_sum = 0.0
        for m, w in relevant:
            if venue == "home" and m.home_team == team:
                value = m.home_goals if goals_for else m.away_goals
            elif venue == "away" and m.away_team == team:
                value = m.away_goals if goals_for else m.home_goals
            else:
                continue
            weighted += value * w
            weight_sum += w
        prior = league_home if (venue == "home" and goals_for) or (venue == "away" and not goals_for) else league_away
        return (weighted + prior_matches * prior) / (weight_sum + prior_matches), weight_sum

    home_gf, _ = venue_average(home_team, "home", True)
    home_ga, _ = venue_average(home_team, "home", False)
    away_gf, _ = venue_average(away_team, "away", True)
    away_ga, _ = venue_average(away_team, "away", False)

    home_attack = home_gf / league_home
    away_defence_weakness = away_ga / league_home
    away_attack = away_gf / league_away
    home_defence_weakness = home_ga / league_away

    lambda_home = max(0.25, min(4.5, league_home * home_attack * away_defence_weakness))
    lambda_away = max(0.20, min(4.0, league_away * away_attack * home_defence_weakness))
    probs = _poisson_probs(lambda_home, lambda_away)
    return probs[0], probs[1], probs[2], lambda_home, lambda_away, len(relevant)


def lineup_continuity(team: Optional[TeamIntelligence]) -> Optional[float]:
    if team is None or not team.expected_xi:
        return None
    numerator = denominator = 0.0
    for player in team.expected_xi:
        strength = max(0.25, float(player.strength or 1.0))
        recent_share = max(0.0, min(1.0, float(player.recent_starts) / 5.0))
        availability = max(0.0, min(1.0, float(player.start_probability)))
        numerator += recent_share * availability * strength
        denominator += strength
    return numerator / denominator if denominator > 0 else None


def recent_xg_context(
    team: Optional[TeamIntelligence],
    current_elo: dict[str, float],
) -> tuple[Optional[float], Optional[float]]:
    if team is None:
        return None, None
    net_values: list[tuple[float, float]] = []
    opponent_values: list[tuple[float, float]] = []
    total = len(team.recent_matches)
    for idx, match in enumerate(team.recent_matches):
        if match.xg_for is None or match.xg_against is None:
            continue
        # More recent matches receive more weight while retaining the raw xG
        # number. Opponent Elo is shown separately rather than being hidden in
        # an arbitrary production probability adjustment.
        weight = float(max(1, total - idx))
        net_values.append((float(match.xg_for) - float(match.xg_against), weight))
        opp = canonical_team(match.opponent)
        if opp in current_elo:
            opponent_values.append((current_elo[opp], weight))

    def weighted(items: list[tuple[float, float]]) -> Optional[float]:
        denom = sum(w for _, w in items)
        return sum(v * w for v, w in items) / denom if denom > 0 else None

    return weighted(net_values), weighted(opponent_values)


def _argmax_label(values: Optional[tuple[float, float, float]]) -> Optional[str]:
    if values is None:
        return None
    labels = ("HOME", "DRAW", "AWAY")
    return labels[max(range(3), key=lambda i: values[i])]


def _consensus(market, elo, poisson) -> str:
    labels = [_argmax_label(item) for item in (market, elo, poisson) if item is not None]
    if not labels:
        return "No comparable models"
    counts = Counter(labels)
    side, number = counts.most_common(1)[0]
    if len(labels) == 3 and number == 3:
        return f"3/3 agree · {side.title()}"
    if number >= 2:
        return f"{number}/{len(labels)} agree · {side.title()}"
    return "Models split"


def build_research_features(
    rows: list[CombinedMatch],
    bundle: Optional[IntelligenceBundle],
    history: list[HistoricalMatch],
) -> dict[str, ResearchMatchFeatures]:
    output: dict[str, ResearchMatchFeatures] = {}
    for row in rows:
        home_team = canonical_team(row.home_team)
        away_team = canonical_team(row.away_team)
        # Historical football-data model is deliberately EPL-only. Other
        # leagues keep the market model and simply have no V2.2 research row.
        if engine.canonical_epl_club(home_team) is None or engine.canonical_epl_club(away_team) is None:
            continue

        market = None
        if all(getattr(row, name, None) is not None for name in ("model_fair_home", "model_fair_draw", "model_fair_away")):
            market = (
                float(getattr(row, "model_fair_home")),
                float(getattr(row, "model_fair_draw")),
                float(getattr(row, "model_fair_away")),
            )

        ratings = elo_ratings_before(history, row.kickoff.astimezone(timezone.utc)) if history else {}
        rh = ratings.get(home_team)
        ra = ratings.get(away_team)
        elo = None
        if rh is not None and ra is not None:
            draw_anchor = market[1] if market is not None else 0.25
            elo = elo_three_way(rh, ra, draw_anchor)

        poisson_fit = time_decayed_poisson_before(
            history,
            home_team,
            away_team,
            row.kickoff.astimezone(timezone.utc),
        ) if history else None
        poisson = poisson_fit[:3] if poisson_fit else None

        intel = bundle.matches.get(row.match_name) if bundle else None
        home_intel = intel.home if intel else None
        away_intel = intel.away if intel else None
        lineup_h = lineup_continuity(home_intel)
        lineup_a = lineup_continuity(away_intel)
        xg_h, opp_h = recent_xg_context(home_intel, ratings)
        xg_a, opp_a = recent_xg_context(away_intel, ratings)

        all_probs = [p for p in (market, elo, poisson) if p is not None]
        disagreement = None
        if len(all_probs) >= 2:
            disagreement = max(
                max(values[i] for values in all_probs) - min(values[i] for values in all_probs)
                for i in range(3)
            ) * 100.0

        history_count = sum(
            1 for m in history
            if m.kickoff < row.kickoff.astimezone(timezone.utc)
            and (m.home_team in {home_team, away_team} or m.away_team in {home_team, away_team})
        )
        quality = "HIGH" if history_count >= 60 and intel and intel.data_quality == "HIGH" else "MEDIUM" if history_count >= 25 else "LOW"

        feature = ResearchMatchFeatures(
            match_name=row.match_name,
            market_home=market[0] if market else None,
            market_draw=market[1] if market else None,
            market_away=market[2] if market else None,
            elo_home_rating=rh,
            elo_away_rating=ra,
            elo_home=elo[0] if elo else None,
            elo_draw=elo[1] if elo else None,
            elo_away=elo[2] if elo else None,
            poisson_home=poisson[0] if poisson else None,
            poisson_draw=poisson[1] if poisson else None,
            poisson_away=poisson[2] if poisson else None,
            poisson_lambda_home=poisson_fit[3] if poisson_fit else None,
            poisson_lambda_away=poisson_fit[4] if poisson_fit else None,
            lineup_home=lineup_h,
            lineup_away=lineup_a,
            lineup_diff_pp=(lineup_h - lineup_a) * 100.0 if lineup_h is not None and lineup_a is not None else None,
            home_recent_net_xg=xg_h,
            away_recent_net_xg=xg_a,
            home_recent_opponent_elo=opp_h,
            away_recent_opponent_elo=opp_a,
            market_research_disagreement_pp=disagreement,
            consensus=_consensus(market, elo, poisson),
            history_matches=history_count,
            data_quality=quality,
        )
        output[row.match_name] = feature
        setattr(row, "v22_research", feature)
    return output
