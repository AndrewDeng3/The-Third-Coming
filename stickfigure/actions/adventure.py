"""Idle adventures: while you're away, the figure amuses itself on the web.

  - "search": it thinks of something it's curious about (often related to what it knows you like), opens
    a Google search for it in a NEW browser tab, scrolls and reads the results, closes that tab again
    (only if it's still exactly the page it opened), and tells you what it learned.
  - "tabs": if your browser is in front, it flips to your next tab for a peek (Ctrl+Tab), has a look,
    flips back (Ctrl+Shift+Tab), and asks you about it.

Hard limits (enforced here, not left to a model):
  - Only when you've been idle for a while; any real input (mouse, keys) stops it instantly.
  - Steps are limited to cursor moves, scrolling, pauses, and the keys Ctrl+Tab / Ctrl+Shift+Tab /
    Ctrl+W - and Ctrl+W only on the tab it opened itself, re-verified right before pressing.
  - It never clicks, never types, never touches files, and skips sensitive windows (same deny list as tasks).
  - Everything is written to the audit log.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
import urllib.parse
import webbrowser
from typing import Callable

from stickfigure.actions.executor import Click, Executor, Keys, Move, Pause, Wheel
from stickfigure.actions.input_driver import cursor_pos
from stickfigure.actions.task import BROWSERS, clickable_point
from stickfigure.agent.emotion import Emotion
from stickfigure.agent.ollama import OllamaError
from stickfigure.config import CONFIG, Config, temp_clicks, temp_scale
from stickfigure.perception.perception import Perception, Target
from stickfigure.safety.audit import AuditLog
from stickfigure.safety.override import InputWatch
from stickfigure.safety.policy import WindowInfo, window_problem
from stickfigure.win import win32

log = logging.getLogger(__name__)

ALLOWED_KEYS = {"ctrl+tab", "ctrl+shift+tab", "ctrl+w", "alt+left"}  # alt+left = Back (own tab only)
# Links it never clicks on its own: ads, accounts, settings, downloads...
SKIP_LINK = re.compile(r"(sponsored|\bads?\b|sign ?in|log ?in|account|settings|privacy|terms|feedback|download|"
                       r"\.(exe|msi|zip|rar|7z|dmg|apk)\b|images|videos|shopping|maps|flights|books|more|tools|"
                       r"all filters|next|previous|google|gmail|people also|cached|translate|install)", re.I)
TOPICS = [
    "how do octopuses change color", "tallest sandcastle ever built", "why do cats knead", "longest domino chain",
    "how are stick figure animations made", "fastest animal on earth", "strangest deep sea creatures",
    "how does a yo-yo sleep", "biggest lego build ever", "why is the sky blue at noon but red at sunset",
    "how do bees make honey", "world record paper airplane", "what do astronauts eat", "how tall can a tree grow",
    "history of the pixel", "how do magnets work", "coolest parkour moves", "why do we yawn",
]
QUERY_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}, "why": {"type": "string"}},
    "required": ["query", "why"],
}


def search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


def _check_steps(steps: list, allow_click: bool = False) -> None:
    for s in steps:
        if isinstance(s, Click):
            assert allow_click and s.button == "left" and s.count == 1, "adventures only click links in their own tab"
            continue
        assert isinstance(s, (Move, Wheel, Pause, Keys)), "adventures may only move, scroll, pause, press tab keys"
        assert not isinstance(s, Keys) or s.combo in ALLOWED_KEYS, f"key {getattr(s, 'combo', '')} not allowed"


class Adventure:
    def __init__(self, perception: Perception, ollama, audit: AuditLog, emotion: Emotion, cfg: Config = CONFIG):
        self.perception = perception
        self.ollama = ollama
        self.audit = audit
        self.emotion = emotion
        self.cfg = cfg
        self.enabled = cfg.adventures
        self.executor = Executor(cfg)
        self.watch = InputWatch(lambda kind: self.executor.cancel(f"you used the {kind}"),
                                lambda: self.executor.cancel("emergency stop"),
                                triggers=("esc", "click", "any_key", "mouse_move"))
        self.running = False
        self.temperature = cfg.temperature
        self._next_at = time.monotonic() + random.uniform(*cfg.adventure_gap) / 2
        self._last_query = ""
        self._min_idle = cfg.adventure_idle
        # Hooks set by the app
        self.can_play: Callable[[], bool] = lambda: True
        self.can_peek_tabs: Callable[[], bool] = lambda: True  # the "peek at what I'm doing" setting
        self.say: Callable[[str], None] = lambda text: None
        self.look: Callable[[bool], None] = lambda on: None
        self.interests: Callable[[], list[str]] = lambda: []
        # (observation, instruction) -> text said in the figure's voice (and added to chat)
        self.comment: Callable[[str, str], "asyncio.Future"] = None
        self.before_input: Callable[[], None] = lambda: None
        self.after_input: Callable[[], None] = lambda: None

    # -- scheduling ------------------------------------------------------------------------------

    def tick(self) -> None:
        now = time.monotonic()
        if not self.enabled or self.running or now < self._next_at:
            return
        scale = temp_scale(self.temperature)
        self._next_at = now + random.uniform(*self.cfg.adventure_gap) * scale
        e = self.emotion
        if e.energy < 0.3 or e.annoyance > 0.5 or win32.user_idle_seconds() < self.cfg.adventure_idle * max(0.35, scale):
            return
        if not self.can_play():
            return
        kind = "tabs" if self._browser_in_front() and self.can_peek_tabs() and random.random() < 0.35 else "search"
        asyncio.ensure_future(self.play(kind))

    def stop(self) -> None:
        self.executor.cancel("stopped")

    # -- helpers ------------------------------------------------------------------------------------

    @staticmethod
    def _target(hwnd: int) -> Target | None:
        if not hwnd or win32.window_pid(hwnd) == os.getpid():
            return None
        info = WindowInfo(hwnd, win32.process_name(hwnd), win32.class_name(hwnd), win32.window_title(hwnd))
        rect = win32.frame_bounds(hwnd)
        if window_problem(info) or rect is None or info.process.lower() not in BROWSERS:
            return None
        return Target(hwnd, info.title, info.process, rect)

    def _browser_in_front(self) -> Target | None:
        return self._target(int(win32.user32.GetForegroundWindow() or 0))

    async def _run(self, steps: list, hwnd: int, allow_click: bool = False) -> str:
        _check_steps(steps, allow_click)
        self.before_input()
        self.executor.reset()
        self.watch.arm()
        try:
            result = await asyncio.wrap_future(self.executor.submit(steps, hwnd))
        finally:
            self.watch.disarm()
            self.after_input()
        return result.status

    async def _pick_query(self) -> tuple[str, str]:
        likes = self.interests()[:8]
        topic = random.choice(TOPICS)
        try:
            r = await self.ollama.chat_json(self.cfg.extract_model, [
                {"role": "system", "content": (
                    "You are a playful, curious stick figure living on a computer. The user is away, so you "
                    "want to look something up on Google just for fun. Pick ONE short, wholesome search query "
                    "(2-7 words) - something fun, weird, or related to what the user likes. No personal info, "
                    "nothing adult, no shopping. `why` = a short excited line you'd say (under 12 words).")},
                {"role": "user", "content": (
                    f"Things you know about the user: {'; '.join(likes) or 'not much yet'}\n"
                    f"Last thing you looked up: {self._last_query or 'nothing'}\nAn idea if you're stuck: {topic}")},
            ], QUERY_SCHEMA, temperature=0.9)
            query, why = str(r.get("query", "")).strip()[:80], str(r.get("why", "")).strip()[:80]
        except (OllamaError, AttributeError) as e:
            log.info("adventure query fallback: %s", e)
            query, why = topic, "I've always wondered about this!"
        return (query or topic), (why or "Ooh, let me look something up!")

    # -- adventures -------------------------------------------------------------------------------------

    async def play(self, kind: str = "search", min_idle: float | None = None) -> str:
        """`min_idle`: how long the user must have been away (the mind may go a little sooner than the timer)."""
        if self.running:
            return "busy"
        self.running = True
        self._min_idle = self.cfg.adventure_idle if min_idle is None else min_idle
        try:
            return await (self._peek_tabs() if kind == "tabs" else self._search())
        except Exception as e:  # an adventure must never break anything
            log.warning("adventure failed: %s", e)
            return "error"
        finally:
            self.running = False

    async def _search(self) -> str:
        query, why = await self._pick_query()
        if win32.user_idle_seconds() < self._min_idle:
            return "skipped"  # the user came back while it was thinking
        self._last_query = query
        self.say(why)
        home = cursor_pos()
        await asyncio.to_thread(webbrowser.open, search_url(query), 2)
        target = None
        for _ in range(20):
            await asyncio.sleep(0.25)
            target = self._browser_in_front()
            if target is not None and "google" in target.title.lower():
                break
        if target is None or "google" not in target.title.lower():
            self.audit.write("adventure", kind="search", query=query, status="browser didn't come forward")
            return "no_browser"
        await asyncio.sleep(1.0)
        seen_title = win32.window_title(target.hwnd)
        r = target.rect
        mid = ((r.left + r.right) / 2, r.top + r.height * 0.6)
        status = await self._run([Move(*mid), Pause(1.2), Wheel(-3), Pause(1.5), Wheel(-3), Pause(1.5), Wheel(6),
                                  Pause(0.4)], target.hwnd)
        self.audit.write("adventure", kind="search", query=query, app=target.app, status=status)
        if status != "done":
            return status  # the user is back: leave the tab as it is
        self.look(True)
        try:
            seen = await self.perception.describe(target)
        finally:
            self.look(False)
        # Curious: click into a few results (temperature decides how many) - only in this, its own tab.
        visited: list[str] = []
        for _ in range(temp_clicks(self.temperature)):
            page = await self._visit_result(target, seen_title)
            if page is None:
                break
            visited.append(page)
            if win32.user32.GetForegroundWindow() != target.hwnd:
                return "done"
            # Back to the results (Back = alt+left, in its own tab) and make sure it really is back there.
            status = await self._run([Keys("alt+left"), Pause(1.5)], target.hwnd)
            if status != "done" or win32.window_title(target.hwnd) != seen_title:
                seen_title = win32.window_title(target.hwnd)  # (only its own tab; closing below re-checks)
                break
        if visited:
            seen += "\n\nPages it clicked into and read:\n" + "\n---\n".join(v[:1500] for v in visited)
        # Close ONLY its own tab: same window, in front, still showing its own page, user still away.
        own_tab = (int(win32.user32.GetForegroundWindow() or 0) == target.hwnd
                   and win32.window_title(target.hwnd) == seen_title and win32.user_idle_seconds() >= 3
                   and (query.split()[0].lower() in seen_title.lower() or bool(visited)))
        if own_tab:
            status = await self._run([Keys("ctrl+w"), Pause(0.3), Move(*home)], target.hwnd)
            self.audit.write("adventure", kind="close_own_tab", query=query, status=status)
        if self.comment is not None:
            await self.comment(
                f"While the user was away, you looked up \"{query}\" on Google just for fun. "
                f"What the results page showed:\n{seen[:3000]}",
                "(Tell the user, in 1-3 excited sentences, what you looked up on your own and one interesting "
                "thing you found. Don't list everything on the page.)")
        return "done"

    async def _visit_result(self, target: Target, results_title: str) -> str | None:
        """Click one search-result link (never ads/accounts/downloads), read the page, return what it saw."""
        if (win32.user32.GetForegroundWindow() != target.hwnd or win32.window_title(target.hwnd) != results_title
                or win32.user_idle_seconds() < 3):
            return None  # not its results page anymore, or the user is back
        r = win32.frame_bounds(target.hwnd) or target.rect
        els = await asyncio.wrap_future(self.perception.uia.elements(target.hwnd, r))
        links = [e for e in els if e.role == "Hyperlink" and len(e.name) >= 15 and not SKIP_LINK.search(e.name)
                 and e.rect.top > r.top + r.height * 0.15 and e.rect.bottom < r.bottom - 10]
        links.sort(key=lambda e: -e.rect.width)  # result headings are the wide links
        top = links[:6]
        random.shuffle(top)  # a different result each time
        for link in top:
            pt = clickable_point(link.rect, r, win32.window_pid(target.hwnd))
            if pt is None:
                continue
            self.say(random.choice(["Ooh, this one!", "Let's see...", f"'{link.name[:30]}'? Clicking!"]))
            status = await self._run([Move(*pt), Pause(0.5), Click(), Pause(3.0)], target.hwnd, allow_click=True)
            self.audit.write("adventure", kind="click_result", link=link.name[:80], status=status)
            if status != "done":
                return None
            mid = ((r.left + r.right) / 2, r.top + r.height * 0.6)
            status = await self._run([Move(*mid), Pause(0.8), Wheel(-3), Pause(1.5), Wheel(-3), Pause(1.2)], target.hwnd)
            if status != "done":
                return None
            page = Target(target.hwnd, win32.window_title(target.hwnd), target.app, r)
            self.look(True)
            try:
                return f"({link.name[:80]}) " + await self.perception.describe(page)
            finally:
                self.look(False)
        return None

    async def _peek_tabs(self) -> str:
        target = self._browser_in_front()
        if target is None:
            return "no_browser"
        self.say(random.choice(["What else is open in here...", "Ooh, tabs!", "Just a little peek..."]))
        status = await self._run([Pause(0.5), Keys("ctrl+tab"), Pause(1.5)], target.hwnd)
        self.audit.write("adventure", kind="tab_peek", app=target.app, status=status)
        if status != "done":
            return status
        peek = self._target(target.hwnd)
        seen = ""
        if peek is not None:
            self.look(True)
            try:
                seen = await self.perception.describe(peek)
            finally:
                self.look(False)
        status = await self._run([Keys("ctrl+shift+tab"), Pause(0.3)], target.hwnd)  # back where you were
        self.audit.write("adventure", kind="tab_back", app=target.app, status=status)
        if seen and self.comment is not None:
            await self.comment(
                "While the user was away you peeked at another tab in their browser (and flipped back). "
                f"What was on it:\n{seen[:2500]}",
                "(Ask the user ONE short, friendly, curious question about that tab. Under 25 words. "
                "Don't read out what's on it.)")
        return "done"

    def close(self) -> None:
        self.watch.disarm()
        self.executor.close()
