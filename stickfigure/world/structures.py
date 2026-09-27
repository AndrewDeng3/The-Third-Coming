"""Little structures the figure builds out of permanent blocks, and where they fit."""

from __future__ import annotations

import random

# Cells as (column, row) in block units; row 0 sits on the floor. Half columns are allowed.
TEMPLATES: dict[str, list[tuple[float, int]]] = {
    "tower": [(0, 0), (0, 1), (0, 2), (0, 3)],
    "wall": [(0, 0), (1, 0), (2, 0), (3, 0)],
    "pyramid": [(0, 0), (1, 0), (2, 0), (0.5, 1), (1.5, 1), (1, 2)],
    "arch": [(0, 0), (0, 1), (2, 0), (2, 1), (0, 2), (1, 2), (2, 2)],
    "steps": [(0, 0), (1, 0), (1, 1), (2, 0), (2, 1), (2, 2)],
    "house": [(0, 0), (0, 1), (2, 0), (2, 1), (0, 2), (1, 2), (2, 2), (0.5, 3), (1.5, 3), (1, 4)],
}
ROOF_ROWS = {"house": 3}  # rows from here up use the accent material

# Special materials are rarer.
MATERIALS = ["wood"] * 3 + ["stone"] * 3 + ["brick"] * 3 + ["grass"] * 2 + ["glass", "gold", "ice", "bouncy"]
ACCENTS = ["wood", "brick", "stone", "gold", "glass"]


def width_cells(template: str) -> float:
    return max(c for c, _ in TEMPLATES[template]) + 1


def plan(template: str, left: float, floor_y: float, size: float,
         material: str | None = None, accent: str | None = None) -> list[tuple[float, float, str]]:
    """Blocks (center x, top y, kind), bottom row first so each one has something under it."""
    material = material or random.choice(MATERIALS)
    accent = accent or random.choice([a for a in ACCENTS if a != material])
    roof = ROOF_ROWS.get(template, 99)
    cells = sorted(TEMPLATES[template], key=lambda cr: (cr[1], cr[0]))
    return [(left + (c + 0.5) * size, floor_y - (r + 1) * size, accent if r >= roof else material) for c, r in cells]


def find_site(span: tuple[float, float], floor_y: float, template: str, size: float,
              occupied: list[tuple[float, float, float, float]], near_x: float, gap: float = 30) -> float | None:
    """Left edge for `template` on the floor span, as close to `near_x` as possible, clear of `occupied`
    rectangles (left, top, right, bottom). None if nothing fits."""
    lo, hi = span
    width = width_cells(template) * size
    height = (max(r for _, r in TEMPLATES[template]) + 1) * size
    top = floor_y - height
    step = size / 2
    candidates = []
    x = lo + gap
    while x + width <= hi - gap:
        candidates.append(x)
        x += step
    candidates.sort(key=lambda left: abs(left + width / 2 - near_x))
    for left in candidates:
        l, r = left - gap, left + width + gap
        if all(not (ol < r and orr > l and ot < floor_y and ob > top) for ol, ot, orr, ob in occupied):
            return left
    return None
