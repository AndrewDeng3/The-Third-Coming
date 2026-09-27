"""Pulsing outline around a located UI element. Click-through and hidden from screen capture
(so it never pollutes the next OCR read)."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget

from stickfigure.win import win32
from stickfigure.world.geometry import Rect

ACCENT = QColor(247, 147, 30)


class Highlight(QWidget):
    def __init__(self, scale: float):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint
            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.s = scale
        self.font_ = QFont("Segoe UI")
        self.font_.setPixelSize(round(13 * scale))
        self.font_.setBold(True)
        self.label = ""
        self._until = 0.0
        self._t0 = 0.0
        self.hwnd = 0
        self.pad = round(8 * scale)
        self.label_h = round(24 * scale)

    def show_rect(self, rect: Rect, label: str, seconds: float = 7.0) -> None:
        self.label = label
        self._t0 = time.monotonic()
        self._until = self._t0 + seconds
        p, lh = self.pad, self.label_h
        x, y = round(rect.left) - p, round(rect.top) - p - lh
        w, h = round(rect.width) + 2 * p, round(rect.height) + 2 * p + lh
        fm = QFontMetricsF(self.font_)
        w = max(w, round(fm.horizontalAdvance(label) + 3 * p))
        self.setFixedSize(w, h)
        self.show()
        if not self.hwnd:
            self.hwnd = int(self.winId())
            win32.add_ex_style(self.hwnd, win32.WS_EX_NOACTIVATE | win32.WS_EX_TRANSPARENT)
            win32.exclude_from_capture(self.hwnd)
        win32.place_window(self.hwnd, x, y, w, h)
        self.update()

    def tick(self) -> None:
        if not self.isVisible():
            return
        if time.monotonic() > self._until:
            self.hide()
        else:
            self.update()

    def paintEvent(self, _event) -> None:
        t = time.monotonic() - self._t0
        remaining = self._until - time.monotonic()
        alpha = min(1.0, t / 0.15, max(0.0, remaining) / 0.5)
        pulse = 0.5 + 0.5 * math.sin(t * 6)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setOpacity(alpha)
        box = QRectF(self.pad / 2, self.label_h + self.pad / 2, self.width() - self.pad, self.height() - self.label_h - self.pad)
        glow = QColor(ACCENT)
        glow.setAlpha(round(60 + 60 * pulse))
        p.setPen(QPen(glow, (6 + 4 * pulse) * self.s))
        p.drawRoundedRect(box, 8 * self.s, 8 * self.s)
        p.setPen(QPen(ACCENT, 3 * self.s))
        p.drawRoundedRect(box, 8 * self.s, 8 * self.s)
        if self.label:
            p.setFont(self.font_)
            fm = QFontMetricsF(self.font_)
            tag = QRectF(box.left(), 0, fm.horizontalAdvance(self.label) + 2 * self.pad, self.label_h - 2)
            p.setPen(Qt.NoPen)
            p.setBrush(ACCENT)
            p.drawRoundedRect(tag, 6 * self.s, 6 * self.s)
            p.setPen(QColor(30, 30, 34))
            p.drawText(tag, Qt.AlignCenter, self.label)
        p.end()
