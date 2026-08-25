# Football Value Betting

Windows desktop research application for comparing football prices, estimating fair probabilities, studying market inefficiencies and validating whether apparent edges persist.

## Current version

**V2.0.0**

## What V2 changes

V2 keeps the existing probability model but substantially improves the user experience and price-comparison layer.

### Live analysis progress

A refresh can now involve a large number of market and football-data requests. The Dashboard therefore shows a live analysis panel with:

- current stage;
- plain-English explanation of what is happening;
- progress percentage;
- elapsed time;
- approximate time remaining.

Typical stages are:

```text
Sportsbet league eligibility
→ Sportsbet fixtures
→ Polymarket + Pinnacle references
→ fixture matching
→ Asian Handicap + totals
→ fair-probability / EV model
→ best-price scan
→ research snapshot saving
→ optional EPL player/xG/tactical enrichment
```

Core market results become usable before the optional EPL football-intelligence layer finishes.

## Condensed navigation

The old collection of many top-level tabs is grouped into six main areas:

```text
Dashboard
Markets
Analysis
Tools
Research
Settings
```

Detailed pages are still available underneath:

- **Markets:** Matches, League coverage, Best prices;
- **Analysis:** Candidates, Market edge, Team context, Football model;
- **Tools:** Dutch calculator;
- **Research:** Validation, History, Diagnostics.

This keeps the detailed calculations auditable without making the first-level navigation crowded.

## Dynamic multi-league coverage

Sportsbet remains the eligibility gate.

The app asks PulseScore for Sportsbet's current soccer league catalogue, then analyses complete pre-match 90-minute H/D/A markets inside the selected date range. Polymarket and Pinnacle are independent reference markets only; they cannot introduce a competition that Sportsbet does not offer.

## Core probability model

Sportsbet is a target price, not the independent probability source used to assess itself.

The fair-probability layer can use:

- Polymarket executable YES asks normalised to 100%;
- Pinnacle/PS3838 power-de-vigged 1X2;
- Pinnacle Asian Handicap + total-goals information converted into an implied Poisson score distribution.

For decimal price `O` and independent fair probability `p`:

```text
break-even probability = 1 / O
EV = p * O - 1
```

Sportsbet is separately power-de-vigged for bookmaker-shading and bias research, but does not vote in its own fair probability.

The existing favourite-longshot, away-favourite, conservative-EV, market-disagreement, AH/totals and context logic remains intact.

## Best-price comparison

After the core model identifies the leading model-priced matches, V2 optionally performs a smaller price-shopping pass.

Default additional PulseScore sources are:

- Bet365;
- Ladbrokes;
- TAB;
- Unibet AU;
- BetRight.

Sportsbet is already present in the core data. Polymarket is also included as a fee-adjusted event-market price using the same taker-fee assumption as the Dutch calculator.

The price scan deliberately does **not** download every event from every bookmaker. It shops up to 15 leading matches across up to five leagues, and caches bookmaker league/event responses for 15 minutes. This reduces both waiting time and use of PulseScore's request allowance.

### Important modelling rule

Price shopping does not change the model probability.

If the model estimates an outcome at 55%:

```text
Sportsbet $1.80 → EV = -1.0%
TAB $1.92       → EV = +5.6%
```

The probability stayed at 55%. Only the available payout changed.

The Best prices page shows:

- league and match;
- outcome;
- model fair probability;
- Sportsbet odds;
- best observed odds;
- source offering the best price;
- EV at Sportsbet;
- EV at the best observed price;
- EV improvement from price shopping.

If an additional bookmaker is unavailable on the user's PulseScore plan, that source is skipped and the rest of the analysis continues.

## Dashboard behaviour

The Dashboard shows the strongest current theoretical edge in simple language.

If a qualifying +EV option exists, it shows the best observed price available from the sources actually checked.

If nothing clears the configured recommendation threshold, the Dashboard still shows the highest-EV available option and labels it honestly as either:

- positive EV but below threshold; or
- negative EV and shown only because it is the least-negative option.

The app never silently relabels a negative-EV selection as a recommendation.

## Football intelligence

The automatic expected-XI, player-strength, recent xG-style form, tactical matchup and rest model remains EPL-specific for now. Other leagues use the same market model without fabricated team-context data.

The football layer remains deliberately capped so it can nudge a market-supported estimate but cannot dominate the independent market evidence.

## Dutch analysis

The Dutch Calculator remains available under **Tools**. It can use Sportsbet decimal odds or Polymarket prices, including the configured Polymarket sports taker-fee treatment.

The Dashboard can still surface a full-market arbitrage or a model-based partial Dutch when appropriate.

## Validation

The Research section retains recommendation history, completed results, flat-stake ROI, Brier-score calibration and last-observed pre-kickoff price / CLV analysis.

These metrics are intended to test whether the model is genuinely useful rather than assuming a displayed positive EV is real.

## Data and settings

User settings, logs, cached public football data and SQLite research data live under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

Existing research data is retained across upgrades.

## Scope

This is a theoretical market-efficiency and probability-modelling project. It does not automatically place bets. A positive model EV is an estimate, not a guarantee, and should ultimately be judged through calibration, closing-line value and out-of-sample results.
