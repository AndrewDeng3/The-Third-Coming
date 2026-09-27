"""Start with Windows: a value under HKCU\\...\\Run (per-user, no admin needed). Only changed when the
user toggles it in Settings."""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "StickFigure"


def command() -> str:
    if getattr(sys, "frozen", False):  # packaged exe
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = Path(__file__).resolve().parents[2] / "run_stickfigure.pyw"
    return f'"{pythonw}" "{launcher}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            value, _ = winreg.QueryValueEx(k, NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass
