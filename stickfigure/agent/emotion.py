"""The figure's mood: four drives in [0, 1] that drift toward a baseline and get bumped by events.

- energy:     drains while active, recovers while sitting/sleeping; baseline follows time of day
- curiosity:  rises when bored (no interaction), falls after exploring or chatting
- affection:  rises with petting/chatting, fades slowly with neglect
- annoyance:  spikes when grabbed/thrown/knocked over, calms down over a few minutes
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

DRIVES = ("energy", "curiosity", "affection", "annoyance")


def _clamp(x: float) -> float:
    return min(1.0, max(0.0, x))


@dataclass
class Emotion:
    energy: float = 0.7
    curiosity: float = 0.5
    affection: float = 0.5
    annoyance: float = 0.0

    # How fast each drive returns to baseline (seconds, e-folding time).
    TAU = {"energy": 900.0, "curiosity": 400.0, "affection": 1800.0, "annoyance": 150.0}

    def baseline(self, now: float | None = None) -> dict[str, float]:
        hour = time.localtime(now if now is not None else time.time())
        h = hour.tm_hour + hour.tm_min / 60
        # Sleepy late at night / early morning, perkiest mid-afternoon.
        energy = 0.55 + 0.3 * math.cos((h - 15) / 24 * math.tau)
        return {"energy": energy, "curiosity": 0.5, "affection": 0.45, "annoyance": 0.0}

    def update(self, dt: float, activity: str, idle_for: float, now: float | None = None) -> None:
        """`activity`: "moving" | "resting" | "sleeping" | "other". `idle_for`: seconds since user interaction."""
        base = self.baseline(now)
        for d in DRIVES:
            k = 1 - math.exp(-dt / self.TAU[d])
            setattr(self, d, getattr(self, d) + (base[d] - getattr(self, d)) * k)
        if activity == "moving":
            self.energy -= 0.0025 * dt
        elif activity == "resting":
            self.energy += 0.004 * dt
        elif activity == "sleeping":
            self.energy += 0.02 * dt
        if idle_for > 120:
            self.curiosity += 0.0015 * dt  # boredom
        self._clamp()

    # -- events ----------------------------------------------------------------------

    def on_pet(self) -> None:
        self.affection += 0.12
        self.annoyance -= 0.15
        self._clamp()

    def on_grab(self) -> None:
        self.annoyance += 0.04
        self._clamp()

    def on_thrown(self, speed: float) -> None:
        self.annoyance += min(0.3, speed / 8000)
        self.energy += 0.05  # adrenaline
        self._clamp()

    def on_knocked(self) -> None:
        self.annoyance += 0.12
        self.energy -= 0.03
        self._clamp()

    def on_chat(self, sentiment: float = 0.0) -> None:
        """A user message arrived. `sentiment` in [-1, 1] from the extraction pass."""
        self.affection += 0.04 + 0.08 * max(0.0, sentiment)
        self.annoyance += 0.12 * max(0.0, -sentiment) - 0.03
        self.curiosity -= 0.04
        self._clamp()

    def on_explored(self) -> None:
        self.curiosity -= 0.08
        self._clamp()

    def _clamp(self) -> None:
        for d in DRIVES:
            setattr(self, d, _clamp(getattr(self, d)))

    # -- derived -------------------------------------------------------------------------

    @property
    def happiness(self) -> float:
        return _clamp(0.5 * self.affection + 0.3 * self.energy - 0.8 * self.annoyance + 0.2)

    def label(self) -> str:
        if self.energy < 0.2:
            return "exhausted"
        if self.annoyance > 0.55:
            return "grumpy"
        if self.annoyance > 0.3:
            return "irritated"
        if self.energy < 0.35:
            return "sleepy"
        if self.happiness > 0.7:
            return "cheerful"
        if self.curiosity > 0.7:
            return "restless and curious"
        if self.affection > 0.7:
            return "affectionate"
        return "content"

    def describe(self) -> str:
        def level(x: float, words: tuple[str, str, str]) -> str:
            return words[0] if x < 0.33 else words[1] if x < 0.66 else words[2]

        return (
            f"{self.label()} (energy: {level(self.energy, ('tired', 'okay', 'energetic'))}, "
            f"curiosity: {level(self.curiosity, ('bored', 'curious', 'itching to explore'))}, "
            f"feelings toward the user: {level(self.affection, ('distant', 'friendly', 'adoring'))}, "
            f"annoyance: {level(self.annoyance, ('calm', 'irritated', 'grumpy'))})"
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> Emotion:
        e = cls()
        for k in DRIVES:
            if d and k in d:
                setattr(e, k, _clamp(float(d[k])))
        return e
