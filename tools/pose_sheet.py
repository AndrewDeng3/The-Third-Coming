"""Render every animation state (and gait cycles) to a PNG for visual review.

    .venv\\Scripts\\python tools\\pose_sheet.py out.png
"""

import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

from stickfigure.config import CONFIG
from stickfigure.figure.animator import Animator, Anim
from stickfigure.figure.controller import Activity, Figure
from stickfigure.overlay.render import COLORS, draw_figure
from stickfigure.win.tracker import DesktopSnapshot
from stickfigure.world.geometry import Monitor, Rect
from stickfigure.world.physics import World

CELL = 190


def main(out: str) -> None:
    QGuiApplication(sys.argv)
    world = World()
    mon = Monitor(Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1040))
    world.sync(DesktopSnapshot([], [mon], False, 0.0), None)
    fig = Figure(world)
    anim = Animator(fig)
    anim.time = 0.7
    bx, by = fig.body.position
    fig.activity_point = (bx + 300, by - 250)  # for POINT / PLACE: up and to the right

    rows: list[list[tuple[str, dict]]] = []
    singles = []
    for state in Anim:
        if state in (Anim.WALK, Anim.RUN):
            continue
        anim.state_time = 1.0 if state == Anim.POINT else 0.12  # point arm finishes extending at 0.25s
        fig.land_timer = 0.08
        fig.tumble_angle = 0.9
        fig.body.velocity = (0, 400 if state in (Anim.FALL, Anim.TUMBLE) else -500)
        fig.knocked_side = 1
        singles.append((state.name, anim._target(state)))
    # air: rising vs falling
    fig.body.velocity = (0, 700)
    singles.append(("AIR (falling)", anim._target(Anim.AIR)))
    rows.append(singles)
    for state in (Anim.WALK, Anim.RUN):
        row = []
        for i in range(8):
            anim.phase = i / 8
            row.append((f"{state.name} {i}/8", anim._target(state)))
        rows.append(row)
    fig.facing = -1
    anim.phase = 0.25
    rows.append([("WALK facing left", anim._target(Anim.WALK)), ("IDLE facing left", anim._target(Anim.IDLE))])

    cols = max(len(r) for r in rows)
    img = QImage(CELL * cols, CELL * len(rows), QImage.Format_ARGB32)
    img.fill(QColor(236, 238, 242))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setFont(QFont("Segoe UI", 8))
    half = CONFIG.figure_height / 2
    for r, row in enumerate(rows):
        for c, (label, pose) in enumerate(row):
            ox, oy = c * CELL + CELL / 2, r * CELL + CELL / 2 - 10
            p.setPen(QPen(QColor(150, 150, 160), 1))
            p.drawLine(QPointF(c * CELL + 10, oy + half), QPointF(c * CELL + CELL - 10, oy + half))
            draw_figure(p, pose, (ox, oy), anim.P, COLORS["Orange"], CONFIG.stroke)
            p.setPen(Qt.black)
            p.drawText(QPointF(c * CELL + 6, r * CELL + 14), label)
    p.end()
    img.save(out)
    print("saved", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pose_sheet.png")
