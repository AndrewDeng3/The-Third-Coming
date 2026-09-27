"""Render The Third Coming's app icon (packaging/third_coming.ico) from the real figure renderer.

    .venv\\Scripts\\python.exe tools\\make_icon.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from stickfigure.figure.animator import Animator  # noqa: E402
from stickfigure.overlay.render import COLORS, draw_figure  # noqa: E402
from stickfigure.ui.lounge import LoungeAnimator  # noqa: E402

OUT = os.path.join(ROOT, "packaging", "third_coming.ico")


def render(size: int) -> QImage:
    anim = LoungeAnimator()
    anim.mode = None
    pose = Animator._wave(anim)  # waving hello
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    # A dark rounded tile with a warm glow, so the orange figure reads on any wallpaper.
    tile = QRectF(size * 0.03, size * 0.03, size * 0.94, size * 0.94)
    glow = QRadialGradient(QPointF(size / 2, size * 0.45), size * 0.6)
    glow.setColorAt(0, QColor("#3a2f45"))
    glow.setColorAt(1, QColor("#17151c"))
    p.setPen(Qt.NoPen)
    p.setBrush(glow)
    p.drawRoundedRect(tile, size * 0.22, size * 0.22)
    P = anim.P
    # Fit the pose's bounding box (head circle and limbs included) into the tile with a margin.
    xs = [x for x, _ in pose.values()]
    ys = [y for _, y in pose.values()]
    hx, hy = pose["head"]
    left, right = min(xs + [hx - P.head_r]), max(xs + [hx + P.head_r])
    top, bottom = min(ys + [hy - P.head_r]), max(ys + [hy + P.head_r])
    pad = 12  # stroke + outline, in figure units
    scale = size * 0.72 / max(right - left + 2 * pad, bottom - top + 2 * pad)
    p.translate(size / 2, size / 2)
    p.scale(scale, scale)
    draw_figure(p, pose, (-(left + right) / 2, -(top + bottom) / 2), P, COLORS["Orange"], 9.0)
    p.end()
    return img


def main() -> None:
    QApplication([])
    big = render(256)
    # Qt's ICO writer stores one image; Windows scales it down cleanly for the desktop and taskbar.
    if not big.save(OUT, "ICO"):
        raise SystemExit("couldn't write the .ico (Qt imageformats plugin missing?)")
    render(256).save(os.path.join(ROOT, "packaging", "third_coming.png"))
    print(OUT)


if __name__ == "__main__":
    main()
