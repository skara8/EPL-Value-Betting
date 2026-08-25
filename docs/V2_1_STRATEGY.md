# V2.1 Strategy — Robust Market Edge Before Model Complexity

## Objective

The project is trying to discover repeatable market inefficiency, not simply generate larger positive EV numbers.

V2.1 therefore makes the decision rule harder to pass while improving the evidence collected about whether the edge is real.

## Public GitHub research reviewed

No public repository reviewed provides convincing evidence of a transferable "magic" football-betting edge. The useful ideas were methodological.

### ProphitBet — `kochlisGit/ProphitBet-Soccer-Bets-Predictor`

Useful ideas:

- sliding and k-fold model evaluation;
- separate train/evaluation analysis;
- feature filtering with variance/correlation analysis;
- Boruta feature selection;
- interpretable logistic coefficients and decision-tree rules;
- comparison of multiple classifiers rather than assuming a complex model is superior.

V2.1 lesson: future football features should earn their place through out-of-sample improvement rather than being added because they sound predictive.

### MatchLabs — `mejding/matchlabs`

This was the most relevant public prototype found. It experiments with:

- xG and xGA;
- fatigue/rest;
- Elo;
- shot volume;
- injuries;
- lineup continuity;
- tactical profiles;
- opponent-adjusted xG;
- XGBoost;
- time-based evaluation;
- log loss, Brier score and calibration;
- bootstrap uncertainty;
- SHAP/permutation importance.

Its own reported model comparisons are an important warning: adding schedule data slightly improved its xG baseline, while adding Elo/shot-volume and injury variants made the reported out-of-sample log loss/Brier score worse in those experiments.

V2.1 lesson: **more features can make probability forecasts worse**. This directly supports keeping football context secondary until its incremental value is demonstrated.

### Football Outcome Predictions — `asadiceccarelli/Football-Outcome-Predictions`

Useful ideas:

- large multi-league historical sample;
- Elo features;
- rolling goals scored/conceded;
- rolling points/form;
- explicit avoidance of future leakage when constructing rolling features.

V2.1 lesson: every future residual feature must be generated as-of the prediction timestamp. No post-match or future-season information can leak into training rows.

### Football League Predictions — `vickyfriss/football-league-predictions`

Uses a Poisson goals model and Monte Carlo simulation. This supports the project’s existing use of score-distribution modelling, but does not by itself demonstrate betting alpha.

## V2.1 primary strategy

### Step 1 — external market probability

Sportsbet remains the target/execution price.

Independent provider components are built from:

- Polymarket;
- Pinnacle 1X2;
- Pinnacle Asian Handicap + totals.

Pinnacle sub-markets are combined into one provider component first.

### Step 2 — point estimate and stress-test estimate

For each outcome:

```text
model probability = combined external provider estimate
conservative probability = minimum probability among external provider components
```

The conservative value is not claimed to be the statistically correct lower confidence bound. It is a transparent stress test that asks whether the price survives the current disagreement between genuinely separate provider components.

### Step 3 — price shopping

The probability stays fixed while V2 checks the best observed executable price.

```text
model EV  = model probability        × best odds - 1
robust EV = conservative probability × best odds - 1
```

### Step 4 — primary signal

`ROBUST +EV` requires all of:

- robust EV at or above the configured EV threshold;
- at least two external provider components;
- medium/high confidence;
- maximum provider disagreement no greater than 4 percentage points.

A large average EV that disappears under the least-favourable reference is a watch/research case, not the primary green signal.

## Why this is preferable to simply adding football-model weight

Most obvious player, injury, manager, rest and tactical information is eventually visible to professional market participants.

A football feature creates incremental edge only if it:

1. contains information not yet reflected in sharp prices; or
2. interprets public information better than the market; or
3. reaches a soft bookmaker before the price is updated.

Until that is proven, the sharper-market estimate is a stronger prior than a hand-selected football adjustment.

## Validation hierarchy

V2.1 should be judged in this order.

### 1. Sharp closing-line value

Store the actual bookmaker/source and odds that generated the signal.

Compare that first execution price with the last captured pre-kickoff Pinnacle price for the same outcome.

Repeated positive CLV is early evidence that the system is finding stale/soft prices rather than merely selecting random winners.

### 2. Probability calibration

Across all model-priced outcomes, test whether predicted frequencies match realised frequencies.

Use:

- log loss;
- multiclass Brier score;
- calibration/ECE;
- probability-bin reliability.

### 3. Realised ROI

ROI matters, but it is extremely noisy at small samples. Report confidence intervals and avoid treating a short winning run as proof.

## Recommended future V2.x research experiments

These should be added as experiments, not automatically promoted to production weights.

### Opponent-adjusted xG residual

Estimate attacking and defensive performance after adjusting for opposition quality. Test whether the residual predicts movement from current sharp price to closing sharp price.

### Elo / latent team-strength residual

Use historical results to maintain a slowly changing strength estimate. The valuable feature is not raw Elo; it is whether Elo materially disagrees with the sharp market and whether that disagreement predicts future closing movement.

### Expected-XI continuity

Measure:

- number of usual starters available;
- minutes played together;
- changes from recent starting XIs;
- position-group losses.

Again, test whether this explains closing-line movement after controlling for the current sharp probability.

### Tactical residual

Use pre-match tactical profile features only if a time-split test demonstrates incremental log-loss/Brier improvement beyond the market baseline.

### Bootstrap / ensemble uncertainty

Once enough historical rows exist, fit multiple walk-forward residual models and use the distribution of predictions to construct a genuine uncertainty interval. At that point the current conservative-provider EV can be supplemented or replaced by a statistically estimated lower-bound EV.

## Performance fix in V2.1

V2.0's all-league expansion exposed an algorithmic problem in Asian Handicap/total enrichment.

For every target fixture the old code scanned every raw Sportsbet event and every raw Pinnacle event, and reparsed the same event markets each time.

If `R` fixtures and `E` provider events are present, this behaves roughly like `O(R × E)` per provider, with expensive parsing inside the loop.

V2.1:

1. parses every raw provider event once;
2. indexes events by local date and league/date;
3. matches a fixture only against the small relevant bucket;
4. reports real fixture counts while enrichment runs.

This is a computational optimisation only. It does not change the market probability formula.

## Current research hypothesis

The most plausible persistent advantage for this project is:

```text
sharp market provides the probability anchor
+ small validated residual information (future)
+ fast detection of stale prices
+ best-price execution across softer books
= potential edge
```

The project should resist moving toward:

```text
more football features
+ more subjective weights
= larger displayed EV
```

unless those features demonstrate out-of-sample incremental value.
