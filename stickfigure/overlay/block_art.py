"""Procedural art for each block kind (no image assets)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen


def _c(hex_: str, a: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(a)
    return c


def paint_block(p: QPainter, r: QRectF, kind: str, t: float = 0.0) -> None:
    """Draw one block of `kind` filling rect `r`. `t` (seconds) animates shiny/bouncy kinds."""
    s = r.width()
    lw = max(2.0, s / 22)
    radius = s * 0.07
    painters = {
        "wood": _wood, "stone": _stone, "brick": _brick, "grass": _grass,
        "glass": _glass, "gold": _gold, "ice": _ice, "bouncy": _bouncy,
    }
    painters.get(kind, _wood)(p, r, s, lw, radius, t)


def _outline(p, r, lw, radius, color):
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, lw))
    p.drawRoundedRect(r, radius, radius)


def _wood(p, r, s, lw, radius, t):
    p.setPen(Qt.NoPen)
    p.setBrush(_c("#c48a4a"))
    p.drawRoundedRect(r, radius, radius)
    inner = r.adjusted(s * 0.12, s * 0.12, -s * 0.12, -s * 0.12)
    p.setPen(QPen(_c("#7a4e24"), lw * 0.8))
    p.drawRect(inner)
    p.setPen(QPen(_c("#7a4e24"), lw * 1.3, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(inner.topLeft(), inner.bottomRight())
    p.setPen(QPen(_c("#e0b070"), lw * 0.7))
    p.drawLine(QPointF(r.left() + s * 0.08, r.top() + lw), QPointF(r.right() - s * 0.08, r.top() + lw))
    _outline(p, r, lw, radius, _c("#7a4e24"))


def _stone(p, r, s, lw, radius, t):
    p.setPen(Qt.NoPen)
    p.setBrush(_c("#8d9096"))
    p.drawRoundedRect(r, radius, radius)
    p.setPen(QPen(_c("#5f6268"), lw * 0.7))
    # Irregular cobbles.
    for (x0, y0, x1, y1) in ((0.1, 0.1, 0.55, 0.45), (0.6, 0.1, 0.9, 0.5), (0.1, 0.55, 0.4, 0.9), (0.45, 0.55, 0.9, 0.9)):
        p.setBrush(_c("#a3a6ac"))
        p.drawRoundedRect(QRectF(r.left() + x0 * s, r.top() + y0 * s, (x1 - x0) * s, (y1 - y0) * s), s * 0.08, s * 0.08)
    _outline(p, r, lw, radius, _c("#4f5257"))


def _brick(p, r, s, lw, radius, t):
    p.setPen(Qt.NoPen)
    p.setBrush(_c("#d8d2c4"))  # mortar
    p.drawRoundedRect(r, radius, radius)
    rows = 4
    h = s / rows
    p.setBrush(_c("#b5452e"))
    for i in range(rows):
        offset = 0 if i % 2 == 0 else s / 4
        x = r.left() - offset
        while x < r.right():
            brick = QRectF(max(x, r.left()) + lw * 0.5, r.top() + i * h + lw * 0.5,
                           min(x + s / 2, r.right()) - max(x, r.left()) - lw, h - lw)
            if brick.width() > 1:
                p.drawRect(brick)
            x += s / 2
    _outline(p, r, lw, radius, _c("#7d2d1d"))


def _grass(p, r, s, lw, radius, t):
    p.setPen(Qt.NoPen)
    p.setBrush(_c("#8a5a34"))  # dirt
    p.drawRoundedRect(r, radius, radius)
    p.setBrush(_c("#6b4426"))
    for fx, fy in ((0.25, 0.6), (0.7, 0.75), (0.5, 0.45), (0.8, 0.5), (0.2, 0.85)):
        p.drawEllipse(QPointF(r.left() + fx * s, r.top() + fy * s), s * 0.05, s * 0.04)
    top = QPainterPath()
    top.moveTo(r.left(), r.top() + s * 0.3)
    for i in range(9):  # grassy fringe
        x = r.left() + (i + 0.5) * s / 9
        top.lineTo(x, r.top() + s * (0.38 if i % 2 else 0.3))
    top.lineTo(r.right(), r.top() + s * 0.3)
    top.lineTo(r.right(), r.top() + radius)
    top.quadTo(r.right(), r.top(), r.right() - radius, r.top())
    top.lineTo(r.left() + radius, r.top())
    top.quadTo(r.left(), r.top(), r.left(), r.top() + radius)
    top.closeSubpath()
    p.setBrush(_c("#4caf3f"))
    p.drawPath(top)
    _outline(p, r, lw, radius, _c("#3d2715"))


def _glass(p, r, s, lw, radius, t):
    p.setPen(Qt.NoPen)
    p.setBrush(_c("#bfe6ff", 90))
    p.drawRoundedRect(r, radius, radius)
    p.setPen(QPen(_c("#ffffff", 200), lw * 1.2, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(r.left() + s * 0.2, r.top() + s * 0.55), QPointF(r.left() + s * 0.55, r.top() + s * 0.2))
    p.drawLine(QPointF(r.left() + s * 0.35, r.top() + s * 0.7), QPointF(r.left() + s * 0.5, r.top() + s * 0.55))
    _outline(p, r, lw, radius, _c("#7fb8d8", 230))


def _gold(p, r, s, lw, radius, t):
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, _c("#fff2a8"))
    g.setColorAt(0.45, _c("#f2c230"))
    g.setColorAt(1, _c("#b8860b"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(r, radius, radius)
    # A glint that sweeps across now and then.
    phase = (t * 0.35) % 1.0
    if phase < 0.3:
        x = r.left() + (phase / 0.3) * s * 1.4 - s * 0.2
        p.setPen(QPen(_c("#ffffff", 170), lw * 2.2))
        p.setClipRect(r)
        p.drawLine(QPointF(x, r.top()), QPointF(x - s * 0.35, r.bottom()))
        p.setClipping(False)
    inner = r.adjusted(s * 0.14, s * 0.14, -s * 0.14, -s * 0.14)
    p.setPen(QPen(_c("#a07406"), lw * 0.7))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(inner, radius, radius)
    _outline(p, r, lw, radius, _c("#8a6206"))


def _ice(p, r, s, lw, radius, t):
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, _c("#e8fbff", 235))
    g.setColorAt(1, _c("#9fdcf0", 235))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(r, radius, radius)
    p.setPen(QPen(_c("#ffffff", 220), lw * 0.8))
    p.drawLine(QPointF(r.left() + s * 0.15, r.top() + s * 0.25), QPointF(r.left() + s * 0.45, r.top() + s * 0.15))
    p.setPen(QPen(_c("#6fb7cf"), lw * 0.6))
    p.drawLine(QPointF(r.left() + s * 0.6, r.top() + s * 0.4), QPointF(r.left() + s * 0.75, r.top() + s * 0.75))
    p.drawLine(QPointF(r.left() + s * 0.68, r.top() + s * 0.57), QPointF(r.left() + s * 0.85, r.top() + s * 0.52))
    _outline(p, r, lw, radius, _c("#5aa9c4"))


def _bouncy(p, r, s, lw, radius, t):
    squish = 1 + 0.04 * math.sin(t * 5)  # a slow jiggle
    body = QRectF(r.left(), r.top() + s * (1 - 1 / squish) / 2, s, s / squish)
    g = QLinearGradient(body.topLeft(), body.bottomLeft())
    g.setColorAt(0, _c("#ff9ad5"))
    g.setColorAt(1, _c("#e0479e"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(body, s * 0.22, s * 0.22)
    p.setBrush(_c("#ffffff", 150))
    p.drawEllipse(QPointF(body.left() + s * 0.28, body.top() + s * 0.25), s * 0.1, s * 0.06)
    # A little face.
    p.setBrush(_c("#5a1640"))
    p.drawEllipse(QPointF(body.center().x() - s * 0.13, body.center().y()), s * 0.045, s * 0.06)
    p.drawEllipse(QPointF(body.center().x() + s * 0.13, body.center().y()), s * 0.045, s * 0.06)
    p.setPen(QPen(_c("#5a1640"), lw * 0.7, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(body.center().x() - s * 0.1, body.center().y() + s * 0.02, s * 0.2, s * 0.14), 200 * 16, 140 * 16)
    p.setPen(QPen(_c("#a3246c"), lw))
    p.drawRoundedRect(body, s * 0.22, s * 0.22)
