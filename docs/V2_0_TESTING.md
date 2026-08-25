# V2.0 release test coverage

The Windows CI test suite includes the existing market-model, Asian Handicap, Dutch, multi-league, context and football-intelligence regressions plus V2 checks for:

- best-price league matching;
- leading-match selection for targeted price shopping;
- best decimal-price selection;
- old-version copy cleanup;
- outcome-side mapping;
- construction of the condensed V2 Tk navigation and key widgets.

The same workflow then builds the PyInstaller executable and Inno Setup installer before a release can be promoted to `main`.
