"""A see-through, click-through layer over the fight's monitor that draws fight effects (figure/effects.py):
hit sparks, magic orbs with trails, energy bursts, and shield bubbles. Hidden from screen capture."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from stickfigure.config import CONFIG
from stickfigure.figure.effects import Effects
from stickfigure.figure.skeleton import Proportions
from stickfigure.overlay.render import draw_figure
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
        props = Proportions(CONFIG.figure_height)
        for g in fx.ghosts:  # afterimages
            k = g.age / g.life
            p.setOpacity(0.45 * (1 - k))
            draw_figure(p, g.pose, (g.x - ox, g.y - oy), props, _c(g.color, 255), CONFIG.stroke)
        p.setOpacity(1.0)
        for d in fx.dust_puffs:
            k = d.age / d.life
            for i, dx in enumerate((-1.0, -0.4, 0.4, 1.0)):
                r = (8 + 18 * k) * d.size * (1.2 - abs(dx) * 0.3)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(200, 190, 170, round(150 * (1 - k))))
                p.drawEllipse(QPointF(d.x - ox + dx * (14 + 40 * k) * d.size, d.y - oy - 6 - 10 * k - i % 2 * 4), r, r * 0.7)
        for a in fx.auras:  # charging up
            k = a.age / a.life
            c = QPointF(a.x - ox, a.y - oy)
            for i in range(3):
                kk = (k + i / 3) % 1.0
                r = a.radius * (1.4 - kk)
                p.setPen(QPen(_c(a.color, 220 * kk), 3))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, r, r)
            p.setPen(Qt.NoPen)
            p.setBrush(_c(a.color, 200))
            for i in range(10):
                ang = i / 10 * math.tau + a.age * 6
                rr = a.radius * (1.2 - k) * (0.6 + 0.4 * math.sin(i * 7.3))
                p.drawEllipse(QPointF(c.x() + math.cos(ang) * rr, c.y() + math.sin(ang) * rr), 3, 3)
        for b in fx.beams:
            k = b.age / b.life
            grow = min(1.0, b.age / 0.08)
            x2 = b.x1 + (b.x2 - b.x1) * grow
            y2 = b.y1 + (b.y2 - b.y1) * grow
            fade = 1 - max(0.0, (k - 0.7) / 0.3)
            wob = 1 + 0.15 * math.sin(b.age * 60)
            for width, alpha, col in ((34, 70, b.color), (20, 170, b.color), (8, 255, (255, 255, 255))):
                p.setPen(QPen(_c(col, alpha * fade), width * wob, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(b.x1 - ox, b.y1 - oy), QPointF(x2 - ox, y2 - oy))
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
        for i in fx.impacts:  # heavy hits: white flash + speed lines
            k = i.age / i.life
            c = QPointF(i.x - ox, i.y - oy)
            r = (18 + 60 * k) * i.size
            p.setPen(QPen(QColor(255, 255, 255, round(255 * (1 - k))), 5 * (1 - k) + 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r, r)
            p.setPen(QPen(QColor(255, 255, 255, round(230 * (1 - k))), 2))
            for j in range(12):
                ang = j / 12 * math.tau + 0.13
                p.drawLine(QPointF(c.x() + math.cos(ang) * r * 1.1, c.y() + math.sin(ang) * r * 1.1),
                           QPointF(c.x() + math.cos(ang) * r * 1.9, c.y() + math.sin(ang) * r * 1.9))
        for s in fx.sparks:
            k = s.age / s.life
            lx, ly = s.x - ox, s.y - oy
            rad = (10 + 26 * k) * s.size
            p.setPen(QPen(_c(s.color, 255 * (1 - k)), 3, Qt.SolidLine, Qt.RoundCap))
            for i in range(8):
                a = i / 8 * math.tau + 0.3
                p.drawLine(QPointF(lx + math.cos(a) * rad * 0.4, ly + math.sin(a) * rad * 0.4),
                           QPointF(lx + math.cos(a) * rad, ly + math.sin(a) * rad))
        self._paint_bars(p, ox, oy)
        for b in fx.banners:  # ROUND 1 / FIGHT! / K.O.!
            k = b.age / b.life
            pop = 1.6 - 0.6 * min(1.0, b.age / 0.12)  # slams in big, settles
            alpha = 1.0 if k < 0.75 else (1 - k) / 0.25
            f = QFont("Impact")
            f.setPixelSize(round(64 * b.size * pop))
            f.setItalic(True)
            path = QPainterPath()
            path.addText(0, 0, f, b.text)
            br = path.boundingRect()
            path.translate(b.x - ox - br.width() / 2, b.y - oy + br.height() / 2)
            p.setOpacity(alpha)
            p.setPen(QPen(QColor(20, 20, 26), 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(_c(b.color, 255))
            p.drawPath(path)
            p.setOpacity(1.0)
        p.end()

    def _paint_bars(self, p: QPainter, ox: float, oy: float) -> None:
        if self.fx.bars is None:
            return
        cx, y = self.fx.bars_at
        cx, y = cx - ox, y - oy
        w, h, gap = 230.0, 16.0, 30.0
        f = QFont("Segoe UI")
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        for i, fighter in enumerate(self.fx.bars):
            left = cx - gap / 2 - w if i == 0 else cx + gap / 2
            frame = QRectF(left, y, w, h)
            p.setPen(QPen(QColor(20, 20, 26), 3))
            p.setBrush(QColor(40, 40, 48, 220))
            p.drawRoundedRect(frame, 4, 4)
            lost = frame.adjusted(2, 2, -2, -2)
            fill_w = lost.width() * max(0.0, fighter.shown) / 100
            real_w = lost.width() * max(0.0, fighter.hp) / 100
            # the red "damage" part drains after the real bar (fighting-game style)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(220, 60, 50))
            p.drawRect(QRectF(lost.right() - fill_w if i == 0 else lost.left(), lost.top(), fill_w, lost.height()))
            g = QLinearGradient(lost.topLeft(), lost.bottomLeft())
            g.setColorAt(0, QColor(255, 235, 120))
            g.setColorAt(1, QColor(240, 170, 40))
            p.setBrush(g)
            p.drawRect(QRectF(lost.right() - real_w if i == 0 else lost.left(), lost.top(), real_w, lost.height()))
            p.setPen(_c(fighter.color, 255) if sum(fighter.color) > 150 else QColor(235, 235, 235))
            name_rect = QRectF(left, y - 20, w, 18)
            p.drawText(name_rect, (Qt.AlignRight if i == 0 else Qt.AlignLeft) | Qt.AlignVCenter, fighter.name)
