# EPL Value Betting — V1.8 Football Intelligence and Validation

V1.8 keeps the V1.5–V1.7 market model as the anchor and adds four larger research capabilities:

1. expected XI and player-strength proxies;
2. automatic recent underlying performance;
3. automatic tactical/style profiles and matchup signals;
4. recommendation, result and closing-line validation.

The main principle is unchanged:

> Football context may nudge an independently supported market estimate, but it is not allowed to manufacture a dashboard recommendation from a negative base-market EV.

## Free data sources

V1.8 requires no new paid sports-data subscription.

### Fantasy Premier League

The public FPL bootstrap feed is used for current EPL player records, availability, minutes, starts, prices and selected performance fields.

Clear transfer/loan departures are ignored so stale FPL records do not become false injury penalties.

### FotMob public web JSON

V1.8 optionally uses the same public JSON responses used by FotMob's web pages for:

- EPL fixture/result IDs;
- recent match xG;
- shots and shots on target;
- big chances;
- possession;
- corners;
- formations;
- recent line-ups and player ratings where exposed.

FotMob is an unofficial/undocumented dependency from the app's perspective and may change its schema. Therefore:

- the provider is optional;
- failure never disables the core betting-market model;
- responses are cached locally;
- finished-match details are cached for seven days;
- new detailed-match requests are capped per refresh;
- requests are deliberately spaced rather than hammered.

## Expected XI

V1.8 estimates an expected XI rather than pretending to know a confirmed line-up before it is announced.

Each current FPL player receives a start-probability proxy based on:

- starts;
- minutes;
- recent FotMob starting appearances;
- recency of those starts;
- current FPL chance of playing.

A player-strength proxy combines:

- likely role/playing time;
- relative FPL price within the player's position;
- points per game;
- expected goal involvement for attacking positions;
- limited position-specific indicators;
- recent FotMob rating when available;
- current availability.

The selected XI uses one goalkeeper, four defenders, four FPL midfielders and two forwards as a stable selection constraint. This is not claimed to be the team's tactical formation. The most recent observed formation is displayed separately.

### Position strength

The expected XI is summarised as:

- GKP strength;
- DEF strength;
- MID strength;
- FWD strength;
- overall XI strength.

These are research proxies, not FIFA-style absolute player ratings.

## Recent underlying performance

V1.8 uses up to the five most recent EPL match-detail records available from FotMob and gives newer matches more weight.

The recent-performance score prioritises:

1. xG difference;
2. shots-on-target difference;
3. big-chance difference;
4. actual goal difference;
5. possession difference.

Actual goals receive less weight than xG/chance-generation information so one lucky result does not dominate the form signal.

The displayed rolling profile includes, where available:

- xG / xGA;
- shots / shots against;
- shots on target;
- big chances;
- possession;
- corners;
- formation.

## Tactical profile

V1.8 deliberately avoids claiming to measure high pressing when PPDA or equivalent pressure data is not available.

Instead it creates transparent recent-data descriptors such as:

- possession control;
- lower-possession / transition profile;
- high shot volume;
- high chance quality;
- set-piece / territory threat;
- strong defensive suppression;
- open-game profile.

The matchup rating compares the teams' recent attack/defence interaction using:

- xG creation versus opponent xGA;
- xG per shot;
- corner/set-piece territory proxies;
- recent defensive suppression.

This produces a small `-3..+3` research rating, positive for the home side and negative for the away side.

## Rest and schedule

The most recent completed match date is compared with the upcoming kick-off to create a small rest difference signal. Its influence remains intentionally smaller than expected XI, recent performance and tactical information.

## How V1.8 enters the probability model

The automatic football model creates four match ratings:

```text
Expected XI
Recent underlying performance
Tactical matchup
Rest / schedule
```

They are fed into the existing contextual layer alongside optional user-entered research.

The existing contextual weights remain:

```text
Player / expected line-up             35%
Recent underlying performance         25%
Tactical / style matchup              20%
Manager / coaching                    10%
Transfer / squad change                5%
Schedule / rest / travel               5%
```

Automatic expected-XI data receives a reduced coefficient before entering the player factor because FPL availability is already included separately. This reduces double-counting.

The total context probability movement remains capped by the user's existing setting, default **1.50 percentage points**.

### Dashboard guardrail

A V1.8 single-outcome dashboard recommendation requires:

```text
base independent-market EV >= 0%
AND
V1.8 context-adjusted EV >= configured threshold
```

So a strong football narrative cannot turn a clearly negative market price into a recommended edge.

## Recommendation persistence

When a V1.8 dashboard idea qualifies, the first observation is persisted in `decision_recommendations`.

The database records:

- event and selection;
- first detection time;
- first Sportsbet odds;
- model probability;
- base EV;
- context-adjusted EV;
- conservative EV;
- market confidence;
- football-data quality;
- expected-XI rating;
- recent-form rating;
- tactical rating;
- rest rating;
- context probability shift.

Later sightings update the last-seen time/price without rewriting the original decision point.

## Historical backfill

Existing V1.5+ `edge_model_snapshots` can be backfilled into the new recommendation table using the first historical snapshot where a side crossed the EV threshold.

These rows are marked `HISTORICAL-BACKFILL`. They are useful for exploratory research but are not treated as if the V1.8 dashboard genuinely issued the recommendation at that time.

## Match results

Completed EPL scores from the free football feed are matched to stored events by home team, away team and kick-off proximity.

For every stored recommendation V1.8 can then calculate a simple flat-stake realised result:

```text
win  = (decimal odds - 1) * 100%
loss = -100%
```

This is a research ROI, not a staking recommendation.

## Closing-line value

For each recommendation V1.8 searches the stored Sportsbet snapshots for the last price captured before kick-off.

The app labels this carefully:

- `CLOSE` — captured within 90 minutes of kick-off;
- `NEAR CLOSE` — within six hours;
- `LAST OBSERVED` — older than six hours;
- `UNKNOWN` — timing unavailable.

Only the first category is genuinely close to a conventional closing price. The app does not pretend an old stored price is a true market close.

V1.8 reports price CLV as:

```text
CLV % = first flagged odds / last observed pre-kickoff odds - 1
```

Example:

```text
Flagged at 2.00
Last observed pre-kickoff 1.80
CLV = +11.11%
```

Positive CLV means the price moved in the same direction as the original model edge.

## Validation metrics

The Validation tab reports:

- recommendation count;
- settled count;
- flat-stake ROI;
- average CLV;
- percentage of observations with positive CLV;
- binary Brier score for recommended selections;
- the number of settled recommendations with a close/near-close stored price.

Small samples should not be used to conclude that the model is profitable.

## What V1.8 still does not claim

V1.8 does **not** claim that:

- FPL fantasy metrics are perfect player ratings;
- an estimated XI is a confirmed line-up;
- the tactical proxy directly measures pressing intensity;
- an unofficial FotMob JSON schema is guaranteed to remain stable;
- a last-observed price many hours before kick-off is the real closing line;
- historical backfilled recommendations are equivalent to prospective signals;
- positive short-run ROI proves an edge.

The purpose of V1.8 is to create a richer, auditable research dataset so those hypotheses can finally be tested rather than assumed.
