# Football Value Betting

Windows desktop research application for independently estimating football match probabilities, comparing them with observed bookmaker/exchange prices and testing whether apparent value survives out-of-sample validation.

## Current version

**V2.4.0**

## The V2.4 modelling rule

V2.4 makes a hard separation between **prediction** and **price**.

```text
historical football results
        ↓
league-specific football models
        ↓
independent Home / Draw / Away probability
        ↓
FREEZE probability
        ↓
compare Sportsbet / TAB / Ladbrokes / Bet365 / other books / Polymarket
        ↓
EV at each observed price = independent probability × decimal odds - 1
```

No current Sportsbet, Pinnacle, Polymarket, Asian Handicap or other bookmaker price is allowed into the V2.4 headline football probability.

Those markets remain valuable **market intelligence**. They show how the independent model differs from current prices, help diagnose possible mispricing and provide execution quotes, but they do not define V2.4 fair probability.

If a league or team cannot be modelled from sufficient historical football data, the application reports **Independent model unavailable**. It does not substitute bookmaker consensus and call that an independent edge.

## Independent multi-league coverage

V2.4 maps current Sportsbet competition labels onto historical Football-Data sources only when the league match is sufficiently confident.

### Main European divisions

- England: Premier League, Championship, League One, League Two, National League
- Scotland: Premiership, Championship, League One, League Two
- Germany: Bundesliga, 2. Bundesliga
- Italy: Serie A, Serie B
- Spain: La Liga, Segunda Division
- France: Ligue 1, Ligue 2
- Netherlands: Eredivisie
- Belgium: Belgian Pro League
- Portugal: Primeira Liga
- Turkey: Super Lig
- Greece: Super League

### Additional worldwide top divisions

- Argentina Primera Division
- Austrian Bundesliga
- Brazil Serie A
- Chinese Super League
- Danish Superliga
- Finland Veikkausliiga
- Ireland Premier Division
- Japan J1 League
- Mexico Liga MX
- Norway Eliteserien
- Poland Ekstraklasa
- Romania Liga I / SuperLiga
- Russian Premier League
- Sweden Allsvenskan
- Swiss Super League
- USA Major League Soccer

Historical data is cached locally. Only leagues represented in the current Sportsbet fixture scan are downloaded/refreshed during a run.

## The four football-model components

V2.4 produces several genuinely bookmaker-independent estimates for the same fixture and uses their disagreement as model uncertainty.

### 1. Time-decayed Dixon-Coles model

The main score model learns, separately for each league:

- league home scoring rate;
- league away scoring rate;
- home-team home attack/defence;
- away-team away attack/defence;
- recency-weighted team form;
- shrinkage toward league averages;
- low-score dependence using a Dixon-Coles correction.

The principal decay half-life is 180 days.

It produces expected goals for both sides, a scoreline distribution and independent H/D/A probabilities.

### 2. League-local Elo model

Each league has its own chronological Elo ratings using only results available before the fixture being priced.

Unlike the old V2.2 research Elo model, **the draw probability no longer borrows the current betting market's draw probability**. It is derived from the league's historical draw environment and the strength difference between the teams.

Ratings are partially regressed toward the league mean between seasons, helping reduce stale multi-season strength estimates.

### 3. Short-decay score model

A 90-day half-life version asks whether recent results imply materially different team strength from the main model.

### 4. Long-decay score model

A 360-day half-life version provides a slower-moving estimate and acts as a stability check against short-term noise.

## Central and cautious probabilities

V2.4 deliberately begins with transparent equal model-family weights rather than fitting weights on the same period later used to judge performance.

For each H/D/A outcome:

```text
central independent probability
    = mean(Dixon-Coles, Elo, short-decay, long-decay)
```

The displayed cautious probability is the least optimistic probability among those independent components for that particular outcome.

This is a stress test, not a formal confidence interval.

The application also reports **model spread**: the largest probability range across the independent components. Large spread reduces confidence.

Future versions can learn model weights only after enough time-ordered snapshots and results exist for genuine walk-forward fitting.

## Fair odds and EV

Once the independent probability `p` is calculated it is frozen.

Independent fair odds are:

```text
fair odds = 1 / p
```

For every observed decimal quote `O`:

```text
EV = p * O - 1
```

Example:

```text
Independent win probability: 52.0%
Independent fair odds:       $1.92
Sportsbet:                    $1.85  => -3.8% EV
TAB:                          $1.96  => +1.9% EV
BetRight:                     $2.05  => +6.6% EV
```

The 52.0% probability does not move simply because a bookmaker offers a different price.

## Robust Independent +EV

A green Dashboard signal is now **ROBUST INDEPENDENT +EV**, not the old market-consensus `ROBUST +EV`.

It requires:

1. a supported independent historical football model;
2. at least three independent model components;
3. medium or high model confidence;
4. the central independent EV to pass the configured threshold;
5. even the least optimistic football-model component to pass the same EV threshold at the best observed price.

