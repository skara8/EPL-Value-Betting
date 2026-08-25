from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from config import REPO, VERSION


@dataclass
class ReleaseInfo:
    version: str
    tag: str
    page_url: str
    installer_url: Optional[str]
    notes: str


def parse_version(value: str) -> tuple[int, ...]:
    clean = value.strip().lower().lstrip("v")
    parts = []
    for piece in clean.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def latest_release(timeout: int = 12) -> Optional[ReleaseInfo]:
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    response = requests.get(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "EPL-Value-Betting-Updater",
        },
        timeout=timeout,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()

    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return None

    installer_url = None
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower().endswith("setup.exe"):
            installer_url = str(asset.get("browser_download_url") or "").strip() or None
            break

    return ReleaseInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        page_url=str(data.get("html_url") or ""),
        installer_url=installer_url,
        notes=str(data.get("body") or "").strip(),
    )


def update_available(release: ReleaseInfo) -> bool:
    return parse_version(release.version) > parse_version(VERSION)


def download_installer(release: ReleaseInfo, progress=None) -> Path:
    if not release.installer_url:
        raise RuntimeError("This release does not contain a Windows Setup.exe asset.")

    target = Path(tempfile.gettempdir()) / f"EPL-Value-Betting-v{release.version}-Setup.exe"
    with requests.get(release.installer_url, stream=True, timeout=30) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        done = 0
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    return target


def launch_installer(path: Path) -> None:
    if os.name == "nt":
        subprocess.Popen([str(path)], close_fds=True)
    else:
        raise RuntimeError("Automatic installer launch is only supported on Windows.")
