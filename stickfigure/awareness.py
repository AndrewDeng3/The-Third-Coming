"""Constant, cheap awareness of what the user is doing (no screenshots, no model calls).

Sampled a couple of times a second from signals Windows hands out for free:
  - the window in front (app + title) -> app switches, page/tab/document changes
  - the focused element (read every couple of seconds) -> "typing in the Search box"
  - input activity -> typing vs. using the mouse vs. away (from idle time + cursor movement;
    individual keystrokes are never read)
  - time spent per app today

It keeps a short rolling log of changes plus the current state, as plain text for the mind and the chat.
Privacy: kept in memory only (never written to disk), sensitive windows (banking, passwords, security
prompts - the same deny list as tasks) are recorded as "a private window" without their title.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

AWAY_AFTER = 120.0  # seconds without input = stepped away
MERGE_TITLE_CHANGES = 3.0  # rapid title changes (loading pages) collapse into one event


@dataclass
class _State:
    app: str = ""
    title: str = ""
    private: bool = False
    since: float = 0.0
    focus: str = ""
    activity: str = "idle"  # typing | mouse | reading | away
    away_since: float | None = None
    app_time: dict[str, float] = field(default_factory=dict)


def _app_name(process: str) -> str:
    p = process.lower().removesuffix(".exe")
    names = {"chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "code": "VS Code", "explorer": "File Explorer",
             "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "discord": "Discord", "spotify": "Spotify",
             "notepad": "Notepad", "slack": "Slack", "teams": "Teams", "steam": "Steam", "obs64": "OBS"}
    return names.get(p, process.removesuffix(".exe") or "an app")


def _short_title(title: str, app: str) -> str:
    """Drop the trailing ' - Google Chrome' style suffix; keep it short."""
    for sep in (" - ", " — ", " | "):
        parts = title.rsplit(sep, 1)
        if len(parts) == 2 and parts[1].lower().replace(" ", "") in (
                app.lower().replace(" ", ""), "googlechrome", "microsoftedge", "mozillafirefox",
                "visualstudiocode", "notepad", "word", "fileexplorer"):
            title = parts[0]
    return title if len(title) <= 70 else title[:67] + "..."


class Awareness:
    def __init__(self, max_events: int = 40):
        self.enabled = True
        self.events: deque[tuple[float, str]] = deque(maxlen=max_events)
        self.s = _State()
        self._last_cursor: tuple[int, int] | None = None
        self._last_tick = 0.0
        self._pending_title: tuple[float, str] | None = None
        self._day = time.localtime().tm_yday

    # -- feeding it ------------------------------------------------------------------------------

    def observe(self, now: float, process: str, title: str, private: bool, cursor: tuple[int, int],
                idle: float) -> None:
        """One sample (call ~2x a second). `now` = time.time()."""
        if not self.enabled:
            return
        s = self.s
        if time.localtime(now).tm_yday != self._day:  # a new day: fresh per-app totals
            self._day, s.app_time = time.localtime(now).tm_yday, {}
        dt = min(5.0, now - self._last_tick) if self._last_tick else 0.0
        self._last_tick = now

        # Away / back
        if idle >= AWAY_AFTER and s.away_since is None:
            s.away_since = now - idle
            self._log(s.away_since, "The user stepped away from the computer")
        elif idle < AWAY_AFTER and s.away_since is not None:
            gone = now - s.away_since
            s.away_since = None
            self._log(now, f"The user came back after {self._dur(gone)}")

        # What kind of activity (from idle time + cursor motion; keys themselves are never read)
        moved = self._last_cursor is not None and (abs(cursor[0] - self._last_cursor[0]) +
                                                   abs(cursor[1] - self._last_cursor[1])) > 6
        self._last_cursor = cursor
        if s.away_since is not None:
            s.activity = "away"
        elif idle < 1.0:
            s.activity = "mouse" if moved else "typing"
        elif idle < 20:
            s.activity = "reading"  # looking at the screen, not touching anything
        else:
            s.activity = "idle"

        # Window in front
        app = _app_name(process) if process else ""
        shown = "a private window" if private else _short_title(title, app)
        if app and s.activity != "away":
            s.app_time[app] = s.app_time.get(app, 0.0) + dt
        if app != s.app:
            self._flush_title()
            if s.app:
                self._log(now, f"The user switched from {s.app} to {app}" + (f" ('{shown}')" if shown else ""))
            else:
                self._log(now, f"The user is in {app}" + (f" ('{shown}')" if shown else ""))
            s.app, s.title, s.private, s.since, s.focus = app, shown, private, now, ""
        elif shown != s.title:
            # Same app, new title: a new page, tab, song, or document. Loading pages flicker, so wait a moment.
            self._pending_title = (now, shown)
            s.title, s.private = shown, private
        if self._pending_title and now - self._pending_title[0] >= MERGE_TITLE_CHANGES:
            self._flush_title()

    def set_focus(self, description: str) -> None:
        """What has the keyboard focus (e.g. "Edit 'Search'"), read every couple of seconds."""
        if not self.enabled or self.s.private:
            return
        if description and description != self.s.focus:
            self.s.focus = description

    # -- reading it --------------------------------------------------------------------------------

    def now_text(self) -> str:
        """One or two lines about what the user is doing right now."""
        s = self.s
        if not self.enabled or not s.app:
            return ""
        if s.activity == "away" and s.away_since is not None:
            return f"The user has been away from the computer for {self._dur(time.time() - s.away_since)}."
        where = f"{s.app}" + (f" ('{s.title}')" if s.title else "")
        doing = {"typing": "typing", "mouse": "using the mouse", "reading": "reading / watching",
                 "idle": "not touching anything"}.get(s.activity, s.activity)
        focus = f", focused on {s.focus}" if s.focus and s.activity == "typing" else ""
        return f"Right now the user is in {where}, {doing}{focus} (for {self._dur(time.time() - s.since)})."

    def recent_text(self, n: int = 8) -> str:
        now = time.time()
        return "\n".join(f"- {self._dur(now - t)} ago: {text}" for t, text in list(self.events)[-n:])

    def today_text(self, n: int = 5) -> str:
        top = sorted(self.s.app_time.items(), key=lambda kv: -kv[1])[:n]
        return ", ".join(f"{app} {self._dur(sec)}" for app, sec in top if sec >= 60)

    def summary(self) -> str:
        """For the chat system prompt / the mind."""
        if not self.enabled:
            return ""
        parts = [self.now_text()]
        if self.events:
            parts.append("What the user has been doing lately:\n" + self.recent_text())
        today = self.today_text()
        if today:
            parts.append(f"Time in apps today: {today}.")
        return "\n".join(p for p in parts if p)

    # -- internals ----------------------------------------------------------------------------------

    def _flush_title(self) -> None:
        if self._pending_title is None:
            return
        t, shown = self._pending_title
        self._pending_title = None
        if shown:
            self._log(t, f"In {self.s.app}, the user moved on to '{shown}'")

    def _log(self, t: float, text: str) -> None:
        if self.events and self.events[-1][1] == text:
            return
        self.events.append((t, text))

    @staticmethod
    def _dur(sec: float) -> str:
        sec = max(0, int(sec))
        if sec < 60:
            return f"{sec}s"
        if sec < 3600:
            return f"{sec // 60} min"
        return f"{sec // 3600} h {sec % 3600 // 60} min"
