"""Pet mischief: small, harmless, unprompted input - the figure being playful with your desktop.

Hard limits (enforced here, not left to a model):
  - Only cursor moves, scroll-wheel notches, and pauses. There is no click or type step in any prank.
  - Only after the user has been idle for a while, never during an action task, never in a
    sensitive window (same deny list as tasks), never in fullscreen apps.
  - A dedicated input watch stops a prank on ANY real input (mouse move, click, key, Esc).
  - The cursor is put back where it was (unless the user took over), and scrolls are undone.
  - Every prank is written to the audit log.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Callable

from stickfigure.actions.executor import Executor, Move, Pause, Wheel
from stickfigure.actions.input_driver import cursor_pos
from stickfigure.actions.task import clickable_point
from stickfigure.agent.emotion import Emotion
from stickfigure.config import CONFIG, Config, temp_scale
from stickfigure.perception.perception import Perception, Target
from stickfigure.safety.audit import AuditLog
from stickfigure.safety.override import InputWatch
from stickfigure.safety.policy import WindowInfo, window_problem
from stickfigure.win import win32

log = logging.getLogger(__name__)

ALLOWED_STEPS = (Move, Wheel, Pause)
LINES = {
    "tug": ["Boop!", "Gotcha!", "Hehe, mine!", "Tug of war!"],
    "read": ["Hmm, what's this...", "Ooh, reading time.", "Let me see..."],
    "hover": ["Ooh, what does '{name}' do?", "What's this button?", "Hmm, '{name}'..."],
}


class Mischief:
    def __init__(self, perception: Perception, audit: AuditLog, emotion: Emotion, cfg: Config = CONFIG):
        self.perception = perception
        self.audit = audit
        self.emotion = emotion
        self.cfg = cfg
        self.enabled = cfg.mischief
        self.executor = Executor(cfg)
        self.watch = InputWatch(lambda kind: self.executor.cancel(f"you used the {kind}"),
                                lambda: self.executor.cancel("emergency stop"),
                                triggers=("esc", "click", "any_key", "mouse_move"))
        self.running = False
        self.temperature = cfg.temperature
        self._next_at = time.monotonic() + random.uniform(*cfg.mischief_gap) / 2
        # Hooks set by the app
        self.can_play: Callable[[], bool] = lambda: True  # e.g. no task running, not fullscreen
        self.say: Callable[[str], None] = lambda text: None
        self.point: Callable[[tuple[float, float]], None] = lambda pt: None
        self.figure_x: Callable[[], float] = lambda: 0.0
        self.before_input: Callable[[], None] = lambda: None
        self.after_input: Callable[[], None] = lambda: None

    # -- scheduling ----------------------------------------------------------------------------

    def tick(self) -> None:
        now = time.monotonic()
        if not self.enabled or self.running or now < self._next_at:
            return
        self._next_at = now + random.uniform(*self.cfg.mischief_gap) * temp_scale(self.temperature)
        e = self.emotion
        playful = (e.happiness > 0.45 or e.curiosity > 0.6) and e.energy > 0.3 and e.annoyance < 0.4
        if not playful or win32.user_idle_seconds() < self.cfg.mischief_idle or not self.can_play():
            return
        target = self._foreground_target()
        if target is not None:
            asyncio.ensure_future(self.play(target))

    def stop(self) -> None:
        self.executor.cancel("stopped")

    def _foreground_target(self) -> Target | None:
        fg = int(win32.user32.GetForegroundWindow() or 0)
        if not fg or win32.window_pid(fg) == os.getpid():
            return None
        info = WindowInfo(fg, win32.process_name(fg), win32.class_name(fg), win32.window_title(fg))
        rect = win32.frame_bounds(fg)
        if window_problem(info) or rect is None or rect.width < 300 or rect.height < 200:
            return None
        cx, cy = cursor_pos()
        if not (rect.left <= cx < rect.right and rect.top <= cy < rect.bottom):
            return None  # the cursor is elsewhere (e.g. on the desktop): leave it alone
        return Target(fg, info.title, info.process, rect)

    # -- pranks -------------------------------------------------------------------------------------

    async def play(self, target: Target, kind: str | None = None) -> str:
        kind = kind or random.choice(["tug", "tug", "read", "hover"])
        home = cursor_pos()
        steps, line, point = await self._plan(kind, target, home)
        if not steps:
            return "skipped"
        assert all(isinstance(s, ALLOWED_STEPS) for s in steps), "mischief may only move, scroll, and pause"
        self.running = True
        self.say(line)
        if point:
            self.point(point)
        self.before_input()
        self.executor.reset()
        self.watch.arm()
        try:
            result = await asyncio.wrap_future(self.executor.submit(steps, target.hwnd))
        finally:
            self.watch.disarm()
            self.after_input()
            self.running = False
        self.audit.write("mischief", kind=kind, app=target.app, status=result.status, detail=result.detail)
        log.info("mischief %s in %s: %s", kind, target.app, result.status)
        return result.status

    async def _plan(self, kind: str, target: Target, home: tuple[int, int]):
        r = target.rect
        clamp = lambda x, y: (min(max(x, r.left + 5), r.right - 6), min(max(y, r.top + 5), r.bottom - 6))  # noqa: E731
        hx, hy = home
        if kind == "tug":
            toward = 1 if self.figure_x() >= hx else -1
            pull = clamp(hx + toward * 70, hy + 10)
            wiggle = [clamp(pull[0] + dx, pull[1]) for dx in (-15, 15, -10)]
            steps = [Move(*pull), Pause(0.25)] + [Move(*w) for w in wiggle] + [Pause(0.2), Move(hx, hy)]
            return steps, random.choice(LINES["tug"]), pull
        if kind == "read":
            notches = random.randint(2, 4)
            steps = []
            for _ in range(notches):
                steps += [Wheel(-1), Pause(0.7)]
            steps += [Pause(1.2)] + [Wheel(notches)]  # scroll back to where it was
            return steps, random.choice(LINES["read"]), None
        if kind == "hover":
            uia = await asyncio.wrap_future(self.perception.uia.elements(target.hwnd, r))
            options = [e for e in uia if e.name and e.role in ("Button", "Hyperlink", "TabItem") and len(e.name) < 40]
            random.shuffle(options)
            for el in options[:8]:
                pt = clickable_point(el.rect, r, win32.window_pid(target.hwnd))
                if pt:
                    steps = [Move(*pt), Pause(1.6), Move(hx, hy)]
                    return steps, random.choice(LINES["hover"]).format(name=el.name[:30]), pt
        return [], "", None

    def close(self) -> None:
        self.watch.disarm()
        self.executor.close()
