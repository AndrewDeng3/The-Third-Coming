"""Small per-pixel-alpha window that follows the figure.

Rather than one screen-sized overlay (expensive to recomposite every frame), the
figure lives in a window just big enough for its pose, moved with SetWindowPos.
Fully transparent pixels of a layered window don't receive clicks, so clicks
everywhere else fall through to the desktop. An alpha=1 halo (invisible) along
the limbs makes the thin figure easy to grab.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

from stickfigure.config import CONFIG, Config
from stickfigure.figure.animator import Animator
from stickfigure.overlay.render import COLORS, draw_figure
from stickfigure.win import win32


class FigureWindow(QWidget):
    def __init__(
        self,
        anim: Animator,
        on_context_menu: Callable[[], None],
        on_double_click: Callable[[], None] = lambda: None,
        cfg: Config = CONFIG,
    ):
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.anim = anim
        self.fig = anim.fig
        self.cfg = cfg
        self.color: QColor = COLORS["Orange"]
        self._on_context_menu = on_context_menu
        self._on_double_click = on_double_click
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._size = int(cfg.figure_height * 2.3)
        self.setFixedSize(self._size, self._size)
        self._last_pos: tuple[int, int] | None = None
        self.hwnd = 0

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.hwnd:
            self.hwnd = int(self.winId())
            win32.add_ex_style(self.hwnd, win32.WS_EX_NOACTIVATE)
            win32.exclude_from_capture(self.hwnd)

    def set_click_through(self, on: bool) -> None:
        """While actions run, the figure must never intercept a synthetic click aimed at the app below."""
        if not self.hwnd:
            return
        if on:
            win32.add_ex_style(self.hwnd, win32.WS_EX_TRANSPARENT)
        else:
            win32.remove_ex_style(self.hwnd, win32.WS_EX_TRANSPARENT)

    def set_color(self, name: str) -> None:
        self.color = COLORS[name]
        self.update()

    # -- per-frame ---------------------------------------------------------------

    def sync(self) -> None:
        x, y = self.fig.body.position
        pos = (round(x - self._size / 2), round(y - self._size / 2))
        if pos != self._last_pos and self.hwnd:
            win32.move_window(self.hwnd, *pos)
            self._last_pos = pos
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        # Pose is relative to the body center, which the window was centered on at the last move.
        ox = oy = self._size / 2
        if self._last_pos is not None:
            x, y = self.fig.body.position
            ox += x - (self._last_pos[0] + self._size / 2)
            oy += y - (self._last_pos[1] + self._size / 2)
        draw_figure(p, self.anim.pose, (ox, oy), self.anim.P, self.color, self.cfg.stroke, self.cfg.hit_width)
        p.end()

    # -- interaction ---------------------------------------------------------------------

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.fig.grab(win32.cursor_pos())
        elif e.button() == Qt.RightButton:
            self._on_context_menu()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.fig.release()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._on_double_click()
