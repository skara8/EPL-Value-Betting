# EPL Value Betting — V1.6 Context Research + Dutch Calculator

V1.6 adds a deliberately small contextual research layer on top of the V1.5 independent market model, plus a general Dutch/equal-return calculator.

## Why context is separate from the base model

The core V1.5 fair probability remains visible and unchanged. Context produces a second, experimental probability estimate.

This is important because public football information is often already incorporated into market prices. A factor should not receive a large probability adjustment merely because it sounds plausible.

The Context Lab is therefore designed to answer two different questions:

1. **What does the independent market model say?**
2. **How sensitive is that probability if our pre-match football research points modestly in one direction?**

The contextual probability movement is capped by default at **1.50 percentage points** for the most affected outcome. The cap is user-adjustable but is intentionally small.

## Evidence hierarchy used in V1.6

### 1. Player / expected line-up — highest priority

Published football forecasting work has shown that individual player ratings can add useful information to team-level forecasting. Holmes and McHale (2024) present a player-rating model that explicitly reflects changing team strength as players change and report positive returns in a historical betting application.

Professional-football injury research also finds that lower injury burden and higher player availability are associated with better team performance.

V1.6 therefore gives player/line-up information the largest contextual research weight.

The app makes one free request to the public Fantasy Premier League bootstrap feed and uses current player status / chance-of-playing fields to construct an **availability penalty proxy** for each EPL team. FPL price and current performance fields are used only as rough player-importance proxies. This is not treated as a standalone fair-probability model.

The FPL signal refers to the next FPL round and can therefore be stale or mismatched for a more distant fixture. The GUI displays the underlying flagged players so the user can judge whether it is relevant.

### 2. Recent underlying performance

Use underlying performance rather than simply recent wins/losses. Examples include xG, xGA, shot quality and chance creation/suppression.

V1.6 stores a manual rating for this factor. A later version can automate it from a stable free event/xG source and fit its coefficient from historical data.

### 3. Tactical / style matchup

Tactical profiles may contain predictive information, especially where one team's pressing, build-up or transition style interacts systematically with the opponent's style.

V1.6 records a manual tactical-matchup rating so the hypothesis can be tested. The correct question is not simply which team has the 'better style', but whether a specific style is unusually effective or vulnerable against the opponent's approach.

Examples of evidence worth recording:

- build-up under pressure versus opponent pressing intensity;
- high defensive line versus direct/transition threat;
- wide crossing versus aerial/box defence;
- set-piece strength versus set-piece concession weakness;
- ability to defend counterattacks after possession losses;
- whether a new manager's first matches actually exhibit the historical style expected from that manager.

### 4. Manager / coaching matchup

Manager quality can matter, but raw manager-v-manager head-to-head records are usually small-sample and heavily confounded by the quality of the teams each manager previously controlled.

Research on manager changes is mixed. Some long-run work finds measurable differences in manager quality, while other work finds teams that replace managers can underperform in the months after the change.

V1.6 therefore keeps manager/coaching as a modest contextual factor and asks the user to rate evidence about current tactical fit, implementation and coaching quality rather than relying on a tiny H2H sample.

### 5. Transfer / squad change

Spending and wage expenditure are associated with longer-run football success because richer squads tend to contain more playing talent. That does **not** mean £200m of summer spending automatically improves the next-match probability by more than £50m of spending.

Transfer fees can reflect age, contract length, scarcity, negotiation, resale value and market inflation. New signings also need time to integrate.

V1.6 therefore records:

- home gross transfer spend;
- away gross transfer spend;
- a separate **transfer/squad-change assessment**.

Only the assessment enters the contextual score. The pound amount itself is saved for later analysis but cannot manufacture a probability adjustment.

Useful transfer questions include:

- Did the club replace genuinely weak positions?
- Did it lose more quality than it bought?
- Are new players expected starters or depth?
- Do arrivals fit the manager's intended style?
- Is there unusually high roster turnover/cohesion risk?
- Have the players actually been integrated into the first team?

### 6. Schedule / rest / travel

Fixture congestion has plausible effects, but systematic evidence is mixed and varies by performance measure. V1.6 therefore gives this the smallest contextual research weight.

## V1.6 research weights

These are **not fitted coefficients**. They simply control the contribution to a small capped sensitivity adjustment:

```text
Player / expected line-up            35%
Recent underlying performance        25%
Tactical / style matchup             20%
Manager / coaching matchup           10%
Transfer / squad-change assessment    5%
Schedule / rest / travel              5%
```

Every manual factor is rated from:

```text
-3 = strong contextual advantage to AWAY
 0 = neutral / no evidence
+3 = strong contextual advantage to HOME
```

Automatic FPL availability is added to the player/line-up factor and the combined player rating is clipped to the same -3 to +3 range.

