"""Which UI elements of the window in front become platforms the figure can stand on."""

from __future__ import annotations

from stickfigure.world.geometry import Rect

STANDABLE = {"Edit", "ComboBox", "Button", "Document", "Image", "TabItem", "List", "Group", "Pane", "Text",
             "Hyperlink", "SplitButton", "Spinner", "Slider", "ProgressBar", "DataGrid", "Table", "Tree", "Header"}


def pick_element_platforms(elements, window: Rect, max_n: int = 40, min_w: float = 50, min_h: float = 16,
                           title_bar: float = 40) -> list[Rect]:
    """Tops of sizeable, visible elements inside the window: text boxes, buttons, images, panels...

    Skips tiny things, things spanning (nearly) the whole window (their top is just the window's own
    edge), anything in the title bar, and near-duplicates (nested elements sharing the same top edge).
    """
    cands: list[Rect] = []
    for e in elements:
        r = e.rect
        if e.role not in STANDABLE or getattr(e, "is_password", False):
            continue
        if r.width < min_w or r.height < min_h or r.width > window.width * 0.92:
            continue
        if r.top < window.top + title_bar or r.left < window.left or r.right > window.right or r.top > window.bottom - 20:
            continue
        cands.append(r)
    cands.sort(key=lambda r: -r.width)
    chosen: list[Rect] = []
    for r in cands:
        dup = any(abs(r.top - c.top) < 8 and r.left < c.right and c.left < r.right for c in chosen)
        if not dup:
            chosen.append(r)
        if len(chosen) >= max_n:
            break
    return chosen
