# V2.0 data-source roles

The V2 model separates probability sources from price-shopping sources.

## Probability / research sources

- Sportsbet: target bookmaker price and de-vig/shading diagnostic; never allowed to prove its own price is fair.
- Polymarket: independent event-market probability where a complete matching H/D/A market is available.
- Pinnacle/PS3838: independent 1X2, Asian Handicap and total-goals information where available.
- EPL football intelligence: optional player, expected-XI, recent underlying-performance, tactical and rest context; deliberately capped.

## Price-shopping sources

The targeted best-price pass may compare Sportsbet and Polymarket with Bet365, Ladbrokes, TAB, Unibet AU, BetRight, Stake and Cloudbet when those PulseScore feeds are available.

A better price changes only the decimal-odds term in EV:

```text
EV = model probability × best observed decimal odds - 1
```

It does not change the underlying model probability.

Optional price-source failures are non-fatal and do not interrupt the core analysis.
