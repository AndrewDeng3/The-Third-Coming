"""One supervised action task: perceive -> propose -> guard -> approve -> execute, repeated.

The model never touches input directly. Every step it proposes is:
  1. validated against the element snapshot it was shown,
  2. re-resolved at the click point (what's *actually* there right now),
  3. judged by the policy guard (deny / ask / allow),
  4. confirmed by the user if risky (or every step, in supervised mode) - asked in chat, no popup,
  5. executed by the executor, which re-checks focus/bounds/cancel before every input event,
with every stage written to the audit log. Esc, the kill switch, or the Stop button stop it at once.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import re
import time
import webbrowser
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from stickfigure.actions.executor import Click, ExecResult, Executor, Keys, Move, Pause, Type, Wheel
from stickfigure.actions.schema import (
    PAYLOAD_TOKEN, ACTION_SCHEMA, PLAN_SCHEMA, Step, parse_step, pick_elements, plan_messages, step_messages,
)
from stickfigure.agent.ollama import Ollama, OllamaError
from stickfigure.config import CONFIG, Config
from stickfigure.perception.perception import Perception, Target
from stickfigure.perception.uia import UIElement
from stickfigure.safety.audit import AuditLog
from stickfigure.safety.override import InputWatch
from stickfigure.safety.policy import ASK, DENY, Decision, Proposed, WindowInfo, decide, window_problem
from stickfigure.win import win32
from stickfigure.world.geometry import Rect

log = logging.getLogger(__name__)
_ids = itertools.count(1)
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe"}
# Windows whose main job is editing a document: Enter/newlines there just make new lines.
DOC_WINDOW = re.compile(r"(- Google Docs|- Word$|- Notepad$|Notepad\+\+|- Replit|Visual Studio Code|- Obsidian|"
                        r"- OneNote|- WordPad|Sublime Text|\.(txt|md|py|js|ts|html|css|json) -)", re.I)
TERMINALISH = re.compile(r"(terminal|console|shell|search)", re.I)
CODE_EDITOR = re.compile(r"(editor content|code editor|monaco|codemirror|cm-content|ace_text|text-input)", re.I)

# approver(step, decision) -> "approve" | "approve_all" | "deny" | "stop"
Approver = Callable[[Step, Decision], Awaitable[str]]


@dataclass
class TaskResult:
    status: str  # done | stopped | denied | blocked | failed | asked | limit
    message: str
    steps: list[str] = field(default_factory=list)


def clickable_point(el_rect: Rect, window: Rect, target_pid: int, pid_at=None) -> tuple[float, float] | None:
    """A point on the *visible* part of an element where a click really reaches the target app.

    The element's center is often wrong: big elements (a Google Docs page) extend past the window,
    and always-on-top windows (our chat box, other apps) can sit over the middle. Try the center of
    the visible part first, then a grid of interior points nearest the center.
    """
    pid_at = pid_at or (lambda x, y: win32.window_pid(win32.root_window_at(int(x), int(y))))
    l, t = max(el_rect.left, window.left), max(el_rect.top, window.top)
    r, b = min(el_rect.right, window.right), min(el_rect.bottom, window.bottom)
    if r - l < 2 or b - t < 2:
        return None
    cx, cy = (l + r) / 2, (b + t) / 2
    grid = [(l + (r - l) * fx, t + (b - t) * fy) for fx in (0.2, 0.35, 0.5, 0.65, 0.8) for fy in (0.2, 0.35, 0.5, 0.65, 0.8)]
    for pt in [(cx, cy)] + sorted(grid, key=lambda p: abs(p[0] - cx) + abs(p[1] - cy)):
        if pid_at(*pt) == target_pid:
            return pt
    return None


def same_target(found: UIElement, wanted: UIElement) -> bool:
    """Is what's under the pointer the element we meant (itself, a part of it, or the same thing)?"""
    a, b = found.rect, wanted.rect
    inside = a.left >= b.left - 3 and a.top >= b.top - 3 and a.right <= b.right + 3 and a.bottom <= b.bottom + 3
    if inside:  # a child of the intended element (the text inside a button, a cell of a list...)
        return True
    fn, wn = found.name.strip().lower(), wanted.name.strip().lower()
    if fn and wn and (fn in wn or wn in fn):
        return True
    ix = max(0.0, min(a.right, b.right) - max(a.left, b.left))
    iy = max(0.0, min(a.bottom, b.bottom) - max(a.top, b.top))
    inter = ix * iy
    union = a.width * a.height + b.width * b.height - inter
    return union > 0 and inter / union > 0.6  # (nearly) the same box, reported differently


