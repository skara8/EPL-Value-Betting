# EPL Value Betting

Windows desktop application for EPL betting-market analysis and value detection.

## Current project stage

The repository is being converted from a local Python prototype into an installable Windows application with automated builds and update-ready versioning.

Current application version: **1.2.0**

## Architecture

- `app/main.py` — Windows application entry point
- `VERSION` — single source of truth for the application version
- `requirements.txt` — Python/build dependencies
- `installer/installer.iss` — Inno Setup Windows installer configuration
- `.github/workflows/build-windows.yml` — GitHub Actions Windows build pipeline

## Automatic Windows build

Every push to `main` automatically runs a Windows GitHub Actions job that:

1. checks out the repository;
2. installs Python 3.12;
3. installs the dependencies;
4. builds `EPLValueBetting.exe` with PyInstaller;
5. builds a normal Windows installer with Inno Setup;
6. uploads both the installer and portable EXE as downloadable workflow artifacts.

The setup branch also builds automatically so the pipeline can be verified before merging.

## Downloading a development build

1. Open the repository on GitHub.
2. Select **Actions**.
3. Open the latest successful **Build Windows installer** run.
4. Scroll to **Artifacts**.
5. Download `EPL-Value-Betting-v1.2.0-Windows-Setup`.
6. Unzip that GitHub artifact once and run the contained `EPL-Value-Betting-v1.2.0-Setup.exe`.

Once formal GitHub Releases are enabled, normal users will download only the installer from the Releases page rather than Actions.

## Updating the app

The application has the initial GitHub Releases update-checking framework. The intended production workflow is:

1. code is updated;
2. `VERSION` is increased;
3. GitHub builds the Windows installer;
4. a GitHub Release is published;
5. the installed application detects that a newer release exists;
6. the user downloads and installs the update over the existing version.

Application research data and settings will be stored outside `Program Files` so upgrades do not erase them.

## Security

Never commit API keys, tokens, betting credentials or personal secrets to this repository. Runtime credentials will be stored locally on the user's Windows computer rather than in GitHub.

## Planned next step

After the Windows installer pipeline is verified, migrate the working EPL V1.2 Sportsbet/Polymarket strategy interface into the installed application, followed by persistent odds-history and closing-line-value storage.
