"""Procedural animation: picks an animation state from the controller, builds a target
pose with IK, crossfades between states, and runs verlet secondary motion so limbs
dangle when grabbed and flop when thrown.
"""

from __future__ import annotations

import math
from enum import Enum, auto

from stickfigure.config import CONFIG, Config
from stickfigure.figure.controller import Activity, Figure
from stickfigure.figure.skeleton import (
    BONES, JOINTS, Pose, Proportions, Vec, blend, mirror, rotate, smoothstep, two_bone_ik,
)

TAU = math.tau


class Anim(Enum):
    IDLE = auto()
    WALK = auto()
    RUN = auto()
    PREP = auto()
    AIR = auto()
    FALL = auto()
    LAND = auto()
    SIT = auto()
    SLEEP = auto()
    LOOK = auto()
    POINT = auto()
    PLACE = auto()
    WAVE = auto()
    GRABBED = auto()
    TUMBLE = auto()
    KNOCKED = auto()
    RIDE = auto()  # hanging from the mouse cursor, legs swinging


# Crossfade time into a state (seconds).
FADE_IN = {Anim.SIT: 0.3, Anim.SLEEP: 0.8, Anim.KNOCKED: 0.25, Anim.GRABBED: 0.08, Anim.AIR: 0.1, Anim.PREP: 0.08}
FADE_OUT = {Anim.KNOCKED: 0.45, Anim.SIT: 0.3, Anim.SLEEP: 0.4}
DEFAULT_FADE = 0.14

# How tightly joints track the target pose (1 = exact, lower = floppier). Per 1/60 s.
STIFFNESS = {Anim.GRABBED: 0.10, Anim.RIDE: 0.25, Anim.TUMBLE: 0.30, Anim.FALL: 0.45, Anim.AIR: 0.7}
LIMB_GRAVITY = 1800.0


