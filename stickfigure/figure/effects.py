"""Fight effects in world (screen) coordinates: hit sparks, magic orbs, energy bursts, shield bubbles.
Plain data + motion; overlay/effects_window.py draws them."""

from __future__ import annotations

from dataclasses import dataclass

Color = tuple[int, int, int]


@dataclass
class Spark:
    x: float
    y: float
    color: Color = (255, 240, 150)
    size: float = 1.0
    age: float = 0.0
    life: float = 0.35


@dataclass
class Orb:
    x: float
    y: float
    vx: float
    color: Color
    size: float = 1.0
    age: float = 0.0
    alive: bool = True
    fade: float = 1.0  # < 1 while fizzling out after a miss


@dataclass
class Burst:
    x: float
    y: float
    color: Color
    size: float = 1.0
    age: float = 0.0
    life: float = 0.45


@dataclass
class Shield:
    x: float
    y: float
    color: Color
    radius: float
    age: float = 0.0
    life: float = 0.6


class Effects:
    def __init__(self):
        self.sparks: list[Spark] = []
        self.orbs: list[Orb] = []
        self.bursts: list[Burst] = []
        self.shields: list[Shield] = []

    @property
    def active(self) -> bool:
        return bool(self.sparks or self.orbs or self.bursts or self.shields)

    def spark(self, x: float, y: float, color: Color = (255, 240, 150), size: float = 1.0) -> Spark:
        s = Spark(x, y, color, size)
        self.sparks.append(s)
        return s

    def orb(self, x: float, y: float, vx: float, color: Color, size: float = 1.0) -> Orb:
        o = Orb(x, y, vx, color, size)
        self.orbs.append(o)
        return o

    def burst(self, x: float, y: float, color: Color, size: float = 1.0) -> Burst:
        b = Burst(x, y, color, size)
        self.bursts.append(b)
        return b

    def shield(self, x: float, y: float, color: Color, radius: float) -> Shield:
        s = Shield(x, y, color, radius)
        self.shields.append(s)
        return s

    def pop(self, orb: Orb) -> None:
        """An orb hits something: it bursts where it is."""
        orb.alive = False
        self.burst(orb.x, orb.y, orb.color, orb.size)

    def fizzle(self, orb: Orb) -> None:
        """An orb missed: it keeps flying and fades away."""
        orb.fade = 0.99

    def update(self, dt: float) -> None:
        for s in self.sparks:
            s.age += dt
        for b in self.bursts:
            b.age += dt
        for s in self.shields:
            s.age += dt
        for o in self.orbs:
            o.age += dt
            o.x += o.vx * dt
            if o.fade < 1:
                o.fade -= dt * 2.5
            if o.fade <= 0 or o.age > 4:
                o.alive = False
        self.sparks = [s for s in self.sparks if s.age < s.life]
        self.bursts = [b for b in self.bursts if b.age < b.life]
        self.shields = [s for s in self.shields if s.age < s.life]
        self.orbs = [o for o in self.orbs if o.alive]

    def clear(self) -> None:
        self.sparks.clear()
        self.orbs.clear()
        self.bursts.clear()
        self.shields.clear()
