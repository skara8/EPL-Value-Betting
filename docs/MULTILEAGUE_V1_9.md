# V1.9 Multi-League Soccer Design

## Goal

V1.9 keeps the existing probability/EV model unchanged and expands the input universe from the English Premier League to the complete current Sportsbet soccer catalogue exposed by PulseScore.

## Sportsbet is the eligibility gate

The app first calls Sportsbet's PulseScore soccer-leagues endpoint.

Only competition names currently returned by Sportsbet are eligible for analysis.

It then downloads the paginated Sportsbet pre-match soccer event catalogue and keeps only:

- non-live fixtures;
- fixtures inside the selected date range;
- competitions present in the current Sportsbet league catalogue;
- fixtures with a complete full-time Home / Draw / Away market.

This means another provider cannot make a league appear in the app if Sportsbet does not currently offer that competition.

## External reference markets

V1.9 queries the normalised PulseScore soccer event feeds for:

- Polymarket;
- Pinnacle / PS3838.

Those fixtures are fuzzy-matched to eligible Sportsbet fixtures using teams, kick-off time and league-name agreement where available.

The existing V1.5–V1.8 model is unchanged:

- Sportsbet remains the price being tested;
- Polymarket can provide an external probability component;
- Pinnacle 1X2 can provide an external probability component;
- Pinnacle Asian Handicap + total-goals data can feed the existing Poisson score calibration;
- Sportsbet AH/totals remain diagnostic rather than being used to prove Sportsbet's own price is wrong.

If a Sportsbet fixture has no usable independent reference market, it remains visible as a Sportsbet fixture but the app does not manufacture a fair probability.

## League catalogue tab

The new Leagues tab shows:

- number of competition names currently offered by Sportsbet;
- number of complete Sportsbet 1X2 matches inside the selected date range;
- Polymarket match coverage;
- Pinnacle match coverage;
- number of fixtures for which the independent model can be built;
- approximate PulseScore request count for the current refresh.

Catalogue responses are cached briefly to reduce unnecessary use of the free API allowance.

## Football-intelligence layer

The market model works across every Sportsbet soccer league.

The automatic expected-XI/FPL/xG/tactical layer remains EPL-specific in V1.9 because the current free football-intelligence sources were designed and tested around the English Premier League. Non-EPL fixtures therefore use the unchanged market model without pretending that EPL-specific player data applies to them.

This separation is deliberate. Broader league-specific player and results sources can be added later without weakening the market model.

## Dashboard behaviour when no recommendation clears the threshold

V1.8 left the hero card blank when no qualifying +EV selection existed.

V1.9 instead displays the highest model-EV outcome available:

- if EV is positive but below the configured recommendation threshold, it is labelled `positive EV, below threshold`;
- if EV is zero or negative, it is labelled `highest EV available` and explicitly states that it remains negative EV.

This is an informational comparison only. A negative-EV fallback is not reclassified as a recommendation.
