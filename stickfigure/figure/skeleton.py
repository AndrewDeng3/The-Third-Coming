"""Stick figure skeleton: joint names, bone lengths, 2-bone IK, and pose helpers.

Poses are dicts of joint -> (x, y) in the figure's local frame: origin at the
collider center, y down, canonical facing = +x. Mirroring for facing happens last.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Vec = tuple[float, float]
Pose = dict[str, Vec]

JOINTS = (
    "pelvis", "neck", "head",
    "knee_b", "foot_b", "knee_f", "foot_f",
    "elbow_b", "hand_b", "elbow_f", "hand_f",
)
# (parent, child) - drawn back limbs first so front limbs overlap them.
BACK_BONES = (("pelvis", "knee_b"), ("knee_b", "foot_b"), ("neck", "elbow_b"), ("elbow_b", "hand_b"))
FRONT_BONES = (("pelvis", "knee_f"), ("knee_f", "foot_f"), ("neck", "elbow_f"), ("elbow_f", "hand_f"))
BONES = (("pelvis", "neck"),) + BACK_BONES + FRONT_BONES + (("neck", "head"),)


@dataclass(frozen=True)
class Proportions:
    height: float

    @property
    def half(self) -> float:
        return self.height / 2

    @property
    def head_r(self) -> float:
        return 0.105 * self.height

    @property
    def neck_head(self) -> float:
        return self.head_r + 0.02 * self.height

    @property
    def torso(self) -> float:
        return 0.28 * self.height

    @property
    def thigh(self) -> float:
        return 0.21 * self.height

    @property
    def shin(self) -> float:
        return 0.21 * self.height

    @property
    def upper_arm(self) -> float:
        return 0.17 * self.height

    @property
    def forearm(self) -> float:
        return 0.17 * self.height

    def bone_length(self, a: str, b: str) -> float:
        kind = {
            ("pelvis", "neck"): self.torso, ("neck", "head"): self.neck_head,
            ("pelvis", "knee_b"): self.thigh, ("pelvis", "knee_f"): self.thigh,
            ("knee_b", "foot_b"): self.shin, ("knee_f", "foot_f"): self.shin,
            ("neck", "elbow_b"): self.upper_arm, ("neck", "elbow_f"): self.upper_arm,
            ("elbow_b", "hand_b"): self.forearm, ("elbow_f", "hand_f"): self.forearm,
        }
        return kind[(a, b)]


def two_bone_ik(root: Vec, target: Vec, l1: float, l2: float, bend: float) -> tuple[Vec, Vec]:
    """Analytic 2-bone IK. Returns (middle joint, end effector).

    `bend` = +1 / -1 picks which side the middle joint goes (in y-down coords,
    -1 puts a downward-pointing leg's knee toward +x). Unreachable targets are
    clamped to full extension along the root->target line.
    """
    dx, dy = target[0] - root[0], target[1] - root[1]
    d = math.hypot(dx, dy)
    lo, hi = abs(l1 - l2) + 1e-3, l1 + l2 - 1e-3
    if d < 1e-6:
        dx, dy, d = 0.0, 1.0, lo
    dc = min(max(d, lo), hi)
    phi = math.atan2(dy, dx)
    cos_a = (l1 * l1 + dc * dc - l2 * l2) / (2 * l1 * dc)
    a = math.acos(max(-1.0, min(1.0, cos_a)))
    mid = (root[0] + l1 * math.cos(phi + bend * a), root[1] + l1 * math.sin(phi + bend * a))
    end = (root[0] + dx / d * dc, root[1] + dy / d * dc)
    return mid, end


def lerp(a: Vec, b: Vec, t: float) -> Vec:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def blend(a: Pose, b: Pose, t: float) -> Pose:
    return {j: lerp(a[j], b[j], t) for j in JOINTS}


def mirror(p: Pose, facing: int) -> Pose:
    return p if facing >= 0 else {j: (-x, y) for j, (x, y) in p.items()}


def rotate(p: Pose, angle: float) -> Pose:
    c, s = math.cos(angle), math.sin(angle)
    return {j: (x * c - y * s, x * s + y * c) for j, (x, y) in p.items()}


def smoothstep(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)
