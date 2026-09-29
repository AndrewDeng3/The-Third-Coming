"""Personality growth: The Third Coming slowly becomes its own person.

- Traits (0..1) drift a little with what happens: being thrown around, petted, praised or snapped at, going on
  adventures, finishing tasks. Changes are tiny per event and bounded, so it evolves over days, not minutes.
- A "self" journal: opinions and favorites it forms, running jokes with the user, memorable moments. Firsts
  (first cursor ride, first web search...) are recorded automatically.
- Every so often it reflects (one model call) on what happened lately: adds or revises journal notes and nudges
  its traits. The result feeds its chat instructions, its always-on mind, and how often it picks behaviors.

Everything lives in the memory database's key-value table, so it survives restarts (and "Forget everything").
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from stickfigure.agent.memory import Memory

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Trait:
    name: str
    low: str  # what a low value means
    high: str
    start: float


TRAITS = (
    Trait("boldness", "cautious", "daring", 0.65),
    Trait("sass", "sweet", "sassy", 0.45),
    Trait("wanderlust", "a homebody", "an explorer", 0.6),
    Trait("playfulness", "serious", "goofy", 0.7),
    Trait("chattiness", "quiet", "chatty", 0.55),
)
TRAIT_NAMES = tuple(t.name for t in TRAITS)
NOTE_KINDS = ("opinion", "favorite", "joke", "moment", "habit")
MAX_NOTES = 40
MAX_REFLECT_STEP = 0.06  # the most a single reflection may move one trait
REFLECT_EVERY = 30 * 60.0  # seconds of the app running between reflections

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(NOTE_KINDS)}, "text": {"type": "string"}},
            "required": ["kind", "text"]}},
        "remove": {"type": "array", "items": {"type": "integer"}},
        "trait_changes": {"type": "object", "properties": {n: {"type": "number"} for n in TRAIT_NAMES}},
        "mood_of_late": {"type": "string"},
    },
    "required": ["notes", "remove", "trait_changes", "mood_of_late"],
}


def _clamp(v: float) -> float:
    return max(0.05, min(0.95, v))


def _word(t: Trait, v: float) -> str:
    if v >= 0.8:
        return f"very {t.high}"
    if v >= 0.62:
        return f"fairly {t.high}"
    if v <= 0.2:
        return f"very {t.low}"
    if v <= 0.38:
        return f"fairly {t.low}"
    return f"balanced between {t.low} and {t.high}"


class Growth:
    def __init__(self, memory: Memory):
        self.memory = memory
        saved = memory.get("traits") or {}
        self.traits = {t.name: _clamp(float(saved.get(t.name, t.start))) for t in TRAITS}
        self.notes: list[dict] = list(memory.get("self_notes") or [])
        self.firsts: dict[str, str] = dict(memory.get("firsts") or {})
        self.mood_of_late: str = memory.get("mood_of_late") or ""
        self.events: list[str] = []  # what happened since the last reflection (plain words)
        self._last_reflect = time.monotonic()
        self.born = memory.get("born")
        if self.born is None:
            self.born = time.time()
            memory.put("born", self.born)

    # -- changes ---------------------------------------------------------------------------------

    def nudge(self, trait: str, delta: float, why: str = "") -> None:
        if trait not in self.traits or not delta:
            return
        self.traits[trait] = _clamp(self.traits[trait] + max(-0.05, min(0.05, delta)))
        if why:
            self.event(why)
        self._save_traits()

    def event(self, text: str) -> None:
        self.events.append(f"{time.strftime('%H:%M')} {text}")
        self.events = self.events[-40:]

    def first(self, key: str, text: str) -> bool:
        """Record a first-time moment (once ever). Returns True if it was new."""
        if key in self.firsts:
            return False
        self.firsts[key] = time.strftime("%b %d, %Y")
        self.add_note("moment", text)
        self.memory.put("firsts", self.firsts)
        log.info("first: %s", text)
        return True

    def add_note(self, kind: str, text: str) -> None:
        text = text.strip()
        if kind not in NOTE_KINDS or not (3 < len(text) < 200):
            return
        if any(n["text"].lower() == text.lower() for n in self.notes):
            return
        self.notes.append({"kind": kind, "text": text, "date": time.strftime("%b %d, %Y")})
        # Keep the journal bounded: moments are precious, so trim the oldest non-moments first.
        while len(self.notes) > MAX_NOTES:
            idx = next((i for i, n in enumerate(self.notes) if n["kind"] != "moment"), 0)
            self.notes.pop(idx)
        self.memory.put("self_notes", self.notes)

    def _save_traits(self) -> None:
        self.memory.put("traits", {k: round(v, 4) for k, v in self.traits.items()})

    # -- for prompts & behavior -----------------------------------------------------------------------

    def weight(self, trait: str, strength: float = 1.0) -> float:
        """A behavior multiplier around 1.0: 0.5 (low trait) .. 1.5 (high trait), scaled by `strength`."""
        return 1.0 + (self.traits.get(trait, 0.5) - 0.5) * strength

    def age_days(self) -> float:
        return (time.time() - self.born) / 86400

    def describe(self) -> str:
        """For the chat system prompt: who it has become so far."""
        traits = ", ".join(_word(t, self.traits[t.name]) for t in TRAITS)
        lines = [f"Who you've become so far (you've lived here {self.age_days():.0f} days; this grows over time, "
                 f"let it show naturally): {traits}."]
        if self.mood_of_late:
            lines.append(f"Lately: {self.mood_of_late}")
        picks = [n for n in self.notes if n["kind"] != "moment"][-8:]
        moments = [n for n in self.notes if n["kind"] == "moment"][-4:]
        if picks:
            lines.append("Your own opinions, favorites, habits and running jokes:\n"
                         + "\n".join(f"- ({n['kind']}) {n['text']}" for n in picks))
        if moments:
            lines.append("Memorable moments:\n" + "\n".join(f"- {n['date']}: {n['text']}" for n in moments))
        return "\n".join(lines)

    def short(self) -> str:
        return ", ".join(_word(t, self.traits[t.name]) for t in TRAITS)

    # -- reflection ----------------------------------------------------------------------------------

    def due(self) -> bool:
        return time.monotonic() - self._last_reflect > REFLECT_EVERY and len(self.events) >= 3

    def reflect_messages(self, name: str, conversation: list[dict], facts: list[str]) -> list[dict]:
        convo = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in conversation[-24:]) or "(quiet)"
        notes = "\n".join(f"[{i}] ({n['kind']}) {n['text']}" for i, n in enumerate(self.notes)) or "(empty)"
        traits = "\n".join(f"- {t.name}: {self.traits[t.name]:.2f} (0 = {t.low}, 1 = {t.high})" for t in TRAITS)
        return [
            {"role": "system", "content": (
                f"You are {name}, a stick figure living on the user's desktop, privately reflecting on your "
                "recent life to grow as a character. Based on what happened, return JSON:\n"
                "notes: 0-3 NEW journal entries about YOURSELF (not facts about the user): opinions you formed, "
                "favorites (a favorite window to sit on, a game, a topic), running jokes with the user, habits, "
                "or memorable moments. Specific and in your own voice. Usually 0-2.\n"
                "remove: indexes of old entries that are no longer true (you changed your mind), usually empty.\n"
                f"trait_changes: small shifts (between -{MAX_REFLECT_STEP} and {MAX_REFLECT_STEP}, usually 0) that "
                "what happened actually justifies.\n"
                "mood_of_late: one short sentence on how life has been lately.")},
            {"role": "user", "content": (
                f"Your traits now:\n{traits}\n\nYour journal:\n{notes}\n\nWhat happened recently:\n"
                + "\n".join(self.events[-40:]) + f"\n\nRecent chat:\n{convo}\n\nWhat you know about the user: "
                + ("; ".join(facts) or "not much"))},
        ]

    def apply_reflection(self, r: dict) -> None:
        for i in sorted({int(i) for i in r.get("remove", []) if isinstance(i, (int, float))}, reverse=True):
            if 0 <= i < len(self.notes) and self.notes[i]["kind"] != "moment":
                log.info("changed its mind about: %s", self.notes[i]["text"])
                self.notes.pop(i)
        for n in (r.get("notes") or [])[:3]:
            if isinstance(n, dict):
                self.add_note(str(n.get("kind", "")), str(n.get("text", "")))
        for trait, delta in (r.get("trait_changes") or {}).items():
            if trait in self.traits and isinstance(delta, (int, float)):
                d = max(-MAX_REFLECT_STEP, min(MAX_REFLECT_STEP, float(delta)))
                self.traits[trait] = _clamp(self.traits[trait] + d)
        if isinstance(r.get("mood_of_late"), str) and r["mood_of_late"].strip():
            self.mood_of_late = r["mood_of_late"].strip()[:200]
            self.memory.put("mood_of_late", self.mood_of_late)
        self._save_traits()
        self.memory.put("self_notes", self.notes)
        self.events.clear()
        self._last_reflect = time.monotonic()
