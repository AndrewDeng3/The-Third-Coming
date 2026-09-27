"""Tracks desktop windows (bounds + Z-order) as candidate physics platforms.

Event-driven via SetWinEventHook, with a slow reconcile poll as a safety net.
The hook callback runs on the Qt thread (WINEVENT_OUTOFCONTEXT delivers through
the thread's message loop), so it only flips a dirty flag.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from stickfigure.win import win32
from stickfigure.world.geometry import Monitor, Rect

IGNORED_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
    "XamlExplorerHostIslandWindow",
    "TopLevelWindowForOverflowXamlIsland",
}

HOOKED_EVENTS = [
    (win32.EVENT_SYSTEM_FOREGROUND, win32.EVENT_SYSTEM_FOREGROUND),
    (win32.EVENT_SYSTEM_MOVESIZESTART, win32.EVENT_SYSTEM_MOVESIZEEND),
    (win32.EVENT_SYSTEM_MINIMIZESTART, win32.EVENT_SYSTEM_MINIMIZEEND),
    (win32.EVENT_OBJECT_DESTROY, win32.EVENT_OBJECT_REORDER),
    (win32.EVENT_OBJECT_LOCATIONCHANGE, win32.EVENT_OBJECT_LOCATIONCHANGE),
    (win32.EVENT_OBJECT_CLOAKED, win32.EVENT_OBJECT_UNCLOAKED),
]


@dataclass(frozen=True, slots=True)
class TrackedWindow:
    hwnd: int
    rect: Rect
    cls: str
    title: str = ""


@dataclass(frozen=True, slots=True)
class DesktopSnapshot:
    windows: list[TrackedWindow]  # top-most first
    monitors: list[Monitor]
    fullscreen_foreground: bool
    taken_at: float


class WindowTracker:
    def __init__(self, min_size: tuple[int, int] = (120, 60), poll_interval: float = 0.2):
        self._own_hwnds: set[int] = set()
        self._min_w, self._min_h = min_size
        self._poll_interval = poll_interval
        self._dirty = True
        self._last_refresh = 0.0
        self._hooks: list[int] = []
        self._proc = win32.WINEVENTPROC(self._on_event)  # keep a reference alive
        self.snapshot = DesktopSnapshot([], win32.monitors(), False, 0.0)
        self.refresh_ms = 0.0
        fg = win32.user32.GetForegroundWindow()
        self.last_foreground: int | None = int(fg) if fg and win32.window_pid(fg) != os.getpid() else None

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        flags = win32.WINEVENT_OUTOFCONTEXT | win32.WINEVENT_SKIPOWNPROCESS
        for lo, hi in HOOKED_EVENTS:
            h = win32.user32.SetWinEventHook(lo, hi, None, self._proc, 0, 0, flags)
            if h:
                self._hooks.append(h)

    def stop(self) -> None:
        for h in self._hooks:
            win32.user32.UnhookWinEvent(h)
        self._hooks.clear()

    def ignore(self, hwnd: int) -> None:
        self._own_hwnds.add(int(hwnd))

    def mark_dirty(self) -> None:
        self._dirty = True

    # -- updates ----------------------------------------------------------------

    def _on_event(self, _hook, event, hwnd, id_object, id_child, _thread, _time) -> None:
        # LOCATIONCHANGE also fires for carets, cursors, scrollbars... only whole windows matter.
        if event == win32.EVENT_OBJECT_LOCATIONCHANGE and (
            id_object != win32.OBJID_WINDOW or id_child != win32.CHILDID_SELF
        ):
            return
        if event == win32.EVENT_SYSTEM_FOREGROUND and hwnd:
            # The hook skips our own process, so this is always the user's last *other* app:
            # what they mean by "my screen" even while they're typing in our chat window.
            self.last_foreground = int(hwnd)
        self._dirty = True

    def update(self) -> bool:
        """Refresh the snapshot if something changed or the poll interval elapsed. Returns True if refreshed."""
        now = time.perf_counter()
        if not self._dirty and now - self._last_refresh < self._poll_interval:
            return False
        self._dirty = False
        self._last_refresh = now
        self.snapshot = self._take_snapshot(now)
        self.refresh_ms = (time.perf_counter() - now) * 1000
        return True

    def _take_snapshot(self, now: float) -> DesktopSnapshot:
        mons = win32.monitors()
        wins: list[TrackedWindow] = []
        for hwnd in win32.z_ordered_windows():
            tw = self._track(hwnd)
            if tw:
                wins.append(tw)
        return DesktopSnapshot(wins, mons, self._foreground_is_fullscreen(mons), now)

    def _track(self, hwnd: int) -> TrackedWindow | None:
        u = win32.user32
        if hwnd in self._own_hwnds or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return None
        ex = u.GetWindowLongW(hwnd, win32.GWL_EXSTYLE)
        if ex & (win32.WS_EX_TRANSPARENT | win32.WS_EX_TOOLWINDOW):
            return None  # click-through overlays, tooltips, floating palettes
        if u.GetWindowTextLengthW(hwnd) == 0:
            return None
        cls = win32.class_name(hwnd)
        if cls in IGNORED_CLASSES or win32.is_cloaked(hwnd):
            return None
        rect = win32.frame_bounds(hwnd)
        if rect is None or rect.width < self._min_w or rect.height < self._min_h:
            return None
        return TrackedWindow(hwnd, rect, cls, win32.window_title(hwnd))

    def _foreground_is_fullscreen(self, mons: list[Monitor]) -> bool:
        fg = win32.user32.GetForegroundWindow()
        if not fg or fg in self._own_hwnds or win32.class_name(fg) in IGNORED_CLASSES:
            return False
        rect = win32.frame_bounds(fg)
        if rect is None or not any(rect == m.bounds for m in mons):
            return False
        # With an auto-hide taskbar, a normal maximized window also fills the monitor.
        style = win32.user32.GetWindowLongW(fg, win32.GWL_STYLE)
        return not (win32.user32.IsZoomed(fg) and style & win32.WS_CAPTION == win32.WS_CAPTION)
