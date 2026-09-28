"""Thin ctypes bindings for the Win32 / DWM calls StickFigure needs."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from stickfigure.world.geometry import Monitor, Rect

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

# --- constants -------------------------------------------------------------

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

GW_HWNDNEXT = 2

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14

WDA_EXCLUDEFROMCAPTURE = 0x11

MONITORINFOF_PRIMARY = 1

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MOVESIZESTART = 0x000A
EVENT_SYSTEM_MOVESIZEEND = 0x000B
EVENT_SYSTEM_MINIMIZESTART = 0x0016
EVENT_SYSTEM_MINIMIZEEND = 0x0017
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
EVENT_OBJECT_REORDER = 0x8004
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
EVENT_OBJECT_CLOAKED = 0x8017
EVENT_OBJECT_UNCLOAKED = 0x8018
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0
CHILDID_SELF = 0

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_CANCEL = 0x03  # Ctrl+Pause arrives as VK_CANCEL
VK_PAUSE = 0x13

# --- structs / prototypes ---------------------------------------------------


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


WINEVENTPROC = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,  # hWinEventHook
    wintypes.DWORD,  # event
    wintypes.HWND,
    wintypes.LONG,  # idObject
    wintypes.LONG,  # idChild
    wintypes.DWORD,  # idEventThread
    wintypes.DWORD,  # dwmsEventTime
)
MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)

user32.GetTopWindow.argtypes = [wintypes.HWND]
user32.GetTopWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.SetWindowLongW.restype = wintypes.LONG
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
user32.SetWinEventHook.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p

dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT

# --- helpers ----------------------------------------------------------------


def _rect(r: wintypes.RECT) -> Rect:
    return Rect(r.left, r.top, r.right, r.bottom)


def frame_bounds(hwnd: int) -> Rect | None:
    """Visible window bounds, excluding the invisible resize borders GetWindowRect includes."""
    r = wintypes.RECT()
    try:
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
    except OSError:
        return None
    return _rect(r)


def is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD()
    try:
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    except OSError:
        return False
    return cloaked.value != 0


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_name(hwnd: int) -> str:
    """Executable name (e.g. 'chrome.exe') of the process owning `hwnd`, or '' if unavailable."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, window_pid(hwnd))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(h)


def z_ordered_windows() -> list[int]:
    """All top-level windows, top-most first."""
    out: list[int] = []
    hwnd = user32.GetTopWindow(None)
    while hwnd:
        out.append(hwnd)
        hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
    return out


def monitors() -> list[Monitor]:
    found: list[Monitor] = []

    def cb(hmon, _hdc, _rc, _lp):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            mon = Monitor(_rect(info.rcMonitor), _rect(info.rcWork))
            if info.dwFlags & MONITORINFOF_PRIMARY:
                found.insert(0, mon)
            else:
                found.append(mon)
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return found


def add_ex_style(hwnd: int, flags: int) -> None:
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | flags)


def exclude_from_capture(hwnd: int) -> bool:
    """Hide the window from screenshots / screen capture (Win10 2004+)."""
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]


user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
GA_ROOT = 2


def root_window_at(x: int, y: int) -> int:
    """The top-level window a mouse click at (x, y) would reach.

    Mirrors how Windows routes mouse input: the top-most visible window containing the point,
    skipping click-through (WS_EX_TRANSPARENT) windows such as our figure, bubble and highlight.
    (WindowFromPoint alone isn't reliable about skipping layered click-through windows.)
    """
    for h in z_ordered_windows():
        if not user32.IsWindowVisible(h) or user32.IsIconic(h) or is_cloaked(h):
            continue
        if user32.GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TRANSPARENT:
            continue
        r = frame_bounds(h)
        if r is not None and r.left <= x < r.right and r.top <= y < r.bottom:
            return int(h)
    return 0


def bring_target_forward(hwnd: int, own_pid: int) -> bool:
    """Make `hwnd` the foreground window, but only by taking focus back from *our own* windows
    (e.g. the chat box the request was typed into). If the user switched to some other app,
    that's their choice: return False rather than yanking focus away from them."""
    import time as _time

    fg = int(user32.GetForegroundWindow() or 0)
    if fg == hwnd or (fg and window_pid(fg) == window_pid(hwnd)):
        return True
    if fg and window_pid(fg) != own_pid:
        return False
    user32.SetForegroundWindow(hwnd)
    for _ in range(20):
        _time.sleep(0.02)
        if int(user32.GetForegroundWindow() or 0) == hwnd:
            _time.sleep(0.1)  # let the app restore its caret/focus
            return True
    return False


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]


def user_idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input (system-wide)."""
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def remove_ex_style(hwnd: int, flags: int) -> None:
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~flags)


def cursor_pos() -> tuple[int, int]:
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def move_window(hwnd: int, x: int, y: int) -> None:
    user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


SWP_NOMOVE = 0x0002


def bring_to_top(hwnd: int) -> None:
    """Put a window at the very top of the always-on-top band, without moving, resizing, or focusing it."""
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def place_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)


def is_per_monitor_dpi_aware() -> bool:
    ctx = user32.GetThreadDpiAwarenessContext()
    return user32.GetAwarenessFromDpiAwarenessContext(ctx) == 2  # DPI_AWARENESS_PER_MONITOR_AWARE


def mouse_down() -> bool:
    """Is the left or right mouse button held right now?"""
    ks = ctypes.windll.user32.GetAsyncKeyState
    return bool((ks(0x01) & 0x8000) or (ks(0x02) & 0x8000))