The weighted contextual score is then converted into a small softmax tilt of the V1.5 Home/Draw/Away probabilities. Positive context raises Home relative to Away; negative context raises Away relative to Home. Draw is neutral before re-normalisation.

At the maximum score, the largest probability movement is capped at the configured maximum (default 1.50 percentage points).

## Base EV versus Context EV

V1.6 always displays both:

```text
Base V1.5 fair probability
Base V1.5 EV
Context-adjusted fair probability
Context-adjusted EV
Probability shift in percentage points
```

This lets the user see whether the contextual view merely changes the story slightly or is actually required to turn a negative-EV price into a positive-EV one.

A useful research rule is to be more sceptical when an apparent opportunity exists **only** after a subjective context adjustment.

## Persistence and future calibration

When the user saves a Context Lab snapshot, V1.6 stores:

- all six factor ratings;
- transfer spending inputs;
- manager names;
- evidence notes;
- automatic FPL availability rating;
- base H/D/A probabilities;
- context-adjusted H/D/A probabilities;
- context-adjusted H/D/A EV;
- the configured maximum shift.

This creates the data needed for a later version to answer the important question:

> Which contextual factors actually improve calibration and closing-line value out of sample?

Once enough observations exist, the heuristic weights should be replaced or shrunk using real historical estimation.

## Strategy guidance from the 2+2 sports-betting forum

Relevant recurring advice in the forum is consistent with this design:

- compare model selections with the closing line, not only realised win/loss results;
- large apparent edges in liquid football markets deserve scepticism;
- line movement can reflect injuries, line-ups, weather, coaching information and other genuine news rather than public sentiment;
- track hypotheses separately so a model can be abandoned quickly if the edge disappears.

V1.6 therefore saves the contextual hypotheses instead of hiding them inside one final probability.

## Dutch betting calculator

Dutch betting allocates stakes across multiple selections so the return is equal if any selected outcome wins.

For effective decimal odds `O_i` and total stake `T`:

```text
S = sum(1 / O_i)
combined Dutch odds = 1 / S
stake_i = T * (1 / O_i) / S
equal return = T / S
equal profit = T / S - T
```

If the selections cover **all mutually exclusive possible outcomes**:

```text
S < 1   => arbitrage / positive locked return
S = 1   => break-even
S > 1   => negative Dutch / locked loss
```

If outcomes are not exhaustive, the calculator labels the strategy as a partial Dutch: returns are equal only when one of the selected outcomes occurs, and an uncovered result may lose the entire outlay.

### Polymarket price conversion and fees

For a Polymarket YES price `p`, raw frictionless decimal odds would be:

```text
1 / p
```

Current Polymarket documentation states that eligible sports-market takers pay a fee:

```text
fee = C * feeRate * p * (1-p)
```

where `C` is the number of shares. Buy fees are collected in shares. V1.6 therefore converts a taker buy into approximate effective winner decimal odds:

```text
effective odds = (1 - feeRate * (1-p)) / p
```

The default sports `feeRate` parameter is 5%, and the GUI lets the user change it. Maker mode applies no trading fee. Actual per-market fee enablement and fee parameters can change, so the user should check the specific Polymarket market.

The calculator does not model order-book slippage beyond the entered price. For arbitrage research, use prices that are actually executable for the intended stake size and ensure the Sportsbet and Polymarket settlement definitions match exactly.

## Selected research references

- Holmes, B. & McHale, I. G. (2024), *Forecasting football match results using a player rating based model*, International Journal of Forecasting 40(1), 302–312. DOI: 10.1016/j.ijforecast.2023.03.002.
- Hägglund, M. et al. (2013), *Injuries affect team performance negatively in professional football: an 11-year follow-up of the UEFA Champions League injury study*, British Journal of Sports Medicine.
- Audas, R., Dobson, S. & Goddard, J. (2002), *The impact of managerial change on team performance in professional sports*, Journal of Economics and Business 54(6), 633–650.
- Frick, B. & Simmons, R. (2008), *The impact of managerial quality on organizational performance: evidence from German soccer*, Managerial and Decision Economics.
- Carmichael, F., McHale, I. & Thomas, D. (2011), *Maintaining market position: team performance, revenue and wage expenditure in the English Premier League*, Bulletin of Economic Research.
- *The Effect of Fixture Congestion on Performance During Professional Male Soccer Match-Play: A Systematic Critical Review with Meta-Analysis* (2021), Sports Medicine.
- *Data-driven classification of playing styles and match outcome prediction in UEFA Champions League teams* (2025).

## Scope

The Context Lab is a research/sensitivity tool. Its default coefficients are not evidence of a profitable betting edge. The objective is to collect enough structured pre-match evidence that later versions can determine empirically whether any of these factors improve probability calibration or closing-line performance.
