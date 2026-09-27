"""Owns the executor, the user-override hooks, the audit log, and the (single) running task."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from stickfigure.actions.executor import Executor
from stickfigure.actions.schema import Step
from stickfigure.actions.task import ActionTask, TaskResult
from stickfigure.agent.ollama import Ollama
from stickfigure.config import CONFIG, Config
from stickfigure.perception.perception import Perception, Target
from stickfigure.safety.audit import AuditLog
from stickfigure.safety.override import InputWatch
from stickfigure.safety.policy import Decision
from stickfigure.world.geometry import Rect

log = logging.getLogger(__name__)


class ActionController:
    def __init__(self, perception: Perception, ollama: Ollama, cfg: Config = CONFIG):
        self.perception = perception
        self.ollama = ollama
        self.cfg = cfg
        self.audit = AuditLog(Path(cfg.data_dir) / "audit.jsonl")
        self.executor = Executor(cfg)
        self.watch = InputWatch(self._user_took_over, self._kill_from_hook, cfg.override_triggers)
        self.current: ActionTask | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ask_task: asyncio.Future | None = None
        # UI hooks (set by the app)
        self.ask: Callable = None  # async (what, why, destructive, keep_clear, element_rect) -> answer
        self.working: Callable[[str, Rect | None], None] = lambda what, rect: None
        self.idle: Callable[[], None] = lambda: None
        self.on_status: Callable[[Step], None] = lambda step: None
        self.before_input: Callable[[], None] = lambda: None
        self.after_input: Callable[[], None] = lambda: None
        self.on_emergency: Callable[[], None] = lambda: None
        self.find_window: Callable[[str], Target | None] = lambda hint: None  # for switch_window steps
        self.list_windows: Callable[[], list[str]] = lambda: []

    @property
    def busy(self) -> bool:
        return self.current is not None

    def add_instruction(self, text: str) -> bool:
        if self.current is None:
            return False
        self.current.add_instruction(text)
        return True

    async def run(self, goal: str, payload: str | None, target: Target, context: str = "") -> TaskResult:
        if self.current is not None:
            return TaskResult("failed", "I'm already in the middle of something.")
        self._loop = asyncio.get_running_loop()

        def keep_clear(step: Step) -> Rect | None:
            # The panel must stay off the exact spot we'll click (not the whole element: a document
            # element can cover the entire screen).
            if step.point is not None:
                x, y = step.point
                return Rect(x - 30, y - 30, x + 30, y + 30)
            return step.element.rect if step.element else None

        async def approve(step: Step, decision: Decision) -> str:
            # Run the question as its own task so Esc / Stop can cancel a pending confirmation.
            self._ask_task = asyncio.ensure_future(self.ask(
                step.describe(), decision.reason, decision.always_ask, keep_clear(step),
                step.element.rect if step.element else None))
            try:
                return await self._ask_task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                return "stop"
            finally:
                self._ask_task = None

        def status(step: Step) -> None:
            self.working(step.describe(), keep_clear(step))
            self.on_status(step)

        task = ActionTask(
            goal, payload, target, perception=self.perception, ollama=self.ollama, executor=self.executor,
            watch=self.watch, audit=self.audit, approve=approve, on_status=status,
            before_input=self.before_input, after_input=self.after_input, context=context,
            find_window=self.find_window, list_windows=self.list_windows, cfg=self.cfg,
        )
        self.current = task
        try:
            result = await task.run()
            log.info("task %d %s: %s", task.id, result.status, result.message)
            return result
        finally:
            self.watch.disarm()
            self.current = None
            self.idle()

    def stop(self, reason: str) -> bool:
        """Thread-safe. Returns True if something was running."""
        task = self.current
        if task is None:
            return False
        task.stop(reason)
        ask = self._ask_task
        if ask is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(ask.cancel)
        return True

    # -- hook-thread callbacks (must be quick and thread-safe) ----------------------------------

    def _user_took_over(self, kind: str) -> None:
        self.stop(f"you pressed {kind}" if kind == "Esc key" else f"you used the {kind}, so I let go")

    def _kill_from_hook(self) -> None:
        self.stop("emergency stop")
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_emergency)

    def close(self) -> None:
        self.stop("shutting down")
        self.watch.disarm()
        self.executor.close()
