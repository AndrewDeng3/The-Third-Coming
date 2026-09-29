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
from stickfigure.figure.effects import Effects, Fighter
from stickfigure.figure.rival import _Puppet, pick_character
from stickfigure.names import NAME
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
        H = self.cfg.figure_height
        me = Fighter(NAME, OUR_MAGIC)
        them = Fighter(who.name, magic_color(who))
        self.fx.bars = (me, them) if side > 0 else (them, me)  # bars sit on the same sides as the fighters
        self._me_right = side < 0
        mid = (fig.body.position.x + rival.x) / 2
        self.fx.bars_at = (mid, fig.body.position.y - 2.4 * H)
        self._round = getattr(self, "_round", 0) + 1
        self._fclock = self._last_ghost = 0.0
        try:
            fig.walk_to(None)
            fig.facing = side
            fig.pose_mode = self._stance(fig.weapon)
            rival.pose(self._stance(rival.weapon))
            self.fx.banner(f"ROUND {self._round}", mid, fig.body.position.y - 1.6 * H, life=0.9)
            yield from self._idle(0.9)
            self.fx.banner("FIGHT!", mid, fig.body.position.y - 1.6 * H, (255, 90, 60), 1.2, life=0.8)
            yield from self._idle(0.6)
            moves = [(3.0, self._x_strike), (1.5, self._x_combo), (1.5, self._x_jumpkick), (1.0, self._x_sweep),
                     (1.0, self._x_dodge), (1.5, self._x_magic), (1.0, self._x_airclash), (1.0, self._x_clash),
                     (1.5, self._x_dash), (1.2, self._x_flurry), (1.0, self._x_beam)]
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
            self.fx.bars = None

    # -- drama ----------------------------------------------------------------------------------------------------

    def _trail(self) -> None:
        """Afterimages for whoever's moving fast (called every frame during dashes, jumps and knockbacks)."""
        self._fclock = getattr(self, "_fclock", 0.0) + self.dt
        if self._fclock - getattr(self, "_last_ghost", 0.0) < 0.04 or self._fight is None:
            return
        self._last_ghost = self._fclock
        rival = self._fight[0]
        if abs(rival.vx) + abs(rival.vy) + abs(rival.puppet.body.velocity.x) > 260:
            self.fx.ghost(dict(rival.anim.pose), *rival.puppet.body.position, rival.who.color)
        v = self.fig.body.velocity
        snap = self.pose_snapshot()
        if snap is not None and abs(v.x) + abs(v.y) > 260:
            pose, (x, y), color = snap
            self.fx.ghost(pose, x, y, color)

    def _moving(self, seconds: float) -> Task:
        """Like _idle, but leaving afterimages behind fast movers."""
        t = seconds
        while t > 0:
            t -= self.dt
            self._trail()
            yield

    def _hitstop(self, seconds: float = 0.09) -> Task:
        """The split-second freeze on a heavy blow that sells the impact."""
        yield from self._idle(seconds)

    def _hurt_them(self, amount: float) -> None:
        self.fx.damage(not self._me_right, amount)

    def _hurt_me(self, amount: float) -> None:
        self.fx.damage(self._me_right, amount)

    def _dust_at(self, x: float) -> None:
        self.fx.dust(x, self.fig.feet[1])

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
        rival.walk_to(None)  # (a leftover target would fight any later movement, e.g. a dash)
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
        big = move in ("kick", "uppercut", "slash", "thrust")
        self.fx.spark(x, y, size=1.3 if big else 1.0)
        if reaction != "block" and big:
            self.fx.impact(x, y)
            yield from self._hitstop()
        dmg = 2.0 if reaction == "block" else (12.0 if big else 8.0)
        (self._hurt_them if ours else self._hurt_me)(dmg)
        if ours:
            if reaction == "block":
                rival.pose("block")
                rival.vx = side * 150.0
            else:
                rival.hit(side, 520.0 if big else 360.0)
                fig.events.append(("spar", "hit", rival.who))
                self.fx.dust(rival.x, rival.floor_y)
        else:
            if reaction == "block":
                fig.pose_mode = "block"
                fig.body.velocity = (-side * 150.0, fig.body.velocity.y)
            else:
                fig.pose_mode = "hurt"
                fig.body.velocity = (-side * (460.0 if big else 320.0), fig.body.velocity.y)
                fig.events.append(("spar", "hurt", rival.who))
                self._dust_at(fig.body.position.x)
        yield from self._moving(0.3)

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
        self._dust_at(fig.body.position.x if ours else rival.x)
        t = 0.0
        while t < 1.5:
            t += self.dt
            self._trail()
            x, y = self._strike_point(ours, "flykick")
            (fx, fy), (rx, ry) = self._centers()
            front = (rx - side * TORSO * H) if ours else (fx + side * TORSO * H)
            if (x - front) * (side if ours else -side) >= 0:
                self.fx.spark(x, y, size=1.3)
                if not block:
                    self.fx.impact(x, y, 1.2)
                (self._hurt_them if ours else self._hurt_me)(3.0 if block else 14.0)
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
            self._trail()
            (fx, fy), (rx, ry) = self._centers()
            if abs(rx - fx) <= 2 * reach:
                mx, my = (fx + rx) / 2, (self._strike_point(True, "flykick")[1] + self._strike_point(False, "flykick")[1]) / 2
                self.fx.spark(mx, my, size=1.6)
                self.fx.impact(mx, my, 1.4)
                fig.body.velocity = (-side * 360.0, fig.body.velocity.y)
                rival.vx = side * 360.0
                break
            yield
        yield from self._moving(0.4)

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
        self.fx.impact((ax + bx) / 2, (ay + by) / 2, 1.2)
        yield from self._hitstop(0.12)  # locked together for a beat
        fig.body.velocity = (-side * 280.0, fig.body.velocity.y)
        rival.vx = side * 280.0
        yield from self._moving(0.35)

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
        cx, cy = self._strike_point(ours, "cast")
        self.fx.aura(cx, cy, color, 0.35 * H, life=0.4)
        yield from self._idle(0.4)
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
                (self._hurt_them if ours else self._hurt_me)(3.0 if reaction == "shielded" else 16.0)
                if reaction == "hit":
                    self.fx.impact(orb.x, orb.y, 1.2)
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

    def _x_dash(self, ours: bool) -> Task:
        """Back off, then rocket in with afterimages and land a heavy blow."""
        rival, side, lo, hi = self._fight
        fig = self.fig
        move = self._pick_strike(ours) if (fig.weapon if ours else rival.weapon) else random.choice(("punch", "kick"))
        H = self.cfg.figure_height
        yield from self._gap_to(min(420.0, hi - lo - 40))
        target_gap = self._reach(ours, move) + TORSO * H
        if ours:
            self._dust_at(fig.body.position.x)
            goal = rival.x - side * target_gap
            while (goal - fig.body.position.x) * side > 3:
                step = min(abs(goal - fig.body.position.x), 1100.0 * self.dt)
                fig.body.position = (fig.body.position.x + side * step, fig.body.position.y)
                fig.body.velocity = (side * 1100.0, fig.body.velocity.y)
                fig.pose_mode = "punch" if move == "punch" else None
                self._trail()
                yield
            fig.body.velocity = (0.0, fig.body.velocity.y)
            self.world.space.reindex_shapes_for_body(fig.body)
        else:
            self.fx.dust(rival.x, rival.floor_y)
            goal = fig.body.position.x + side * target_gap
            while (rival.x - goal) * side > 3:
                step = min(abs(rival.x - goal), 1100.0 * self.dt)
                rival.x -= side * step
                rival.puppet.body.velocity.x = -side * 1100.0
                self._trail()
                yield
        yield from self._land_blow(ours, move, "block" if random.random() < 0.25 else "hit")

    def _x_flurry(self, ours: bool) -> Task:
        """A barrage of rapid punches, then a launcher that knocks them up into the air."""
        rival, side, _, _ = self._fight
        fig = self.fig
        H = self.cfg.figure_height
        yield from self._gap_to(self._reach(ours, "punch") + TORSO * H)
        for i in range(random.randint(5, 8)):
            if ours:
                fig.pose_mode = "punch" if i % 2 == 0 else "stance"
                rival.pose("block" if i < 3 else "hurt")
            else:
                rival.pose("punch" if i % 2 == 0 else "stance")
                fig.pose_mode = "block" if i < 3 else "hurt"
            yield from self._idle(0.07)
            if i % 2 == 0:
                x, y = self._strike_point(ours, "punch")
                self.fx.spark(x, y + random.uniform(-12, 12), size=0.8)
                (self._hurt_them if ours else self._hurt_me)(1.0 if i < 3 else 3.0)
        # the launcher
        if ours:
            fig.pose_mode = "uppercut"
        else:
            rival.pose("uppercut")
        yield from self._idle(0.12)
        x, y = self._strike_point(ours, "uppercut")
        self.fx.spark(x, y, size=1.5)
        self.fx.impact(x, y, 1.4)
        yield from self._hitstop(0.1)
        (self._hurt_them if ours else self._hurt_me)(10.0)
        if ours:
            rival.air(side * 180.0, -760.0, "tuck", spin=side * 9.0)
        else:
            fig.pose_mode = None
            fig.air_pose = "tuck"
            fig.jump(-side * 180.0, -760.0)
            fig.tumble_spin = -side * 9.0
        yield from self._moving(0.7)

    def _x_beam(self, ours: bool) -> Task:
        """Charge up (aura), then an energy beam across to the other fighter: blocked by a shield, or a hit."""
        rival, side, _, _ = self._fight
        fig = self.fig
        H = self.cfg.figure_height
        yield from self._gap_to(random.uniform(300, 420))
        color = OUR_MAGIC if ours else magic_color(rival.who)
        if ours:
            fig.pose_mode = "cast"
        else:
            rival.pose("cast")
        x, y = self._strike_point(ours, "cast")
        self.fx.aura(x, y, color, 0.45 * H, life=0.8)
        yield from self._idle(0.8)
        x, y = self._strike_point(ours, "cast")
        (fx, fy), (rx, ry) = self._centers()
        tx = (rx - side * TORSO * H) if ours else (fx + side * TORSO * H)
        shield = random.random() < 0.45
        if shield:
            cx, cy = (rx, ry) if ours else (fx, fy)
            tx = cx - (side if ours else -side) * 0.55 * H
            self.fx.shield(cx, cy, magic_color(rival.who) if ours else OUR_MAGIC, 0.55 * H)
            if ours:
                rival.pose("block")
            else:
                fig.pose_mode = "block"
        self.fx.beam(x, y, tx, y, color, life=0.7)
        yield from self._idle(0.25)
        self.fx.burst(tx, y, color, 1.4)
        (self._hurt_them if ours else self._hurt_me)(4.0 if shield else 18.0)
        if not shield:
            self.fx.impact(tx, y, 1.3)
            if ours:
                rival.hit(side, 560.0)
                fig.events.append(("spar", "hit", rival.who))
            else:
                fig.pose_mode = "hurt"
                fig.body.velocity = (-side * 480.0, fig.body.velocity.y)
                fig.events.append(("spar", "hurt", rival.who))
        yield from self._moving(0.45)

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
            self.fx.impact(*self._strike_point(True, "cast" if style == "magic" else style), 2.0)
            self.fx.slowmo(1.1, 0.3)
            yield from self._hitstop(0.18)
            self._hurt_them(100.0)
            rival.launch(side)
            fig.events.append(("spar", "win", who))
            self.fx.banner("K.O.!", (fig.body.position.x + rival.x) / 2, fig.body.position.y - 1.6 * self.cfg.figure_height,
                           (255, 80, 60), 1.4, life=1.6)
            yield from self._moving(0.6)
            fig.pose_mode = "victory"
            yield from self._idle(1.6)
        else:
            move = self._pick_strike(False) if rival.weapon else "kick"
            yield from self._gap_to(self._reach(False, move) + TORSO * self.cfg.figure_height)
            rival.pose(move)
            yield from self._idle(0.13)
            x, y = self._strike_point(False, move)
            self.fx.spark(x, y, size=1.6)
            self.fx.impact(x, y, 1.8)
            self.fx.slowmo(0.9, 0.35)
            yield from self._hitstop(0.15)
            self._hurt_me(100.0)
            self.fx.banner("K.O.", (fig.body.position.x + rival.x) / 2, fig.body.position.y - 1.6 * self.cfg.figure_height,
                           (200, 200, 220), 1.2, life=1.6)
            fig.pose_mode = None
            fig.jump(-side * 380.0, -520.0)
            fig.tumble_spin = -side * 9.0
            fig.events.append(("spar", "lose", who))
            yield from self._await_landing(None)
            rival.pose("taunt")
            yield from self._idle(1.3)
            rival.vanish()

