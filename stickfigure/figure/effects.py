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


@dataclass
class Ghost:
    """An afterimage: a fading copy of a fighter's pose (motion trail)."""
    pose: dict
    x: float
    y: float
    color: Color
    age: float = 0.0
    life: float = 0.28


@dataclass
class Dust:
    x: float
    y: float
    size: float = 1.0
    age: float = 0.0
    life: float = 0.55


@dataclass
class Beam:
    x1: float
    y1: float
    x2: float
    y2: float
    color: Color
    age: float = 0.0
    life: float = 0.6


@dataclass
class Aura:
    """Charging up: rings closing in on the caster, with sparkles."""
    x: float
    y: float
    color: Color
    radius: float
    age: float = 0.0
    life: float = 0.7


@dataclass
class Impact:
    """A heavy hit: a white flash ring with speed lines."""
    x: float
    y: float
    size: float = 1.0
    age: float = 0.0
    life: float = 0.22


@dataclass
class Banner:
    text: str
    x: float
    y: float
    color: Color = (255, 230, 90)
    size: float = 1.0
    age: float = 0.0
    life: float = 1.1


@dataclass
class Fighter:
    name: str
    color: Color
    hp: float = 100.0
    shown: float = 100.0  # the bar drains smoothly toward hp


class Effects:
    def __init__(self):
        self.sparks: list[Spark] = []
        self.orbs: list[Orb] = []
        self.bursts: list[Burst] = []
        self.shields: list[Shield] = []
        self.ghosts: list[Ghost] = []
        self.dust_puffs: list[Dust] = []
        self.beams: list[Beam] = []
        self.auras: list[Aura] = []
        self.impacts: list[Impact] = []
        self.banners: list[Banner] = []
        self.bars: tuple[Fighter, Fighter] | None = None  # (left, right) health bars during a match
        self.bars_at: tuple[float, float] = (0.0, 0.0)  # where to draw them (center x, y)
        self._slow_left = 0.0
        self._slow = 1.0

    @property
    def active(self) -> bool:
        return bool(self.sparks or self.orbs or self.bursts or self.shields or self.ghosts or self.dust_puffs
                    or self.beams or self.auras or self.impacts or self.banners or self.bars)

    @property
    def time_scale(self) -> float:
        """< 1 during slow motion (the finisher). The app slows the rival and effects by this."""
        return self._slow if self._slow_left > 0 else 1.0

    def slowmo(self, seconds: float, scale: float = 0.35) -> None:
        self._slow_left, self._slow = seconds, scale

    def ghost(self, pose: dict, x: float, y: float, color: Color) -> None:
        self.ghosts.append(Ghost(pose, x, y, color))

    def dust(self, x: float, y: float, size: float = 1.0) -> None:
        self.dust_puffs.append(Dust(x, y, size))

    def beam(self, x1: float, y1: float, x2: float, y2: float, color: Color, life: float = 0.6) -> Beam:
        b = Beam(x1, y1, x2, y2, color, life=life)
        self.beams.append(b)
        return b

    def aura(self, x: float, y: float, color: Color, radius: float, life: float = 0.7) -> None:
        self.auras.append(Aura(x, y, color, radius, life=life))

    def impact(self, x: float, y: float, size: float = 1.0) -> None:
        self.impacts.append(Impact(x, y, size))

    def banner(self, text: str, x: float, y: float, color: Color = (255, 230, 90), size: float = 1.0,
               life: float = 1.1) -> None:
        self.banners.append(Banner(text, x, y, color, size, life=life))

    def damage(self, right: bool, amount: float) -> None:
        if self.bars is not None:
            f = self.bars[1 if right else 0]
            f.hp = max(0.0, f.hp - amount)

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
        self._slow_left = max(0.0, self._slow_left - dt)
        dt *= self.time_scale  # effects play in slow motion too
        for group in (self.ghosts, self.dust_puffs, self.beams, self.auras, self.impacts, self.banners):
            for e in group:
                e.age += dt
        self.ghosts = [g for g in self.ghosts if g.age < g.life]
        self.dust_puffs = [d for d in self.dust_puffs if d.age < d.life]
        self.beams = [b for b in self.beams if b.age < b.life]
        self.auras = [a for a in self.auras if a.age < a.life]
        self.impacts = [i for i in self.impacts if i.age < i.life]
        self.banners = [b for b in self.banners if b.age < b.life]
        if self.bars is not None:
            for f in self.bars:
                f.shown += (f.hp - f.shown) * min(1.0, dt * 6)
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
        for group in (self.sparks, self.orbs, self.bursts, self.shields, self.ghosts, self.dust_puffs, self.beams,
                      self.auras, self.impacts, self.banners):
            group.clear()
        self.bars = None
