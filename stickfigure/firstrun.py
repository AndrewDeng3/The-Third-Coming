"""First-run requirements: is Ollama installed and running, and are the AI models downloaded?

Everything here is plain blocking code (run it in a thread); the setup window drives it and shows progress.
The voice models (Kokoro, Whisper) aren't handled here: they download by themselves on first use.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from stickfigure.config import CONFIG, Config

OLLAMA_SETUP_URL = "https://ollama.com/download/OllamaSetup.exe"
# Rough download sizes, for the setup window (GB).
MODEL_SIZES = {"qwen3:14b": 9.3, "qwen3:8b": 5.2, "qwen3:4b": 2.5, "embeddinggemma": 0.6, "qwen2.5:7b-instruct": 4.7,
               "llama3.2:3b": 2.0, "qwen2.5-coder:7b": 4.7}


@dataclass(frozen=True)
class Brain:
    label: str
    model: str
    gb: float
    note: str
    min_vram: float  # GB of graphics memory it runs well in


# The choice of "brain" (one model does everything). Bigger = smarter, but a bigger download and slower on small GPUs.
BRAINS = (
    Brain("Light", "qwen3:4b", 2.5, "smallest download, runs on any PC", 0.0),
    Brain("Balanced", "qwen3:8b", 5.2, "smarter; best with 8 GB+ of graphics memory", 7.0),
    Brain("Smart", "qwen3:14b", 9.3, "smartest; needs 12 GB+ of graphics memory", 11.0),
)


def gpu_memory_gb() -> float:
    """The largest graphics card's dedicated memory (GB), from the display drivers' registry info (0 if unknown)."""
    try:
        import winreg
    except ImportError:
        return 0.0
    best = 0
    key_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as cls:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(cls, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(cls, sub) as k:
                        for name in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                v, _ = winreg.QueryValueEx(k, name)
                            except OSError:
                                continue
                            if isinstance(v, bytes):
                                v = int.from_bytes(v[:8], "little")
                            best = max(best, int(v))
                except OSError:
                    continue
    except OSError:
        return 0.0
    return best / 2**30


def recommended_brain(vram_gb: float | None = None) -> Brain:
    vram = gpu_memory_gb() if vram_gb is None else vram_gb
    fits = [b for b in BRAINS if b.min_vram <= vram]
    return fits[-1] if fits else BRAINS[0]


def brain_for(model: str) -> Brain | None:
    return next((b for b in BRAINS if b.model == model), None)


def delete_model(model: str, url: str = CONFIG.ollama_url) -> None:
    r = httpx.request("DELETE", f"{url}/api/delete", json={"model": model}, timeout=30)
    r.raise_for_status()


def unused_brains(chosen: str, url: str = CONFIG.ollama_url) -> list[Brain]:
    """Other brain sizes that are downloaded but not in use (they can be removed to free disk space)."""
    try:
        have = installed_models(url)
    except (httpx.HTTPError, ValueError, KeyError):
        return []
    return [b for b in BRAINS if b.model != chosen and _norm(b.model) in have]

Progress = Callable[[float, str], None]  # (fraction 0..1, status text)


@dataclass
class Status:
    ollama_installed: bool
    ollama_running: bool
    missing_models: list[str]

    @property
    def ready(self) -> bool:
        return self.ollama_running and not self.missing_models


def required_models(cfg: Config = CONFIG) -> list[str]:
    seen: list[str] = []
    for m in (cfg.chat_model, cfg.extract_model, cfg.action_model, cfg.code_model, cfg.embed_model):
        if m and m not in seen:
            seen.append(m)
    return seen


def _norm(name: str) -> str:
    return name if ":" in name else name + ":latest"


def ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(default) if default.exists() else None


def ollama_running(url: str = CONFIG.ollama_url, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(f"{url}/api/version", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def installed_models(url: str = CONFIG.ollama_url) -> set[str]:
    r = httpx.get(f"{url}/api/tags", timeout=5)
    r.raise_for_status()
    return {_norm(m["name"]) for m in r.json().get("models", [])}


def check(cfg: Config = CONFIG) -> Status:
    running = ollama_running(cfg.ollama_url)
    installed = running or ollama_exe() is not None
    missing = required_models(cfg)
    if running:
        try:
            have = installed_models(cfg.ollama_url)
            missing = [m for m in missing if _norm(m) not in have]
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return Status(installed, running, missing)


def start_ollama(url: str = CONFIG.ollama_url, wait: float = 20.0) -> bool:
    """Start the Ollama server (in the background, no window) if it's installed but not running."""
    if ollama_running(url):
        return True
    exe = ollama_exe()
    if exe is None:
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([exe, "serve"], creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, close_fds=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if ollama_running(url):
            return True
        time.sleep(0.5)
    return False


def download_ollama_installer(on_progress: Progress, cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Fetch the official Ollama installer (from ollama.com) into a temp folder."""
    dest = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
    part = dest.with_suffix(".part")
    with httpx.stream("GET", OLLAMA_SETUP_URL, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) or None
        done = 0
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                if cancelled():
                    raise InterruptedError("cancelled")
                f.write(chunk)
                done += len(chunk)
                on_progress(done / total if total else 0.0, f"Downloading Ollama… {done / 1e6:.0f} MB")
    part.replace(dest)
    return dest


def run_ollama_installer(installer: Path) -> int:
    """Run Ollama's own installer (the user sees and clicks through it) and wait for it to finish."""
    return subprocess.run([str(installer)], check=False).returncode


def pull_model(model: str, on_progress: Progress, url: str = CONFIG.ollama_url,
               cancelled: Callable[[], bool] = lambda: False) -> None:
    """Download a model through the Ollama API, reporting overall progress across its layers."""
    layers: dict[str, tuple[int, int]] = {}
    with httpx.stream("POST", f"{url}/api/pull", json={"model": model, "stream": True}, timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if cancelled():
                raise InterruptedError("cancelled")
            if not line:
                continue
            msg = json.loads(line)
            if "error" in msg:
                raise RuntimeError(msg["error"])
            status = msg.get("status", "")
            if msg.get("digest") and msg.get("total"):
                layers[msg["digest"]] = (msg.get("completed", 0), msg["total"])
            total = sum(t for _, t in layers.values())
            done = sum(c for c, _ in layers.values())
            frac = done / total if total else 0.0
            if total:
                status = f"{status} - {done / 1e9:.2f} / {total / 1e9:.2f} GB"
            on_progress(frac if status != "success" else 1.0, status)
