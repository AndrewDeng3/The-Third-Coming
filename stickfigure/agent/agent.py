"""Conversation orchestration: prompt assembly, streaming replies, background memory extraction.

UI-agnostic: callers pass callbacks for streamed text. Only one generation runs at a time;
a user message cancels in-progress idle chatter.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from stickfigure.agent.emotion import Emotion
from stickfigure.agent.memory import Fact, Memory
from stickfigure.agent.ollama import Ollama, OllamaError
from stickfigure.agent.persona import (
    ACTIONS, EXTRACT_SCHEMA, needs_depth, IDLE_CHATTER_PROMPT, ROUTE_SCHEMA, Situation, TagFilter, clean_reply,
    extraction_messages, maybe_about_screen, route_messages, system_prompt,
)
from stickfigure.config import CONFIG, Config

log = logging.getLogger(__name__)

OFFLINE_LINE = "(My brain isn't answering. Is Ollama running?)"


@dataclass
class Reply:
    text: str
    actions: list[str]
    error: str | None = None


def ollama_embedder(ollama: Ollama, model: str):
    # embeddinggemma is trained with these task prefixes; they noticeably improve recall.
    async def embed(texts: list[str], kind: str) -> list[list[float]]:
        if kind == "query":
            texts = [f"task: search result | query: {t}" for t in texts]
        else:
            texts = [f"title: none | text: {t}" for t in texts]
        return await ollama.embed(model, texts)

    return embed


class Agent:
    def __init__(self, ollama: Ollama, memory: Memory, emotion: Emotion, cfg: Config = CONFIG):
        self.ollama = ollama
        self.memory = memory
        self.emotion = emotion
        self.cfg = cfg
        self._lock = asyncio.Lock()
        self._chatter_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()
        self.busy = False
        # Names can change at runtime (Settings), so they live here rather than in the frozen config.
        self.name = cfg.buddy_name
        self.user_name = cfg.user_name
        # Body actions decided by the (smarter) extraction pass arrive after the reply finishes.
        self.on_action: Callable[[str], None] = lambda action: None
        self.growth = None  # agent.growth.Growth: who it's becoming (set by the app)
        self.on_sentiment: Callable[[float], None] = lambda s: None
        self.awareness: Callable[[], str] = lambda: ""  # what the user is doing right now (set by the app)

    # -- public ---------------------------------------------------------------------------

    async def route(self, user_text: str) -> dict:
        """Does this message need a look at the screen? {"screen": none|describe|locate, "target", "app"}."""
        none = {"screen": "none", "target": "", "app": "", "content": ""}
        if not maybe_about_screen(user_text):
            return none
        recent = self.memory.recent_messages(6)  # so "open it up" / "whichever you want" make sense
        try:
            r = await self.ollama.chat_json(self.cfg.extract_model, route_messages(user_text, recent), ROUTE_SCHEMA)
        except OllamaError as e:
            log.warning("routing failed: %s", e)
            return none
        if r.get("screen") not in ("describe", "locate", "act"):
            return none
        if r["screen"] == "locate" and not str(r.get("target", "")).strip():
            r["screen"] = "describe"
        return r

    async def reply(
        self,
        user_text: str,
        situation: Situation,
        on_text: Callable[[str], None],
        observation: str | None = None,
        suppress_actions: bool = False,
        on_thinking: Callable[[str], None] | None = None,
        prefetched: "asyncio.Future | None" = None,
    ) -> Reply:
        if self._chatter_task and not self._chatter_task.done():
            self._chatter_task.cancel()
        async with self._lock:
            self.busy = True
            try:
                self.memory.add_message("user", user_text)
                facts, episodes = await (prefetched if prefetched is not None else self._recall(user_text))
                messages = [{"role": "system", "content": self._system(situation, facts, observation, episodes)}]
                messages += self.memory.recent_messages(self.cfg.history_messages)
                deep = needs_depth(user_text)
                result = await self._stream(messages, on_text, self.cfg.max_reply_tokens + (3000 if deep else 0),
                                            think=deep, on_thinking=on_thinking)
                if result.error is None:
                    self.memory.add_message("assistant", result.text)
                    # After looking at the screen, the reply is full of screen contents: keep those out of
                    # long-term memory by only letting the extractor see what the user said.
                    reply_for_memory = "(the companion described what it saw on screen)" if observation else result.text
                    self._spawn(self._extract(
                        user_text, reply_for_memory, acted=bool(result.actions) or suppress_actions
                    ))
                    self._spawn(self._remember_episode(user_text, reply_for_memory))
                else:
                    self.emotion.on_chat(0.0)
                return result
            finally:
                self.busy = False

    def chatter(
        self, situation: Situation, on_text: Callable[[str], None], on_done: Callable[[str], None]
    ) -> None:
        """Say something spontaneous (fire and forget; cancelled if the user starts talking).

        `on_done` gets the final line; it isn't called if generation fails or is cancelled.
        """
        if self.busy or self._lock.locked():
            return
        self._chatter_task = asyncio.ensure_future(self._chatter(situation, on_text, on_done))

    async def follow_up(
        self, situation: Situation, observation: str, instruction: str, on_text: Callable[[str], None]
    ) -> str:
        """An unprompted message about something that just happened (e.g. a task finished).

        Stored in chat history as the companion's turn so the user's next message has context.
        Returns the text, or "" if the model is unavailable.
        """
        async with self._lock:
            facts = self.memory.recent_facts(6)
            messages = [{"role": "system", "content": self._system(situation, facts, observation)}]
            messages += self.memory.recent_messages(12)
            messages.append({"role": "user", "content": instruction})
            result = await self._stream(messages, on_text, 300, quiet_errors=True)
            if result.error is not None or not result.text:
                return ""
            self.memory.add_message("assistant", result.text)
            return result.text

    async def warm_up(self) -> None:
        """Load all three models so the first exchange doesn't pay for model loading."""
        log.info("warming up %s", self.cfg.chat_model)
        await self.ollama.preload(self.cfg.chat_model)
        log.info("warming up %s", self.cfg.embed_model)
        await self.ollama.embed(self.cfg.embed_model, ["warm up"])
        if self.cfg.extract_model != self.cfg.chat_model:
            log.info("warming up %s", self.cfg.extract_model)
            await self.ollama.preload(self.cfg.extract_model)

    async def drain(self) -> None:
        """Wait for background memory work (used on shutdown and in tests)."""
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    # -- internals ------------------------------------------------------------------------

    async def _chatter(
        self, situation: Situation, on_text: Callable[[str], None], on_done: Callable[[str], None]
    ) -> None:
        async with self._lock:
            facts = self.memory.recent_facts(4)
            messages = [{"role": "system", "content": self._system(situation, facts)}]
            messages += self.memory.recent_messages(6)
            messages.append({"role": "user", "content": IDLE_CHATTER_PROMPT})
            result = await self._stream(messages, on_text, 60, quiet_errors=True)
            if result.error is None and result.text:
                self.memory.add_message("assistant", result.text)
                on_done(result.text)

    def _system(self, situation: Situation, facts: list[Fact], observation: str | None = None,
                episodes: list[Fact] | None = None) -> str:
        return system_prompt(self.name, self.emotion, situation, facts, observation=observation,
                             user_name=self.user_name, episodes=episodes,
                             self_text=self.growth.describe() if self.growth is not None else "",
                             aware=self.awareness())

    async def reflect(self) -> bool:
        """Look back on recent life and grow a little (journal notes, trait shifts). One model call."""
        from stickfigure.agent.growth import REFLECT_SCHEMA

        g = self.growth
        if g is None or self.busy or self._lock.locked():
            return False
        msgs = g.reflect_messages(self.name, self.memory.recent_messages(30, asides=True),
                                  [f.text for f in self.memory.recent_facts(8)])
        try:
            r = await self.ollama.chat_json(self.cfg.extract_model, msgs, REFLECT_SCHEMA, temperature=0.7)
        except OllamaError as e:
            log.info("reflection failed: %s", e)
            return False
        g.apply_reflection(r)
        log.info("reflected: %s | traits %s", g.mood_of_late, g.short())
        return True

    async def compose(self, request: str, current_text: str | None = None) -> str:
        """Write the actual content for a task ("write a snake game in python"): code or prose, verbatim,
        so the hands can paste it instead of the step model improvising text inside JSON."""
        from stickfigure.agent.persona import compose_messages, strip_code_fence

        text = ""
        try:
            msgs = compose_messages(request, current_text, self.name, self.user_name,
                                    [f.text for f in self.memory.recent_facts(6)])
            async for chunk in self.ollama.chat_stream(self.cfg.code_model, msgs, temperature=0.5, max_tokens=4000):
                text += chunk
        except OllamaError as e:
            log.warning("compose failed: %s", e)
            return ""
        return strip_code_fence(text)

    async def think(self, situation: Situation, context: dict, recent: list[str]) -> dict | None:
        """The always-on mind: {"thought", "action", "say"} for what to do next, or None."""
        from stickfigure.agent.persona import MIND_ACTIONS, MIND_SCHEMA, mind_messages

        if self.busy or self._lock.locked():
            return None
        try:
            r = await self.ollama.chat_json(
                self.cfg.extract_model,
                mind_messages(self.name, self.emotion, situation, context, recent, self.memory.recent_facts(5)),
                MIND_SCHEMA, temperature=0.9)
        except OllamaError as e:
            log.info("thinking failed: %s", e)
            return None
        if r.get("action") not in MIND_ACTIONS:
            return None
        try:
            target = int(r.get("target", -1))
        except (TypeError, ValueError):
            target = -1
        return {"thought": str(r.get("thought", ""))[:200], "action": r["action"], "target": target,
                "say": str(r.get("say", ""))[:100]}

    async def learn(self, goal: str, app: str, status: str, message: str, steps: list[str]) -> str:
        """After a task: distill one reusable lesson and remember it (recalled for similar tasks later)."""
        from stickfigure.agent.persona import LESSON_SCHEMA, lesson_messages

        if not steps:
            return ""
        try:
            r = await self.ollama.chat_json(self.cfg.extract_model, lesson_messages(goal, app, status, message, steps),
                                            LESSON_SCHEMA, temperature=0.2)
            lesson = str(r.get("lesson", "")).strip()
            if len(lesson) > 8:
                await self.memory.add_episode(f"Lesson ({app}): {lesson}")
                log.info("learned: %s", lesson)
                return lesson
        except (OllamaError, AttributeError) as e:
            log.info("couldn't learn from the task: %s", e)
        return ""

    async def lessons_for(self, goal: str, app: str, k: int = 3) -> list[str]:
        try:
            hits = await self.memory.recall_episodes(f"Lesson ({app}): {goal}", k=12, max_distance=0.8)
        except OllamaError:
            return []
        lessons = [h for h in hits if h.text.startswith("Lesson")]
        lessons.sort(key=lambda h: h.distance)
        return [h.text for h in lessons[:k]]

    async def _episodes_for(self, text: str) -> list[Fact]:
        """Older conversations related to this message (the recent ones are already in the history)."""
        try:
            before = self.memory.oldest_recent_message_time(self.cfg.history_messages)
            hits = await self.memory.recall_episodes(text, k=self.cfg.recall_episodes + 3, before=before)
            return [h for h in hits if not h.text.startswith("Lesson")][: self.cfg.recall_episodes]
        except OllamaError as e:
            log.warning("episode recall failed: %s", e)
            return []

    async def _remember_episode(self, user_text: str, reply: str) -> None:
        try:
            await self.memory.add_episode(f"User: {user_text}\nYou: {reply}")
        except OllamaError as e:
            log.warning("couldn't store the conversation: %s", e)

    async def _facts_for(self, text: str) -> list[Fact]:
        try:
            relevant = await self.memory.recall(text, k=self.cfg.recall_facts)
        except OllamaError as e:
            log.warning("memory recall failed: %s", e)
            relevant = []
        seen = {f.id for f in relevant}
        # Always keep a few recent facts (like the user's name) in view.
        return relevant + [f for f in self.memory.recent_facts(6) if f.id not in seen]

    def prefetch(self, user_text: str) -> asyncio.Future:
        """Start recalling memories for this message right away (runs alongside routing, not after it)."""
        return asyncio.ensure_future(self._recall(user_text))

    async def _recall(self, user_text: str) -> tuple[list[Fact], list[Fact]]:
        facts, episodes = await asyncio.gather(self._facts_for(user_text), self._episodes_for(user_text))
        return facts, episodes

    async def _stream(
        self, messages: list[dict], on_text: Callable[[str], None], max_tokens: int, quiet_errors: bool = False,
        think: bool = False, on_thinking: Callable[[str], None] | None = None,
    ) -> Reply:
        tags = TagFilter()
        visible = ""
        try:
            async for chunk in self.ollama.chat_stream(self.cfg.chat_model, messages, max_tokens=max_tokens,
                                                       think=think, on_thinking=on_thinking):
                visible += tags.feed(chunk)
                on_text(clean_reply(visible))
        except OllamaError as e:
            log.warning("chat failed: %s", e)
            if not quiet_errors:
                on_text(OFFLINE_LINE)
            return Reply(OFFLINE_LINE, [], str(e))
        visible += tags.flush()
        text = clean_reply(visible)
        on_text(text)
        return Reply(text, tags.actions[:1])

    async def _extract(self, user_text: str, reply: str, acted: bool = False) -> None:
        sentiment = 0.0
        try:
            known = await self.memory.recall(user_text, k=6, max_distance=0.9)
            known_ids = {f.id for f in known}
            result = await self.ollama.chat_json(
                self.cfg.extract_model, extraction_messages(user_text, reply, known), EXTRACT_SCHEMA
            )
            action = result.get("action", "none")
            if not acted and action in ACTIONS:
                log.info("extraction requested action: %s", action)
                self.on_action(action)
            sentiment = max(-1.0, min(1.0, float(result.get("sentiment", 0.0))))
            if abs(sentiment) < 0.35:  # the model hedges neutral messages slightly; treat as neutral
                sentiment = 0.0
            # Add first: a "new" fact that's really a restatement merges into the old row,
            # and that row must then survive even if the model also listed it for removal.
            kept: set[int] = set()
            for fact in result.get("facts", [])[:5]:
                if isinstance(fact, str) and 3 < len(fact) < 200:
                    fid = await self.memory.add_fact(fact)
                    if fid in known_ids:
                        kept.add(fid)
                    else:
                        log.info("remembered: %s", fact)
            for fid in result.get("remove_ids", []):
                if fid in known_ids and fid not in kept:
                    self.memory.remove_fact(fid)
                    log.info("forgot fact %d", fid)
        except (OllamaError, ValueError, TypeError) as e:
            log.warning("memory extraction failed: %s", e)
        finally:
            self.emotion.on_chat(sentiment)
            self.on_sentiment(sentiment)

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
