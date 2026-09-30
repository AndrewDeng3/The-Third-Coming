"""Self-update from GitHub Releases.

Every push to main publishes TheThirdComing-Setup.exe as a new release (.github/workflows/release.yml). An installed
copy checks the latest release now and then; when it's newer, the figure asks in chat, and on "yes" it downloads the
installer, runs it silently (it closes this copy, updates the files in place, and relaunches), and quits.
Chat memory and settings live in %LOCALAPPDATA%\\StickFigure, so they carry over.

Only packaged builds update (a source checkout updates with git). Plain blocking code: run it in a thread.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

REPO = "TechWA1/The-Third-Coming"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "TheThirdComing-Setup.exe"
# Release downloads redirect from github.com to GitHub's own download hosts; anything else is refused.
TRUSTED_HOSTS = ("github.com", "githubusercontent.com")

Progress = Callable[[float, str], None]


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    notes: str = ""


def current_version() -> str:
    """This build's version ("" when running from source: never updates)."""
    try:
        from stickfigure._version import VERSION  # written by packaging/build.ps1
    except ImportError:
        return ""
    return VERSION


def can_update() -> bool:
    return bool(getattr(sys, "frozen", False) and current_version())


def parse_version(v: str) -> tuple[int, ...]:
    parts = []
    for p in v.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    if not latest or not current:
        return False
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _trusted(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return urlparse(url).scheme == "https" and any(host == h or host.endswith("." + h) for h in TRUSTED_HOSTS)


def latest_release(timeout: float = 10.0) -> Release | None:
    """The newest published release that has the installer attached (None if none / offline)."""
    r = httpx.get(LATEST_URL, timeout=timeout, headers={"Accept": "application/vnd.github+json"},
                  follow_redirects=True)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
    if asset is None or not _trusted(asset.get("browser_download_url", "")):
        return None
    return Release(data.get("tag_name", "").lstrip("vV"), asset["browser_download_url"], int(asset.get("size", 0)),
                   (data.get("body") or "")[:2000])


def check(current: str | None = None) -> Release | None:
    """The latest release if it's newer than this build, else None."""
    current = current_version() if current is None else current
    rel = latest_release()
    return rel if rel is not None and is_newer(rel.version, current) else None


def download(rel: Release, on_progress: Progress = lambda f, s: None,
             cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Fetch the installer into a temp folder, checking where it comes from and that it's complete."""
    folder = Path(tempfile.gettempdir()) / "TheThirdComing-Update"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"TheThirdComing-Setup-{rel.version}.exe"
    part = dest.with_suffix(".part")
    with httpx.stream("GET", rel.url, follow_redirects=True, timeout=60) as r:
        if not _trusted(str(r.url)):
            raise RuntimeError(f"refusing a download from {urlparse(str(r.url)).hostname}")
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) or rel.size or None
        done = 0
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                if cancelled():
                    raise InterruptedError("cancelled")
                f.write(chunk)
                done += len(chunk)
                on_progress(done / total if total else 0.0, f"Downloading update… {done / 1e6:.0f} MB")
    if rel.size and part.stat().st_size != rel.size:
        part.unlink(missing_ok=True)
        raise RuntimeError("the download was incomplete")
    part.replace(dest)
    return dest


def launch_installer(installer: Path) -> None:
    """Run the installer silently and detached. It closes this app, updates it in place, and starts it again
    (installer.iss relaunches after a silent install). The caller should quit right after."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                      "/FORCECLOSEAPPLICATIONS"], creationflags=flags, close_fds=True,
                     cwd=os.path.dirname(installer))