def _contains_words(haystack: str, needle: str, threshold: float = 0.8) -> bool:
    """Is `needle` (roughly) in `haystack`? Tolerates OCR noise: most of its words must appear."""
    norm = lambda s: re.findall(r"[a-z0-9']+", s.lower())  # noqa: E731
    words, have = norm(needle), set(norm(haystack))
    return bool(words) and sum(w in have for w in words) / len(words) >= threshold


class ActionTask:
    def __init__(
        self,
        goal: str,
        payload: str | None,
        target: Target,
        *,
        perception: Perception,
        ollama: Ollama,
        executor: Executor,
        watch: InputWatch,
        audit: AuditLog,
        approve: Approver,
        on_status: Callable[[Step], None] = lambda s: None,
        before_input: Callable[[], None] = lambda: None,
        after_input: Callable[[], None] = lambda: None,
        context: str = "",
        find_window: Callable[[str], Target | None] = lambda hint: None,
        list_windows: Callable[[], list[str]] = lambda: [],
        cfg: Config = CONFIG,
    ):
        self.id = next(_ids)
        self.goal, self.payload, self.target = goal, payload, target
        self.context = context  # e.g. what the previous task did, or the user's answer to a question
        self.perception, self.ollama, self.executor, self.watch = perception, ollama, executor, watch
        self.audit, self.approve, self.on_status = audit, approve, on_status
        self.before_input, self.after_input = before_input, after_input
        self.cfg = cfg
        self.find_window, self.list_windows = find_window, list_windows
        self.plan: list[str] = []
        self.history: list[str] = []
        # Unsupervised by default: steps just run. Destructive ones still ask (policy.always_ask).
        self.task_approved = not cfg.supervised
        self._stopped = ""
        self._typed: set[str] = set()
        self._last_sig = ""  # the last step that ran successfully, and how many times in a row
        self._repeats = 0
        self._stuck = 0  # texts typed successfully this task (no accidental repeats)
        self._snapshot: list[UIElement] = []
        self._rect: Rect | None = None

    def add_instruction(self, text: str) -> None:
        """The user said something more while the task runs ("also make it bold"): the next step sees it."""
        self.history.append(f"(the user added an instruction: {text})")
        self._log("instruction", text=text)

    def stop(self, reason: str) -> None:
        """Thread-safe: from the kill switch, the user-override hook, or a Stop button."""
        self._stopped = self._stopped or reason
        self.executor.cancel(reason)

    def _window(self) -> WindowInfo | None:
        if not win32.user32.IsWindow(self.target.hwnd):
            return None
        return WindowInfo(self.target.hwnd, self.target.app, win32.class_name(self.target.hwnd),
                          win32.window_title(self.target.hwnd))

    def _log(self, event: str, **kw) -> None:
        self.audit.write(event, task=self.id, app=self.target.app, hwnd=self.target.hwnd, **kw)

    async def run(self) -> TaskResult:
        self._log("task_start", goal=self.goal, payload_chars=len(self.payload or ""), supervised=not self.task_approved)
        self.executor.reset()
        # Watch for Esc / the kill combo for the WHOLE task, including while the model is thinking.
        self.watch.arm()
        try:
            await self._make_plan()
            return await self._loop()
        finally:
            self.watch.disarm()

    async def _loop(self) -> TaskResult:
        failures = 0
        try:
            for _ in range(self.cfg.max_steps):
                if self._stopped:
                    return self._end("stopped", f"Stopped: {self._stopped}.")
                window = self._window()
                if window is None:
                    return self._end("failed", "The window I was working in closed.")
                problem = window_problem(window)
                if problem:
                    return self._end("denied", f"I can't work there: {problem}.")

                step = await self._propose(window)
                if isinstance(step, TaskResult):
                    return step
                if isinstance(step, str):  # invalid proposal: tell the model and retry
                    failures += 1
                    self.history.append(f"(invalid: {step})")
                    if failures >= self.cfg.max_failures:
                        return self._end("failed", "I kept getting confused about what to do next.")
                    continue

                if (step.kind == "type_text" and self.payload and step.text.count(self.payload) > 1):
                    failures += 1
                    self.history.append(f"{step.describe()} -> REFUSED (that has the prepared text twice: type "
                                        f"{PAYLOAD_TOKEN} once)")
                    continue
                if (step.kind == "type_text" and self.payload and self.payload not in self._typed
                        and self.payload not in step.text and len(step.text.strip()) > 3):
                    # The model wrote its own words instead of the prepared text (e.g. copied a placeholder
                    # like "Hi, I'm [Your Name]" from its plan): refuse, and point it at the real text.
                    failures += 1
                    self.history.append(f"{step.describe()} -> REFUSED (that isn't the prepared text: use exactly "
                                        f"{PAYLOAD_TOKEN} as `text` to type it)")
                    if failures >= self.cfg.max_failures:
                        return self._end("failed", "I kept typing the wrong words instead of what I wrote, so I stopped.")
                    continue

                if step.kind == "type_text" and step.text.strip() and step.text in self._typed:
                    failures += 1
                    self.history.append("(invalid: you already typed exactly this text in this task - check "
                                        "'Current text' and don't type it again unless the goal asks for a repeat)")
                    if failures >= self.cfg.max_failures:
                        return self._end("failed", "I kept trying to type the same thing twice, so I stopped.")
                    continue

                # Stuck in a loop? (The same step already ran successfully twice in a row.)
                sig = step.describe()
                if sig == self._last_sig and self._repeats >= 2 and step.kind not in ("wait", "done", "ask_user"):
                    failures += 1
                    self._stuck += 1
                    self.history.append(
                        f"(invalid: \"{sig}\" already ran {self._repeats} times in a row and "
                        "succeeded - repeating it changes nothing. Do the NEXT step of the plan instead"
                        + (" (the caret is in the text: use type_text now)" if step.kind == "click" and
                           self._in_text_body(await asyncio.wrap_future(self.perception.uia.focused())) else "")
                        + ".)")
                    if self._stuck >= 3 or failures >= self.cfg.max_failures:
                        return self._end("failed", "I got stuck repeating the same step, so I stopped.")
                    continue

                if step.kind == "done":
                    return self._end("done", step.say or "All done!")
                if step.kind == "ask_user":
                    return self._end("asked", step.say or "I'm not sure how to continue - can you tell me more?")
                if step.kind in ("open_url", "switch_window"):
                    decision = decide(Proposed(step.kind, None, step.text), window, None, self.task_approved)
                    self._log("proposed", step=step.describe(), thought=step.thought, verdict=decision.verdict,
                              reason=decision.reason, element=None)
                    if decision.verdict == DENY:
                        failures += 1
                        self.history.append(f"{step.describe()} -> BLOCKED ({decision.reason})")
                        if failures >= self.cfg.max_failures:
                            return self._end("blocked", f"I'm not allowed to do that: {decision.reason}.")
                        continue
                    self.on_status(step)
                    outcome = await (self._open_url(step.text) if step.kind == "open_url"
                                     else self._switch_window(step.text))
                    self._log("executed", step=step.describe(), status=outcome, detail="", events=0, ms=0)
                    self.history.append(f"{step.describe()} -> {outcome}")
                    if self._stopped:
                        return self._end("stopped", f"Stopped: {self._stopped}.")
                    failures = failures + 1 if outcome.startswith("failed") else 0
                    if failures >= self.cfg.max_failures:
                        return self._end("failed", "I couldn't get to the right window.")
                    continue

                if step.element is not None and step.kind in ("click", "double_click", "scroll", "type_text"):
                    bounds = win32.frame_bounds(self.target.hwnd) or self.target.rect
                    pt = clickable_point(step.element.rect, bounds, win32.window_pid(self.target.hwnd))
                    if pt is None:
                        failures += 1
                        cover = win32.root_window_at(*(int(v) for v in step.element.center))
                        who = f"{win32.process_name(cover) or 'another window'} '{win32.window_title(cover)[:30]}'"
                        self.history.append(f"{step.describe()} -> BLOCKED (covered by {who} or off-screen)")
                        self._log("covered", step=step.describe(), by=who)
                        if failures >= self.cfg.max_failures:
                            return self._end("failed", f"I can't reach it: {who} is in the way. Can you move it?")
                        continue
                    step.point = pt

                # What will the click *actually* hit right now? (The snapshot may be stale.)
                hit = step.element
                if step.point is not None and step.kind in ("click", "double_click"):
                    now = await asyncio.wrap_future(self.perception.uia.at_point(*step.point))
                    if step.element is not None and now is not None and not same_target(now, step.element):
                        # Something else is under that spot (an overlay, a neighbor, a popup): look for a
                        # point on the element that really hits it, or don't click at all.
                        better = await self._aim_at(step.element)
                        if better is None:
                            failures += 1
                            self.history.append(f"{step.describe()} -> NOT CLICKED (at that spot is "
                                                f"{now.describe()}, not {step.element.describe()}; pick "
                                                "another element or scroll it into view)")
                            self._log("wrong_target", step=step.describe(), found=now.describe())
                            if failures >= self.cfg.max_failures:
                                return self._end("failed", f"I couldn't reach {step.element.describe()}: "
                                                           f"{now.describe()} is in the way.")
                            continue
                        step.point, now = better
                    hit = now or step.element
                focused = await asyncio.wrap_future(self.perception.uia.focused())
                decision = decide(Proposed(step.kind, hit, step.text, step.keys, step.amount), window, focused,
                                  self.task_approved, multiline=self._in_text_body(focused))
                self._log("proposed", step=step.describe(), thought=step.thought, verdict=decision.verdict,
                          reason=decision.reason, element=hit.describe() if hit else None)
                if decision.verdict == DENY:
                    failures += 1
                    self.history.append(f"{step.describe()} -> BLOCKED ({decision.reason})")
                    if failures >= self.cfg.max_failures:
                        return self._end("blocked", f"I'm not allowed to do that: {decision.reason}.")
                    continue

                if decision.verdict == ASK:
                    answer = await self.approve(step, decision)
                    self._log("approval", step=step.describe(), answer=answer)
                    if answer in ("stop", "deny"):
                        return self._end("stopped", "Okay, I stopped." if answer == "stop" else "Okay, I won't do that.")
                    if answer == "approve_all":
                        self.task_approved = True
                    if self._stopped:
                        return self._end("stopped", f"Stopped: {self._stopped}.")

                self.on_status(step)
                result = await self._execute(step, focused)
                self._log("executed", step=step.describe(), status=result.status, detail=result.detail,
                          events=result.events, ms=round(result.elapsed * 1000))
                note = ""
                if step.kind == "type_text" and result.ok:
                    self._typed.add(step.text)
                    await asyncio.sleep(0.3)
                    after = await self._field_text()
                    if after is not None:
                        note = (" (verified: the text is now in the field)" if _contains_words(after, step.text)
                                else " (warning: I can't see the typed text in the field - check before retrying)")
                if result.ok:
                    self._repeats = self._repeats + 1 if sig == self._last_sig else 1
                    self._last_sig = sig
                if step.kind in ("click", "double_click") and result.ok:
                    await asyncio.sleep(0.25)
                    now_focused = await asyncio.wrap_future(self.perception.uia.focused())
                    if self._in_text_body(now_focused):
                        note = " (the text caret is now in the document/editor: next, type_text)"
                    elif now_focused is not None:
                        note = f" (keyboard focus is now on {now_focused.describe()})"
                self.history.append(f"{step.describe()} -> {result.status}"
                                    f"{': ' + result.detail if result.detail else ''}{note}")
                if result.status == "cancelled":
                    return self._end("stopped", f"Stopped: {result.detail or self._stopped}.")
                if result.status == "foreground_lost":
                    return self._end("stopped", "The window I was working in lost focus, so I stopped to be safe.")
                if not result.ok:
                    failures += 1
                    if failures >= self.cfg.max_failures:
                        return self._end("failed", f"That didn't work ({result.status}).")
                else:
                    failures = 0
                await asyncio.sleep(0.4)  # let the app react before looking again
            return self._end("limit", f"I hit my {self.cfg.max_steps}-step limit, so I stopped.")
        except asyncio.CancelledError:
            self.stop("cancelled")
            self._log("task_end", status="stopped", message="cancelled")
            raise

    async def _propose(self, window: WindowInfo) -> Step | str | TaskResult:
        rect = win32.frame_bounds(self.target.hwnd) or self.target.rect
        target = Target(self.target.hwnd, window.title, self.target.app, rect)
        t0 = time.perf_counter()
        uia, ocr, timings = await self.perception.elements(target)
        self._snapshot, self._rect = uia, rect
        elements = pick_elements(uia + ocr, self.goal)
        focused = next((e for e in uia if e.focused), None)
        goal = self.goal + (f"\nContext: {self.context}" if self.context else "")
        field_text = await self._field_text()
        msgs = step_messages(goal, self.payload is not None, target.label(), rect, focused, elements, self.history,
                             field_text, plan=self.plan, windows=self._other_windows())
        try:
            t1 = time.perf_counter()
            raw = await self.ollama.chat_json(self.cfg.action_model, msgs, ACTION_SCHEMA, temperature=0.1)
        except OllamaError as e:
            return self._end("failed", f"My brain didn't answer ({e}).")
        log.info("task %d step %d: looked in %.0f ms (%s, %d elements), decided in %.0f ms", self.id,
                 len(self.history) + 1, (t1 - t0) * 1000, {k: round(v) for k, v in timings.items()},
                 len(uia) + len(ocr), (time.perf_counter() - t1) * 1000)
        return parse_step(raw, elements, rect, self.payload)

    async def _execute(self, step: Step, focused: UIElement | None):
        steps: list = []
        if step.kind in ("click", "double_click"):
            steps = [Move(*step.point), Click(count=2 if step.kind == "double_click" else 1)]
        elif step.kind == "type_text":
            already = self._in_text_body(focused)  # e.g. Google Docs' hidden text box: clicking again moves the caret
            if step.element is not None and not already and (focused is None or focused.rect != step.element.rect):
                steps = [Move(*(step.point or step.element.center)), Click(), Pause(0.2)]  # focus the field first
            steps.append(Type(step.text))
        elif step.kind == "hotkey":
            steps = [Keys(step.keys)]
        elif step.kind == "scroll":
            steps = [Move(*step.point), Wheel(step.amount)]
        elif step.kind == "wait":
            steps = [Pause(1.0)]
        ok = await asyncio.to_thread(win32.bring_target_forward, self.target.hwnd, os.getpid())
        if not ok:
            return ExecResult("foreground_lost", "you switched to another app")
        self.before_input()
        try:
            return await asyncio.wrap_future(self.executor.submit(steps, self.target.hwnd))
        finally:
            self.after_input()

    # -- planning & moving between windows ---------------------------------------------------------

    def _other_windows(self) -> list[str]:
        try:
            return [w for w in self.list_windows() if w != self.target.label()]
        except Exception:  # noqa: BLE001 - optional context only
            return []

    async def _make_plan(self) -> None:
        """A short plan first: multi-step tasks go much better when the step model can follow one."""
        goal = self.goal + (f"\nContext: {self.context}" if self.context else "")
        try:
            raw = await self.ollama.chat_json(
                self.cfg.action_model, plan_messages(goal, self.target.label(), self.payload is not None,
                                                     self._other_windows()), PLAN_SCHEMA, temperature=0.2)
            self.plan = [str(p)[:160] for p in raw.get("plan", []) if str(p).strip()][:8]
        except (OllamaError, AttributeError, TypeError) as e:
            log.warning("planning failed: %s", e)
            self.plan = []
        if self.plan:
            self._log("plan", plan=self.plan)

    def _retarget(self, target: Target) -> str | None:
        info = WindowInfo(target.hwnd, target.app, win32.class_name(target.hwnd), target.title)
        problem = window_problem(info)
        if problem:
            return problem
        self.target = target
        self._typed.clear()
        return None

    async def _open_url(self, url: str) -> str:
        await asyncio.to_thread(webbrowser.open, url, 2)
        for _ in range(12):  # wait for the browser to come forward
            await asyncio.sleep(0.25)
            if self._stopped:
                return "stopped"
            fg = int(win32.user32.GetForegroundWindow() or 0)
            proc = win32.process_name(fg).lower() if fg else ""
            if proc in BROWSERS:
                rect = win32.frame_bounds(fg)
                if rect is not None:
                    problem = self._retarget(Target(fg, win32.window_title(fg), win32.process_name(fg), rect))
                    if problem:
                        return f"failed: the page opened but I can't work there ({problem})"
                    await asyncio.sleep(1.0)  # let the page load a bit
                    return f"done: now working in {self.target.label()}"
        return "done, but the browser didn't come to the front (try switch_window)"

    async def _switch_window(self, hint: str) -> str:
        target = self.find_window(hint)
        if target is None or target.hwnd == self.target.hwnd:
            return (f"failed: no OTHER window matches '{hint[:40]}' - you're already in {self.target.label()}; "
                    "keep working here")
        if not self._goal_mentions(target):
            return (f"failed: the goal doesn't mention {target.label()}, so stay in {self.target.label()} "
                    "(only switch windows when the user asked for that app)")
        problem = self._retarget(target)
        if problem:
            return f"failed: {problem}"
        ok = await asyncio.to_thread(win32.bring_target_forward, target.hwnd, os.getpid())
        return f"done: now working in {target.label()}" if ok else "done (it may not be in front yet)"

    async def _aim_at(self, el: UIElement) -> tuple[tuple[float, float], UIElement] | None:
        """A point on `el`'s visible part where the element under the pointer really is `el` (or its child)."""
        bounds = win32.frame_bounds(self.target.hwnd) or self.target.rect
        r = el.rect
        l, t = max(r.left, bounds.left), max(r.top, bounds.top)
        rr, b = min(r.right, bounds.right), min(r.bottom, bounds.bottom)
        if rr - l < 2 or b - t < 2:
            return None
        cx, cy = (l + rr) / 2, (t + b) / 2
        pts = [(l + (rr - l) * fx, t + (b - t) * fy) for fx in (0.25, 0.5, 0.75) for fy in (0.3, 0.5, 0.7)]
        pts.sort(key=lambda p: abs(p[0] - cx) + abs(p[1] - cy))
        for pt in pts[:7]:
            got = await asyncio.wrap_future(self.perception.uia.at_point(*pt))
            if got is not None and same_target(got, el):
                return pt, got
        return None

    def _goal_mentions(self, target: Target) -> bool:
        """Does the user's request actually involve this window (its app or a word of its title)?"""
        goal = f"{self.goal} {self.context}".lower()
        app = target.app.lower().removesuffix(".exe")
        words = {w for w in re.findall(r"[a-z0-9]{4,}", f"{app} {target.title.lower()}")}
        words -= {"google", "chrome", "window", "microsoft", "edge", "firefox", "mozilla", "untitled", "new tab"}
        return any(w in goal for w in words)

    def _text_body(self) -> UIElement | None:
        """The window's main text area (e.g. the Google Docs page): the biggest Document/Edit element."""
        cands = [e for e in self._snapshot if e.role in ("Document", "Edit") and not e.is_password]
        body = max(cands, key=lambda e: e.rect.width * e.rect.height, default=None)
        if body is None or self._rect is None:
            return None
        return body if body.rect.width * body.rect.height >= 0.15 * self._rect.width * self._rect.height else None

    def _in_text_body(self, focused: UIElement | None) -> bool:
        """Is the caret in a multi-line text area (where Enter just makes a new line)?"""
        if DOC_WINDOW.search(win32.window_title(self.target.hwnd) or self.target.title) and not (
                focused is not None and (focused.is_password or focused.role in ("ComboBox",)
                                         or TERMINALISH.search(f"{focused.name} {focused.automation_id}"))):
            # A document editor (Docs, Word, Notepad...): unless focus is clearly in a search/combo box.
            if focused is None or focused.role not in ("Edit",) or focused.rect.height >= 60 or \
                    focused.rect.width * focused.rect.height < 2000:
                return True
        if focused is not None and not focused.is_password:
            if focused.role == "Document" or (focused.role == "Edit" and focused.rect.height >= 60):
                return True
            if CODE_EDITOR.search(f"{focused.name} {focused.automation_id}"):
                return True  # Monaco / CodeMirror (Replit, VS Code web...): a hidden textarea inside the editor
        body = self._text_body()
        if body is None:
            return False
        # Web editors like Google Docs put the caret in a tiny hidden textbox inside the page.
        if focused is None or focused.rect.width * focused.rect.height < 2000:
            return True
        cx, cy = focused.center
        return body.rect.contains(cx, cy)

    async def _field_text(self) -> str | None:
        """What's currently in the field being edited: from UIA if the app exposes it, else OCR of the
        text area (Google Docs draws text on a canvas). None if unknown. Never read from password fields."""
        text = await asyncio.wrap_future(self.perception.uia.focused_text())
        if text and text.strip():
            return text[-1500:]
        body = self._text_body()
        if body is None or self._rect is None:
            return text  # "" (empty field) or None (unknown)
        r = body.rect
        region = Rect(max(r.left, self._rect.left), max(r.top, self._rect.top),
                      min(r.right, self._rect.right), min(r.bottom, self._rect.bottom))
        if region.width < 20 or region.height < 20:
            return text
        lines = await asyncio.wrap_future(self.perception.ocr.read(region))
        lines.sort(key=lambda e: (round(e.rect.top / 12), e.rect.left))
        return "\n".join(e.name for e in lines)[-1500:]

    def _end(self, status: str, message: str) -> TaskResult:
        self._log("task_end", status=status, message=message, steps=len(self.history))
        return TaskResult(status, message, list(self.history))
