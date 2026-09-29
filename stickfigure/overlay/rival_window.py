"""Draws a sparring rival (see figure/rival.py): click-through, always on top, hidden from screen capture.

Extras: a name tag while it arrives, King Orange's crown and cape, a warp ring on arrival, and hit sparks.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from stickfigure.config import CONFIG, Config
from stickfigure.figure.rival import Rival
from stickfigure.overlay.render import draw_figure
from stickfigure.win import win32


class RivalWindow(QWidget):
    def __init__(self, rival: Rival, scale: float, cfg: Config = CONFIG):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.rival = rival
        self.cfg = cfg
        self.ui_scale = scale
        self.color = QColor(*rival.who.color)
        self._size = int(cfg.figure_height * 3.4)
        self.setFixedSize(self._size, self._size)
        self.hwnd = 0
        self._origin = (0, 0)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self.hwnd:
            self.hwnd = int(self.winId())
            win32.add_ex_style(self.hwnd, win32.WS_EX_NOACTIVATE | win32.WS_EX_TRANSPARENT)
            win32.exclude_from_capture(self.hwnd)

    def sync(self) -> None:
        x, y = self.rival.puppet.body.position
        self._origin = (round(x - self._size / 2), round(y - self._size / 2))
        if self.hwnd:
            win32.move_window(self.hwnd, *self._origin)
            win32.bring_to_top(self.hwnd)
        self.update()

    def paintEvent(self, _e) -> None:
        r = self.rival
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        ox, oy = self._origin
        bx, by = r.puppet.body.position
        cx, cy = bx - ox, by - oy  # body center in window coordinates
        pose = r.anim.pose
        P = r.anim.P
        p.setOpacity(r.alpha)

        # Warp-in ring
        if r.age < 0.45:
            k = r.age / 0.45
            p.setPen(QPen(QColor(255, 255, 255, round(220 * (1 - k))), 4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), P.height * (0.3 + 0.9 * k), P.height * (0.3 + 0.9 * k))

        # King Orange's cape (behind the body)
        hx, hy = pose["head"]
        nx, ny = pose["neck"]
        px, py = pose["pelvis"]
        if r.who.extra == "crown":
            back = -r.puppet.facing
            cape = QPainterPath(QPointF(cx + nx, cy + ny))
            cape.lineTo(cx + nx + back * P.height * 0.18, cy + py + P.height * 0.08)
            cape.lineTo(cx + px + back * P.height * 0.04, cy + py + P.height * 0.1)
            cape.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(112, 32, 64))
            p.drawPath(cape)

        draw_figure(p, pose, (cx, cy), P, self.color, self.cfg.stroke)

        # Crown
        if r.who.extra == "crown":
            hr = P.head_r
            base_y = cy + hy - hr * 0.75
            crown = QPainterPath(QPointF(cx + hx - hr * 0.8, base_y))
            for i, dx in enumerate((-0.8, -0.5, -0.25, 0.0, 0.25, 0.5, 0.8)):
                crown.lineTo(cx + hx + dx * hr, base_y - (hr * 0.8 if i % 2 == 1 else hr * 0.25))
            crown.lineTo(cx + hx + hr * 0.8, base_y)
            crown.closeSubpath()
            p.setPen(QPen(QColor(120, 80, 0), 1.5))
            p.setBrush(QColor(240, 192, 0))
            p.drawPath(crown)

        # Name tag while arriving
        if r.age < 2.2:
            f = QFont("Segoe UI")
            f.setPixelSize(round(13 * self.ui_scale))
            f.setBold(True)
            p.setFont(f)
            fm = QFontMetricsF(f)
            text = r.who.name
            w = fm.horizontalAdvance(text) + 14
            tag = QRectF(cx - w / 2, cy + hy - P.head_r - fm.height() - 16, w, fm.height() + 6)
            p.setOpacity(r.alpha * min(1.0, (2.2 - r.age) * 2))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 20, 26, 210))
            p.drawRoundedRect(tag, 6, 6)
            p.setPen(QColor(*r.who.color) if sum(r.who.color) > 150 else QColor(235, 235, 235))
            p.drawText(tag, Qt.AlignCenter, text)
            p.setOpacity(r.alpha)

        # Hit sparks
        p.setOpacity(1.0)
        for sx, sy, age in r.sparks:
            k = age / 0.35
            lx, ly = sx - ox, sy - oy
            rad = 10 + 26 * k
            p.setPen(QPen(QColor(255, 240, 150, round(255 * (1 - k))), 3, Qt.SolidLine, Qt.RoundCap))
            for i in range(8):
                a = i / 8 * math.tau + 0.3
                p.drawLine(QPointF(lx + math.cos(a) * rad * 0.4, ly + math.sin(a) * rad * 0.4),
                           QPointF(lx + math.cos(a) * rad, ly + math.sin(a) * rad))
        p.end()
