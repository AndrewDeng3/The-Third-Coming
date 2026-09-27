"""Downloads voice model files on first use into <data_dir>/models."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from stickfigure.config import CONFIG

log = logging.getLogger(__name__)

KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
# fp32 on CPU measured ~0.18x real time here; the int8 build was ~5x slower (poor int8 kernels) and
# DirectML can't run this model (ConvTranspose fails), so full precision on CPU it is.
KOKORO_FILES = {"model": "kokoro-v1.0.onnx", "voices": "voices-v1.0.bin"}


def models_dir() -> Path:
    d = Path(CONFIG.data_dir) / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure(url: str, dest: Path) -> Path:
    """Download `url` to `dest` if it isn't there yet (atomic: via a .part file)."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    log.info("downloading %s", url)
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60.0, connect=10.0)) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with part.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
        if total and done != total:
            raise IOError(f"incomplete download of {url}: {done}/{total} bytes")
    part.replace(dest)
    log.info("saved %s (%.0f MB)", dest.name, dest.stat().st_size / 2**20)
    return dest


def kokoro_files() -> tuple[Path, Path]:
    d = models_dir()
    return tuple(ensure(KOKORO_BASE + name, d / name) for name in (KOKORO_FILES["model"], KOKORO_FILES["voices"]))
