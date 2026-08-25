# EPL Value Betting

Windows desktop research application for analysing EPL bookmaker prices, exchange probabilities, Asian Handicap markets and market-efficiency hypotheses.

## Current version

**V1.5.0**

## V1.5 model architecture

V1.5 makes an important separation between the **price being tested** and the **probability model used to test it**.

### Sportsbet = target price

Sportsbet Home / Draw / Away odds are the executable bookmaker prices being evaluated.

V1.5 calculates:

- raw Sportsbet implied probabilities;
- Sportsbet overround;
- power-method Sportsbet de-vig probabilities;
- the difference between Sportsbet's de-vig probability and the external model.

Sportsbet's de-vig probability is a bookmaker-shading diagnostic. It does **not** vote in the fair probability used to calculate EV on Sportsbet's own price.

### External probability model

V1.5 can use:

- Polymarket executable YES asks, normalised to 100%;
- Pinnacle/PS3838 1X2 prices with margin removed using the power method;
- Pinnacle Asian Handicap + goal-total prices converted into an implied Poisson score distribution.

Pinnacle's 1X2 and AH estimates are combined into one Pinnacle provider component first, so one provider does not accidentally receive two full votes.

The available external provider components are then averaged and renormalised into the V1.5 fair probability.

### Expected value

For a Sportsbet decimal price `O` and independent fair probability `p`:

```text
break-even probability = 1 / O
EV = p * O - 1
price edge = p - (1 / O)
```

### Conservative EV

V1.5 also calculates a deliberately harder number:

```text
conservative probability = lowest probability among external provider components
conservative EV = conservative probability * Sportsbet odds - 1
```

This helps distinguish an edge supported by multiple reference markets from one created by averaging disagreeing estimates.

## Asian Handicap and totals

V1.5 numerically calibrates an independent-Poisson football score model to the main full-time Asian Handicap and total-goals markets.

The fitted model estimates:

- expected home goals;
- expected away goals;
- Home / Draw / Away probabilities.

Quarter handicap and total lines are settled as split bets, for example:

```text
-0.25 = half 0, half -0.5
-0.75 = half -0.5, half -1.0
Over 2.75 = half Over 2.5, half Over 3.0
```

Pinnacle AH + totals can contribute to the external fair model. If only Sportsbet AH + totals are available, the derived score model is labelled **SPORTSBET-DIAGNOSTIC** and is shown as a cross-market consistency check rather than being fed back into Sportsbet EV.

## Favourite-longshot and away-favourite research

V1.5 tracks the football-market hypotheses discussed during development:

- favourite-longshot bias;
- away-favourite / possible home-field overpricing;
- longshot caution.

These appear as research tags such as:

- `FAVOURITE-LONGSHOT ALIGNMENT`
- `AWAY-FAVOURITE`
- `POSSIBLE HOME-ADVANTAGE OVERPRICE`
- `LONGSHOT CAUTION`

V1.5 deliberately does **not** add an arbitrary probability bonus for these effects. A numerical bias adjustment should be learned from the project's own historical outcomes and closing prices and then validated out-of-sample.

## V1.5 edge signals

- `PASS` — model EV below threshold.
- `EDGE - SINGLE REFERENCE` — headline EV passes but only one external provider is available.
- `EDGE - REFERENCE DISAGREEMENT` — headline EV passes but worst external reference does not remain positive EV.
- `EDGE - DIVERGENT MARKETS` — external references disagree materially.
- `ROBUST EDGE` — headline EV passes, conservative EV is positive and external references broadly agree.
- `BIAS-ALIGNED ROBUST EDGE` — robust edge plus a favourite/away-favourite research tag.

These are research classifications, not guarantees of profitability.

## User interface

V1.5 includes:

- Dashboard with model-edge and conservative-EV summaries;
- model-focused Matches table;
- click-to-explain match calculations;
- separate Outcome Calculations table for H/D/A;
- Asian Handicap + expected-goals explanation;
- Candidates view sorted by conservative EV;
- **Edge Lab** showing every Home / Draw / Away outcome ranked by model and conservative EV;
- persistent historical storage of V1.5 edge-model features;
- existing V1.3/V1.4 history retained during upgrades;
- built-in updater and Windows installer.

## Research database

User settings, logs and the SQLite research database are stored outside `Program Files` under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

V1.5 adds an `edge_model_snapshots` table containing all three outcomes for every fetched fixture, including the bookmaker probability, external model probability, conservative probability, EV, market residuals, bias tags and AH-model features.

## Detailed methodology

See:

```text
docs/MODEL_V1_5.md
```

for the full modelling rationale and academic research references.

## Windows releases

Every successful build produces:

- `EPL-Value-Betting-vX.Y.Z-Setup.exe`
- `EPL-Value-Betting-vX.Y.Z-Portable.exe`

When a tested version reaches `main`, GitHub Actions publishes the corresponding Release. The installed app can detect, download and launch the newer installer.

## Important scope

This project is a theoretical market-efficiency and probability-modelling application. It does not place bets automatically. An estimated positive EV is only as reliable as the probability model behind it; the purpose of the historical database is to test whether apparent edges persist out-of-sample.
