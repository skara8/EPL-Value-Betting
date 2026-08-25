# EPL Value Betting — V1.5 Edge Model

V1.5 changes the project from a simple Sportsbet-vs-Polymarket comparison into a multi-market probability model.

## Core rule

**Sportsbet is the price being tested. Sportsbet is not allowed to be the independent fair-probability baseline used to prove that its own price has value.**

For a Sportsbet decimal price `O` and independent fair probability `p`:

```text
break-even probability = 1 / O
EV = p * O - 1
price edge (percentage points) = p - (1 / O)
```

The app also removes Sportsbet's 1X2 margin separately. That de-vig probability is a diagnostic for bookmaker shading and bias; it is not the probability used in the Sportsbet EV formula.

## Removing margin / vig

### Polymarket

V1.5 continues to use the executable YES ask prices. The three ask-implied probabilities can sum above 100% because of bid/ask structure. They are proportionally normalised to 100% and labelled as an ask-normalised market probability rather than bookmaker vig.

### Sportsbet

Sportsbet 1X2 odds are processed two ways conceptually:

1. Raw implied probability: `1 / odds`.
2. Power-method de-vig probability.

For raw implied probabilities `q_i`, the power method solves for `k` such that:

```text
sum(q_i ** k) = 1
```

and returns `q_i ** k` as the de-vig probabilities.

This is preferable to treating the bookmaker margin as if it were distributed identically across favourites and longshots. The result is used as a **bookmaker-shading diagnostic only**.

### Pinnacle / PS3838

When available, Pinnacle 1X2 is also power-de-vigged. Because this is external to Sportsbet, it can contribute to the independent probability model.

## Asian Handicap + totals score model

Research has found that football Asian Handicap markets can contain more efficient forecasting information than traditional 1X2 prices. V1.5 therefore attempts to turn the main full-time AH and total-goals markets into an implied score distribution.

The model assumes independent Poisson home and away goals with means:

```text
lambda_home
lambda_away
```

V1.5 numerically finds the two lambdas that make both of these sharp-market positions approximately zero-EV after two-way margin removal:

1. the quoted main Asian Handicap home side;
2. the quoted main Over total.

Quarter Asian lines are settled correctly as split bets. For example:

```text
-0.25 = half 0, half -0.5
-0.75 = half -0.5, half -1.0
O2.75 = half O2.5, half O3.0
```

The fitted Poisson score distribution then produces implied Home / Draw / Away probabilities.

### Source hierarchy

Pinnacle AH + totals are preferred because they are external to Sportsbet.

If only Sportsbet AH + totals are available, V1.5 still fits and displays the score model, but labels it `SPORTSBET-DIAGNOSTIC` and **does not feed it into Sportsbet EV**. This avoids circular reasoning.

## Independent fair model

The model treats providers, rather than individual market types, as independent votes.

### Polymarket component

Polymarket ask-normalised H/D/A.

### Pinnacle component

If available, the Pinnacle component combines:

- Pinnacle power-de-vig 1X2;
- Pinnacle AH+totals Poisson H/D/A.

These are averaged within the Pinnacle provider first. This prevents one bookmaker receiving two full votes simply because it supplies two market types.

### Final fair probability

The available provider components are averaged and renormalised.

With both providers available:

```text
Model fair = 50% Polymarket provider component
           + 50% Pinnacle provider component
```

With only Polymarket available, the app explicitly labels the result as a single-reference model.

The weights are deliberately simple in V1.5. Data-driven weights should only be learned after a sufficiently large historical sample with outcomes and closing prices exists.

## Conservative EV

Headline model EV can be misleading when reference markets disagree.

For each outcome V1.5 therefore also calculates:

```text
conservative probability = minimum probability among external provider components
conservative EV = conservative probability * Sportsbet odds - 1
```

An edge that remains positive under the least favourable external provider is stronger evidence than an edge created only by averaging two disagreeing markets.

## Sportsbet residual

V1.5 reports:

```text
Sportsbet residual = independent model probability - Sportsbet power-de-vig probability
```

This is **not EV**.

It asks a different question:

> After removing Sportsbet's margin, where is Sportsbet allocating probability differently from the external market model?

This is useful for studying favourite-longshot bias and home-field shading.

## Favourite-longshot and away-favourite research

Earlier project research identified two hypotheses worth tracking:

1. **Favourite-longshot bias** — football 1X2 markets have repeatedly shown a pattern where longshots are relatively overvalued and favourites relatively undervalued.
2. **Away-favourite / home-field bias** — Vlastakis, Dotsis and Markellos reported that overestimation of home advantage combined with favourite-longshot bias could create relatively better returns for away favourites than home favourites.

V1.5 does **not** invent a numerical probability bonus for either effect.

Instead it adds research tags when the live market structure is consistent with the hypothesis:

- `FAVOURITE-LONGSHOT ALIGNMENT`
- `AWAY-FAVOURITE`
- `POSSIBLE HOME-ADVANTAGE OVERPRICE`
- `LONGSHOT CAUTION`

A numerical bias adjustment should be estimated only from the project's own historical data and validated out-of-sample.

## Edge signals

### PASS
Model EV is below the configured minimum EV.

### EDGE - SINGLE REFERENCE
Headline model EV passes, but only one external provider is available.

### EDGE - REFERENCE DISAGREEMENT
Headline model EV passes, but the least favourable external provider does not imply positive EV.

### EDGE - DIVERGENT MARKETS
Headline and conservative EV pass but external reference disagreement is large.

### ROBUST EDGE
Headline model EV passes, conservative EV is positive, at least two external providers are available, and disagreement is acceptable.

### BIAS-ALIGNED ROBUST EDGE
The robust-edge conditions pass and the selection also matches one of the favourite / away-favourite research hypotheses.

The word `ROBUST` means robust to the reference-market checks in this model. It does not mean guaranteed profitable.

## Research basis

Relevant literature used to shape the research design includes:

- Cain, Law & Peel (2000), *The Favourite-Longshot Bias and Market Efficiency in UK Football Betting*, Scottish Journal of Political Economy, DOI 10.1111/1467-9485.00151.
- Vlastakis, Dotsis & Markellos (2009), *How Efficient is the European Football Betting Market? Evidence from Arbitrage and Trading Strategies*, Journal of Forecasting, DOI 10.1002/for.1085.
- Angelini & De Angelis (2019), *Efficiency of online football betting markets*, International Journal of Forecasting.
- Constantinou (2022), *Investigating the efficiency of the Asian handicap football betting market with ratings and Bayesian networks*, Journal of Sports Analytics.
- Hegarty & Whelan (2025), *Forecasting soccer matches with betting odds: A tale of two markets*, International Journal of Forecasting 41(2), 803–820, DOI 10.1016/j.ijforecast.2024.06.013.

The Hegarty & Whelan results are especially relevant to V1.5 because they report strong favourite-longshot bias in traditional 1X2 markets while finding that Asian Handicap prices can produce efficient football forecasts.

## What V1.5 still does not know

V1.5 improves the information set but does not prove a persistent exploitable edge.

The next empirical stage requires:

- completed match results;
- closing prices;
- model probability at decision time;
- bookmaker and reference-market movements;
- enough observations in each home/away/favourite/underdog cell;
- calibration and Brier/log-loss analysis;
- out-of-sample tests of model weights and bias adjustments.

Only after that should the app learn numerical favourite-longshot or away-favourite corrections.