If the central estimate passes but model variants disagree, the application labels the result as a watch/model-spread case rather than promoting it to green.

If nothing is positive, the Dashboard still shows the **highest EV available**, including a negative value, but explicitly labels it as not a robust signal.

## Market intelligence remains useful

V2.4 still calculates/displays where available:

- Sportsbet raw implied probabilities;
- Sportsbet power-method de-vig probabilities;
- Polymarket ask-normalised probabilities;
- Pinnacle/PS3838 de-vigged 1X2;
- Asian Handicap and total-goals context;
- broader non-Sportsbet bookmaker consensus;
- best observed execution prices.

The old market-derived fair estimate is preserved internally as `market_reference_*` and displayed beside the independent football model.

This lets the research ask questions such as:

> Independent model: Arsenal 55.8%  
> Current market reference: Arsenal 52.9%  
> Football-v-market residual: +2.9 percentage points

That residual can later be tested against sharp closing-line movement rather than assumed to be correct.

## Price shopping

Once the independent probability has been frozen, V2.4 can scan observed prices from sources exposed by the user's PulseScore access, including where available:

- Sportsbet
- Bet365
- Ladbrokes
- TAB
- Unibet AU
- BetRight
- Stake
- Cloudbet
- Polymarket

Because Polymarket no longer contributes to the headline probability, its fee-adjusted executable quote can also participate in V2.4 best-price comparison without creating circularity.

Best observed price does not mean guaranteed globally executable price. Prices can move, markets can suspend, liquidity can be insufficient and account/jurisdiction conditions can differ.

## New Independent model page

Under **Analysis -> Independent model**, V2.4 shows for each independently modelled fixture:

- central H/D/A probability;
- independent fair H/D/A odds;
- Dixon-Coles probabilities;
- Elo probabilities;
- short-decay probabilities;
- long-decay probabilities;
- implied home/away expected goals;
- model spread;
- current market reference probability;
- largest football-v-market probability gap;
- confidence.

The Dashboard remains intentionally simple and shows the best current independent theoretical edge first.

## Data integrity and leakage controls

Historical model calculations use only matches that occurred before the target fixture cutoff.

Current bookmaker odds are never used to:

- estimate team attack strength;
- estimate team defensive strength;
- determine Elo draw probability;
- estimate league scoring environment;
- choose the V2.4 H/D/A probability.

This is essential because the project is now explicitly testing whether football-derived information can identify errors in the market rather than asking one betting market to assess another.

## Persistence and validation

V2.4 adds separate SQLite storage for:

### `v24_independent_snapshots`

Stores:

- the central/cautious independent probabilities;
- all four model components;
- expected-goal parameters;
- model spread;
- historical sample size;
- confidence;
- market-reference probabilities observed at the same time.

### `v24_decisions`

Stores:

- actual observed execution source;
- actual observed price;
- independent probability;
- independent fair odds;
- central EV;
- cautious/robust EV;
- model spread;
- market probability and football-v-market residual.

These snapshots are designed for future walk-forward tests of:

- Ranked Probability Score;
- Brier score;
- log loss;
- calibration;
- sharp closing-line value;
- realised theoretical ROI;
- model-family ablation;
- learned ensemble weights.

## Why this architecture

The public-model review reinforced two ideas.

Strong football analytics libraries such as `penaltyblog` provide Poisson, bivariate Poisson, Dixon-Coles, Bayesian/hierarchical models and rating systems. The useful lesson is that football probability should be generated from football data and evaluated using proper scoring rules rather than made convincing by adding more bookmaker inputs.

The Open Model uses an auditable Elo -> Dixon-Coles pipeline and emphasises pre-kickoff snapshots, walk-forward testing, Brier/RPS/log-loss and calibration. V2.4 follows that experimental discipline while retaining this project's separate price-shopping and market-efficiency layer.

V2.4 does **not** claim these model choices prove a profitable betting edge. It creates an architecture capable of measuring one without probability leakage from the prices being tested.

## Multicore and performance

The V2.2 multicore market-context acceleration remains in the app. V2.4 also loads independent league-history files concurrently and caches them locally.

Network/API stages can still be I/O-bound, so CPU usage is expected to fall during downloads and rise during computation-heavy stages.

## Navigation

Top-level navigation remains:

```text
Dashboard
Markets
Analysis
Tools
Research
Settings
```

Detailed market diagnostics remain available; the new production probability appears under:

```text
Analysis -> Independent model
```

## Data and settings

Settings, logs, cached historical football data and SQLite research files live under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

Existing research data is retained across upgrades.

## Scope

This is a theoretical market-efficiency and probability-modelling research application. It does not automatically place bets. A displayed positive EV is an estimate, not proof of profitability. The core V2.4 goal is to produce a bookmaker-independent probability first and then make that probability easy to falsify against future results and sharp closing markets.
