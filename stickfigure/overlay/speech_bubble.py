"""Speech bubble that floats above the figure's head. Click-through and hidden from screen capture."""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from stickfigure.win import win32
from stickfigure.world.geometry import Monitor

BG = QColor(255, 255, 255, 245)
BORDER = QColor(30, 30, 36, 230)
TEXT = QColor(20, 20, 24)
THINKING_TEXT = QColor(110, 110, 120)


class SpeechBubble(QWidget):
    def __init__(self, font_px: int, max_width: int):
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.font_ = QFont("Segoe UI")
        self.font_.setPixelSize(font_px)
        self.pad = round(font_px * 0.7)
        self.tail = round(font_px * 0.9)
        self.max_width = max_width
        self.text = ""
        self.thinking = False
        self.streaming = False
        self._hide_at: float | None = None
        self._tail_x = 0.0
        self._tail_down = True
        self.hwnd = 0
        self.max_lines = 10
        self._fit_cache: tuple[str, str, float, float] = ("", " ", 0.0, 0.0)
        # The window is resized to each bubble (see _fit), so the tail always starts at the bubble's edge.
        self.setFixedSize(max_width + 2 * self.pad + 8, round(font_px * 4) + self.tail + 2 * self.pad)

    # -- content ------------------------------------------------------------------------

    def think(self) -> None:
        """Show animated '...' while waiting for the first token."""
        self.thinking, self.streaming, self.text = True, True, ""
        self._hide_at = None
        self._show()

    def stream(self, text: str) -> None:
        self.thinking = False
        self.streaming = True
        self.text = text
        self._hide_at = None
        self._show()
        self.update()

    def say(self, text: str, hold: float | None = None) -> None:
        """Show a complete line, auto-hiding after a reading-time delay."""
        self.thinking = False
        self.streaming = False
        self.text = text
        self._hide_at = time.monotonic() + (hold if hold is not None else self._reading_time(text))
        self._show()
        self.update()

    def finish(self) -> None:
        self.streaming = False
        self.thinking = False
        self._hide_at = time.monotonic() + self._reading_time(self.text)
        self.update()

    @property
    def busy(self) -> bool:
        return self.streaming or self.thinking

    @staticmethod
    def _reading_time(text: str) -> float:
        return min(12.0, 2.5 + 0.33 * len(text.split()))

    def _show(self) -> None:
        if not self.isVisible():
            self.show()
            if not self.hwnd:
                self.hwnd = int(self.winId())
                win32.add_ex_style(self.hwnd, win32.WS_EX_NOACTIVATE | win32.WS_EX_TRANSPARENT)
                win32.exclude_from_capture(self.hwnd)

    # -- per frame ---------------------------------------------------------------------------

    def follow(self, head: tuple[float, float], head_r: float, monitors: list[Monitor], visible: bool) -> None:
        if self._hide_at is not None and time.monotonic() > self._hide_at:
            self._hide_at = None
            self.text = ""
            self.hide()
            return
        if not self.isVisible() or not self.hwnd:
            return
        if not visible:
            self.hide()
            return
        w, h = self._bubble_size()
        need_w, need_h = round(w) + 8, round(h) + self.tail + 8
        if (need_w, need_h) != (self.width(), self.height()):
            self.setFixedSize(need_w, need_h)
        hx, hy = head
        mon = next((m.work for m in monitors if m.work.left <= hx < m.work.right), monitors[0].work)
        # Prefer above the head; flip below if there's no room.
        bx = min(max(hx - w / 2, mon.left + 4), mon.right - w - 4)
        by = hy - head_r - 6 - self.tail - h
        self._tail_down = by >= mon.top + 4
        if not self._tail_down:
            by = hy + head_r + 6 + self.tail
        wx = round(bx - 4)
        wy = round(by - (0 if self._tail_down else self.tail)) - 4
        self._tail_x = hx - wx
        win32.move_window(self.hwnd, wx, wy)
        if self.thinking:
            self.update()

    # -- painting ---------------------------------------------------------------------------------

    def _raw_text(self) -> str:
        if self.thinking:
            return "." * (1 + int(time.monotonic() * 3) % 3)
        return self.text or " "

    def _fit(self) -> tuple[str, float, float]:
        """(text to draw, bubble width, bubble height): at most `max_lines` lines, cut with "…" if longer."""
        raw = self._raw_text()
        if self._fit_cache[0] == raw:
            return self._fit_cache[1:]
        fm = QFontMetricsF(self.font_)
        limit = self.max_lines * fm.lineSpacing() + 1

        def measure(t: str) -> QRectF:
            return fm.boundingRect(QRectF(0, 0, self.max_width, 100_000), Qt.TextWordWrap, t)

        text, r = raw, measure(raw)
        if r.height() > limit:  # binary-search the longest prefix that fits, then end it with an ellipsis
            lo, hi = 0, len(raw)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if measure(raw[:mid].rstrip() + "…").height() <= limit:
                    lo = mid
                else:
                    hi = mid - 1
            text = raw[:lo].rstrip() + "…"
            r = measure(text)
        w = max(r.width(), fm.horizontalAdvance("...")) + 2 * self.pad
        h = r.height() + 2 * self.pad
        self._fit_cache = (raw, text, w, h)
        return text, w, h

    def _layout_text(self) -> str:
        return self._fit()[0]

    def _bubble_size(self) -> tuple[float, float]:
        _, w, h = self._fit()
        return w, h

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        w, h = self._bubble_size()
        top = 4 if self._tail_down else 4 + self.tail
        body = QRectF(4, top, w, h)
        radius = self.pad * 1.2
        path = QPainterPath()
        path.addRoundedRect(body, radius, radius)
        tx = min(max(self._tail_x, body.left() + radius + 6), body.right() - radius - 6)
        tail = QPainterPath()
        if self._tail_down:
            tail.moveTo(tx - self.tail * 0.5, body.bottom() - 1)
            tail.lineTo(tx + self.tail * 0.1, body.bottom() + self.tail)
            tail.lineTo(tx + self.tail * 0.5, body.bottom() - 1)
        else:
            tail.moveTo(tx - self.tail * 0.5, body.top() + 1)
            tail.lineTo(tx + self.tail * 0.1, body.top() - self.tail)
            tail.lineTo(tx + self.tail * 0.5, body.top() + 1)
        tail.closeSubpath()
        shape = path.united(tail)
        p.setPen(QPen(BORDER, 2))
        p.setBrush(BG)
        p.drawPath(shape)
        p.setFont(self.font_)
        p.setPen(THINKING_TEXT if self.thinking else TEXT)
        p.drawText(body.adjusted(self.pad, self.pad, -self.pad, -self.pad), Qt.TextWordWrap, self._layout_text())
        p.end()
