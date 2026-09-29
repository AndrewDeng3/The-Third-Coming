"""A see-through, click-through layer over the fight's monitor that draws fight effects (figure/effects.py):
hit sparks, magic orbs with trails, energy bursts, and shield bubbles. Hidden from screen capture."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from stickfigure.figure.effects import Effects
from stickfigure.win import win32


def _c(rgb, a: float) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], max(0, min(255, round(a))))


class EffectsWindow(QWidget):
    def __init__(self, fx: Effects):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.fx = fx
        self.hwnd = 0

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self.hwnd:
            self.hwnd = int(self.winId())
            win32.add_ex_style(self.hwnd, win32.WS_EX_NOACTIVATE | win32.WS_EX_TRANSPARENT)
            win32.exclude_from_capture(self.hwnd)

    def cover(self, rect) -> None:
        """Cover the given monitor rect (physical px)."""
        g = (int(rect.left), int(rect.top), int(rect.width), int(rect.height))
        if (self.x(), self.y(), self.width(), self.height()) != g:
            self.setGeometry(*g)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        ox, oy = self.x(), self.y()
        fx = self.fx
        for s in fx.shields:
            k = s.age / s.life
            c = QPointF(s.x - ox, s.y - oy)
            g = QRadialGradient(c, s.radius)
            g.setColorAt(0.0, _c(s.color, 0))
            g.setColorAt(0.75, _c(s.color, 60 * (1 - k)))
            g.setColorAt(1.0, _c(s.color, 200 * (1 - k)))
            p.setPen(QPen(_c(s.color, 230 * (1 - k)), 3))
            p.setBrush(g)
            p.drawEllipse(c, s.radius, s.radius)
        for o in fx.orbs:
            a = 255 * o.fade
            r = 13 * o.size
            c = QPointF(o.x - ox, o.y - oy)
            tail = -1 if o.vx > 0 else 1
            for i in range(1, 6):  # trail
                p.setPen(Qt.NoPen)
                p.setBrush(_c(o.color, a * 0.35 * (1 - i / 6)))
                p.drawEllipse(QPointF(c.x() + tail * i * r * 0.7, c.y() + math.sin(o.age * 30 + i) * 2),
                              r * (1 - i / 8), r * (1 - i / 8))
            g = QRadialGradient(c, r * 1.8)
            g.setColorAt(0.0, QColor(255, 255, 255, round(a)))
            g.setColorAt(0.35, _c(o.color, a))
            g.setColorAt(1.0, _c(o.color, 0))
            p.setBrush(g)
            p.drawEllipse(c, r * 1.8, r * 1.8)
        for b in fx.bursts:
            k = b.age / b.life
            c = QPointF(b.x - ox, b.y - oy)
            rad = (20 + 70 * k) * b.size
            g = QRadialGradient(c, rad)
            g.setColorAt(0.0, QColor(255, 255, 255, round(220 * (1 - k))))
            g.setColorAt(0.5, _c(b.color, 180 * (1 - k)))
            g.setColorAt(1.0, _c(b.color, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, rad, rad)
            p.setPen(QPen(_c(b.color, 220 * (1 - k)), 3))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, rad * 0.9, rad * 0.9)
        for s in fx.sparks:
            k = s.age / s.life
            lx, ly = s.x - ox, s.y - oy
            rad = (10 + 26 * k) * s.size
            p.setPen(QPen(_c(s.color, 255 * (1 - k)), 3, Qt.SolidLine, Qt.RoundCap))
            for i in range(8):
                a = i / 8 * math.tau + 0.3
                p.drawLine(QPointF(lx + math.cos(a) * rad * 0.4, ly + math.sin(a) * rad * 0.4),
                           QPointF(lx + math.cos(a) * rad, ly + math.sin(a) * rad))
        p.end()
