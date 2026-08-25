from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.request import Request, urlopen

APP_NAME = "EPL Value Betting"
REPO = "skara8/EPL-Value-Betting"
ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"


def read_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return "1.2.0"


VERSION = read_version()


def parse_version(value: str) -> tuple[int, ...]:
    clean = value.strip().lower().lstrip("v")
    parts = []
    for piece in clean.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def latest_release() -> tuple[str, str] | None:
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    req = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "EPL-Value-Betting-Updater",
        },
    )
    with urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))

    tag = str(data.get("tag_name") or "").strip()
    html_url = str(data.get("html_url") or "").strip()
    if not tag or not html_url:
        return None
    return tag, html_url


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("760x460")
        self.minsize(680, 420)

        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text=APP_NAME,
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            outer,
            text=f"Version {VERSION}",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(2, 22))

        card = ttk.LabelFrame(outer, text="Windows application setup", padding=18)
        card.pack(fill="x")

        ttk.Label(
            card,
            text=(
                "The Windows installer and update pipeline are now configured. "
                "The full EPL odds and strategy interface will replace this setup screen "
                "once the build pipeline has been verified."
            ),
            wraplength=650,
            justify="left",
        ).pack(anchor="w")

        ttk.Separator(card).pack(fill="x", pady=16)

        ttk.Label(
            card,
            text=(
                "Future versions will preserve local settings and research data under "
                "your Windows user profile rather than inside Program Files."
            ),
            wraplength=650,
            justify="left",
        ).pack(anchor="w")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(24, 0))

        ttk.Button(
            actions,
            text="Check for updates",
            command=self.check_updates,
        ).pack(side="left")

        ttk.Button(
            actions,
            text="Close",
            command=self.destroy,
        ).pack(side="right")

        self.status = tk.StringVar(value="Ready")
        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=(20, 0))

    def check_updates(self) -> None:
        self.status.set("Checking GitHub Releases…")
        self.update_idletasks()
        try:
            result = latest_release()
            if result is None:
                self.status.set("No published release found yet.")
                messagebox.showinfo(
                    "Updates",
                    "No published GitHub Release exists yet. This is expected during initial setup.",
                )
                return

            latest, release_url = result
            if parse_version(latest) > parse_version(VERSION):
                self.status.set(f"Update {latest} is available.")
                messagebox.showinfo(
                    "Update available",
                    f"Version {latest} is available.\n\nRelease page:\n{release_url}",
                )
            else:
                self.status.set("You are using the latest published version.")
                messagebox.showinfo(
                    "Updates",
                    f"You are using the latest published version ({VERSION}).",
                )
        except Exception as exc:
            self.status.set("Could not check for updates.")
            messagebox.showwarning(
                "Update check failed",
                f"The update check could not be completed.\n\n{exc}",
            )


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
