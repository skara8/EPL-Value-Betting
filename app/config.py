from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

APP_NAME = "EPL Value Betting"
APP_ID = "EPLValueBetting"
REPO = "skara8/EPL-Value-Betting"


def resource_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def read_version() -> str:
    try:
        return (resource_root() / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "3.0.0"


VERSION = read_version()


def user_data_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path.home() / ".local" / "share"
    path = root / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = user_data_dir()
LOG_DIR = DATA_DIR / "logs"
EXPORT_DIR = DATA_DIR / "exports"
SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "research.db"

for folder in (LOG_DIR, EXPORT_DIR):
    folder.mkdir(parents=True, exist_ok=True)


@dataclass
class AppSettings:
    min_ev_pct: float = 4.0
    default_days_ahead: int = 7
    save_snapshots: bool = True
    remember_api_key: bool = True
    candidates_only: bool = False
    min_pm_volume: float = 0.0
    check_updates_on_start: bool = True

    @classmethod
    def load(cls) -> "AppSettings":
        if not SETTINGS_FILE.exists():
            return cls()
        try:
            data: dict[str, Any] = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            allowed = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            obj = cls(**allowed)
            obj.min_ev_pct = float(obj.min_ev_pct)
            obj.default_days_ahead = max(1, min(60, int(obj.default_days_ahead)))
            obj.min_pm_volume = max(0.0, float(obj.min_pm_volume))
            return obj
        except Exception:
            return cls()

    def save(self) -> None:
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
