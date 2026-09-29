"""Sparring matches with the legends (the fight choreography), mixed into Brain.

Moves: strikes (punch / kick / uppercut, or weapon slash / thrust), combos, leg sweeps, flying jump kicks, mid-air
clashes, somersault dodges, and magic (energy orbs vs. shields), ending in a finisher.

Everything lines up: before a strike, the fighters are spaced by the move's real reach - where the fist, foot or
weapon tip is at full extension, measured on the very skeleton that gets drawn - so the blow lands on the other
fighter's body, and the spark is drawn exactly at that point.
"""

from __future__ import annotations

import math
import random
from typing import Generator

from stickfigure.figure.animator import Animator
from stickfigure.figure.effects import Effects
from stickfigure.figure.rival import _Puppet, pick_character
from stickfigure.overlay.render import weapon_segment
from stickfigure.world.nav import usable_span

Task = Generator[None, None, bool]

GROUND_MOVES = {"punch": "hand_f", "uppercut": "hand_f", "kick": "foot_f", "sweep": "foot_f", "cast": "hand_f",
                "slash": "hand_f", "thrust": "hand_f"}
AIR_MOVES = {"flykick": "foot_f", "divepunch": "hand_f"}
WEAPON_STRIKES = {"sword": ("slash", "thrust", "slash"), "staff": ("thrust", "slash", "thrust"),
                  "pickaxe": ("slash", "slash", "thrust")}
OUR_MAGIC = (255, 170, 40)
TORSO = 0.05  # how far in front of a body's center its "front" is (fraction of height)
_offsets: dict[tuple, tuple[float, float]] = {}


def strike_offset(move: str, weapon: str | None = None) -> tuple[float, float]:
    """Where the striking point (fist, foot, or weapon tip) is at full extension, relative to the body center, for
    a fighter facing +x. Measured by building the pose on a throwaway skeleton (cached)."""
    key = (move, weapon)
    if key not in _offsets:
        puppet = _Puppet()
        anim = Animator(puppet)
        if move in AIR_MOVES:
            puppet.grounded = False
            puppet.air_pose = move
        else:
            puppet.pose_mode = move
        for _ in range(30):
            anim.update(1 / 60)
        pose = anim.pose
        joint = AIR_MOVES.get(move) or GROUND_MOVES.get(move, "hand_f")
        if weapon and move in ("slash", "thrust"):
            _, tip = weapon_segment(pose, weapon, anim.P.height)
            _offsets[key] = tip
        else:
            _offsets[key] = pose[joint]
    return _offsets[key]


def magic_color(who) -> tuple[int, int, int]:
    return (120, 200, 255) if sum(who.color) < 60 else who.color  # the Chosen One is black: blue energy


