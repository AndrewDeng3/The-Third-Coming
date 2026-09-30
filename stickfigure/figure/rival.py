"""Sparring partners: the legendary stick figures from Alan Becker's Animator vs. Animation series warp in for a
friendly fight now and then. Drawn with the same skeleton/animator as The Third Coming, in their own colors.

This is the logic only (position, motion, pose, fade); overlay/rival_window.py draws it.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from stickfigure.config import CONFIG, Config
from stickfigure.figure.animator import Animator
from stickfigure.figure.controller import Activity


@dataclass(frozen=True)
class Character:
    name: str
    color: tuple[int, int, int]
    extra: str = ""  # "crown" (King Orange)
    greet: tuple[str, ...] = ()
    weapon: str = "sword"  # what they pick up in a weapons match
    accent: tuple[int, int, int] | None = None  # weapon glow/tint


# Colors sampled from the "Alan Becker DPETs" sprite frames.
ROSTER = (
    Character("The Chosen One", (0, 0, 0), greet=("The Chosen One?! Let's go!", "You again, Chosen One?"),
              weapon="staff"),
    Character("The Dark Lord", (224, 32, 32), greet=("The Dark Lord?! Not today!", "Uh oh. Dark Lord's here."),
              weapon="staff", accent=(255, 60, 60)),
    Character("The Second Coming", (240, 96, 0), greet=("Big bro! Sparring time?", "Second Coming! Teach me something!"),
              weapon="sword"),
    Character("King Orange", (208, 96, 0), "crown", greet=("King Orange?! Bow to THIS!", "Oh great, the king."),
              weapon="staff", accent=(240, 192, 0)),
    Character("Red", (208, 0, 16), greet=("Red! Rematch!", "Oh hey Red, wanna go?"), weapon="sword"),
    Character("Blue", (64, 176, 224), greet=("Blue! You're going down!", "Blue wants a round, huh?"), weapon="pickaxe"),
    Character("Green", (48, 176, 64), greet=("Green! Let's see what you've got.", "Green? Bet."), weapon="sword"),
    Character("Yellow", (240, 192, 0), greet=("Yellow! Round one!", "Yellow's here, fight!"), weapon="pickaxe"),
    Character("Purple", (144, 32, 128), greet=("Purple?! Where'd you come from?", "Purple! Rematch!"), weapon="sword"),
)


class _Vec:
    def __init__(self, x: float = 0.0, y: float = 0.0):
        self.x, self.y = x, y

    def __iter__(self):
        return iter((self.x, self.y))


class _Body:
    def __init__(self):
        self.position = _Vec()
        self.velocity = _Vec()


class _Puppet:
    """Just enough of controller.Figure for the Animator to read."""

    def __init__(self):
        self.body = _Body()
        self.grabbed = False
        self.riding = False
        self.knocked = 0.0
        self.knocked_side = 1
        self.grounded = True
        self.tumble_spin = 0.0
        self.tumble_angle = 0.0
        self.air_time = 0.0
        self.land_timer = 0.0
        self.activity = Activity.NONE
        self.activity_point = None
        self.pose_mode: str | None = "stance"
        self.air_pose: str | None = None
        self.facing = 1
        self.tilt = 0.0

    @property
    def tumbling(self) -> bool:
        return self.tumble_spin != 0.0


class Rival:
    """One sparring partner standing on the same surface as the figure (feet at `floor_y`)."""

    def __init__(self, who: Character, x: float, floor_y: float, facing: int, cfg: Config = CONFIG):
        self.who = who
        self.cfg = cfg
        self.puppet = _Puppet()
        self.anim = Animator(self.puppet, cfg)
        self.half = cfg.figure_height / 2
        self.x, self.floor_y, self.lift = x, floor_y, 0.0  # lift: height above the floor (px)
        self.vx = self.vy = 0.0
        self.walk_target: float | None = None
        self.alpha = 0.0
        self.fading = False  # warping out
        self.done = False
        self.age = 0.0
        self.sparks: list[tuple[float, float, float]] = []  # (x, y, age) hit flashes
        self.weapon: str | None = None
        self.run_speed = 620.0  # fighters sprint at each other
        self.flying = False
        self.fly_target: tuple[float, float] | None = None  # body center to steer to (None = coast)
        self.fly_speed = 900.0
        self.bounds: tuple[float, float] | None = None  # the arena floor's ends (it can't slide off them)
        self.puppet.facing = facing
        self._sync()

    # -- commands (from the fight choreography) -------------------------------------------------------------

    def pose(self, name: str | None) -> None:
        self.puppet.pose_mode = name
        self.puppet.activity = Activity.NONE

    def walk_to(self, x: float | None) -> None:
        self.walk_target = x

    def hit(self, direction: int, force: float = 380.0) -> None:
        """Take a hit: recoil pose and slide back."""
        self.pose("hurt")
        self.vx = direction * force

    def launch(self, direction: int) -> None:
        """The finisher: flying and spinning off, then gone."""
        self.pose(None)
        self.vx, self.vy = direction * 420.0, -950.0
        self.puppet.tumble_spin = direction * 11.0
        self.fading = True

    def air(self, vx: float, vy: float, pose: str | None, spin: float = 0.0) -> None:
        """Leap: a jump kick, a hop over a sweep, a somersault (with `spin`)."""
        self.pose(None)
        self.puppet.air_pose = pose
        self.vx, self.vy = vx, vy
        self.puppet.tumble_spin = spin
        self.walk_target = None

    def fly(self, target: tuple[float, float] | None, speed: float = 900.0) -> None:
        """Take off / steer toward a body-center position anywhere on screen (None = coast, slowing down)."""
        if not self.flying:
            self.flying = True
            self.walk_target = None
        self.fly_target = target
        self.fly_speed = speed

    def land(self) -> None:
        """Stop flying and drop back to the floor."""
        self.flying = False
        self.fly_target = None
        self.puppet.tilt = 0.0
        self.vy = max(self.vy, 0.0) if self.lift > 0 else self.vy

    @property
    def y(self) -> float:
        return self.floor_y - self.lift - self.half

    def vanish(self) -> None:
        self.fading = True

    def spark(self, x: float, y: float) -> None:
        self.sparks.append((x, y, 0.0))

    @property
    def feet(self) -> tuple[float, float]:
        return (self.x, self.floor_y - self.lift)

    # -- per frame ------------------------------------------------------------------------------------------

    def update(self, dt: float) -> None:
        self.age += dt
        self.alpha = max(0.0, self.alpha - dt * 1.4) if self.fading else min(1.0, self.alpha + dt * 4)
        if self.fading and self.alpha <= 0:
            self.done = True
        if self.flying:
            self._fly(dt)
            return
        walking = 0.0
        if self.walk_target is not None and self.lift <= 0 and abs(self.vx) < 30:
            d = self.walk_target - self.x
            if abs(d) > 3:
                walking = math.copysign(min(self.run_speed, abs(d) * 7), d)
                self.puppet.facing = 1 if d > 0 else -1
            else:
                self.walk_target = None
        self.x += (self.vx + walking) * dt
        if self.bounds is not None and not self.fading:
            lo, hi = self.bounds
            if not lo <= self.x <= hi:
                self.x = min(max(self.x, lo), hi)
                self.vx = 0.0
        self.vx *= math.exp(-6 * dt) if self.lift <= 0 else 1.0  # friction on the ground
        if self.lift > 0 or self.vy < 0:
            self.vy += self.cfg.gravity * dt
            self.lift = max(0.0, self.lift - self.vy * dt)
            self.puppet.tumble_angle += self.puppet.tumble_spin * dt
            if self.lift <= 0 and self.vy > 0 and not self.fading:
                self.vy = 0.0
                self.puppet.tumble_spin = self.puppet.tumble_angle = 0.0
                self.puppet.air_pose = None
        self.puppet.grounded = self.lift <= 0 and self.vy >= 0
        self.puppet.body.velocity.x = walking + self.vx
        self.puppet.body.velocity.y = self.vy
        self.sparks = [(x, y, a + dt) for x, y, a in self.sparks if a + dt < 0.35]
        self._sync()
        self.anim.update(dt)

    def _fly(self, dt: float) -> None:
        if self.fly_target is not None:
            dx, dy = self.fly_target[0] - self.x, self.fly_target[1] - self.y
            dist = math.hypot(dx, dy)
            speed = min(self.fly_speed, dist * 7)
            wx, wy = (dx / dist * speed, dy / dist * speed) if dist > 0.5 else (0.0, 0.0)
            k = min(1.0, dt * 14)
            self.vx += (wx - self.vx) * k
            self.vy += (wy - self.vy) * k
        else:
            drag = math.exp(-2.2 * dt)
            self.vx *= drag
            self.vy *= drag
        self.x += self.vx * dt
        self.lift = max(0.0, self.lift - self.vy * dt)  # never below its own floor
        self.puppet.tumble_angle += self.puppet.tumble_spin * dt
        self.puppet.grounded = False
        self.puppet.body.velocity.x = self.vx
        self.puppet.body.velocity.y = self.vy
        self.sparks = [(x, y, a + dt) for x, y, a in self.sparks if a + dt < 0.35]
        self._sync()
        self.anim.update(dt)

    def _sync(self) -> None:
        self.puppet.body.position.x = self.x
        self.puppet.body.position.y = self.floor_y - self.lift - self.half


def pick_character(rng: random.Random | None = None, avoid: str = "") -> Character:
    choices = [c for c in ROSTER if c.name != avoid]
    return (rng or random).choice(choices)
