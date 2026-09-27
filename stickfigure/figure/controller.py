"""The figure's physical body: a capsule character controller.

Owns movement (walk/run to a target x, ballistic jumps, drop-through, drag and
throw, tumble and knockdown). It knows nothing about drawing or decision-making.
The animator reads its state; the brain writes its intentions.
"""

from __future__ import annotations

import math
import time
from collections import deque
from enum import Enum, auto

import pymunk

from stickfigure.config import CONFIG, Config
from stickfigure.world.physics import CAT_FIGURE, CAT_WORLD, CT_FIGURE, CT_PLATFORM, World


class Activity(Enum):
    NONE = auto()
    SIT = auto()
    PLACE = auto()
    WAVE = auto()
    PREP = auto()  # crouching before a jump
    SLEEP = auto()
    LOOK = auto()  # shading eyes, scanning the screen
    POINT = auto()  # arm out toward activity_point


class Figure:
    def __init__(self, world: World, cfg: Config = CONFIG):
        self.cfg = cfg
        self.world = world
        self.body = pymunk.Body(cfg.body_mass, float("inf"))  # infinite moment: collider stays upright
        self.shape = pymunk.Poly.create_box(self.body, (cfg.body_width, cfg.body_height), cfg.body_radius)
        self.shape.friction = 1.0
        # Bounce = product of both elasticities: every surface is 0 except bouncy blocks.
        self.shape.elasticity = 1.0
        self.shape.collision_type = CT_FIGURE
        self._solid_filter = pymunk.ShapeFilter(categories=CAT_FIGURE, mask=CAT_WORLD)
        self.shape.filter = self._solid_filter
        world.space.add(self.body, self.shape)

        self.facing = 1
        self.move_target_x: float | None = None
        self.run = False
        self.speed_scale = 1.0  # mood: tired figures shuffle, excited ones hurry
        # Things that happened to the figure since the last frame, drained by the app:
        # ("grab",), ("pet",), ("thrown", speed), ("knocked", impact), ("landed", impact)
        self.events: list[tuple] = []
        self.activity = Activity.NONE
        self.activity_point: tuple[float, float] | None = None  # world point for PLACE/WAVE

        self.grounded = False
        self.ground_shape: pymunk.Shape | None = None
        self.air_time = 0.0
        self.land_timer = 0.0
        self.last_impact = 0.0
        self.jumping = False
        self._jump_grace = 0.0
        self._last_vy = 0.0

        self.grabbed = False
        self.riding = False  # hanging from the mouse cursor by choice (not being dragged)
        self._grab_offset = (0.0, 0.0)
        self._cursor_samples: deque[tuple[float, float, float]] = deque(maxlen=12)
        self._clock = 0.0  # simulation time, for cursor-speed samples

        self.tumble_angle = 0.0
        self.tumble_spin = 0.0
        self.knocked = 0.0  # seconds left lying down
        self.knocked_side = 1

        self._offscreen_since: float | None = None
        self.respawn()

    # -- geometry ---------------------------------------------------------------

    @property
    def half_extents(self) -> tuple[float, float]:
        return (self.cfg.body_width / 2 + self.cfg.body_radius, self.cfg.body_height / 2 + self.cfg.body_radius)

    @property
    def feet(self) -> tuple[float, float]:
        x, y = self.body.position
        return (x, y + self.half_extents[1])

    @property
    def tumbling(self) -> bool:
        return self.tumble_spin != 0.0

    @property
    def busy(self) -> bool:
        """True while physics owns the figure (grabbed, airborne, knocked)."""
        return self.grabbed or self.riding or not self.grounded or self.knocked > 0

    def respawn(self) -> None:
        if not self.world.monitors:
            return
        self.body.position = self.world.spawn_point()
        self.body.velocity = (0, 0)
        self.tumble_spin = self.tumble_angle = 0.0
        self.world.space.reindex_shapes_for_body(self.body)

    # -- commands ------------------------------------------------------------------

    def walk_to(self, x: float | None, run: bool = False) -> None:
        self.move_target_x = x
        self.run = run

    def jump(self, vx: float, vy: float) -> None:
        self.move_target_x = None
        self.activity = Activity.NONE
        self.body.velocity = (vx, vy)
        self.grounded = False
        self.jumping = True
        self._jump_grace = 0.08
        if abs(vx) > 5:
            self.facing = 1 if vx > 0 else -1

    def drop_through(self) -> bool:
        """Fall through the one-way platform we're standing on."""
        if self.ground_shape is None or self.ground_shape.collision_type != CT_PLATFORM:
            return False
        self.world.passthrough_body = self.ground_shape.body
        self.move_target_x = None
        return True

    # -- grabbing -------------------------------------------------------------------

    def grab(self, cursor: tuple[float, float]) -> None:
        self.riding = False
        self.grabbed = True
        self.move_target_x = None
        self.activity = Activity.NONE
        self.knocked = 0.0
        self.tumble_spin = self.tumble_angle = 0.0
        self.world.carry_target = None
        self._grab_offset = (self.body.position.x - cursor[0], self.body.position.y - cursor[1])
        self._grab_start = (time.perf_counter(), cursor)
        self._cursor_samples.clear()
        self.events.append(("grab",))
        self.shape.filter = pymunk.ShapeFilter(categories=CAT_FIGURE, mask=0)  # pass through everything

    # -- riding the cursor -----------------------------------------------------------------------

    def ride_offset(self) -> tuple[float, float]:
        """Body center relative to the cursor tip when hanging from it by the hands."""
        return (0.0, 0.36 * self.cfg.figure_height)

    def start_ride(self) -> None:
        self.riding = True
        self.move_target_x = None
        self.activity = Activity.NONE
        self.tumble_spin = self.tumble_angle = 0.0
        self.world.carry_target = None
        self._grab_offset = self.ride_offset()
        self._cursor_samples.clear()
        self.events.append(("ride",))
        self.shape.filter = pymunk.ShapeFilter(categories=CAT_FIGURE, mask=0)

    def cursor_speed(self) -> tuple[float, float]:
        return self._throw_velocity()

    def stop_ride(self, fling: bool = False) -> None:
        """Let go: gently (drop straight down), or flung off by a fast mouse move (tumbling)."""
        if not self.riding:
            return
        self.riding = False
        self.shape.filter = self._solid_filter
        vx, vy = self._throw_velocity() if fling else (0.0, 0.0)
        self.body.velocity = (vx, vy)
        self.grounded = False
        self._jump_grace = 0.05
        if fling and math.hypot(vx, vy) > self.cfg.tumble_threshold:
            self.tumble_spin = max(-14.0, min(14.0, vx / 110 or 6.0))
            self.events.append(("thrown", math.hypot(vx, vy)))

    def release(self) -> None:
        if not self.grabbed:
            return
        self.grabbed = False
        self.shape.filter = self._solid_filter
        t0, (cx0, cy0) = self._grab_start
        last = self._cursor_samples[-1] if self._cursor_samples else (t0, cx0, cy0)
        if time.perf_counter() - t0 < 0.3 and math.hypot(last[1] - cx0, last[2] - cy0) < 6:
            # A quick click without dragging is a pat on the head, not a drop.
            self.body.position = (cx0 + self._grab_offset[0], cy0 + self._grab_offset[1])
            self.body.velocity = (0, 0)
            self.events.append(("pet",))
            return
        vx, vy = self._throw_velocity()
        self.body.velocity = (vx, vy)
        self.grounded = False
        self._jump_grace = 0.05
        if abs(vx) > 50:
            self.facing = 1 if vx > 0 else -1
        speed = math.hypot(vx, vy)
        if speed > self.cfg.tumble_threshold:
            self.tumble_spin = max(-14.0, min(14.0, vx / 110 or 6.0))
            self.events.append(("thrown", speed))

    def _throw_velocity(self) -> tuple[float, float]:
        s = self._cursor_samples
        if len(s) < 2:
            return (0.0, 0.0)
        now = s[-1][0]
        # Use motion over roughly the last 60 ms so a pause before release means a gentle drop.
        old = next((p for p in s if now - p[0] <= 0.06), s[0])
        dt = now - old[0]
        if dt <= 0:
            return (0.0, 0.0)
        vx, vy = (s[-1][1] - old[1]) / dt, (s[-1][2] - old[2]) / dt
        speed = math.hypot(vx, vy)
        cap = self.cfg.max_throw_speed
        if speed > cap:
            vx, vy = vx * cap / speed, vy * cap / speed
        return (vx, vy)

    # -- simulation (per physics substep) -----------------------------------------------

    def pre_step(self, dt: float, cursor: tuple[float, float]) -> None:
        if self.grabbed or self.riding:
            self._clock += dt
            self._cursor_samples.append((self._clock, cursor[0], cursor[1]))
            tx, ty = cursor[0] + self._grab_offset[0], cursor[1] + self._grab_offset[1]
            # Drive velocity so the body lands exactly on target after this step (cancel gravity too).
            self.body.velocity = (
                (tx - self.body.position.x) / dt,
                (ty - self.body.position.y) / dt - self.cfg.gravity * dt,
            )
            self.shape.surface_velocity = (0, 0)
            return

        target_vx = 0.0
        can_move = self.grounded and self.knocked <= 0 and self.activity in (Activity.NONE, Activity.WAVE, Activity.LOOK)
        if can_move and self.move_target_x is not None:
            dx = self.move_target_x - self.body.position.x
            if abs(dx) > 2:
                self.facing = 1 if dx > 0 else -1
                top = (self.cfg.run_speed if self.run else self.cfg.walk_speed) * self.speed_scale
                target_vx = self.facing * min(top, abs(dx) * 8)  # ease into the target
        # Pymunk platformer trick: surface velocity + friction walks relative to whatever we stand on.
        self.shape.surface_velocity = (-target_vx, 0) if self.grounded else (0, 0)

    def post_step(self, dt: float) -> None:
        self._jump_grace = max(0.0, self._jump_grace - dt)
        ground_shape: pymunk.Shape | None = None

        def check(arb: pymunk.Arbiter) -> None:
            nonlocal ground_shape
            mine = arb.shapes[0] is self.shape
            n = arb.normal if mine else -arb.normal
            if n.y > 0.6 and arb.process_collision:
                ground_shape = arb.shapes[1] if mine else arb.shapes[0]

        self.body.each_arbiter(check)
        was_grounded = self.grounded
        vy_before = self._last_vy
        self.grounded = (ground_shape is not None and not self.grabbed and not self.riding
                         and self._jump_grace <= 0)
        self.ground_shape = ground_shape if self.grounded else None
        body = ground_shape.body if self.grounded else None
        self.world.carry_target = body if body is not None and body.body_type == pymunk.Body.KINEMATIC else None

        if self.grounded:
            if not was_grounded:
                self._on_land(vy_before)
            self.air_time = 0.0
        elif not self.grabbed and not self.riding:
            self.air_time += dt
            self.tumble_angle += self.tumble_spin * dt
        self._last_vy = self.body.velocity.y

    def _on_land(self, impact: float) -> None:
        self.jumping = False
        self.last_impact = impact
        self.land_timer = 0.16 if impact > 350 else 0.0
        tilted = abs(math.remainder(self.tumble_angle, math.tau)) > 0.7
        if impact > self.cfg.knockdown_impact or (self.tumbling and (tilted or impact > 700)):
            self.knocked = self.cfg.knocked_time
            self.knocked_side = 1 if math.remainder(self.tumble_angle, math.tau) >= 0 else -1
            self.move_target_x = None
            self.activity = Activity.NONE
            self.events.append(("knocked", impact))
        elif impact > 900:
            self.events.append(("landed", impact))
        self.tumble_spin = self.tumble_angle = 0.0

    # -- per frame --------------------------------------------------------------------------

    def update(self, dt: float) -> None:
        self.land_timer = max(0.0, self.land_timer - dt)
        self.knocked = max(0.0, self.knocked - dt)
        self._clear_passthrough()

        x, y = self.body.position
        if self.world.on_screen(x, y) or self.grabbed or self.riding:
            self._offscreen_since = None
        elif self._offscreen_since is None:
            self._offscreen_since = time.perf_counter()
        elif time.perf_counter() - self._offscreen_since > self.cfg.respawn_after_offscreen:
            self._offscreen_since = None
            self.respawn()

    def _clear_passthrough(self) -> None:
        pb = self.world.passthrough_body
        if pb is None:
            return
        # Keep ignoring the platform until our head is below it.
        top = self.body.position.y - self.half_extents[1]
        if pb not in self.world.space.bodies or top > pb.position.y + 4:
            self.world.passthrough_body = None
