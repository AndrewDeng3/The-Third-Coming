"""Optional full-desktop, click-through overlay that draws the physics world. Debug only."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from stickfigure.win import win32
from stickfigure.world.physics import World

PLATFORM = QColor(60, 220, 120, 230)
STATIC = QColor(80, 160, 255, 230)
BLOCK = QColor(255, 170, 40, 230)
PATH = QColor(255, 60, 200, 230)
TEXT_BG = QColor(0, 0, 0, 170)


class DebugOverlay(QWidget):
    def __init__(
        self,
        world: World,
        stats: Callable[[], list[str]],
        path: Callable[[], list[tuple[float, float]]] = lambda: [],
    ):
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.world = world
        self._stats = stats
        self._path = path
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._origin = (0, 0)
        self.hwnd = 0

    def show_over_desktop(self) -> None:
        mons = self.world.monitors
        left = min(m.bounds.left for m in mons)
        top = min(m.bounds.top for m in mons)
        right = max(m.bounds.right for m in mons)
        bottom = max(m.bounds.bottom for m in mons)
        self._origin = (left, top)
        self.show()
        if not self.hwnd:
            self.hwnd = int(self.winId())
            win32.add_ex_style(self.hwnd, win32.WS_EX_NOACTIVATE | win32.WS_EX_TRANSPARENT)
            win32.exclude_from_capture(self.hwnd)
        win32.place_window(self.hwnd, left, top, right - left, bottom - top)

    def paintEvent(self, _event) -> None:
        ox, oy = self._origin
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        for color, segs in ((STATIC, self.world.static_segments()), (PLATFORM, self.world.platform_segments())):
            p.setPen(QPen(color, 4))
            for (ax, ay), (bx, by) in segs:
                p.drawLine(QPointF(ax - ox, ay - oy), QPointF(bx - ox, by - oy))

        p.setPen(QPen(BLOCK, 3))
        for b in self.world.blocks.values():
            cx, top = b.body.position
            p.drawRect(QRectF(cx - b.width / 2 - ox, top - oy, b.width, b.height))

        path = self._path()
        if len(path) > 1:
            p.setPen(QPen(PATH, 3, Qt.DashLine))
            for (ax, ay), (bx, by) in zip(path, path[1:]):
                p.drawLine(QPointF(ax - ox, ay - oy), QPointF(bx - ox, by - oy))
            p.setBrush(PATH)
            for x, y in path:
                p.drawEllipse(QPointF(x - ox, y - oy), 5, 5)

        lines = self._stats()
        m = self.world.monitors[0].work
        p.setFont(QFont("Consolas", 10))
        box = QRectF(m.left - ox + 12, m.top - oy + 12, 320, 18 * len(lines) + 12)
        p.fillRect(box, TEXT_BG)
        p.setPen(Qt.white)
        for i, line in enumerate(lines):
            p.drawText(QPointF(box.left() + 8, box.top() + 20 + 18 * i), line)
        p.end()
