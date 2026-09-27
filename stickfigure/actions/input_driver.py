"""Synthetic mouse/keyboard input via SendInput, plus clipboard helpers.

Every event we inject carries INJECT_TAG in dwExtraInfo so the user-override hook can tell
our input from the user's (anything else - physical or other software - counts as the user).
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INJECT_TAG = 0x5F1C0DE5

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

# Key names -> virtual-key codes (only what the hotkey allowlist can use, plus modifiers).
VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "space": 0x20, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
}
EXTENDED = {0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28}
MODIFIERS = ("ctrl", "shift", "alt", "win")


class InputError(RuntimeError):
    pass


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        # Typically UIPI: the target runs elevated and we don't. We never try to get around that.
        raise InputError(f"SendInput delivered {sent}/{len(inputs)} events (error {ctypes.get_last_error()})")


def _key(vk: int = 0, scan: int = 0, up: bool = False, unicode: bool = False) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_UNICODE if unicode else 0)
    if vk in EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    i = INPUT(type=INPUT_KEYBOARD)
    i.u.ki = KEYBDINPUT(vk, scan, flags, 0, INJECT_TAG)
    return i


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.u.mi = MOUSEINPUT(dx, dy, ctypes.c_ulong(data & 0xFFFFFFFF).value, flags, 0, INJECT_TAG)
    return i


def move_to(x: float, y: float) -> None:
    """Absolute move in physical screen pixels (virtual desktop)."""
    vx, vy = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    nx = round((x - vx) * 65535 / max(1, vw - 1))
    ny = round((y - vy) * 65535 / max(1, vh - 1))
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))


def mouse_button(button: str, down: bool) -> None:
    flag = {("left", True): MOUSEEVENTF_LEFTDOWN, ("left", False): MOUSEEVENTF_LEFTUP,
            ("right", True): MOUSEEVENTF_RIGHTDOWN, ("right", False): MOUSEEVENTF_RIGHTUP}[(button, down)]
    _send(_mouse(flag))


def wheel(clicks: int) -> None:
    _send(_mouse(MOUSEEVENTF_WHEEL, data=120 * clicks))


def key(name: str, up: bool = False) -> None:
    _send(_key(VK[name], up=up))


def unicode_char(ch: str) -> None:
    """Type one character regardless of keyboard layout. Handles astral chars via UTF-16 pairs."""
    units = ch.encode("utf-16-le")
    downs = [_key(scan=int.from_bytes(units[i:i + 2], "little"), unicode=True) for i in range(0, len(units), 2)]
    ups = [_key(scan=int.from_bytes(units[i:i + 2], "little"), unicode=True, up=True) for i in range(0, len(units), 2)]
    _send(*downs, *ups)


def release_all() -> None:
    """Emergency cleanup: lift every modifier and mouse button we might be holding."""
    for name in MODIFIERS:
        try:
            _send(_key(VK[name], up=True))
        except InputError:
            pass
    for b in ("left", "right"):
        try:
            mouse_button(b, False)
        except InputError:
            pass


def bezier_path(start: tuple[float, float], end: tuple[float, float], steps: int, bend: float) -> list[tuple[float, float]]:
    """A gently curved path (quadratic Bézier with ease-in-out), like a hand moving a mouse."""
    (x0, y0), (x1, y1) = start, end
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = x1 - x0, y1 - y0
    cx, cy = mx - dy * bend, my + dx * bend  # control point off to the side
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)
        pts.append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1,
                    (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1))
    return pts


def cursor_pos() -> tuple[int, int]:
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


# -- clipboard (text only) ------------------------------------------------------------------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
TEXT_FORMATS = {1, 7, 13, 16}  # CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_LOCALE


def _open_clipboard() -> bool:
    for _ in range(10):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_is_text_only() -> bool:
    """True if replacing the clipboard with text and restoring it loses nothing (no images/files)."""
    if not _open_clipboard():
        return False
    try:
        fmt, formats = 0, set()
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            formats.add(fmt)
        return formats <= TEXT_FORMATS
    finally:
        user32.CloseClipboard()


def get_clipboard_text() -> str | None:
    if not _open_clipboard():
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str | None) -> bool:
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        if text is None:
            return True
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, size)
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()
