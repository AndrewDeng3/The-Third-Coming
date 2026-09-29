"""Draws a stick figure pose with QPainter (shared by the overlay window and offscreen previews)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from stickfigure.figure.skeleton import BACK_BONES, FRONT_BONES, Pose, Proportions

COLORS = {
    "Orange": QColor(247, 147, 30),
    "Red": QColor(222, 45, 38),
    "Green": QColor(57, 181, 74),
    "Blue": QColor(41, 121, 255),
    "Yellow": QColor(250, 210, 30),
    "Black": QColor(20, 20, 22),
}
OUTLINE = QColor(15, 15, 18, 200)
OUTLINE_ON_BLACK = QColor(235, 235, 235, 220)
HIT = QColor(0, 0, 0, 1)


def draw_figure(
    p: QPainter,
    pose: Pose,
    origin: tuple[float, float],
    props: Proportions,
    color: QColor,
    stroke: float,
    hit_width: float = 0.0,
) -> None:
    ox, oy = origin

    def pt(j: str) -> QPointF:
        x, y = pose[j]
        return QPointF(ox + x, oy + y)

    head = pt("head")
    r = props.head_r

    if hit_width > 0:
        # Invisible (alpha=1) fat outline so the thin figure is easy to grab.
        path = QPainterPath()
        for a, b in (("pelvis", "neck"),) + BACK_BONES + FRONT_BONES:
            path.moveTo(pt(a))
            path.lineTo(pt(b))
        p.strokePath(path, QPen(HIT, hit_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setPen(Qt.NoPen)
        p.setBrush(HIT)
        p.drawEllipse(head, r + hit_width / 2, r + hit_width / 2)

    outline = OUTLINE_ON_BLACK if color.lightness() < 40 else OUTLINE
    back = color.darker(135)

    def limbs(bones, c: QColor) -> None:
        path = QPainterPath()
        for a, b in bones:
            path.moveTo(pt(a))
            path.lineTo(pt(b))
        p.strokePath(path, QPen(outline, stroke + 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.strokePath(path, QPen(c, stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    limbs(BACK_BONES, back)
    limbs((("pelvis", "neck"),) + FRONT_BONES, color)
    p.setPen(QPen(outline, 1.5))
    p.setBrush(color)
    p.drawEllipse(head, r, r)


# -- weapons ---------------------------------------------------------------------------------------------------

WEAPON_COLORS = {"sword": QColor(220, 225, 235), "staff": QColor(107, 74, 43), "pickaxe": QColor(107, 74, 43)}


def weapon_segment(pose: Pose, kind: str, height: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """(grip, tip) of a held weapon, in the pose's coordinates. The tip is what hits (see figure/sparring.py)."""
    hx, hy = pose["hand_f"]
    if kind == "staff":  # held in both hands: along the line between them (or the forearm if they're together)
        bx, by = pose["hand_b"]
        dx, dy = hx - bx, hy - by
        if math.hypot(dx, dy) < 0.06 * height:
            ex, ey = pose["elbow_f"]
            dx, dy = hx - ex, hy - ey
    else:  # swords and pickaxes point along the forearm
        ex, ey = pose["elbow_f"]
        dx, dy = hx - ex, hy - ey
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    reach = {"sword": 0.46, "staff": 0.5, "pickaxe": 0.36}.get(kind, 0.4) * height
    back = {"sword": 0.07, "staff": 0.45, "pickaxe": 0.06}.get(kind, 0.06) * height
    return (hx - ux * back, hy - uy * back), (hx + ux * reach, hy + uy * reach)


def draw_weapon(p: QPainter, pose: Pose, origin: tuple[float, float], props: Proportions, kind: str,
                accent: QColor | None = None) -> None:
    ox, oy = origin
    H = props.height
    (gx, gy), (tx, ty) = weapon_segment(pose, kind, H)
    hx, hy = pose["hand_f"]
    dx, dy = tx - gx, ty - gy
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    px_, py_ = -uy, ux  # perpendicular
    P = lambda x, y: QPointF(ox + x, oy + y)  # noqa: E731
    outline = QPen(OUTLINE, 7, Qt.SolidLine, Qt.RoundCap)
    if kind == "sword":
        guard = (hx + ux * 0.04 * H, hy + uy * 0.04 * H)
        p.setPen(outline)
        p.drawLine(P(*guard), P(tx, ty))
        p.setPen(QPen(accent or WEAPON_COLORS["sword"], 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(P(*guard), P(tx, ty))  # blade
        p.setPen(QPen(QColor(90, 60, 35), 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(P(gx, gy), P(*guard))  # grip
        p.setPen(QPen(QColor(200, 160, 60), 4, Qt.SolidLine, Qt.RoundCap))
        w = 0.06 * H
        p.drawLine(P(guard[0] - px_ * w, guard[1] - py_ * w), P(guard[0] + px_ * w, guard[1] + py_ * w))
    elif kind == "staff":
        color = accent or WEAPON_COLORS["staff"]
        if accent is not None:  # glowing staff
            p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 70), 12, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(P(gx, gy), P(tx, ty))
        p.setPen(outline)
        p.drawLine(P(gx, gy), P(tx, ty))
        p.setPen(QPen(color, 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(P(gx, gy), P(tx, ty))
    elif kind == "pickaxe":  # Minecraft-style: wooden handle, diamond head
        p.setPen(outline)
        p.drawLine(P(gx, gy), P(tx, ty))
        p.setPen(QPen(WEAPON_COLORS["pickaxe"], 4, Qt.SolidLine, Qt.FlatCap))
        p.drawLine(P(gx, gy), P(tx, ty))
        w = 0.13 * H
        a = (tx - px_ * w - ux * 0.03 * H, ty - py_ * w - uy * 0.03 * H)
        b = (tx + px_ * w - ux * 0.03 * H, ty + py_ * w - uy * 0.03 * H)
        p.setPen(QPen(OUTLINE, 8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(P(*a), P(tx, ty))
        p.drawLine(P(tx, ty), P(*b))
        p.setPen(QPen(accent or QColor(90, 225, 215), 5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(P(*a), P(tx, ty))
        p.drawLine(P(tx, ty), P(*b))
