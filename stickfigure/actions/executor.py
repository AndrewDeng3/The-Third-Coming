"""Executes low-level input steps on a dedicated thread, re-checking safety before EVERY event.

Before each individual SendInput:
  - not cancelled (kill switch / Esc / stop button)
  - the locked target window (or a popup from the same process, e.g. a menu) is in the foreground
    (if one of StickFigure's own windows grabbed focus, it's handed back; any other app means stop)
  - where the cursor lands, the point is inside that window's current bounds
  - for clicks/scrolls, the window actually under the point belongs to the target
  - rate limits (typing WPM, clicks per second)
Cancel latency is therefore one event (a few ms), and on any abort every held key/button is released.
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import time
from dataclasses import dataclass, field

from stickfigure.actions import input_driver as drv
from stickfigure.config import CONFIG, Config
from stickfigure.safety.policy import normalize_keys
from stickfigure.win import win32
from stickfigure.world.geometry import Rect


# -- steps ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    x: float
    y: float


@dataclass(frozen=True)
class Click:
    button: str = "left"
    count: int = 1


@dataclass(frozen=True)
class Keys:
    combo: str


@dataclass(frozen=True)
class Type:
    text: str


@dataclass(frozen=True)
class Wheel:
    clicks: int


@dataclass(frozen=True)
class Pause:
    seconds: float


@dataclass
class ExecResult:
    status: str  # done | cancelled | foreground_lost | out_of_bounds | blocked | error
    detail: str = ""
    events: int = 0
    elapsed: float = 0.0
    halted_at: float = 0.0  # perf_counter of the last event we sent (for kill-latency measurement)

    @property
    def ok(self) -> bool:
        return self.status == "done"


class Aborted(Exception):
    def __init__(self, status: str, detail: str = ""):
        super().__init__(detail)
        self.status, self.detail = status, detail


class OSDriver:
    """The real thing. Tests substitute a fake with the same methods."""

    move_to = staticmethod(drv.move_to)
    mouse_button = staticmethod(drv.mouse_button)
    wheel = staticmethod(drv.wheel)
    key = staticmethod(drv.key)
    unicode_char = staticmethod(drv.unicode_char)
    release_all = staticmethod(drv.release_all)
    cursor_pos = staticmethod(drv.cursor_pos)
    clipboard_is_text_only = staticmethod(drv.clipboard_is_text_only)
    get_clipboard_text = staticmethod(drv.get_clipboard_text)
    set_clipboard_text = staticmethod(drv.set_clipboard_text)

    @staticmethod
    def foreground() -> int:
        return int(win32.user32.GetForegroundWindow() or 0)

    @staticmethod
    def window_pid(hwnd: int) -> int:
        return win32.window_pid(hwnd)

    @staticmethod
    def bounds(hwnd: int) -> Rect | None:
        return win32.frame_bounds(hwnd)

    @staticmethod
    def pid_at(x: float, y: float) -> int:
        """Process owning the top-level window that would receive a click at (x, y)."""
        return win32.window_pid(win32.root_window_at(int(x), int(y)))

    own_pid = os.getpid()

    @staticmethod
    def describe(hwnd: int) -> str:
        return f"{win32.process_name(hwnd) or 'a protected window'} '{win32.window_title(hwnd)[:40]}'"

    @staticmethod
    def describe_at(x: float, y: float) -> str:
        h = win32.root_window_at(int(x), int(y))
        return OSDriver.describe(h) if h else "nothing"

    @staticmethod
    def refocus(hwnd: int) -> bool:
        return win32.bring_target_forward(hwnd, os.getpid())


@dataclass
class Executor:
    cfg: Config = CONFIG
    driver: object = field(default_factory=OSDriver)

    def __post_init__(self) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="executor")
        self._cancel = threading.Event()
        self._cancel_reason = ""
        self._last_click = 0.0
        self._target = 0
        self._target_pid = 0
        self._events = 0
        self._last_event_t = 0.0

    # -- control ------------------------------------------------------------------------

    def cancel(self, reason: str) -> None:
        """Thread-safe; takes effect before the next input event."""
        if not self._cancel.is_set():
            self._cancel_reason = reason
            self._cancel.set()

    def reset(self) -> None:
        self._cancel.clear()
        self._cancel_reason = ""

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def submit(self, steps: list, target_hwnd: int) -> concurrent.futures.Future:
        return self._pool.submit(self.run, steps, target_hwnd)

    # -- execution -----------------------------------------------------------------------

    def run(self, steps: list, target_hwnd: int) -> ExecResult:
        t0 = time.perf_counter()
        self._target = target_hwnd
        self._target_pid = self.driver.window_pid(target_hwnd)
        self._events = 0
        self._aim: tuple[float, float] | None = None
        try:
            for step in steps:
                self._do(step)
            return ExecResult("done", events=self._events, elapsed=time.perf_counter() - t0,
                              halted_at=self._last_event_t)
        except Aborted as a:
            self._cleanup()
            return ExecResult(a.status, a.detail, self._events, time.perf_counter() - t0, self._last_event_t)
        except drv.InputError as e:
            self._cleanup()
            return ExecResult("blocked", str(e), self._events, time.perf_counter() - t0, self._last_event_t)
        except Exception as e:  # noqa: BLE001 - any surprise must still release keys
            self._cleanup()
            return ExecResult("error", f"{e.__class__.__name__}: {e}", self._events, time.perf_counter() - t0,
                              self._last_event_t)

    def _cleanup(self) -> None:
        try:
            self.driver.release_all()
        except Exception:
            pass

    def _check(self, point: tuple[float, float] | None = None, press: bool = False) -> None:
        """Safety checks before an input event.

        point: the event lands here -> must be inside the target (or a popup from the same app).
        press: a click/scroll -> additionally, the window actually under the point must be the target's.
        (Cursor *travel* on the way to a point only checks cancel + focus: hovering is harmless.)
        """
        if self._cancel.is_set():
            raise Aborted("cancelled", self._cancel_reason)
        fg = self._ensure_foreground()
        if point is not None:
            # Popups (menus) from the target app may extend outside the main window.
            b = self.driver.bounds(self._target)
            fb = self.driver.bounds(fg) if fg != self._target else None
            inside = any(r is not None and r.left <= point[0] < r.right and r.top <= point[1] < r.bottom for r in (b, fb))
            if not inside:
                raise Aborted("out_of_bounds", f"point {tuple(round(v) for v in point)} is outside the target window")
        if press and point is not None and self.driver.pid_at(*point) != self._target_pid:
            raise Aborted("out_of_bounds", f"{self._describe_at(point)} is covering the target at that point")

    def _ensure_foreground(self) -> int:
        fg = self.driver.foreground()
        if fg == self._target or (fg and self.driver.window_pid(fg) == self._target_pid):
            return fg
        # One of StickFigure's own windows (panel, bubble, chat) grabbed focus: take it back for the
        # target. Any *other* app in front means the user switched away - stop.
        own = getattr(self.driver, "own_pid", None)
        if fg and own is not None and self.driver.window_pid(fg) == own and self.driver.refocus(self._target):
            return self.driver.foreground()
        who = self._describe(fg) if fg else "nothing"
        raise Aborted("foreground_lost", f"{who} took focus from the target window")

    def _describe(self, hwnd: int) -> str:
        return self.driver.describe(hwnd) if hasattr(self.driver, "describe") else f"window {hwnd}"

    def _describe_at(self, point: tuple[float, float]) -> str:
        return self.driver.describe_at(*point) if hasattr(self.driver, "describe_at") else "another window"

    def _wait(self, seconds: float) -> None:
        if self._cancel.wait(seconds):
            raise Aborted("cancelled", self._cancel_reason)

    def _sent(self) -> None:
        self._events += 1
        self._last_event_t = time.perf_counter()

    def _do(self, step) -> None:
        d = self.driver
        if isinstance(step, Move):
            self._check((step.x, step.y))  # refuse a bad destination before the cursor moves at all
            self._aim = (step.x, step.y)
            start = d.cursor_pos()
            dist = abs(step.x - start[0]) + abs(step.y - start[1])
            n = max(4, min(60, int(dist / 25)))
            path = drv.bezier_path(start, (step.x, step.y), n, bend=0.12)
            for i, pt in enumerate(path):
                self._check(pt if i == len(path) - 1 else None)  # only the destination must be on target
                d.move_to(*pt)
                self._sent()
                self._wait(0.008)
        elif isinstance(step, Click):
            gap = 1.0 / self.cfg.max_clicks_per_sec - (time.perf_counter() - self._last_click)
            if gap > 0:
                self._wait(gap)
            for i in range(step.count):
                pt = self._reaim()
                self._check(pt, press=True)
                d.mouse_button(step.button, True)
                self._sent()
                self._wait(0.04)
                d.mouse_button(step.button, False)  # always lift, even if cancelled mid-click
                self._sent()
                if i + 1 < step.count:
                    self._wait(0.07)
            self._last_click = time.perf_counter()
        elif isinstance(step, Keys):
            parts = normalize_keys(step.combo).split("+")
            mods, main = parts[:-1], parts[-1]
            self._check()
            pressed = []
            try:
                for m in mods:
                    self._check()
                    d.key(m)
                    pressed.append(m)
                    self._sent()
                self._check()
                d.key(main)
                self._sent()
                self._wait(0.03)
                d.key(main, up=True)
                self._sent()
            finally:
                for m in reversed(pressed):
                    d.key(m, up=True)
        elif isinstance(step, Type):
            if len(step.text) >= self.cfg.paste_threshold and d.clipboard_is_text_only():
                self._paste(step.text)
            else:
                interval = 60.0 / (self.cfg.typing_wpm * 5)
                if len(step.text) > 60:  # long text (the clipboard couldn't be borrowed): type it quickly
                    interval = min(interval, 0.012)
                for ch in step.text:
                    self._check()
                    if ch == "\n":
                        d.key("enter")
                        d.key("enter", up=True)
                    else:
                        d.unicode_char(ch)
                    self._sent()
                    self._wait(interval)
        elif isinstance(step, Wheel):
            for _ in range(abs(step.clicks)):
                pt = self._reaim()
                self._check(pt, press=True)
                d.wheel(1 if step.clicks > 0 else -1)
                self._sent()
                self._wait(0.05)
        elif isinstance(step, Pause):
            self._wait(step.seconds)
        else:
            raise Aborted("error", f"unknown step {step!r}")

    def _reaim(self) -> tuple[float, float]:
        """Put the cursor back on the intended point right before pressing: the user's hand may be on
        the mouse (moving it doesn't stop a task), and a nudge must never redirect a click."""
        if self._aim is None:
            return self.driver.cursor_pos()
        self._check(self._aim)
        self.driver.move_to(*self._aim)
        self._sent()
        return self._aim

    def _paste(self, text: str) -> None:
        """Long text: one Ctrl+V instead of thousands of keystrokes; the user's clipboard is restored."""
        d = self.driver
        saved = d.get_clipboard_text()
        self._check()
        if not d.set_clipboard_text(text):
            raise Aborted("error", "couldn't use the clipboard")
        try:
            self._wait(0.05)
            self._do(Keys("ctrl+v"))
            self._wait(0.25)  # let the app read the clipboard before we put the old contents back
        finally:
            d.set_clipboard_text(saved)

    def close(self) -> None:
        self.cancel("shutting down")
        self._pool.shutdown(wait=False, cancel_futures=True)
