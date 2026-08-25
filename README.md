# Football Value Betting

Windows desktop research application for comparing football prices, estimating fair probabilities, studying market inefficiencies and validating whether apparent edges persist.

## Current version

**V2.1.0**

## What V2.1 changes

V2.1 does **not** add a new pile of subjective football weights. It strengthens the part of the system most likely to contain real edge: sharp-market comparison, price shopping, uncertainty control and validation.

### 1. Faster Asian Handicap / totals stage

V2.0 could appear to freeze around `Asian handicap & totals` after the all-soccer catalogue became large. The cause was algorithmic: every Sportsbet fixture repeatedly scanned and reparsed the complete Sportsbet and Pinnacle raw-event catalogues.

V2.1 parses each raw provider event once, builds date/league lookup indexes, and then matches each fixture against a small candidate bucket. The Dashboard also reports real fixture progress through this stage.

The probability mathematics are unchanged by this optimisation.

### 2. Robust EV becomes the primary decision number

The existing model already calculated both:

- **Model EV** using the combined external fair probability; and
- **Conservative EV** using the least-favourable available external provider probability.

V2.1 promotes the conservative number into the primary Dashboard rule.

A green `ROBUST +EV` signal requires:

1. at least two external provider components;
2. medium or high reference-market confidence;
3. external disagreement no greater than 4 percentage points;
4. the **least-favourable** external probability still produces EV at or above the configured threshold at the best observed eligible execution price.

If the average model says +7% but the less optimistic reference says -1%, V2.1 no longer presents +7% as the main green signal. It becomes a watch/research case.

If nothing passes the robust rule, the Dashboard still shows the highest-EV available outcome, but labels it explicitly as **not a robust signal**.

### 3. Football context becomes secondary evidence

Expected XI, availability, recent xG-style performance, tactical matchup, manager/squad context and rest remain available in Analysis.

They remain useful research features, but the main V2.1 green signal is market-supported. A football story cannot rescue a price that fails the independent-market robustness test.

This is intentional: most public football information is eventually incorporated into sharp prices. The research question is whether football features add incremental information beyond that market baseline.

### 4. Best-price execution remains separate from probability

The fair probability is calculated before price shopping.

V2.1 can compare the same outcome across observed prices from Sportsbet, Polymarket and supported additional PulseScore feeds such as Bet365, Ladbrokes, TAB, Unibet AU, BetRight, Stake and Cloudbet.

Changing from $1.90 to $2.02 can increase EV because the payout improved. It does not change the estimated chance of the team winning.

Polymarket remains visible in Best Prices and the Dutch tools, but it is **not used as the execution price for the primary V2.1 robust signal** because Polymarket also contributes to the fair-probability model. This avoids using the same market partly as both the probability reference and the price being tested.

### 5. Sharp closing-line validation

V2.1 stores the actual non-reference price source and odds that created each decision signal.

For robust signals it can compare that first observed execution price with the last captured pre-kickoff Pinnacle price on the same outcome. A Pinnacle quote only counts in the headline V2.1 sharp-CLV statistics when it was captured within **six hours of kickoff**; an older snapshot is not presented as a closing price.

The new Research validation panel reports:

- robust signals;
- signals with a captured near-close sharp price;
- average sharp CLV;
- percentage with positive sharp CLV;
- average robust EV when first flagged.

Positive sharp CLV is the most useful early test of whether the application is repeatedly identifying prices that the sharper market later agrees were too large. ROI remains much noisier over small samples.

## Current probability architecture

Sportsbet is a target/execution price, not the independent probability source used to assess itself.

The external fair layer can use:

- Polymarket executable YES asks normalised to 100%;
- Pinnacle/PS3838 power-de-vigged 1X2;
- Pinnacle Asian Handicap + totals converted into an implied Poisson score distribution.

Pinnacle's 1X2 and AH/totals estimates are combined into one Pinnacle provider component so one provider does not receive multiple full votes.

For decimal price `O` and fair probability `p`:

```text
EV = p * O - 1
```

V2.1 additionally asks whether the same price remains attractive using the least-favourable external provider probability.

## Why this direction

A review of public football-prediction repositories reinforced several useful lessons:

- time-ordered out-of-sample evaluation matters more than in-sample accuracy;
- log loss, Brier score and calibration matter more than simply predicting the winner;
- bootstrap/ensemble uncertainty is useful for identifying unstable probabilities;
- feature selection and ablation are necessary because adding more football variables can make out-of-sample performance worse;
- xG, Elo, lineup continuity, fatigue, Dixon-Coles/time-decayed team strength and tactical features are promising **research candidates**, but should only enter the production probability correction after demonstrating incremental value;
- all rolling football features must use only information available before kickoff.

See `docs/V2_1_STRATEGY.md` for the detailed V2.1 research plan and the public repositories reviewed.

## Navigation

Top-level navigation remains deliberately compact:

```text
Dashboard
Markets
Analysis
Tools
Research
Settings
```

Detailed market, AH, context, football, Dutch, history and diagnostic views remain underneath.

## Data and settings

Settings, logs, cached public football data and SQLite research data live under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

Existing research data is retained across upgrades.

## Scope

This is a theoretical market-efficiency and probability-modelling project. It does not automatically place bets. A displayed positive EV is an estimate, not proof of profitability; V2.1 is specifically designed to make that estimate harder to pass and easier to validate against the sharp closing market.
