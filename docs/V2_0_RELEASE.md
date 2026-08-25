# V2.0 release notes

V2.0 keeps the existing fair-probability and context model while improving refresh visibility, navigation and price shopping.

## Refresh experience

The Dashboard now shows a live progress panel during long refreshes. It reports the current stage, a plain-English description, percentage complete, elapsed time and an approximate remaining time.

The progress stages separate:

1. Sportsbet league eligibility;
2. Sportsbet fixture download;
3. Polymarket and Pinnacle reference downloads;
4. fixture matching;
5. Asian Handicap / goal-total enrichment;
6. fair-probability and EV calculation;
7. targeted best-price shopping;
8. research persistence;
9. optional EPL football-intelligence enrichment.

The ETA is explicitly approximate because network latency and the number of pages returned by providers vary between refreshes.

## Navigation

Top-level navigation is condensed to Dashboard, Markets, Analysis, Tools, Research and Settings. Detailed screens remain nested underneath, so no analytical drill-down has been removed.

## Version copy

Legacy V1.x labels inherited by technical screens are normalised at runtime. This includes ordinary labels, explanatory text, StringVar-backed dynamic copy and nested notebook tab captions. Diagnostics logs retain literal historical version strings because those are useful for debugging.

## Best-price shopping

The independent fair-probability model is unchanged. Once the core model has identified the leading model-priced fixtures, V2 checks a targeted group of additional price sources rather than downloading every event from every bookmaker.

Fixed-odds / Australian-facing sources:

- Bet365
- Ladbrokes
- TAB
- Unibet AU
- BetRight

Crypto sportsbook / event-market sources:

- Stake
- Cloudbet
- Polymarket (already present in the core data; displayed with the configured taker-fee treatment)

Sportsbet remains part of the core data and is always included in price comparison for eligible fixtures.

The scan is capped at 15 leading model-priced matches across at most five leagues and caches bookmaker league/event responses for 15 minutes. Unsupported or plan-restricted optional sources are skipped without interrupting the core model.

For every covered outcome, V2 reports Sportsbet odds, best observed odds, the best source, EV at Sportsbet, EV at the best observed price and the improvement due solely to price shopping.

Price shopping never alters the model probability. It changes only the payout used in the EV equation.
