"""Draws a stick figure pose with QPainter (shared by the overlay window and offscreen previews)."""

from __future__ import annotations

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