class SparMixin:
    fx: Effects

    def _spar(self) -> Task:
        fig = self.fig
        seg = self._seg(self._current_key())
        span = usable_span(seg, self.cfg) if seg else None
        if span is None:
            return False
        lo, hi, _ = span
        fx = fig.body.position.x
        side = random.choice((-1, 1))
        if not lo <= fx + side * 320 <= hi:
            side = -side
            if not lo <= fx + side * 320 <= hi:
                return False  # no room to fight here
        who = pick_character()
        rival = self.make_rival(who, fx + side * 320, fig.feet[1], -side)
        if rival is None:
            return False
        self._fight = (rival, side, lo, hi)
        if random.random() < 0.5:  # a weapons match
            fig.weapon = random.choice(("sword", "pickaxe"))
            rival.weapon = who.weapon or "sword"
        fig.events.append(("spar", "start", who))
        try:
            fig.walk_to(None)
            fig.facing = side
            fig.pose_mode = self._stance(fig.weapon)
            rival.pose(self._stance(rival.weapon))
            yield from self._idle(0.9)
            moves = [(3.0, self._x_strike), (1.5, self._x_combo), (1.5, self._x_jumpkick), (1.0, self._x_sweep),
                     (1.0, self._x_dodge), (1.5, self._x_magic), (1.0, self._x_airclash), (1.0, self._x_clash)]
            for _ in range(random.randint(4, 7)):
                total = sum(w for w, _ in moves)
                r = random.uniform(0, total)
                for w, move in moves:
                    r -= w
                    if r <= 0:
                        yield from move(random.random() < 0.5)
                        break
                yield from self._settle()
                yield from self._idle(random.uniform(0.2, 0.5))
            yield from self._finisher(random.random() < 0.8)
            return True
        finally:
            fig.pose_mode = fig.air_pose = fig.weapon = None
            fig.walk_to(None)
            if not rival.fading:
                rival.vanish()
            self._fight = None

    # -- positioning -------------------------------------------------------------------------------------------

    @staticmethod
    def _stance(weapon: str | None) -> str:
        return "guard" if weapon else "stance"

    def _centers(self) -> tuple[tuple[float, float], tuple[float, float]]:
        rival = self._fight[0]
        return tuple(self.fig.body.position), tuple(rival.puppet.body.position)

    def _gap_to(self, gap: float) -> Task:
        """Walk both fighters (toward/away from their midpoint) until their centers are `gap` apart."""
        rival, side, lo, hi = self._fight
        fig = self.fig
        fig.pose_mode = None
        rival.pose(None)
        mid = min(max((fig.body.position.x + rival.x) / 2, lo + gap / 2), hi - gap / 2)
        mine, theirs = mid - side * gap / 2, mid + side * gap / 2
        fig.walk_to(mine)
        rival.walk_to(theirs)
        t = 0.0
        while t < 1.8 and (abs(fig.body.position.x - mine) > 4 or abs(rival.x - theirs) > 4):
            t += self.dt
            yield
        fig.walk_to(None)
        fig.facing = side
        rival.puppet.facing = -side
        yield from self._settle()

    def _settle(self) -> Task:
        """Back to fighting stances (after landing if anyone's in the air)."""
        rival, side, _, _ = self._fight
        fig = self.fig
        t = 0.0
        while (not fig.grounded or rival.lift > 0) and t < 3:
            t += self.dt
            yield
        fig.air_pose = None
        rival.puppet.air_pose = None
        fig.facing = side
        rival.puppet.facing = -side
        fig.pose_mode = self._stance(fig.weapon)
        rival.pose(self._stance(rival.weapon))

    def _strike_point(self, ours: bool, move: str) -> tuple[float, float]:
        """World position of the attacker's fist/foot/weapon tip for `move` right now."""
        rival, side, _, _ = self._fight
        weapon = self.fig.weapon if ours else rival.weapon
        dx, dy = strike_offset(move, weapon if move in ("slash", "thrust") else None)
        (fx, fy), (rx, ry) = self._centers()
        if ours:
            return fx + side * dx, fy + dy
        return rx - side * dx, ry + dy

    def _reach(self, ours: bool, move: str) -> float:
        rival = self._fight[0]
        weapon = self.fig.weapon if ours else rival.weapon
        return strike_offset(move, weapon if move in ("slash", "thrust") else None)[0]

    def _pick_strike(self, ours: bool) -> str:
        weapon = self.fig.weapon if ours else self._fight[0].weapon
        if weapon:
            return random.choice(WEAPON_STRIKES[weapon])
        return random.choice(("punch", "punch", "kick"))

    # -- blows ---------------------------------------------------------------------------------------------------

    def _land_blow(self, ours: bool, move: str, reaction: str) -> Task:
        """Attacker performs `move` (already in range); the defender blocks or gets hit, at the exact point."""
        rival, side, _, _ = self._fight
        fig = self.fig
        if ours:
            fig.pose_mode = move
        else:
            rival.pose(move)
        yield from self._idle(0.11)  # full extension
        x, y = self._strike_point(ours, move)
        big = move in ("kick", "uppercut", "slash")
        self.fx.spark(x, y, size=1.3 if big else 1.0)
        if ours:
            if reaction == "block":
                rival.pose("block")
                rival.vx = side * 150.0
            else:
                rival.hit(side, 420.0 if big else 330.0)
                fig.events.append(("spar", "hit", rival.who))
        else:
            if reaction == "block":
                fig.pose_mode = "block"
                fig.body.velocity = (-side * 150.0, fig.body.velocity.y)
            else:
                fig.pose_mode = "hurt"
                fig.body.velocity = (-side * (380.0 if big else 300.0), fig.body.velocity.y)
                fig.events.append(("spar", "hurt", rival.who))
        yield from self._idle(0.3)

    def _x_strike(self, ours: bool) -> Task:
        move = self._pick_strike(ours)
        yield from self._gap_to(self._reach(ours, move) + TORSO * self.cfg.figure_height)
        yield from self._land_blow(ours, move, "block" if random.random() < 0.35 else "hit")

    def _x_combo(self, ours: bool) -> Task:
        weapon = self.fig.weapon if ours else self._fight[0].weapon
        combo = list(WEAPON_STRIKES[weapon]) if weapon else ["punch", "punch", "kick"]
        for i, move in enumerate(combo):
            yield from self._gap_to(self._reach(ours, move) + TORSO * self.cfg.figure_height)
            yield from self._land_blow(ours, move, "hit" if i == len(combo) - 1 else "block")

    def _x_sweep(self, ours: bool) -> Task:
        rival, side, _, _ = self._fight
        fig = self.fig
        yield from self._gap_to(self._reach(ours, "sweep") + 0.03 * self.cfg.figure_height)
        hop = random.random() < 0.6
        if ours:
            fig.pose_mode = "sweep"
            if hop:
                rival.air(0.0, -520.0, None)
        else:
            rival.pose("sweep")
            if hop:
                fig.pose_mode = None
                fig.jump(0.0, -520.0)
        yield from self._idle(0.12)
        if not hop:
            x, y = self._strike_point(ours, "sweep")
            self.fx.spark(x, y)
            if ours:
                rival.hit(side, 300.0)
            else:
                fig.pose_mode = "hurt"
                fig.body.velocity = (-side * 280.0, fig.body.velocity.y)
        yield from self._idle(0.35)

    def _x_dodge(self, ours_attack: bool) -> Task:
        """The attacker swings; the defender somersaults back out of reach (a clean miss, no spark)."""
        rival, side, _, _ = self._fight
        fig = self.fig
        move = self._pick_strike(ours_attack)
        yield from self._gap_to(self._reach(ours_attack, move) + TORSO * self.cfg.figure_height)
        spin = math.tau / (2 * 620.0 / self.cfg.gravity)
        if ours_attack:
            fig.pose_mode = move
            rival.air(side * 260.0, -620.0, "tuck", spin=side * spin)
        else:
            rival.pose(move)
            fig.pose_mode = None
            fig.air_pose = "tuck"
            fig.jump(-side * 260.0, -620.0)
            fig.facing = side
            fig.tumble_spin = -side * spin
        yield from self._idle(0.5)

    def _x_jumpkick(self, ours: bool) -> Task:
        """A flying kick: the attacker leaps in; the kick connects when the foot reaches the defender's body."""
        rival, side, _, _ = self._fight
        fig = self.fig
        reach = self._reach(ours, "flykick")
        H = self.cfg.figure_height
        yield from self._gap_to(reach + 150)
        block = random.random() < 0.4
        if ours:
            fig.pose_mode = None
            fig.air_pose = "flykick"
            fig.jump(side * 420.0, -560.0)
            fig.facing = side
        else:
            rival.air(-side * 420.0, -560.0, "flykick")
        t = 0.0
        while t < 1.5:
            t += self.dt
            x, y = self._strike_point(ours, "flykick")
            (fx, fy), (rx, ry) = self._centers()
            front = (rx - side * TORSO * H) if ours else (fx + side * TORSO * H)
            if (x - front) * (side if ours else -side) >= 0:
                self.fx.spark(x, y, size=1.3)
                if ours:
                    if block:
                        rival.pose("block")
                    else:
                        rival.hit(side, 450.0)
                        fig.events.append(("spar", "hit", rival.who))
                    fig.body.velocity = (-side * 220.0, min(fig.body.velocity.y, -120.0))
                else:
                    fig.pose_mode = "block" if block else "hurt"
                    fig.body.velocity = (-side * (160.0 if block else 380.0), fig.body.velocity.y)
                    rival.vx = side * 220.0
                break
            yield
        yield from self._idle(0.3)

    def _x_airclash(self, _: bool) -> Task:
        """Both leap at each other and collide in mid-air - sparks - and bounce apart."""
        rival, side, _, _ = self._fight
        fig = self.fig
        reach = self._reach(True, "flykick")
        yield from self._gap_to(2 * reach + 200)
        fig.pose_mode = None
        fig.air_pose = "flykick"
        fig.jump(side * 380.0, -640.0)
        fig.facing = side
        rival.air(-side * 380.0, -640.0, "flykick")
        t = 0.0
        while t < 1.5:
            t += self.dt
            (fx, fy), (rx, ry) = self._centers()
            if abs(rx - fx) <= 2 * reach:
                mx, my = (fx + rx) / 2, (self._strike_point(True, "flykick")[1] + self._strike_point(False, "flykick")[1]) / 2
                self.fx.spark(mx, my, size=1.6)
                fig.body.velocity = (-side * 300.0, fig.body.velocity.y)
                rival.vx = side * 300.0
                break
            yield
        yield from self._idle(0.3)

    def _x_clash(self, _: bool) -> Task:
        """Both strike at once and the blows meet in the middle (blade on blade, or fist on fist)."""
        rival, side, _, _ = self._fight
        fig = self.fig
        mine = "slash" if fig.weapon else "punch"
        theirs = "slash" if rival.weapon else "punch"
        yield from self._gap_to(self._reach(True, mine) + self._reach(False, theirs))  # tips just touch
        fig.pose_mode = mine
        rival.pose(theirs)
        yield from self._idle(0.12)
        (ax, ay), (bx, by) = self._strike_point(True, mine), self._strike_point(False, theirs)
        self.fx.spark((ax + bx) / 2, (ay + by) / 2, size=1.5)
        fig.body.velocity = (-side * 200.0, fig.body.velocity.y)
        rival.vx = side * 200.0
        yield from self._idle(0.35)

    def _x_magic(self, ours: bool) -> Task:
        """An energy orb from the caster's hand; the defender shields, jumps it, or takes it."""
        rival, side, _, _ = self._fight
        fig = self.fig
        H = self.cfg.figure_height
        yield from self._gap_to(random.uniform(280, 380))
        color = OUR_MAGIC if ours else magic_color(rival.who)
        if ours:
            fig.pose_mode = "cast"
        else:
            rival.pose("cast")
        yield from self._idle(0.15)
        x, y = self._strike_point(ours, "cast")
        orb = self.fx.orb(x, y, (side if ours else -side) * 900.0, color)
        roll = random.random()
        reaction = "shield" if roll < 0.4 else "jump" if roll < 0.7 else "hit"
        t, jumped = 0.0, False
        while t < 2.0 and orb.alive:
            t += self.dt
            (fx, fy), (rx, ry) = self._centers()
            front = (rx - side * TORSO * H) if ours else (fx + side * TORSO * H)
            gap = (front - orb.x) * (side if ours else -side)
            if reaction == "jump" and not jumped and gap < 150:
                jumped = True
                if ours:
                    rival.air(0.0, -700.0, "tuck", spin=-side * 10.0)
                else:
                    fig.pose_mode = None
                    fig.air_pose = "tuck"
                    fig.jump(0.0, -700.0)
                    fig.tumble_spin = side * 10.0
            if reaction == "shield" and gap < 110:
                cx, cy = (rx, ry) if ours else (fx, fy)
                self.fx.shield(cx, cy, magic_color(rival.who) if ours else OUR_MAGIC, 0.55 * H)
                if ours:
                    rival.pose("block")
                else:
                    fig.pose_mode = "block"
                reaction = "shielded"
            if gap <= (0.55 * H - TORSO * H if reaction == "shielded" else 0):
                if reaction == "jump":
                    self.fx.fizzle(orb)  # sails underneath
                    break
                self.fx.pop(orb)
                if reaction == "hit":
                    if ours:
                        rival.hit(side, 460.0)
                        fig.events.append(("spar", "hit", rival.who))
                    else:
                        fig.pose_mode = "hurt"
                        fig.body.velocity = (-side * 420.0, fig.body.velocity.y)
                        fig.events.append(("spar", "hurt", rival.who))
                break
            yield
        yield from self._idle(0.35)

    def _finisher(self, win: bool) -> Task:
        rival, side, _, _ = self._fight
        fig = self.fig
        who = rival.who
        style = random.choice(("uppercut", "magic") if not fig.weapon else ("slash", "magic"))
        if win:
            if style == "magic":
                yield from self._gap_to(300)
                fig.pose_mode = "cast"
                yield from self._idle(0.2)
                x, y = self._strike_point(True, "cast")
                orb = self.fx.orb(x, y, side * 1100.0, OUR_MAGIC, size=1.8)
                while orb.alive and (rival.x - self.cfg.figure_height * TORSO * side - orb.x) * side > 0:
                    yield
                self.fx.pop(orb)
            else:
                yield from self._gap_to(self._reach(True, style) + TORSO * self.cfg.figure_height)
                fig.pose_mode = style
                yield from self._idle(0.13)
                x, y = self._strike_point(True, style)
                self.fx.spark(x, y, size=1.8)
            rival.launch(side)
            fig.events.append(("spar", "win", who))
            yield from self._idle(0.5)
            fig.pose_mode = "victory"
            yield from self._idle(1.4)
        else:
            move = self._pick_strike(False) if rival.weapon else "kick"
            yield from self._gap_to(self._reach(False, move) + TORSO * self.cfg.figure_height)
            rival.pose(move)
            yield from self._idle(0.13)
            x, y = self._strike_point(False, move)
            self.fx.spark(x, y, size=1.6)
            fig.pose_mode = None
            fig.jump(-side * 380.0, -520.0)
            fig.tumble_spin = -side * 9.0
            fig.events.append(("spar", "lose", who))
            yield from self._await_landing(None)
            rival.pose("taunt")
            yield from self._idle(1.3)
            rival.vanish()