class Animator:
    def __init__(self, fig: Figure, cfg: Config = CONFIG):
        self.fig = fig
        self.cfg = cfg
        self.P = Proportions(cfg.figure_height)
        self.state = Anim.IDLE
        self.state_time = 0.0
        self.time = 0.0
        self.phase = 0.0
        self.droop = 0.0  # 0 perky .. 1 exhausted/sad, set from mood
        self.talk = 0.0  # 0..1 loudness of the figure's own speech (head bobs along)
        self._talk_smooth = 0.0
        self._fade_from: Pose | None = None
        self._fade_time = 0.0
        self._fade_dur = DEFAULT_FADE
        base = self._idle()
        self.pose: Pose = base  # local-frame output
        bx, by = fig.body.position
        self._pts = {j: (x + bx, y + by) for j, (x, y) in base.items()}
        self._prev = dict(self._pts)

    # -- state selection ------------------------------------------------------------

    def _classify(self) -> Anim:
        f = self.fig
        if f.grabbed:
            return Anim.GRABBED
        if getattr(f, "riding", False):
            return Anim.RIDE
        if f.knocked > 0:
            return Anim.KNOCKED
        if not f.grounded:
            if f.tumbling:
                return Anim.TUMBLE
            if f.air_time > 0.45 and f.body.velocity.y > 600:
                return Anim.FALL
            return Anim.AIR
        act = {
            Activity.PREP: Anim.PREP, Activity.SIT: Anim.SIT, Activity.SLEEP: Anim.SLEEP,
            Activity.PLACE: Anim.PLACE, Activity.WAVE: Anim.WAVE, Activity.POINT: Anim.POINT,
        }
        if f.activity == Activity.LOOK and abs(f.body.velocity.x) < 15:
            return Anim.LOOK
        if f.activity in act:
            return act[f.activity]
        if f.land_timer > 0:
            return Anim.LAND
        vx = abs(f.body.velocity.x)
        if vx > 15:
            return Anim.RUN if vx > (self.cfg.walk_speed + self.cfg.run_speed) / 2 else Anim.WALK
        return Anim.IDLE

    # -- per frame ---------------------------------------------------------------------

    def update(self, dt: float) -> Pose:
        self.time += dt
        state = self._classify()
        if state != self.state:
            self._fade_from = self.pose
            self._fade_time = 0.0
            self._fade_dur = max(FADE_IN.get(state, DEFAULT_FADE), FADE_OUT.get(self.state, 0.0))
            self.state = state
            self.state_time = 0.0
        self.state_time += dt

        if state in (Anim.WALK, Anim.RUN):
            stride = self._stride(state)
            self.phase = (self.phase + abs(self.fig.body.velocity.x) * dt / (2 * stride)) % 1.0

        target = self._target(state)
        if self._fade_from is not None:
            self._fade_time += dt
            t = self._fade_time / self._fade_dur
            if t >= 1:
                self._fade_from = None
            else:
                target = blend(self._fade_from, target, smoothstep(t))

        # Talking: the head bobs with the loudness of the speech (smoothed so it reads as syllables).
        self._talk_smooth += (self.talk - self._talk_smooth) * min(1.0, dt * 18)
        if self._talk_smooth > 0.02 and state not in (Anim.KNOCKED, Anim.TUMBLE, Anim.GRABBED):
            hx, hy = target["head"]
            bob = self._talk_smooth * 0.045 * self.P.height
            target = {**target, "head": (hx + 0.2 * bob * self.fig.facing, hy - bob)}

        self.pose = self._secondary(target, STIFFNESS.get(state, 1.0), dt)
        return self.pose

    def _target(self, state: Anim) -> Pose:
        f = self.fig
        builders = {
            Anim.IDLE: self._idle,
            Anim.WALK: lambda: self._gait(run=False),
            Anim.RUN: lambda: self._gait(run=True),
            Anim.PREP: lambda: self._crouch(min(1.0, self.state_time / self.cfg.jump_prep_time)),
            Anim.LAND: lambda: self._crouch(min(1.0, f.land_timer / 0.16)),  # deeper for longer drops
            Anim.AIR: self._air,
            Anim.FALL: self._flail,
            Anim.TUMBLE: self._flail,
            Anim.SIT: self._sit,
            Anim.SLEEP: self._sleep,
            Anim.LOOK: self._look,
            Anim.POINT: self._point,
            Anim.PLACE: self._place,
            Anim.WAVE: self._wave,
            Anim.GRABBED: self._grabbed,
            Anim.RIDE: self._ride,
            Anim.KNOCKED: self._knocked,
        }
        canonical = builders[state]()
        facing = f.facing
        if state == Anim.KNOCKED:
            facing = f.knocked_side
        pose = mirror(canonical, facing)
        if state == Anim.TUMBLE:
            pose = rotate(pose, f.tumble_angle)
        return pose

    # -- secondary motion -----------------------------------------------------------------

    def _secondary(self, target: Pose, stiffness: float, dt: float) -> Pose:
        bx, by = self.fig.body.position
        tw = {j: (x + bx, y + by) for j, (x, y) in target.items()}
        if stiffness >= 1.0:
            self._prev = self._pts
            self._pts = tw
            return target

        k = 1 - (1 - stiffness) ** (dt * 60)
        g = LIMB_GRAVITY * dt * dt
        pts, prev = self._pts, self._prev
        new: dict[str, Vec] = {}
        for j in JOINTS:
            if j == "pelvis":
                new[j] = tw[j]
                continue
            (x, y), (px, py), (tx, ty) = pts[j], prev[j], tw[j]
            nx = x + (x - px) * 0.96
            ny = y + (y - py) * 0.96 + g
            new[j] = (nx + (tx - nx) * k, ny + (ty - ny) * k)
        for _ in range(3):  # keep bone lengths
            for a, b in BONES:
                (ax, ay), (bx2, by2) = new[a], new[b]
                dx, dy = bx2 - ax, by2 - ay
                d = math.hypot(dx, dy) or 1e-6
                diff = (d - self.P.bone_length(a, b)) / d
                if a == "pelvis":
                    new[b] = (bx2 - dx * diff, by2 - dy * diff)
                else:
                    new[a] = (ax + dx * diff * 0.5, ay + dy * diff * 0.5)
                    new[b] = (bx2 - dx * diff * 0.5, by2 - dy * diff * 0.5)
        self._prev, self._pts = pts, new
        return {j: (x - bx, y - by) for j, (x, y) in new.items()}

    # -- pose building (canonical frame: facing +x) -------------------------------------------

    def _assemble(
        self,
        pelvis: Vec,
        lean: float,
        foot_b: Vec,
        foot_f: Vec,
        hand_b: Vec,
        hand_f: Vec,
        hands_abs: bool = False,
        knee: float = -1.0,
        elbow: float = 1.0,
        elbow_f: float | None = None,
    ) -> Pose:
        P = self.P
        neck = (pelvis[0] + math.sin(lean) * P.torso, pelvis[1] - math.cos(lean) * P.torso)
        head = (neck[0] + math.sin(lean) * P.neck_head, neck[1] - math.cos(lean) * P.neck_head)
        if not hands_abs:
            hand_b = (neck[0] + hand_b[0], neck[1] + hand_b[1])
            hand_f = (neck[0] + hand_f[0], neck[1] + hand_f[1])
        knee_b, foot_b = two_bone_ik(pelvis, foot_b, P.thigh, P.shin, knee)
        knee_f, foot_f = two_bone_ik(pelvis, foot_f, P.thigh, P.shin, knee)
        elbow_b, hand_b = two_bone_ik(neck, hand_b, P.upper_arm, P.forearm, elbow)
        elbow_f2, hand_f = two_bone_ik(neck, hand_f, P.upper_arm, P.forearm, elbow if elbow_f is None else elbow_f)
        return {
            "pelvis": pelvis, "neck": neck, "head": head,
            "knee_b": knee_b, "foot_b": foot_b, "knee_f": knee_f, "foot_f": foot_f,
            "elbow_b": elbow_b, "hand_b": hand_b, "elbow_f": elbow_f2, "hand_f": hand_f,
        }

    @property
    def _leg(self) -> float:
        return self.P.thigh + self.P.shin

    @property
    def _arm(self) -> float:
        return self.P.upper_arm + self.P.forearm

    def _stride(self, state: Anim) -> float:
        return self.P.height * (0.58 if state == Anim.RUN else 0.34)

    def _idle(self) -> Pose:
        H, half, t = self.P.height, self.P.half, self.time
        br = math.sin(t * (2.2 - 0.8 * self.droop))
        sway = math.sin(t * 1.3) * 0.012 * H
        d = self.droop
        pelvis = (0.0, half - (0.96 - 0.05 * d) * self._leg + br * 0.5)
        return self._assemble(
            pelvis, 0.03 + 0.2 * d + br * 0.012,
            (-0.07 * H, half), (0.08 * H, half),
            (-0.07 * H * (1 - d) + sway, self._arm * 0.93), (0.07 * H * (1 - d) - sway, self._arm * 0.93),
        )

    def _sleep(self) -> Pose:
        """Sitting, slumped forward, head down, slow breathing."""
        H, half, t = self.P.height, self.P.half, self.time
        br = math.sin(t * 1.1)
        pelvis = (0.0, half - 0.015 * H)
        return self._assemble(
            pelvis, 0.42 + 0.03 * br,
            (0.1 * H, half + 0.3 * H), (0.13 * H, half + 0.3 * H),
            (0.2 * H, half - 0.02 * H), (0.24 * H, half - 0.03 * H),
            hands_abs=True,
        )

    def _gait(self, run: bool) -> Pose:
        H, half = self.P.height, self.P.half
        S = self._stride(Anim.RUN if run else Anim.WALK)
        lift = H * (0.16 if run else 0.09)

        def foot(p: float) -> Vec:
            p %= 1.0
            if p < 0.5:  # stance: planted, sliding back at exactly body speed
                return (S / 2 - S * (p / 0.5), half)
            u = (p - 0.5) / 0.5  # swing
            return (-S / 2 + S * (1 - math.cos(math.pi * u)) / 2, half - lift * math.sin(math.pi * u))

        p = self.phase
        bob = abs(math.sin(TAU * p)) * H * (0.03 if run else 0.018)
        pelvis = (0.0, half - (0.9 if run else 0.97) * self._leg - bob)
        swing = math.cos(TAU * p) * H * (0.2 if run else 0.12)
        arm_y = self._arm * (0.5 if run else 0.9)
        return self._assemble(
            pelvis, (0.28 if run else 0.08) + 0.12 * self.droop,
            foot(p + 0.5), foot(p),
            (swing, arm_y), (-swing, arm_y),
        )

    def _crouch(self, k: float) -> Pose:
        H, half = self.P.height, self.P.half
        k = smoothstep(k)
        pelvis = (0.0, half - self._leg * (0.96 - 0.28 * k))
        return self._assemble(
            pelvis, 0.05 + 0.3 * k,
            (-0.09 * H, half), (0.11 * H, half),
            (-0.22 * H * k, self._arm * 0.85), (-0.12 * H * k, self._arm * 0.85),
        )

    def _air(self) -> Pose:
        H, half = self.P.height, self.P.half
        vy = self.fig.body.velocity.y
        s = smoothstep((vy + 300) / 600)  # 0 rising .. 1 falling
        pelvis = (0.0, half - 0.96 * self._leg)
        rise = self._assemble(
            pelvis, 0.12,
            (-0.05 * H, half - 0.12 * H), (0.12 * H, half - 0.2 * H),
            (0.08 * H, -self._arm * 0.7), (0.18 * H, -self._arm * 0.55),
        )
        fall = self._assemble(
            pelvis, 0.02,
            (-0.1 * H, half - 0.06 * H), (0.1 * H, half),
            (-0.24 * H, -0.08 * H), (0.24 * H, -0.12 * H),
            elbow_f=-1.0,
        )
        return blend(rise, fall, s)

    def _flail(self) -> Pose:
        H, half, t = self.P.height, self.P.half, self.time
        w = 13.0
        pelvis = (0.0, half - 0.95 * self._leg)
        return self._assemble(
            pelvis, -0.05,
            (-0.08 * H + 0.06 * H * math.sin(w * t), half - 0.08 * H * (1 + math.cos(w * t))),
            (0.08 * H - 0.06 * H * math.sin(w * t + 2), half - 0.08 * H * (1 + math.cos(w * t + 2))),
            (-0.2 * H * math.cos(w * t), -0.12 * H + 0.14 * H * math.sin(w * t)),
            (0.2 * H * math.cos(w * t + 1.5), -0.12 * H + 0.14 * H * math.sin(w * t + 1.5)),
        )

    def _sit(self) -> Pose:
        H, half, t = self.P.height, self.P.half, self.time
        pelvis = (0.0, half - 0.015 * H)
        return self._assemble(
            pelvis, -0.14,
            (0.12 * H + 0.05 * H * math.sin(t * 2.4 + 1.8), half + 0.3 * H),
            (0.16 * H + 0.05 * H * math.sin(t * 2.4), half + 0.28 * H),
            (-0.16 * H, half - 0.01 * H), (-0.11 * H, half - 0.01 * H),
            hands_abs=True,
        )

    def _place(self) -> Pose:
        H, half = self.P.height, self.P.half
        k = smoothstep(self.state_time / 0.2)
        pelvis = (0.0, half - 0.94 * self._leg)
        base = self._assemble(
            pelvis, 0.12 * k,
            (-0.07 * H, half), (0.1 * H, half),
            (-0.1 * H, self._arm * 0.85), (0.0, self._arm * 0.85),
        )
        reach = (0.35 * H, -0.2 * H)
        if self.fig.activity_point is not None:
            bx, by = self.fig.body.position
            px, py = self.fig.activity_point
            reach = ((px - bx) * self.fig.facing, py - by)
        neck = base["neck"]
        hand = (neck[0] + (reach[0] - neck[0]) * k, neck[1] + (reach[1] - neck[1]) * k)
        elbow, hand = two_bone_ik(neck, hand, self.P.upper_arm, self.P.forearm, 1.0)
        base["elbow_f"], base["hand_f"] = elbow, hand
        return base

    def _look(self) -> Pose:
        """Hand shading the eyes, leaning in, slowly scanning side to side."""
        H, half, t = self.P.height, self.P.half, self.state_time
        scan = math.sin(t * 2.5) * 0.04
        pelvis = (0.0, half - 0.95 * self._leg)
        return self._assemble(
            pelvis, 0.1 + scan,
            (-0.07 * H, half), (0.09 * H, half),
            (-0.06 * H, self._arm * 0.9), (0.2 * H, -0.19 * H),
            elbow_f=-1.0,
        )

    def _point(self) -> Pose:
        """Arm stretched toward activity_point (any direction), other hand on hip."""
        H, half = self.P.height, self.P.half
        k = smoothstep(self.state_time / 0.25)
        pelvis = (0.0, half - 0.96 * self._leg)
        base = self._assemble(
            pelvis, 0.03,
            (-0.08 * H, half), (0.1 * H, half),
            (-0.1 * H, self._arm * 0.55), (0.02 * H, self._arm * 0.9),
        )
        if self.fig.activity_point is None:
            return base
        bx, by = self.fig.body.position
        px, py = self.fig.activity_point
        neck = base["neck"]
        dx, dy = (px - bx) * self.fig.facing - neck[0], (py - by) - neck[1]
        d = math.hypot(dx, dy) or 1.0
        reach = self._arm * 0.98
        hand = (neck[0] + dx / d * reach * k, neck[1] + dy / d * reach * k + (1 - k) * self._arm * 0.8)
        base["elbow_f"], base["hand_f"] = two_bone_ik(neck, hand, self.P.upper_arm, self.P.forearm, 1.0)
        # Tilt the head toward the target a little.
        hx, hy = base["head"]
        nx, ny = neck
        tilt = max(-0.35, min(0.35, math.atan2(dx, -dy) * 0.25))
        c, s = math.cos(tilt), math.sin(tilt)
        rx, ry = hx - nx, hy - ny
        base["head"] = (nx + rx * c - ry * s, ny + rx * s + ry * c)
        return base

    def _wave(self) -> Pose:
        H, half, t = self.P.height, self.P.half, self.state_time
        br = math.sin(self.time * 2.2)
        pelvis = (0.0, half - 0.96 * self._leg + br * 0.5)
        return self._assemble(
            pelvis, -0.02,
            (-0.07 * H, half), (0.08 * H, half),
            (-0.07 * H, self._arm * 0.93),
            (0.14 * H + 0.07 * H * math.sin(t * 11), -0.3 * H),
            elbow_f=-1.0,
        )

    def _grabbed(self) -> Pose:
        H, half = self.P.height, self.P.half
        pelvis = (0.0, half - 0.96 * self._leg)
        return self._assemble(
            pelvis, 0.0,
            (-0.04 * H, half + 0.02 * H), (0.04 * H, half + 0.02 * H),
            (-0.12 * H, -0.05 * H), (0.12 * H, -0.05 * H),
        )

    def _ride(self) -> Pose:
        """Both hands up on the cursor tip, legs kicking happily."""
        H, half, t = self.P.height, self.P.half, self.time
        kick = math.sin(t * 7)
        pelvis = (0.0, half - 0.96 * self._leg)
        grip = (0.0, -0.36 * H)  # the cursor, relative to the body center
        return self._assemble(
            pelvis, 0.0,
            (-0.06 * H + 0.07 * H * kick, half - 0.05 * H), (0.06 * H - 0.07 * H * kick, half - 0.08 * H),
            (grip[0] - 0.02 * H, grip[1]), (grip[0] + 0.02 * H, grip[1]),
            hands_abs=True, elbow=-1.0, elbow_f=1.0,
        )

    def _knocked(self) -> Pose:
        H, half = self.P.height, self.P.half
        ground = half
        pelvis = (0.08 * H, ground - 0.05 * H)
        return self._assemble(
            pelvis, -1.47,
            (0.52 * H, ground - 0.02 * H), (0.36 * H, ground - 0.14 * H),
            (-0.18 * H, ground - 0.02 * H), (0.02 * H, ground - 0.05 * H),
            hands_abs=True,
        )
