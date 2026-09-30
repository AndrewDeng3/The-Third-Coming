"""Sparring matches with the legends (the fight choreography), mixed into Brain.

A match uses the whole screen: the rival arrives (sometimes crashing down from the sky), both sprint at each other
and clash, then they trade exchanges - strikes, combos, sweeps, flying kicks, dodges, magic orbs and beams, dashes,
and sky battles where both take off and fight in mid-air (clashing passes, zip-through strikes, knock-aways across
the screen that bounce off its edges) until someone gets spiked back down into the ground. Once the loser's health
runs low they get finished off, on the ground or with a launch-chase-spike aerial finisher.

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
        # As far apart as the surface allows (it's a whole-screen fight), but at least 320 px.
        room = {1: hi - fx, -1: fx - lo}
        side = random.choice((-1, 1))
        if room[side] < 320:
            side = -side
            if room[side] < 320:
                return False  # no room to fight here
        dist = max(320.0, min(room[side] - 20, random.uniform(560, 900)))
        who = pick_character()
        rival = self.make_rival(who, fx + side * dist, fig.feet[1], -side)
        if rival is None:
            return False
        self._fight = (rival, side, lo, hi)
        rival.bounds = (lo, hi)
        self._ground_cy = fig.body.position.y  # the figure's body center standing on the arena floor
        if random.random() < 0.5:  # a weapons match
            fig.weapon = random.choice(("sword", "pickaxe"))
            rival.weapon = who.weapon or "sword"
        fig.events.append(("spar", "start", who))
        H = self.cfg.figure_height
        me = Fighter(NAME, OUR_MAGIC)
        them = Fighter(who.name, magic_color(who))
        self.fx.bars = (me, them) if side > 0 else (them, me)  # bars sit on the same sides as the fighters
        self._me_right = side < 0
        x0, x1, y0, _ = self._sky()
        self.fx.bars_at = ((x0 + x1) / 2, y0 - 0.3 * H)  # top of the screen: the whole screen is the arena
        self._round = getattr(self, "_round", 0) + 1
        self._fclock = self._last_ghost = 0.0
        self._win = random.random() < 0.7
        fig.speed_override = 620.0  # fighters sprint
        try:
            fig.walk_to(None)
            fig.facing = side
            fig.pose_mode = self._stance(fig.weapon)
            if random.random() < 0.5:
                yield from self._sky_entrance()
            rival.pose(self._stance(rival.weapon))
            mid = (fig.body.position.x + rival.x) / 2
            self.fx.banner(f"ROUND {self._round}", mid, fig.body.position.y - 1.6 * H, life=0.9)
            yield from self._idle(0.9)
            self.fx.banner("FIGHT!", mid, fig.body.position.y - 1.6 * H, (255, 90, 60), 1.2, life=0.8)
            yield from self._idle(0.35)
            yield from self._x_charge()
            yield from self._settle()
            moves = [(3.0, self._x_strike), (1.5, self._x_combo), (1.4, self._x_jumpkick), (1.0, self._x_sweep),
                     (1.0, self._x_dodge), (1.3, self._x_magic), (0.8, self._x_airclash), (0.8, self._x_clash),
                     (1.5, self._x_dash), (1.2, self._x_flurry), (1.0, self._x_beam), (2.6, self._x_skyfight),
                     (1.6, self._x_sendoff), (1.3, self._x_juggle)]
            loser = self._bar(not self._win)
            for n in range(12):
                if n >= 4 and loser is not None and loser.hp <= 30:
                    break
                ours = random.random() < (0.6 if self._win else 0.4)  # the eventual winner presses more
                total = sum(w for w, _ in moves)
                r = random.uniform(0, total)
                for w, move in moves:
                    r -= w
                    if r <= 0:
                        yield from move(ours)
                        break
                yield from self._settle()
                yield from self._idle(random.uniform(0.15, 0.4))
            yield from self._finisher(self._win)
            return True
        finally:
            fig.pose_mode = fig.air_pose = fig.weapon = None
            fig.speed_override = None
            fig.tilt = 0.0
            fig.land()
            fig.walk_to(None)
            if not rival.fading:
                rival.vanish()
            self._fight = None
            self.fx.bars = None

    def _sky_entrance(self) -> Task:
        """The rival drops in from high above, spinning, and lands hard (dust, shockwave), then taunts."""
        rival = self._fight[0]
        _, _, y0, _ = self._sky()
        rival.lift = max(0.0, rival.floor_y - rival.half - y0)
        rival._sync()
        rival.fly((rival.x, rival.floor_y - rival.half), 1900)
        rival.puppet.air_pose = "tuck"
        rival.puppet.tumble_spin = random.choice((-1, 1)) * 12.0
        t = 0.0
        while rival.lift > 1 and t < 2.0:
            t += self.dt
            self._trail()
            yield
        rival.land()
        rival.puppet.tumble_spin = rival.puppet.tumble_angle = 0.0
        rival.puppet.air_pose = None
        self._crash(rival.x, rival.floor_y, 1.4)
        rival.pose("land")
        yield from self._idle(0.45)
        rival.pose("taunt")
        yield from self._idle(0.6)

    # -- drama ----------------------------------------------------------------------------------------------------

    def _trail(self) -> None:
        """Afterimages for whoever's moving fast (called every frame during dashes, jumps and knockbacks)."""
        if self._fight is None:
            return
        self._air_style()
        self._keep_in_sky()
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

    def _bar(self, mine: bool) -> Fighter | None:
        if self.fx.bars is None:
            return None
        return self.fx.bars[1 if (self._me_right if mine else not self._me_right) else 0]

    def _hurt(self, mine: bool, amount: float) -> None:
        """Take health off a bar. The match's winner never drops below 22, and the loser keeps a sliver for the
        finisher (which does 100)."""
        f = self._bar(mine)
        if f is None:
            return
        winner = mine == getattr(self, "_win", True)
        keep = 0.0 if amount >= 100 else (22.0 if winner else 5.0)
        f.hp = max(f.hp - amount, min(f.hp, keep))

    def _hurt_them(self, amount: float) -> None:
        self._hurt(False, amount)

    def _hurt_me(self, amount: float) -> None:
        self._hurt(True, amount)

    def _crash(self, x: float, floor_y: float, size: float = 1.0) -> None:
        """Someone hits the ground hard: dust thrown both ways and an impact ring."""
        self.fx.dust(x - 30 * size, floor_y, size)
        self.fx.dust(x + 30 * size, floor_y, size)
        self.fx.impact(x, floor_y - 10, size)

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
        """Run both fighters (toward/away from their midpoint) until their centers are `gap` apart."""
        self._resync_side()
        rival, side, lo, hi = self._fight
        fig = self.fig
        fig.pose_mode = None
        rival.pose(None)
        mid = min(max((fig.body.position.x + rival.x) / 2, lo + gap / 2), hi - gap / 2)
        mine, theirs = mid - side * gap / 2, mid + side * gap / 2
        fig.walk_to(mine, run=True)
        rival.walk_to(theirs)
        t = 0.0
        while t < 4.0 and (abs(fig.body.position.x - mine) > 4 or abs(rival.x - theirs) > 4):
            t += self.dt
            self._trail()
            yield
        fig.walk_to(None)
        rival.walk_to(None)  # (a leftover target would fight any later movement, e.g. a dash)
        fig.facing = side
        rival.puppet.facing = -side
        yield from self._settle()

    def _settle(self) -> Task:
        """Back to fighting stances (after landing if anyone's in the air)."""
        if self.fig.flying or self._fight[0].flying:
            yield from self._descend()
        self._resync_side()
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

    def _resync_side(self) -> None:
        """Fighters can swap sides (zip-through strikes, flying): `side` always points from us to the rival."""
        rival, side, lo, hi = self._fight
        dx = rival.x - self.fig.body.position.x
        if abs(dx) > 1:
            self._fight = (rival, 1 if dx > 0 else -1, lo, hi)

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
        rival, side, _, _ = self._fight
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
        rival, side, _, _ = self._fight
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
        rival, side, _, _ = self._fight
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
        rival, side, _, _ = self._fight
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

    def _x_charge(self) -> Task:
        """The opener: both sprint at each other from across the screen and collide in a ground-shaking clash."""
        rival = self._fight[0]
        self._dust_at(self.fig.body.position.x)
        self.fx.dust(rival.x, rival.floor_y)
        yield from self._x_clash(True, big=True)

    def _x_clash(self, _: bool, big: bool = False) -> Task:
        """Both strike at once and the blows meet in the middle (blade on blade, or fist on fist)."""
        rival, side, _, _ = self._fight
        fig = self.fig
        mine = "slash" if fig.weapon else "punch"
        theirs = "slash" if rival.weapon else "punch"
        yield from self._gap_to(self._reach(True, mine) + self._reach(False, theirs))  # tips just touch
        rival, side, _, _ = self._fight
        fig.pose_mode = mine
        rival.pose(theirs)
        yield from self._idle(0.12)
        (ax, ay), (bx, by) = self._strike_point(True, mine), self._strike_point(False, theirs)
        self.fx.spark((ax + bx) / 2, (ay + by) / 2, size=1.8 if big else 1.5)
        self.fx.impact((ax + bx) / 2, (ay + by) / 2, 1.8 if big else 1.2)
        if big:
            self._crash((ax + bx) / 2, rival.floor_y, 1.3)
            self.fx.slowmo(0.3, 0.4)
        yield from self._hitstop(0.2 if big else 0.12)  # locked together for a beat
        push = 460.0 if big else 280.0
        fig.body.velocity = (-side * push, fig.body.velocity.y)
        rival.vx = side * push
        yield from self._moving(0.35)

    def _x_magic(self, ours: bool) -> Task:
        """An energy orb from the caster's hand; the defender shields, jumps it, or takes it."""
        rival, side, lo, hi = self._fight
        fig = self.fig
        H = self.cfg.figure_height
        yield from self._gap_to(min(random.uniform(280, 620), hi - lo - 20))
        rival, side, _, _ = self._fight
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
        yield from self._gap_to(min(random.uniform(420, 720), hi - lo - 40))
        rival, side, _, _ = self._fight
        target_gap = self._reach(ours, move) + TORSO * H
        if ours:
            self._dust_at(fig.body.position.x)
            goal = rival.x - side * target_gap
            while (goal - fig.body.position.x) * side > 3:
                step = min(abs(goal - fig.body.position.x), 1700.0 * self.dt)
                fig.body.position = (fig.body.position.x + side * step, fig.body.position.y)
                fig.body.velocity = (side * 1700.0, fig.body.velocity.y)
                fig.pose_mode = "punch" if move == "punch" else None
                self._trail()
                yield
            fig.body.velocity = (0.0, fig.body.velocity.y)
            self.world.space.reindex_shapes_for_body(fig.body)
        else:
            self.fx.dust(rival.x, rival.floor_y)
            goal = fig.body.position.x + side * target_gap
            while (rival.x - goal) * side > 3:
                step = min(abs(rival.x - goal), 1700.0 * self.dt)
                rival.x -= side * step
                rival.puppet.body.velocity.x = -side * 1700.0
                self._trail()
                yield
        yield from self._land_blow(ours, move, "block" if random.random() < 0.25 else "hit")

    def _x_flurry(self, ours: bool) -> Task:
        """A barrage of rapid punches, then a launcher that knocks them up into the air."""
        rival, side, _, _ = self._fight
        fig = self.fig
        H = self.cfg.figure_height
        yield from self._gap_to(self._reach(ours, "punch") + TORSO * H)
        rival, side, _, _ = self._fight
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
        rival, side, lo, hi = self._fight
        fig = self.fig
        H = self.cfg.figure_height
        yield from self._gap_to(min(random.uniform(300, 760), hi - lo - 20))
        rival, side, _, _ = self._fight
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

    # -- flight --------------------------------------------------------------------------------------------------

    def _sky(self) -> tuple[float, float, float, float]:
        """Where body centers may fly: (x0, x1, y_top, y_low) - the whole monitor above the arena floor."""
        H = self.cfg.figure_height
        fx = self.fig.body.position.x
        ground = getattr(self, "_ground_cy", self.fig.body.position.y)
        mons = self.world.monitors
        if not mons:
            return fx - 700, fx + 700, ground - 700, ground - 1.2 * H
        w = next((m for m in mons if m.work.left <= fx < m.work.right), mons[0]).work
        return w.left + 0.5 * H, w.right - 0.5 * H, min(w.top + 1.0 * H, ground - 1.6 * H), ground - 1.2 * H

    def _clamp_sky(self, x: float, y: float) -> tuple[float, float]:
        x0, x1, y0, y1 = self._sky()
        return min(max(x, x0), x1), min(max(y, y0), y1)

    def _set(self, ours: bool, name: str | None) -> None:
        """Pose a fighter, standing or in the air."""
        if ours:
            if self.fig.flying or not self.fig.grounded:
                self.fig.air_pose, self.fig.tilt = name, 0.0
            else:
                self.fig.pose_mode = name
        else:
            r = self._fight[0]
            if r.flying or r.lift > 0:
                r.pose(None)
                r.puppet.air_pose, r.puppet.tilt = name, 0.0
            else:
                r.pose(name)

    def _air_style(self) -> None:
        """Flyers lean into fast flight (superhero style) and straighten up as they slow down."""
        rival = self._fight[0]
        for flying, puppet, (vx, vy) in ((self.fig.flying, self.fig, tuple(self.fig.body.velocity)),
                                          (rival.flying, rival.puppet, (rival.vx, rival.vy))):
            if not flying or puppet.air_pose != "fly":
                continue
            if math.hypot(vx, vy) > 220:
                if abs(vx) > 40:
                    puppet.facing = 1 if vx > 0 else -1
                puppet.tilt = max(-2.3, min(2.3, math.atan2(vx, -vy)))
            else:
                puppet.tilt *= 0.8

    def _keep_in_sky(self) -> None:
        """Fighters knocked flying bounce off the screen edges (with a thud) instead of leaving the screen."""
        H = self.cfg.figure_height
        x0, x1, y0, _ = self._sky()
        x0, x1 = x0 - 0.3 * H, x1 + 0.3 * H
        y0, y1 = y0 - 0.6 * H, getattr(self, "_ground_cy", y0 + 600) - 0.3 * H
        rival, fig = self._fight[0], self.fig
        if fig.flying and fig.fly_target is None:
            (x, y), (vx, vy) = tuple(fig.body.position), tuple(fig.body.velocity)
            if (x < x0 and vx < 0) or (x > x1 and vx > 0):
                vx = -vx * 0.45
                self.fx.impact(x, y, 1.0)
            if (y < y0 and vy < 0) or (y > y1 and vy > 0):
                vy = -vy * 0.45
            fig.body.velocity = (vx, vy)
        if rival.flying and rival.fly_target is None:
            if (rival.x < x0 and rival.vx < 0) or (rival.x > x1 and rival.vx > 0):
                rival.vx = -rival.vx * 0.45
                self.fx.impact(rival.x, rival.y, 1.0)
            if (rival.y < y0 and rival.vy < 0) or (rival.y > y1 and rival.vy > 0):
                rival.vy = -rival.vy * 0.45

    def _arrived(self, tol: float = 8.0) -> bool:
        rival, fig = self._fight[0], self.fig
        if fig.flying and fig.fly_target is not None and math.dist(tuple(fig.body.position), fig.fly_target) > tol:
            return False
        return not (rival.flying and rival.fly_target is not None
                    and math.dist((rival.x, rival.y), rival.fly_target) > tol)

    def _fly_until(self, done, timeout: float) -> Task:
        t = 0.0
        while t < timeout and not done():
            t += self.dt
            self._trail()
            yield

    def _face_off(self) -> None:
        """Both face each other; whoever's flying hovers in a fighting stance."""
        self._resync_side()
        rival, side, _, _ = self._fight
        fig = self.fig
        fig.facing, rival.puppet.facing = side, -side
        if fig.flying:
            fig.air_pose, fig.tilt = "hover", 0.0
            fig.tumble_spin = fig.tumble_angle = 0.0
        if rival.flying:
            rival.pose(None)
            rival.puppet.air_pose, rival.puppet.tilt = "hover", 0.0
            rival.puppet.tumble_spin = rival.puppet.tumble_angle = 0.0

    def _park(self, fig_at: tuple[float, float] | None, rival_at: tuple[float, float] | None) -> None:
        """Stop flyers exactly on their marks (so the next blow lines up to the pixel)."""
        fig, rival = self.fig, self._fight[0]
        if fig_at is not None and fig.flying:
            fig.body.position = fig_at
            fig.body.velocity = (0.0, 0.0)
            fig.fly(fig_at, fig.fly_speed)
            self.world.space.reindex_shapes_for_body(fig.body)
        if rival_at is not None and rival.flying:
            rival.x = rival_at[0]
            rival.lift = max(0.0, rival.floor_y - rival.half - rival_at[1])
            rival.vx = rival.vy = 0.0
            rival.fly(rival_at, rival.fly_speed)
            rival._sync()

    def _take_off(self) -> Task:
        """Both blast off the ground into the sky."""
        self._resync_side()
        rival, side, _, _ = self._fight
        fig = self.fig
        _, _, y0, y1 = self._sky()
        alt = random.uniform(y0, (y0 + y1) / 2)
        self._dust_at(fig.body.position.x)
        self.fx.dust(rival.x, rival.floor_y)
        fig.pose_mode = None
        fig.fly(self._clamp_sky(fig.body.position.x - side * 90, alt + random.uniform(-50, 50)), 1400)
        fig.air_pose = "fly"
        rival.pose(None)
        rival.fly(self._clamp_sky(rival.x + side * 90, alt + random.uniform(-50, 50)), 1400)
        rival.puppet.air_pose = "fly"
        yield from self._fly_until(self._arrived, 1.8)
        self._face_off()
        yield from self._idle(0.2)

    def _meet_in_air(self, gap: float, speed: float = 1600.0) -> Task:
        """Both fly at each other and stop level, facing each other, with centers exactly `gap` apart."""
        self._resync_side()
        rival, side, _, _ = self._fight
        fig = self.fig
        x0, x1, y0, y1 = self._sky()
        (fx, fy), (rx, ry) = self._centers()
        mx = min(max((fx + rx) / 2 + random.uniform(-220, 220), x0 + gap / 2), x1 - gap / 2)
        y = min(max((fy + ry) / 2 + random.uniform(-160, 160), y0), y1)
        mine, theirs = (mx - side * gap / 2, y), (mx + side * gap / 2, y)
        fig.fly(mine, speed)
        fig.air_pose = "fly"
        rival.fly(theirs, speed)
        rival.pose(None)
        rival.puppet.air_pose = "fly"
        yield from self._fly_until(lambda: self._arrived(6.0), 2.5)
        self._park(mine, theirs)
        self._face_off()

    def _close_on(self, ours: bool, gap: float, dy: float = 0.0, speed: float = 1800.0) -> Task:
        """The attacker flies after the defender (who may still be sailing through the air) and stops `gap` in front
        of them (and `dy` above/below); then both freeze on their marks."""
        rival, fig = self._fight[0], self.fig
        if ours and not fig.flying:
            fig.pose_mode = None
            self._dust_at(fig.body.position.x)
        if not ours and not rival.flying:
            rival.pose(None)
            self.fx.dust(rival.x, rival.floor_y)
        t = 0.0
        while t < 3.0:
            (fx, fy), (rx, ry) = self._centers()
            if ours:
                d = 1 if rx >= fx else -1
                goal = (rx - d * gap, ry + dy)
                fig.fly(goal, speed)
                fig.air_pose = "fly"
                here, victim_speed = (fx, fy), abs(rival.vx) + abs(rival.vy)
            else:
                d = 1 if fx >= rx else -1
                goal = (fx - d * gap, fy + dy)
                rival.fly(goal, speed)
                rival.pose(None)
                rival.puppet.air_pose = "fly"
                here, victim_speed = (rx, ry), abs(fig.body.velocity.x) + abs(fig.body.velocity.y)
            if math.dist(here, goal) < 10 and victim_speed < 220:
                break
            t += self.dt
            self._trail()
            yield
        (fx, fy), (rx, ry) = self._centers()
        if ours:
            d = 1 if rx >= fx else -1
            self._park((rx - d * gap, ry + dy), (rx, ry))
        else:
            d = 1 if fx >= rx else -1
            self._park((fx, fy), (fx - d * gap, fy + dy))
        self._face_off()
        self._set(not ours, "hurt")

    def _air_blow(self, ours: bool, move: str, reaction: str, knock: float = 560.0) -> Task:
        """A strike in mid-air (both already spaced): blocked, or a hit that sends the defender sailing."""
        rival, side, _, _ = self._fight
        fig = self.fig
        self._set(ours, move)
        yield from self._idle(0.1)
        x, y = self._strike_point(ours, move)
        big = move in ("kick", "uppercut", "slash", "thrust", "divepunch")
        self.fx.spark(x, y, size=1.3 if big else 1.0)
        if reaction == "hit":
            self.fx.impact(x, y, 1.2 if big else 0.9)
            yield from self._hitstop()
        self._hurt(not ours, 2.0 if reaction == "block" else (12.0 if big else 8.0))
        d = side if ours else -side  # the direction the defender gets pushed
        push = 170.0 if reaction == "block" else knock
        self._set(not ours, "block" if reaction == "block" else "hurt")
        if ours:
            rival.fly(None)
            rival.vx, rival.vy = d * push, -60.0
            if reaction == "hit":
                fig.events.append(("spar", "hit", rival.who))
        else:
            fig.fly(None)
            fig.body.velocity = (d * push, -60.0)
            if reaction == "hit":
                fig.events.append(("spar", "hurt", rival.who))
        yield from self._moving(0.35)

    def _air_clash(self) -> Task:
        """Both swing in mid-air, the blows meet between them, and they rebound apart."""
        fig = self.fig
        rival = self._fight[0]
        mine = "slash" if fig.weapon else random.choice(("punch", "kick"))
        theirs = "slash" if rival.weapon else random.choice(("punch", "kick"))
        yield from self._meet_in_air(self._reach(True, mine) + self._reach(False, theirs))
        rival, side, _, _ = self._fight
        self._set(True, mine)
        self._set(False, theirs)
        yield from self._idle(0.1)
        (ax, ay), (bx, by) = self._strike_point(True, mine), self._strike_point(False, theirs)
        self.fx.spark((ax + bx) / 2, (ay + by) / 2, size=1.6)
        self.fx.impact((ax + bx) / 2, (ay + by) / 2, 1.4)
        yield from self._hitstop(0.12)
        fig.fly(None)
        fig.body.velocity = (-side * 620.0, random.uniform(-200, 200))
        rival.fly(None)
        rival.vx, rival.vy = side * 620.0, random.uniform(-200, 200)
        yield from self._moving(0.4)

    def _zip_through(self, ours: bool) -> Task:
        """The attacker rockets straight through the defender, a spark as they pass, ends up behind them, and a
        beat later the hit registers (they've swapped sides)."""
        yield from self._meet_in_air(random.uniform(360, 480))
        rival, side, _, _ = self._fight
        fig = self.fig
        x0, x1, _, _ = self._sky()
        d = side if ours else -side
        (fx, fy), (rx, ry) = self._centers()
        vx0, vy0 = (rx, ry) if ours else (fx, fy)
        end = (min(max(vx0 + d * random.uniform(240, 320), x0), x1), vy0)
        pose = "slash" if (fig.weapon if ours else rival.weapon) else "flykick"
        if ours:
            fig.fly(end, 2600)
        else:
            rival.fly(end, 2600)
        self._set(ours, pose)
        passed, t = False, 0.0
        while t < 1.5:
            t += self.dt
            self._trail()
            ax = fig.body.position.x if ours else rival.x
            if not passed and (ax - vx0) * d >= 0:
                passed = True
                self.fx.spark(vx0, vy0, size=1.4)
            if passed and self._arrived(8.0):
                break
            yield
        yield from self._idle(0.15)  # the beat before the hit lands
        self.fx.impact(vx0, vy0, 1.4)
        self._hurt(not ours, 14.0)
        self._set(not ours, "hurt")
        if ours:
            rival.fly(None)
            rival.vx, rival.vy = -d * 260.0, 120.0
            fig.events.append(("spar", "hit", rival.who))
        else:
            fig.fly(None)
            fig.body.velocity = (-d * 260.0, 120.0)
            fig.events.append(("spar", "hurt", rival.who))
        yield from self._moving(0.35)
        self._face_off()

    def _spike(self, ours: bool, final: bool = False) -> Task:
        """The attacker gets above the defender and smashes them straight down into the ground (crater dust)."""
        rival, _, lo, hi = self._fight
        fig = self.fig
        H = self.cfg.figure_height
        move = "slash" if (fig.weapon if ours else rival.weapon) else "divepunch"
        yield from self._close_on(ours, self._reach(ours, move) + TORSO * H, dy=-0.25 * H)
        rival, side, _, _ = self._fight
        self._set(ours, move)
        yield from self._idle(0.1)
        x, y = self._strike_point(ours, move)
        self.fx.spark(x, y, size=1.7)
        self.fx.impact(x, y, 1.7)
        if final:
            self.fx.slowmo(0.8, 0.3)
        yield from self._hitstop(0.16 if final else 0.1)
        self._hurt(not ours, 100.0 if final else 14.0)
        d = side if ours else -side
        spin = d * 12.0
        if ours:
            gx = min(max(rival.x + d * 90, lo), hi)
            rival.fly((gx, rival.floor_y - rival.half), 2600)
            rival.pose(None)
            rival.puppet.air_pose = "tuck"
            rival.puppet.tumble_spin = spin
            fig.events.append(("spar", "hit", rival.who))
        else:
            gx = min(max(fig.body.position.x + d * 90, lo), hi)
            fig.fly((gx, self._ground_cy - 3), 2600)
            fig.air_pose = "tuck"
            fig.tumble_spin = spin
            fig.events.append(("spar", "hurt", rival.who))
        t = 0.0
        while t < 2.0:
            if ours and rival.lift <= 30:
                break
            if not ours and fig.body.position.y >= self._ground_cy - 30:
                break
            t += self.dt
            self._trail()
            yield
        if ours:
            rival.land()
            rival.puppet.tumble_spin = rival.puppet.tumble_angle = 0.0
            self._crash(gx, rival.floor_y, 2.2 if final else 1.7)
        else:
            fig.land()
            self._crash(gx, fig.feet[1] + 30, 2.2 if final else 1.7)
        yield from self._moving(0.3)
        self._set(not ours, "hurt")

    def _launch(self, ours: bool) -> Task:
        """A ground uppercut that sends the defender rocketing up into the sky."""
        H = self.cfg.figure_height
        yield from self._gap_to(self._reach(ours, "uppercut") + TORSO * H)
        rival, side, _, _ = self._fight
        fig = self.fig
        self._set(ours, "uppercut")
        yield from self._idle(0.12)
        x, y = self._strike_point(ours, "uppercut")
        self.fx.spark(x, y, size=1.5)
        self.fx.impact(x, y, 1.4)
        yield from self._hitstop(0.1)
        self._hurt(not ours, 10.0)
        d = side if ours else -side
        _, _, y0, y1 = self._sky()
        up = random.uniform(y0, (y0 + y1) / 2)
        if ours:
            rival.pose(None)
            rival.fly(self._clamp_sky(rival.x + d * 120, up), 1700)
            rival.puppet.air_pose = "tuck"
            rival.puppet.tumble_spin = d * 10.0
            self.fx.dust(rival.x, rival.floor_y)
            fig.events.append(("spar", "hit", rival.who))
        else:
            fig.pose_mode = None
            fig.fly(self._clamp_sky(fig.body.position.x + d * 120, up), 1700)
            fig.air_pose = "tuck"
            fig.tumble_spin = d * 10.0
            self._dust_at(fig.body.position.x)
            fig.events.append(("spar", "hurt", rival.who))
        yield from self._moving(0.25)

    def _descend(self) -> Task:
        """Flyers come back down onto the arena floor and touch down."""
        rival, _, lo, hi = self._fight
        fig = self.fig
        if fig.flying:
            fig.tumble_spin = fig.tumble_angle = 0.0
            fig.fly((min(max(fig.body.position.x, lo), hi), self._ground_cy - 3), 1300)
            fig.air_pose, fig.tilt = "hover", 0.0
        if rival.flying:
            rival.puppet.tumble_spin = rival.puppet.tumble_angle = 0.0
            rival.fly((min(max(rival.x, lo), hi), rival.floor_y - rival.half), 1300)
            rival.pose(None)
            rival.puppet.air_pose, rival.puppet.tilt = "hover", 0.0
        yield from self._fly_until(lambda: self._arrived(5.0), 2.5)
        if fig.flying:
            fig.land()
            self.fx.dust(fig.body.position.x, self._ground_cy + fig.half_extents[1], 0.7)
        if rival.flying:
            rival.land()
            self.fx.dust(rival.x, rival.floor_y, 0.7)

    # -- sky exchanges -------------------------------------------------------------------------------------------

    def _x_skyfight(self, ours: bool) -> Task:
        """Both take off and fight in the sky: clashing passes, strikes that send people sailing, zip-throughs -
        often ending with someone spiked back down into the ground."""
        H = self.cfg.figure_height
        yield from self._take_off()
        for i in range(random.randint(2, 4)):
            a = ours if i == 0 else random.random() < (0.6 if self._win else 0.4)
            kind = random.choice(("strike", "strike", "clash", "zip"))
            if kind == "clash":
                yield from self._air_clash()
            elif kind == "zip":
                yield from self._zip_through(a)
            else:
                move = self._pick_strike(a)
                yield from self._meet_in_air(self._reach(a, move) + TORSO * H)
                yield from self._air_blow(a, move, "block" if random.random() < 0.35 else "hit")
            yield from self._idle(0.12)
        if random.random() < 0.6:
            yield from self._spike(random.random() < (0.6 if self._win else 0.4))

    def _x_sendoff(self, ours: bool) -> Task:
        """A huge kick sends the defender flying across the screen (bouncing off its edge); the attacker flies
        after them and follows up in the air."""
        H = self.cfg.figure_height
        rival = self._fight[0]
        weapon = self.fig.weapon if ours else rival.weapon
        move = random.choice(WEAPON_STRIKES[weapon]) if weapon else "kick"
        yield from self._gap_to(self._reach(ours, move) + TORSO * H)
        rival, side, _, _ = self._fight
        fig = self.fig
        self._set(ours, move)
        yield from self._idle(0.11)
        x, y = self._strike_point(ours, move)
        self.fx.spark(x, y, size=1.5)
        self.fx.impact(x, y, 1.6)
        yield from self._hitstop(0.13)
        self._hurt(not ours, 12.0)
        d = side if ours else -side
        if ours:
            rival.pose(None)
            rival.fly(None)
            rival.vx, rival.vy = d * 1500.0, -420.0
            rival.puppet.air_pose = "tuck"
            rival.puppet.tumble_spin = d * 12.0
            fig.events.append(("spar", "hit", rival.who))
        else:
            fig.pose_mode = None
            fig.fly(None)
            fig.body.velocity = (d * 1500.0, -420.0)
            fig.air_pose = "tuck"
            fig.tumble_spin = d * 12.0
            fig.events.append(("spar", "hurt", rival.who))
        yield from self._moving(0.6)
        self._set(not ours, "hover")  # recovers in mid-air
        if ours:
            rival.puppet.tumble_spin = rival.puppet.tumble_angle = 0.0
        else:
            fig.tumble_spin = fig.tumble_angle = 0.0
        if random.random() < 0.5:
            yield from self._spike(ours)
        else:
            move = self._pick_strike(ours)
            yield from self._close_on(ours, self._reach(ours, move) + TORSO * H)
            yield from self._air_blow(ours, move, "hit")

    def _x_juggle(self, ours: bool) -> Task:
        """Launch them with an uppercut, chase them up, hit them again in mid-air, then spike them down."""
        H = self.cfg.figure_height
        yield from self._launch(ours)
        for _ in range(random.randint(1, 2)):
            move = self._pick_strike(ours)
            yield from self._close_on(ours, self._reach(ours, move) + TORSO * H)
            yield from self._air_blow(ours, move, "hit", knock=300.0)
        yield from self._spike(ours)

    def _finisher(self, win: bool) -> Task:
        if random.random() < 0.45:
            yield from self._aerial_finisher(win)
            return
        self._resync_side()
        rival, side, _, _ = self._fight
        fig = self.fig
        who = rival.who
        style = random.choice(("uppercut", "magic") if not fig.weapon else ("slash", "magic"))
        if win:
            if style == "magic":
                yield from self._gap_to(300)
                rival, side, _, _ = self._fight
                fig.pose_mode = "cast"
                yield from self._idle(0.2)
                x, y = self._strike_point(True, "cast")
                orb = self.fx.orb(x, y, side * 1700.0, OUR_MAGIC, size=1.8)
                while orb.alive and (rival.x - self.cfg.figure_height * TORSO * side - orb.x) * side > 0:
                    yield
                self.fx.pop(orb)
            else:
                yield from self._gap_to(self._reach(True, style) + TORSO * self.cfg.figure_height)
                rival, side, _, _ = self._fight
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
            rival, side, _, _ = self._fight
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


    def _aerial_finisher(self, win: bool) -> Task:
        """Launch, chase into the sky, and a final spike into the ground: K.O."""
        H = self.cfg.figure_height
        yield from self._launch(win)
        move = self._pick_strike(win)
        yield from self._close_on(win, self._reach(win, move) + TORSO * H)
        yield from self._air_blow(win, move, "hit", knock=260.0)
        yield from self._spike(win, final=True)
        rival = self._fight[0]
        fig = self.fig
        mid = (fig.body.position.x + rival.x) / 2
        if win:
            self.fx.banner("K.O.!", mid, self._ground_cy - 1.6 * H, (255, 80, 60), 1.4, life=1.6)
            fig.events.append(("spar", "win", rival.who))
            yield from self._descend()
            yield from self._settle()
            rival.pose("hurt")
            fig.pose_mode = "victory"
            yield from self._idle(1.4)
            rival.vanish()
            yield from self._idle(0.4)
        else:
            self.fx.banner("K.O.", mid, self._ground_cy - 1.6 * H, (200, 200, 220), 1.2, life=1.6)
            fig.events.append(("spar", "lose", rival.who))
            yield from self._descend()
            yield from self._settle()
            fig.pose_mode = "hurt"
            rival.pose("taunt")
            yield from self._idle(1.3)
            rival.vanish()
