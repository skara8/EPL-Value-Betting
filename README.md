# EPL Value Betting

Windows desktop research application for analysing EPL bookmaker prices, exchange probabilities, Asian Handicap markets, contextual football information and market-efficiency hypotheses.

## Current version

**V1.7.0**

## What V1.7 changes

V1.7 keeps the detailed V1.5/V1.6 modelling underneath, but makes the first screen much easier to use.

The Dashboard now shows:

- one large **Best current theoretical edge** card;
- Sportsbet price;
- model EV;
- confidence;
- a short high-school-level explanation;
- other qualifying +EV options;
- an automatic Dutch-opportunity summary;
- **No +EV option found** when nothing safely clears the configured threshold.

The detailed calculations remain available in Matches, Edge Lab, Context Lab, Dutch Calculator and History.

## Core market model

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

## V1.7 recommendation guardrail

The simplified dashboard deliberately does **not** let team context rescue a clearly negative base-market idea.

To appear as a dashboard +EV option:

- the independent-market base EV must be at least 0%; and
- the context-adjusted EV must clear the configured minimum EV threshold.

The shortlist ranks robustness/confidence before simply chasing the largest headline EV.

## Simplified Context Lab

Context Lab now has three internal views:

1. **Simple summary** — plain-English result first;
2. **Research inputs** — optional player/form/tactics/manager/transfer/rest ratings;
3. **Technical details** — full underlying calculation for auditability.

The contextual layer remains capped so subjective research cannot create a large artificial edge.

## Player availability improvement

V1.7 fixes a real data-quality problem found in V1.6: the FPL bootstrap feed can retain players who have transferred away or gone out on loan.

Clear departure/loan records are now ignored rather than treated as injuries.

Availability is also tracked by position group:

- goalkeeper;
- defence;
- midfield;
- attack.

This is the first foundation for a later expected-XI / position-v-position model.

## Dutch analysis

The full Dutch Calculator remains available.

The Dashboard now also automatically checks the best effective Home/Draw/Away prices across Sportsbet and Polymarket and can surface:

- full-market arbitrage; or
- a positive-model-EV two-result partial Dutch.

Partial Dutch ideas are clearly labelled as non-arbitrage because an uncovered result can lose the entire outlay.

Polymarket taker fees are included in the effective-price calculation.

## Existing research features

V1.7 retains:

- favourite-longshot research tags;
- away-favourite / possible home-field overpricing tags;
- market agreement/disagreement;
- model EV and conservative EV;
- Asian Handicap-implied expected goals;
- contextual probability sensitivity;
- research snapshots and historical SQLite storage;
- Windows installer and built-in updater.

## Next major modelling stages

The next development sequence is intended to automate more of the football-analysis layer:

1. expected XI and position-group player strength;
2. rolling xG/xGA, shot/chance quality and underlying form;
3. team style profiles and style-v-style matchup features;
4. automated rest/schedule/travel context;
5. manager/system implementation features;
6. completed results and closing-line ingestion;
7. out-of-sample learning of contextual coefficients and model weights.

Those factors should only receive larger numerical probability weights after they prove incremental predictive value against closing prices/results.

## Research data

User settings, logs and SQLite research data are stored under:

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
```

## Windows releases

Every tested release produces:

- `EPL-Value-Betting-vX.Y.Z-Setup.exe`
- `EPL-Value-Betting-vX.Y.Z-Portable.exe`

When a tested version reaches `main`, GitHub Actions publishes the versioned Release and the installed application can detect the update.

## Scope

This is a theoretical market-efficiency and probability-modelling application. It does not place bets automatically. Positive model EV is not proof of a persistent edge; the historical dataset exists so the model can be evaluated using calibration, closing-line value and out-of-sample results.
