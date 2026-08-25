# EPL Value Betting

Windows desktop research application for comparing EPL Sportsbet prices with Polymarket market probabilities, screening for value and building a persistent odds-history dataset.

## Current version

**V1.3.0**

### V1.3 features

- Native Windows desktop application and installer
- EPL-only filtering using the official 2026/27 Premier League club membership
- Sportsbet Australia 90-minute H/D/A odds via the free PulseScore BASIC API
- Polymarket regulation-time H/D/A market retrieval
- Removal of Polymarket derivative rows such as halftime, exact score, corners and player props
- Normalised Polymarket executable YES-ask probabilities
- Sportsbet expected-value calculation for Home / Draw / Away
- Configurable minimum EV threshold
- `VALUE`, `AWAY-FAV VALUE`, `PASS`, `LOW VOLUME` and `NO COMPARISON` screening states
- Dashboard and dedicated Candidates tab
- Optional Polymarket-volume quality filter
- Persistent SQLite research database under the Windows user profile
- Automatic saving of market snapshots after successful fetches
- Research-history browser and CSV export
- PulseScore API-key storage through Windows Credential Manager
- Local rotating diagnostic logs
- Built-in update checking and installer download
- Automatic GitHub Release publishing on successful `main` builds
- Automated regression tests on every Windows build

## Strategy-preview calculation

For each matched EPL fixture, V1.3 converts the Polymarket Home / Draw / Away executable YES asks back to implied probabilities and normalises the three values so they total 100%.

It then calculates each Sportsbet outcome's comparison EV:

```text
EV = (normalised Polymarket probability × Sportsbet decimal odds) - 1
```

The historical away-favourite finding is currently **a research flag only**. V1.3 does not add an arbitrary probability boost for an away favourite.

## Data storage

User settings, logs and the research database are stored outside `Program Files`, under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

This means installing a newer application version does not overwrite the research database.

## Windows releases

Every successful build produces:

- `EPL-Value-Betting-vX.Y.Z-Setup.exe`
- `EPL-Value-Betting-vX.Y.Z-Portable.exe`

When a version is merged into `main`, the workflow automatically creates or refreshes the corresponding GitHub Release. The installed app can check the latest Release, download its installer and start the upgrade.

## Development workflow

1. Develop on a feature branch.
2. Run automated tests in GitHub Actions.
3. Build the Windows `.exe` using PyInstaller.
4. Build the installer using Inno Setup.
5. Merge the tested pull request into `main`.
6. GitHub automatically publishes the versioned Release.
7. Installed applications can detect the new version.

## Important scope

This project analyses betting-market data. It does not place bets automatically. Strategy flags are research outputs and are not evidence by themselves that an edge is profitable.
