"""Autonomous behavior. Each task is a generator that yields once per frame, so
multi-step behaviors (walk -> crouch -> jump -> land -> re-plan) read top to bottom.

Tasks are cancelled whenever physics takes over (grabbed, knocked down).
"""

from __future__ import annotations

import math
import random
import time
from typing import Generator

from stickfigure.agent.emotion import Emotion
from stickfigure.config import CONFIG, Config
from stickfigure.figure.controller import Activity, Figure
from stickfigure.world.blocks import STAIR_KINDS, BlockManager
from stickfigure.world.structures import TEMPLATES, find_site, width_cells
from stickfigure.world.structures import plan as plan_structure
from stickfigure.world.nav import find_path, plan_staircase, solve_jump, usable_span
from stickfigure.world.physics import SurfaceSeg, World

Task = Generator[None, None, bool]

WAVE_RADIUS = 220.0
WAVE_COOLDOWN = 25.0
STRUCTURE_COOLDOWN = 600.0  # at most one unprompted structure per 10 minutes (asking works any time)


class Brain:
    def __init__(
        self, fig: Figure, world: World, blocks: BlockManager, emotion: Emotion | None = None, cfg: Config = CONFIG
    ):
        self.fig = fig
        self.world = world
        self.blocks = blocks
        self.emotion = emotion or Emotion()
        self.cfg = cfg
        self.enabled = True
        self.task: Task | None = None
        self.task_name = "none"
        self.debug_path: list[tuple[float, float]] = []
        self.dt = 0.0
        self.cursor = (0.0, 0.0)
        self._since_wave = WAVE_COOLDOWN
        self._queued: tuple[str, Task] | None = None
        self._last_structure = time.monotonic()  # first unprompted build waits a while too

    # -- frame update ----------------------------------------------------------------

    def update(self, dt: float, cursor: tuple[float, float]) -> None:
        self.dt = dt
        self.cursor = cursor
        self._since_wave += dt
        fig = self.fig
        if fig.grabbed or fig.knocked > 0:
            if fig.riding:
                fig.stop_ride()
            self.cancel()
            return
        if self._queued and fig.grounded:
            self.cancel()
            self.task_name, self.task = self._queued
            self._queued = None
        if self.task is None:
            if not fig.grounded or fig.activity == Activity.LOOK:
                return
            if self._should_wave():
                self._start("wave", self._wave())
            elif self.enabled:
                self._choose()
            else:
                return
        try:
            next(self.task)
        except StopIteration:
            self._finish()

    def cancel(self) -> None:
        if self.task is not None:
            self.task.close()
        if self.fig.riding:
            self.fig.stop_ride()
        self._finish()

    def _finish(self) -> None:
        self.task = None
        self.task_name = "none"
        self.debug_path = []
        self.fig.walk_to(None)
        self.fig.activity = Activity.NONE
        self.fig.activity_point = None

    def _start(self, name: str, task: Task) -> None:
        self.task_name, self.task = name, task

    # -- commands (from menu / later from the agent) ------------------------------------

    def command_goto(self, key: tuple, rel_x: float | None = None, build: bool = True) -> None:
        self._queued = (f"goto {key[0]}", self._goto(key, rel_x, build))

    def command_sit(self) -> None:
        self._queued = ("sit", self._sit(random.uniform(8, 15)))

    def command(self, action: str) -> None:
        """Body actions the chat model can request (see persona.ACTIONS)."""
        makers = {
            "sit": lambda: self._sit(random.uniform(8, 15)),
            "wave": self._wave,
            "hop": lambda: self._hops(1),
            "dance": self._dance,
            "come": self._come,
            "sleep": lambda: self._sleep(random.uniform(20, 40)),
            "climb": self._climb_highest,
            "build": self._build_structure,
            "demolish": self._demolish,
            "ride": self._ride_cursor,
            "follow": lambda: self._follow_cursor(random.uniform(45, 75)),
            "flip": self._flip,
            "chase": self._chase,
        }
        if action in makers:
            self._queued = (action, makers[action]())

    def command_point(self, target: tuple[float, float], hold: float = 6.0) -> None:
        self._queued = ("point", self._point_at(target, hold))

    def look(self, on: bool) -> None:
        """Shade eyes while perception runs (only if nothing more important is happening)."""
        if on and self.fig.grounded and self.fig.activity in (Activity.NONE, Activity.SIT):
            self.cancel()
            self.fig.activity = Activity.LOOK
        elif not on and self.fig.activity == Activity.LOOK:
            self.fig.activity = Activity.NONE

    @property
    def sleeping(self) -> bool:
        return self.fig.activity == Activity.SLEEP

    def highest_surface(self) -> SurfaceSeg | None:
        cands = [s for s in self.world.surfaces() if s.kind == "window" and usable_span(s, self.cfg)]
        return min(cands, key=lambda s: s.world()[2], default=None)

    # -- decision making ------------------------------------------------------------------

    def _cursor_distance(self) -> float:
        x, y = self.fig.body.position
        return math.hypot(self.cursor[0] - x, self.cursor[1] - y)

    def _should_wave(self) -> bool:
        e = self.emotion
        cooldown = WAVE_COOLDOWN * (1.5 - e.affection)
        if self._since_wave < cooldown or e.annoyance > 0.3 or e.energy < 0.2:
            return False
        return self._cursor_distance() < WAVE_RADIUS * (0.6 + e.affection)

    def _choose(self) -> None:
        cur = self._current_key()
        if cur is None:
            self._start("idle", self._idle(0.5))
            return
        e = self.emotion
        near_cursor = self._cursor_distance() < 300
        options = [
            (20 * (1.3 - e.energy), "idle", lambda: self._idle(random.uniform(1.5, 4.0))),
            (20, "stroll", self._stroll),
            (12 * (1.5 - e.energy), "sit", lambda: self._sit(random.uniform(5, 12))),
            (30 * (0.4 + e.energy) * (0.5 + e.curiosity), "travel", self._travel_somewhere),
            (7 * (0.3 + e.curiosity) * (e.energy > 0.4), "build", self._build_somewhere),
            (12 * max(0.0, e.happiness - 0.6), "hop", lambda: self._hops(random.randint(1, 3))),
            (10 * max(0.0, e.affection - 0.55), "come", self._come),
            (5 * max(0.0, e.happiness - 0.4) * (e.energy > 0.45), "flip", self._flip),
            (4 * (e.energy > 0.5) * (0.4 + e.curiosity) * near_cursor, "ride", self._ride_cursor),
            (4 * (e.energy > 0.55) * max(0.0, e.happiness - 0.3), "chase", self._chase),
            (4 * max(0.0, e.affection - 0.4) * near_cursor, "follow", lambda: self._follow_cursor(random.uniform(15, 30))),
            (25 * (e.annoyance > 0.45 and near_cursor), "avoid", self._avoid),
            (400 * (e.energy < 0.2), "sleep", lambda: self._sleep(random.uniform(30, 90))),  # exhausted: nap
            (2 * (0.4 + e.curiosity) * (e.energy > 0.35) * (self.blocks.permanent_room >= 12)
             * (time.monotonic() - self._last_structure > STRUCTURE_COOLDOWN), "structure", self._build_structure),
            (2.5 * (sum(sid > 0 for sid in self.blocks.structures()) >= 4 or self.blocks.permanent_room < 12),
             "renovate", lambda: self._demolish(own_only=True)),
        ]
        total = sum(w for w, *_ in options)
        r = random.uniform(0, total)
        for w, name, make in options:
            r -= w
            if r <= 0:
                self._start(name, make())
                return

    def _travel_somewhere(self) -> Task:
        cur = self._current_key()
        surfaces = self.world.surfaces()
        random.shuffle(surfaces)
        for s in surfaces:
            if s.key == cur or usable_span(s, self.cfg) is None:
                continue
            if find_path(surfaces, cur, self.fig.body.position.x, s.key, self.cfg) is not None:
                return (yield from self._goto(s.key, None, build=False))
        return (yield from self._stroll())

    def _build_somewhere(self) -> Task:
        cur = self._current_key()
        surfaces = self.world.surfaces()
        fx, fy = self.fig.feet
        above = [
            s for s in surfaces
            if s.kind == "window" and usable_span(s, self.cfg) and usable_span(s, self.cfg)[2] < fy - 200
        ]
        random.shuffle(above)
        for s in above:
            if find_path(surfaces, cur, fx, s.key, self.cfg) is None:
                return (yield from self._goto(s.key, None, build=True))
        return (yield from self._idle(1.0))

    # -- tasks -------------------------------------------------------------------------------

    def _idle(self, duration: float) -> Task:
        t = duration
        while t > 0:
            t -= self.dt
            yield
        return True

    def _stroll(self) -> Task:
        seg = self._seg(self._current_key())
        span = usable_span(seg, self.cfg) if seg else None
        if span is None:
            return False
        lo, hi, _ = span
        x = self.fig.body.position.x
        target = min(max(x + random.uniform(-400, 400), lo), hi)
        return (yield from self._walk(seg.key, target - seg.body.position.x))

    def _sit(self, duration: float) -> Task:
        self.fig.activity = Activity.SIT
        yield from self._idle(duration)
        self.fig.activity = Activity.NONE
        return (yield from self._idle(0.35))

    def _wave(self) -> Task:
        self._since_wave = 0.0
        x = self.fig.body.position.x
        self.fig.facing = 1 if self.cursor[0] >= x else -1
        self.fig.activity = Activity.WAVE
        yield from self._idle(1.6)
        self.fig.activity = Activity.NONE
        return True

    def _sleep(self, duration: float) -> Task:
        self.fig.activity = Activity.SLEEP
        t = duration
        while t > 0 and self.emotion.energy < 0.85:
            t -= self.dt
            yield
        self.fig.activity = Activity.SIT  # groggy moment before getting up
        yield from self._idle(1.5)
        self.fig.activity = Activity.NONE
        return True

    def _hops(self, n: int) -> Task:
        for _ in range(n):
            self.fig.activity = Activity.PREP
            yield from self._idle(0.1)
            self.fig.activity = Activity.NONE
            if not self.fig.grounded:
                return False
            self.fig.jump(0.0, -random.uniform(450, 620))
            if not (yield from self._await_landing(None)):
                return False
            yield from self._idle(0.08)
        return True

    def _dance(self) -> Task:
        for step in range(4):
            yield from self._hops(1)
            self.fig.facing *= -1
            if step == 1:
                yield from self._wave()
        return True

    def surface_under(self, x: float, y: float) -> tuple[SurfaceSeg, float] | None:
        """The walkable surface right below a point (the one the cursor is "over"), and the x to stand at."""
        best = None
        for s in self.world.surfaces():
            span = usable_span(s, self.cfg)
            if span is None:
                continue
            lo, hi, sy = span
            if lo - 40 <= x <= hi + 40 and sy >= y - 20:
                if best is None or sy < best[2]:
                    best = (s, min(max(x, lo), hi), sy)
        return (best[0], best[1]) if best else None

    def _follow_cursor(self, duration: float) -> Task:
        """Tag along after the pointer for a while: walk/run along surfaces, jump or build up to the
        surface under it, and hop onto the pointer itself when it's close overhead."""
        fig = self.fig
        t = 0.0
        while t < duration:
            t += self.dt
            if fig.grabbed or fig.knocked > 0:
                return False
            cx, cy = self.cursor
            here = self._current_key()
            spot = self.surface_under(cx, cy)
            if here is None or spot is None:
                yield
                continue
            seg, x = spot
            fx = fig.body.position.x
            if seg.key == here:
                far = abs(x - fx) > 320
                fig.walk_to(x - 45 * (1 if x > fx else -1) if abs(x - fx) > 60 else None, run=far)
                yield
                continue
            # A different surface: travel there (this finishes the hop/climb before re-aiming).
            elapsed_before = t
            gen = self._goto(seg.key, x - seg.body.position.x, build=False)
            reached = False
            try:
                while True:
                    next(gen)
                    t += self.dt
                    tx, ty = self.cursor
                    moved = self.surface_under(tx, ty)
                    if (moved is None or moved[0].key != seg.key) and t - elapsed_before > 1.5 and fig.grounded:
                        gen.close()  # the cursor went somewhere else: re-aim
                        break
                    yield
            except StopIteration as done:
                reached = bool(done.value)
            if not reached:  # unreachable from here: at least get as close as this surface allows
                fig.walk_to(cx)
            yield
        fig.walk_to(None)
        return True

    def _flip(self) -> Task:
        """A backflip on the spot: one full turn in the air, landing on its feet."""
        fig = self.fig
        fig.activity = Activity.PREP
        yield from self._idle(0.12)
        fig.activity = Activity.NONE
        if not fig.grounded:
            return False
        vy = 640.0
        air = 2 * vy / self.cfg.gravity
        fig.jump(0.0, -vy)
        fig.tumble_spin = -fig.facing * math.tau / air
        return (yield from self._await_landing(None))

    def _chase(self) -> Task:
        """Sprint after the cursor along the current surface (then a victory hop)."""
        seg = self._seg(self._current_key())
        if seg is None:
            return False
        t = 0.0
        while t < 4.0:
            t += self.dt
            x = self.cursor[0]
            span = usable_span(seg, self.cfg)
            if span is None:
                return False
            lo, hi, _ = span
            self.fig.walk_to(min(max(x, lo), hi), run=True)
            if abs(self.fig.body.position.x - x) < 25:
                break
            yield
        self.fig.walk_to(None)
        return (yield from self._hops(1))

    def _ride_cursor(self) -> Task:
        """Jump up and grab onto the mouse cursor, hang on for a ride, let go when shaken off or bored."""
        fig = self.fig
        seg = self._seg(self._current_key())
        if seg is None:
            return False
        span = usable_span(seg, self.cfg)
        if span is None:
            return False
        lo, hi, floor_y = span
        cx, cy = self.cursor
        if not (lo <= cx <= hi):
            return False
        yield from self._walk(seg.key, cx - seg.body.position.x)
        cx, cy = self.cursor
        hand_rise = floor_y - cy - self.cfg.figure_height * 0.86  # how far above its raised hands
        max_rise = self.cfg.jump_speed ** 2 / (2 * self.cfg.gravity)
        if hand_rise < -40 or hand_rise > max_rise * 0.9:
            return False  # cursor is below its head, or out of reach
        fig.activity = Activity.PREP
        yield from self._idle(0.12)
        fig.activity = Activity.NONE
        vy = math.sqrt(2 * self.cfg.gravity * max(40.0, hand_rise + 30))
        fig.jump((self.cursor[0] - fig.body.position.x) * 2.0, -vy)
        t = 0.0
        while not fig.grounded or t < 0.1:
            t += self.dt
            ox, oy = fig.ride_offset()
            bx, by = fig.body.position
            if math.hypot(bx - ox - self.cursor[0], by - oy - self.cursor[1]) < 70:
                fig.start_ride()
                break
            if t > 2.0:
                return False
            yield
        if not fig.riding:
            return False
        hold = random.uniform(6, 20)
        t = 0.0
        while fig.riding and t < hold:
            t += self.dt
            vx, vy = fig.cursor_speed()
            if math.hypot(vx, vy) > 2600:  # shaken off!
                fig.stop_ride(fling=True)
                return True
            yield
        fig.stop_ride()
        return (yield from self._await_landing(None))

    def _come(self) -> Task:
        """Walk toward the cursor along the current surface."""
        seg = self._seg(self._current_key())
        if seg is None:
            return False
        x = self.cursor[0] - math.copysign(60, self.cursor[0] - self.fig.body.position.x)
        return (yield from self._walk(seg.key, x - seg.body.position.x))

    def _avoid(self) -> Task:
        seg = self._seg(self._current_key())
        if seg is None:
            return False
        away = -1 if self.cursor[0] > self.fig.body.position.x else 1
        x = self.fig.body.position.x + away * 350
        return (yield from self._walk(seg.key, x - seg.body.position.x))

    def best_point_spot(self, target: tuple[float, float]) -> tuple[SurfaceSeg, float] | None:
        """Where to stand to point at `target`: close to it, but not a long trek away."""
        cur = self._current_key()
        if cur is None:
            return None
        tx, ty = target
        fx, fy = self.fig.feet
        chest = 0.7 * self.cfg.figure_height
        surfaces = self.world.surfaces()
        best: tuple[float, SurfaceSeg, float] | None = None
        for s in surfaces:
            span = usable_span(s, self.cfg)
            if span is None:
                continue
            lo, hi, y = span
            x = min(max(tx, lo), hi)
            if s.key == cur:
                travel = abs(x - fx)
            else:
                path = find_path(surfaces, cur, fx, s.key, self.cfg)
                if path is None:
                    continue
                travel = abs(x - fx) + abs(y - fy) + 150 * len(path)
            cost = math.hypot(x - tx, (y - chest) - ty) + 0.35 * travel
            if best is None or cost < best[0]:
                best = (cost, s, x)
        return (best[1], best[2]) if best else None

    def _point_at(self, target: tuple[float, float], hold: float) -> Task:
        spot = self.best_point_spot(target)
        if spot is not None:
            seg, x = spot
            yield from self._goto(seg.key, x - seg.body.position.x, build=False)
        fig = self.fig
        fig.facing = 1 if target[0] >= fig.body.position.x else -1
        fig.activity_point = target
        fig.activity = Activity.POINT
        yield from self._idle(hold)
        fig.activity = Activity.NONE
        fig.activity_point = None
        return True

    # -- permanent structures -------------------------------------------------------------------

    def _floor_under_me(self) -> SurfaceSeg | None:
        fx = self.fig.body.position.x
        floors = [s for s in self.world.surfaces() if s.kind == "floor"]
        return next((s for s in floors if s.x0 <= fx <= s.x1), floors[0] if floors else None)

    def _build_structure(self, template: str | None = None) -> Task:
        """Walk to a clear spot on the taskbar and build something that stays."""
        floor = self._floor_under_me()
        if floor is None or self.blocks.permanent_room <= 0:
            return False
        size = self.cfg.block_size
        template = template or random.choice(list(TEMPLATES))
        occupied = [(b.body.position.x - b.width / 2, b.body.position.y, b.body.position.x + b.width / 2,
                     b.body.position.y + b.height) for b in self.world.blocks.values()]
        x0, x1, floor_y = floor.world()
        left = find_site((x0, x1), floor_y, template, size, occupied, self.fig.body.position.x)
        cells = plan_structure(template, left, floor_y, size) if left is not None else []
        if not cells or len(cells) > self.blocks.permanent_room:
            return False
        # Stand just beside the site and place the blocks from there (bottom row first).
        width = width_cells(template) * size
        side = -1 if left - x0 > size else 1
        stand = left - 0.7 * size if side < 0 else left + width + 0.7 * size
        if not (yield from self._goto(floor.key, stand - floor.body.position.x, build=False)):
            return False
        sid = self.blocks.new_structure_id()
        self._last_structure = time.monotonic()
        for cx, top, kind in cells:
            self.fig.facing = 1 if cx >= self.fig.body.position.x else -1
            self.fig.activity_point = (cx, top + size / 2)
            self.fig.activity = Activity.PLACE
            yield from self._idle(0.25)
            placed = self.blocks.spawn(cx, top, kind=kind, permanent=True, structure=sid)
            yield from self._idle(0.1)
            if placed is None:
                break
        self.fig.activity = Activity.NONE
        self.fig.activity_point = None
        self.emotion.on_explored()
        return True

    def _demolish(self, structure: int | None = None, own_only: bool = False) -> Task:
        """Knock down a structure, top blocks first. Unprompted renovating (`own_only`) leaves alone what
        the user built in build mode (negative structure ids)."""
        groups = {sid: bs for sid, bs in self.blocks.structures().items() if sid > 0 or not own_only}
        if not groups:
            return False
        sid = structure if structure in groups else random.choice(list(groups))
        blocks = sorted(groups[sid], key=lambda b: b.body.position.y)  # highest first
        cx = sum(b.body.position.x for b in blocks) / len(blocks)
        floor = self._floor_under_me()
        if floor is not None:
            yield from self._goto(floor.key, cx - self.cfg.block_size * 2.2 - floor.body.position.x, build=False)
        for b in blocks:
            if b.id not in self.world.blocks:
                continue
            bx, top = b.body.position
            self.fig.facing = 1 if bx >= self.fig.body.position.x else -1
            self.fig.activity_point = (bx, top + b.height / 2)
            self.fig.activity = Activity.PLACE
            yield from self._idle(0.18)
            self.blocks.remove(b.id)
            yield from self._idle(0.12)
        self.fig.activity = Activity.NONE
        self.fig.activity_point = None
        return True

    def _climb_highest(self) -> Task:
        target = self.highest_surface()
        if target is None:
            return False
        return (yield from self._goto(target.key, None, build=True))

    def _goto(self, key: tuple, rel_x: float | None, build: bool) -> Task:
        """Travel to surface `key`; if it's unreachable and `build`, build a staircase."""
        for _attempt in range(3):
            cur = self._current_key()
            seg = self._seg(key)
            if cur is None or seg is None:
                return False
            if rel_x is None:
                lo, hi, _ = usable_span(seg, self.cfg) or (0, 0, 0)
                x = min(max(self.fig.body.position.x, lo), hi)
                rel_x = x - seg.body.position.x if hi > lo else (seg.x0 + seg.x1) / 2
            path = find_path(self.world.surfaces(), cur, self.fig.body.position.x, key, self.cfg)
            if path is None:
                if build and (yield from self._build_to(key)):
                    return (yield from self._walk(key, rel_x))
                return False
            self._show_path(path, key, rel_x)
            ok = True
            for move in path:
                if not (yield from self._walk(move.src, move.takeoff_rel)):
                    ok = False
                    break
                done = yield from (self._jump(move.dst, move.land_rel) if move.kind == "jump" else self._drop())
                if not done:
                    ok = False
                    break
            if ok:
                if path:
                    self.emotion.on_explored()
                return (yield from self._walk(key, rel_x))
            yield from self._idle(0.3)  # re-plan from wherever we ended up
        return False

    def _build_to(self, key: tuple) -> Task:
        goal = self._seg(key)
        if goal is None:
            return False
        fx, fy = self.fig.feet
        mon = next((m for m in self.world.monitors if m.work.left <= fx < m.work.right), self.world.monitors[0])
        plan = plan_staircase(fx, fy, goal, (mon.work.left, mon.work.right), self.cfg)
        if plan is None:
            return False
        blocks, land_x = plan
        stair_kind = random.choice(STAIR_KINDS)
        self.debug_path = [(fx, fy)] + [(bx, by) for bx, by in blocks] + [(land_x, goal.world()[2])]
        reach = 0.28 * self.cfg.figure_height
        for bx, top in blocks:
            cur = self._seg(self._current_key())
            span = usable_span(cur, self.cfg) if cur else None
            if span is None:
                return False
            x = self.fig.body.position.x
            direction = 1 if bx >= x else -1
            stand = min(max(bx - direction * reach, span[0]), span[1])
            if not (yield from self._walk(cur.key, stand - cur.body.position.x)):
                return False
            self.fig.facing = 1 if bx >= self.fig.body.position.x else -1
            self.fig.activity_point = (bx, top)
            self.fig.activity = Activity.PLACE
            yield from self._idle(0.25)
            block = self.blocks.spawn(bx, top, kind=stair_kind)
            yield from self._idle(0.12)
            self.fig.activity = Activity.NONE
            self.fig.activity_point = None
            if block is None or not (yield from self._jump(("block", block.id), 0.0)):
                return False
        goal = self._seg(key)
        if goal is None:
            return False
        return (yield from self._jump(key, land_x - goal.body.position.x))

    # -- primitives ------------------------------------------------------------------------------

    def _walk(self, key: tuple, rel_x: float) -> Task:
        stuck = 0.0
        last_x = self.fig.body.position.x
        while True:
            seg = self._seg(key)
            span = usable_span(seg, self.cfg) if seg else None
            if span is None or not self.fig.grounded or self._current_key() != key:
                self.fig.walk_to(None)
                return False
            target = min(max(seg.body.position.x + rel_x, span[0]), span[1])
            x = self.fig.body.position.x
            if abs(target - x) < 4:
                self.fig.walk_to(None)
                return True
            self.fig.walk_to(target, run=abs(target - x) > self.cfg.run_threshold)
            stuck = stuck + self.dt if abs(x - last_x) < 0.5 else 0.0
            last_x = x
            if stuck > 1.0:
                self.fig.walk_to(None)
                return False
            yield

    def _jump(self, key: tuple, rel_x: float) -> Task:
        fig = self.fig
        fig.walk_to(None)
        fig.activity = Activity.PREP
        yield from self._idle(self.cfg.jump_prep_time)
        fig.activity = Activity.NONE
        seg = self._seg(key)
        span = usable_span(seg, self.cfg) if seg else None
        if span is None or not fig.grounded:
            return False
        fx, fy = fig.feet
        tx = min(max(seg.body.position.x + rel_x, span[0]), span[1])
        sol = solve_jump(fx, fy, tx, span[2], self.cfg)
        if sol is None:
            return False
        fig.jump(*sol)
        return (yield from self._await_landing(key))

    def _drop(self) -> Task:
        start = self._current_key()
        if not self.fig.drop_through():
            return False
        return (yield from self._await_landing(None, not_on=start))

    def _await_landing(self, key: tuple | None, not_on: tuple | None = None) -> Task:
        t = 0.0
        yield
        while not self.fig.grounded:
            t += self.dt
            if t > 4.0:
                return False
            yield
        landed = self._current_key()
        if key is not None:
            return landed == key
        return landed is not None and landed != not_on

    # -- helpers ------------------------------------------------------------------------------------

    def _current_key(self) -> tuple | None:
        return self.world.surface_for_shape(self.fig.ground_shape)

    def _seg(self, key: tuple | None) -> SurfaceSeg | None:
        if key is None:
            return None
        return next((s for s in self.world.surfaces() if s.key == key), None)

    def _show_path(self, path, goal_key: tuple, goal_rel: float) -> None:
        pts = [self.fig.feet]
        for m in path:
            src, dst = self._seg(m.src), self._seg(m.dst)
            if src and dst:
                pts.append((src.body.position.x + m.takeoff_rel, src.world()[2]))
                pts.append((dst.body.position.x + m.land_rel, dst.world()[2]))
        g = self._seg(goal_key)
        if g:
            pts.append((g.body.position.x + goal_rel, g.world()[2]))
        self.debug_path = pts
