"""Click-through windows that draw the blocks the figure builds (staircases and structures)."""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from stickfigure.overlay.block_art import paint_block
from stickfigure.win import win32
from stickfigure.world.blocks import BlockManager
from stickfigure.world.physics import Block

POP_TIME = 0.15
MARGIN = 3
ANIMATED = {"gold", "bouncy"}


class BlockWindow(QWidget):
    def __init__(self, block: Block):
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
        self.block = block
        self.alpha = 1.0
        self.pop = 0.0
        w, h = int(block.width) + 2 * MARGIN, int(block.height) + 2 * MARGIN
        self.setFixedSize(w, h)
        self.show()
        hwnd = int(self.winId())
        win32.add_ex_style(hwnd, win32.WS_EX_NOACTIVATE | win32.WS_EX_TRANSPARENT)
        win32.exclude_from_capture(hwnd)
        cx, top = block.body.position
        win32.place_window(hwnd, round(cx - w / 2), round(top - MARGIN), w, h)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setOpacity(self.alpha)
        s = 0.6 + 0.4 * min(1.0, self.pop / POP_TIME)  # pop-in scale from the top edge
        w, h = self.block.width * s, self.block.height * s
        paint_block(p, QRectF((self.width() - w) / 2, MARGIN, w, h), self.block.kind, time.monotonic())
        p.end()


class BlockViews:
    """Keeps one BlockWindow per live block."""

    def __init__(self, manager: BlockManager):
        self.windows: dict[int, BlockWindow] = {}
        manager.on_spawn = self._spawn
        manager.on_fade = self._fade
        manager.on_remove = self._remove

    def _spawn(self, block: Block) -> None:
        self.windows[block.id] = BlockWindow(block)

    def _fade(self, bid: int, alpha: float) -> None:
        w = self.windows.get(bid)
        if w:
            w.alpha = alpha
            w.update()

    def _remove(self, bid: int) -> None:
        w = self.windows.pop(bid, None)
        if w:
            w.close()
            w.deleteLater()

    def update(self, dt: float) -> None:
        self._anim_t = getattr(self, "_anim_t", 0.0) + dt
        animate = self._anim_t >= 0.05  # shiny/jiggly kinds repaint at ~20 fps, the rest only when changing
        if animate:
            self._anim_t = 0.0
        for w in self.windows.values():
            if w.pop < POP_TIME:
                w.pop += dt
                w.update()
            elif animate and w.block.kind in ANIMATED:
                w.update()

    def set_visible(self, on: bool) -> None:
        for w in self.windows.values():
            w.setVisible(on)
