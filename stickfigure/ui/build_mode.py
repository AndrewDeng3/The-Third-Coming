"""Build mode: place and remove blocks yourself.

A translucent overlay covers one monitor's work area with a snapping grid (the bottom row rests on the
taskbar). A floating palette picks the block kind and whether blocks are permanent.
  - left click / drag: place blocks      - right click / drag (or the eraser): remove blocks
  - Esc or Done: leave build mode
Blocks placed here are ordinary blocks: the figure walks and jumps on them, and permanent ones persist.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QWidget

from stickfigure.overlay.block_art import paint_block
from stickfigure.world.blocks import KINDS, BlockManager
from stickfigure.world.geometry import Rect


def _swatch(kind: str, px: int) -> QIcon:
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    paint_block(p, QRectF(1, 1, px - 2, px - 2), kind, 0.0)
    p.end()
    return QIcon(pm)


class Grid:
    """Snapping cells over a work area: column/row -> (block center x, block top y)."""

    def __init__(self, area: Rect, size: float):
        self.area, self.size = area, size

    def cell(self, x: float, y: float) -> tuple[int, int] | None:
        a, s = self.area, self.size
        if not (a.left <= x < a.right and a.top <= y < a.bottom):
            return None
        return int((x - a.left) // s), int((a.bottom - y) // s)

    def place(self, col: int, row: int) -> tuple[float, float]:
        a, s = self.area, self.size
        return a.left + (col + 0.5) * s, a.bottom - (row + 1) * s

    def rect(self, col: int, row: int) -> QRectF:
        cx, top = self.place(col, row)
        return QRectF(cx - self.size / 2, top, self.size, self.size)


class BuildMode(QWidget):
    closed = Signal()

    def __init__(self, blocks: BlockManager, area: Rect, scale: float, on_change: Callable[[], None] = lambda: None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.blocks = blocks
        self.grid = Grid(area, blocks.cfg.block_size)
        self.on_change = on_change
        self.kind = "wood"
        self.structure = -blocks.new_structure_id()  # negative: built by the user (the figure won't renovate it)
        self._hover: tuple[int, int] | None = None
        self._drag: str | None = None  # "place" | "erase"
        self._last_cell: tuple[int, int] | None = None
        self.setGeometry(int(area.left), int(area.top), int(area.width), int(area.height))
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        s = scale

        # Palette
        self.palette_bar = QWidget(self)
        self.palette_bar.setStyleSheet(
            f"""QWidget {{ background: rgba(30, 31, 36, 240); color: #e8e8ee; font-family: 'Segoe UI';
                           font-size: 10pt; border-radius: {round(10 * s)}px; }}
                QPushButton {{ background: #3a3b44; border: 2px solid transparent; border-radius: {round(8 * s)}px;
                               padding: {round(4 * s)}px; }}
                QPushButton:checked {{ border-color: #f7931e; background: #4a4b55; }}
                QPushButton#done {{ background: #f7931e; color: #1e1f24; font-weight: 600;
                                    padding: {round(6 * s)}px {round(14 * s)}px; }}
                QCheckBox {{ background: transparent; spacing: {round(6 * s)}px; }}
                QLabel {{ background: transparent; color: #9a9aa6; font-size: 9pt; }}""")
        row = QHBoxLayout(self.palette_bar)
        row.setContentsMargins(round(10 * s), round(8 * s), round(10 * s), round(8 * s))
        row.setSpacing(round(6 * s))
        icon = round(30 * s)
        self.kind_buttons: dict[str, QPushButton] = {}
        for name in KINDS:
            b = QPushButton()
            b.setIcon(_swatch(name, icon))
            b.setIconSize(QSize(icon, icon))
            b.setCheckable(True)
            b.setToolTip(name.capitalize())
            b.clicked.connect(lambda _=False, k=name: self.set_kind(k))
            row.addWidget(b)
            self.kind_buttons[name] = b
        self.eraser = QPushButton("⌫ Eraser")
        self.eraser.setCheckable(True)
        self.eraser.setToolTip("Remove blocks with left click too (right click always removes)")
        row.addWidget(self.eraser)
        self.permanent = QCheckBox("Permanent")
        self.permanent.setChecked(True)
        self.permanent.setToolTip("Permanent blocks stay (even after a restart) until cleared; "
                                  "others fade after a little while")
        row.addWidget(self.permanent)
        row.addWidget(QLabel("Left: place · Right: remove · Esc: done"))
        done = QPushButton("Done", objectName="done")
        done.clicked.connect(self.close)
        row.addWidget(done)
        self.palette_bar.adjustSize()
        self.palette_bar.move(int((area.width - self.palette_bar.width()) // 2), round(16 * s))
        self.set_kind("wood")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(50)

    # -- state -----------------------------------------------------------------------------------

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.eraser.setChecked(False)
        for k, b in self.kind_buttons.items():
            b.setChecked(k == kind)

    def _block_at(self, col: int, row: int):
        cx, top = self.grid.place(col, row)
        half = self.grid.size / 2
        for b in self.blocks.world.blocks.values():
            if b.id in self.blocks.fading:
                continue
            x, y = b.body.position
            if abs(x - cx) < half and abs(y - top) < half:
                return b
        return None

    def _apply(self, cell: tuple[int, int], mode: str) -> None:
        if cell == self._last_cell:
            return
        self._last_cell = cell
        existing = self._block_at(*cell)
        if mode == "erase":
            if existing is not None:
                self.blocks.remove(existing.id)
                self.on_change()
            return
        if existing is None:
            cx, top = self.grid.place(*cell)
            permanent = self.permanent.isChecked()
            if self.blocks.spawn(cx, top, self.kind, permanent=permanent,
                                 structure=self.structure if permanent else 0) is not None:
                self.on_change()

    # -- input ----------------------------------------------------------------------------------------

    def mousePressEvent(self, e: QMouseEvent) -> None:
        cell = self.grid.cell(e.position().x() + self.grid.area.left, e.position().y() + self.grid.area.top)
        if cell is None:
            return
        erase = e.button() == Qt.RightButton or (e.button() == Qt.LeftButton and self.eraser.isChecked())
        self._drag = "erase" if erase else "place"
        self._last_cell = None
        self._apply(cell, self._drag)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        cell = self.grid.cell(e.position().x() + self.grid.area.left, e.position().y() + self.grid.area.top)
        self._hover = cell
        if cell is not None and self._drag:
            self._apply(cell, self._drag)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag = None
        self._last_cell = None

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Escape:
            self.close()
        elif Qt.Key_1 <= e.key() <= Qt.Key_8:
            self.set_kind(list(KINDS)[e.key() - Qt.Key_1])
        else:
            super().keyPressEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.activateWindow()
        self.setFocus()

    def closeEvent(self, e) -> None:
        self._timer.stop()
        super().closeEvent(e)
        self.closed.emit()

    # -- painting ------------------------------------------------------------------------------------

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        a = self.grid.area
        p.fillRect(self.rect(), QColor(20, 30, 60, 55))  # tint (also catches the clicks)
        s = self.grid.size
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        x = 0.0
        while x <= a.width:
            p.drawLine(QPointF(x, 0), QPointF(x, a.height))
            x += s
        y = float(a.height)
        while y >= 0:
            p.drawLine(QPointF(0, y), QPointF(a.width, y))
            y -= s
        if self._hover is not None:
            r = self.grid.rect(*self._hover).translated(-a.left, -a.top)
            erase = self.eraser.isChecked() or self._drag == "erase"
            if erase:
                p.setPen(QPen(QColor(255, 80, 80, 230), 3))
                p.setBrush(QColor(255, 80, 80, 50))
                p.drawRect(r)
            elif self._block_at(*self._hover) is None:
                p.setOpacity(0.55)
                paint_block(p, r, self.kind, time.monotonic())
                p.setOpacity(1.0)
                p.setPen(QPen(QColor("#f7931e"), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r)
        p.end()
