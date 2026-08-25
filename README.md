# EPL Value Betting

Windows desktop research application for analysing EPL bookmaker prices, exchange probabilities, Asian Handicap markets, football context and market-efficiency hypotheses.

## Current version

**V1.8.0**

## What V1.8 adds

V1.8 keeps the simple V1.7 decision-first Dashboard while substantially expanding the analysis that runs underneath it.

New modelling layers:

- expected XI estimation;
- position-group player-strength proxies;
- recent xG/xGA and underlying-performance analysis;
- tactical/style profile and matchup signals;
- automatic rest/schedule signal;
- persistent recommendation tracking;
- completed-result ingestion;
- last-observed pre-kickoff price / closing-line-value research;
- flat-stake ROI, calibration and Brier-score validation.

The Dashboard remains simple: **best theoretical edge, price, EV, confidence and one short explanation**.

## Core market model remains the anchor

Sportsbet is the target price, not the independent probability source used to assess itself.

The fair-probability layer can use:

- Polymarket executable YES asks normalised to 100%;
- Pinnacle/PS3838 power-de-vigged 1X2;
- Pinnacle Asian Handicap + total-goals information converted into an implied Poisson score distribution.

For Sportsbet decimal price `O` and independent fair probability `p`:

```text
break-even probability = 1 / O
EV = p * O - 1
```

Sportsbet is separately power-de-vigged for bookmaker-shading/bias diagnostics, but does not vote in its own fair probability.

## V1.8 football intelligence

### Expected XI

V1.8 estimates the likely XI from free current FPL player data plus recent FotMob starting line-ups where available.

The start-probability proxy considers:

- recent starts;
- minutes;
- recency of starts;
- current chance of playing.

The player-strength proxy uses current role, position-relative FPL price, selected performance fields, availability and recent match rating where available.

The GUI displays:

- expected player;
- position;
- estimated start chance;
- strength proxy;
- recent starts;
- recent rating;
- availability.

These are research proxies, not official player ratings or confirmed line-ups.

### Recent underlying performance

Up to five recent league matches are recency-weighted using available:

- xG / xGA;
- shots;
- shots on target;
- big chances;
- goals;
- possession;
- corners.

The form score deliberately gives more weight to chance-quality information than to raw wins/losses.

### Tactical/style profiles

V1.8 derives transparent profile descriptions such as:

- possession control;
- lower-possession / transition;
- high shot volume;
- high chance quality;
- set-piece / territory threat;
- strong defensive suppression;
- open-game profile.

It does not claim to measure pressing intensity without pressure/PPDA data.

The tactical-matchup rating compares recent chance creation, opponent suppression, shot quality and set-piece territory.

### Rest/schedule

The most recent completed match is compared with the upcoming fixture to identify meaningful rest differences. This receives only a small weight.

## Free-source architecture

V1.8 adds no new paid sports-data subscription.

It uses:

- **Fantasy Premier League public bootstrap data** for current player/squad information;
- **FotMob public web JSON** as an optional source for recent EPL match details such as xG, line-ups and formations.

FotMob is treated as an optional, unofficial dependency:

- failure never stops the core market model;
- responses are cached locally;
- finished-match details are cached for seven days;
- detailed requests are capped and spaced out.

## Context remains deliberately small

The automatic football layer feeds the existing V1.6/V1.7 context model rather than replacing the market model.

The existing default context weights remain:

```text
Player / expected line-up             35%
Recent underlying performance         25%
Tactical / style matchup              20%
Manager / coaching                    10%
Transfer / squad change                5%
Schedule / rest / travel               5%
```

The total contextual probability movement remains capped — default **1.50 percentage points**.

### Recommendation guardrail

To appear on the Dashboard:

```text
base independent-market EV >= 0%
AND
V1.8 adjusted EV >= configured minimum EV
```

So football context can confirm, trim or slightly strengthen an existing market idea, but cannot rescue a clearly negative market bet.

## Football Model tab

V1.8 adds a detailed **Football Model** tab for users who want to inspect the backend evidence.

It includes:

- simple automatic matchup lean;
- data-quality rating;
- expected XI for both clubs;
- overall and position-group player strength;
- likely recent formation;
- rolling xG/xGA;
- shots/SOT;
- possession;
- tactical labels;
- recent match-by-match underlying data.

This keeps the Dashboard uncluttered while making the reasoning auditable.

## Validation tab

V1.8 starts measuring whether the model actually works rather than only generating EV estimates.

When an outcome first qualifies for the Dashboard, V1.8 stores its original:

- Sportsbet price;
- model probability;
- base EV;
- context-adjusted EV;
- conservative EV;
- market confidence;
- football-data quality;
- expected-XI/form/tactical/rest ratings.

Completed results are then matched back to the recommendation.

### Closing-line value

The app compares the first flagged price with the **last Sportsbet price actually stored before kick-off**.

Timing is labelled honestly:

- `CLOSE` — within 90 minutes;
- `NEAR CLOSE` — within six hours;
- `LAST OBSERVED` — older;
- `UNKNOWN` — no timing available.

The app does not call an old snapshot a true close.

Price CLV is:

```text
CLV % = flagged odds / last observed pre-kickoff odds - 1
```

Positive CLV means the later market price moved in the same direction as the original model view.

### Research metrics

Validation reports:

- number of recommendations;
- number settled;
- flat-stake ROI;
- average CLV;
- positive-CLV rate;
- binary Brier score;
- close/near-close sample count.

Small samples are descriptive only.

## Dutch analysis

The full Dutch Calculator remains unchanged and the Dashboard still automatically checks the best effective H/D/A prices across Sportsbet and Polymarket.

It can surface:

- full-market arbitrage; or
- a positive-model-EV two-result partial Dutch.

Partial Dutch ideas remain clearly labelled as non-arbitrage because an uncovered outcome can lose the entire outlay.

## Existing research features retained

V1.8 retains:

- favourite-longshot research tags;
- away-favourite / possible home-field overpricing tags;
- market agreement/disagreement;
- model EV and conservative EV;
- Asian Handicap-implied expected goals;
- manual manager/transfer/tactical research inputs;
- historical SQLite storage;
- Windows installer and built-in updater.

## Research data

User settings, logs, cached public football data and SQLite research data live under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

Existing data from earlier versions is retained through upgrades.

## Methodology

See:

```text
docs/MODEL_V1_5.md
docs/MODEL_V1_6.md
docs/MODEL_V1_7.md
docs/MODEL_V1_8.md
```

## Windows releases

Every tested release produces:

- `EPL-Value-Betting-vX.Y.Z-Setup.exe`
- `EPL-Value-Betting-vX.Y.Z-Portable.exe`

When a tested version reaches `main`, GitHub Actions publishes the versioned Release and the installed application can detect the update.

## Scope

This is a theoretical market-efficiency and probability-modelling application. It does not place bets automatically. Positive model EV is not proof of a persistent edge. V1.8 is specifically designed to accumulate the closing-line, calibration and result evidence needed to test whether the apparent edge survives out-of-sample.
