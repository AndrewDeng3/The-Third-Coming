"""Which UI elements of the window in front become ledges the figure can stand on."""

from __future__ import annotations

from stickfigure.world.geometry import Rect, subtract_span

# Things with a visible top edge. Layout containers (Pane, Group, Document, Custom...) are left out: web pages
# are full of invisible boxes, and standing on one looks like hovering in mid-air.
STANDABLE = {"Edit", "ComboBox", "Button", "Image", "TabItem", "Hyperlink", "Text", "List", "SplitButton",
             "Spinner", "Slider", "ProgressBar", "DataGrid", "Table", "Header", "CheckBox", "RadioButton", "MenuItem"}
MIN_SPAN = 40.0


def pick_element_platforms(elements, window: Rect, max_n: int = 40, min_w: float = 50, min_h: float = 16,
                           title_bar: float = 40) -> list:
    """Sizeable, visible elements inside the window: text boxes, buttons, images, text, links...

    Skips tiny things, things spanning (nearly) the whole window, anything in the title bar, and
    near-duplicates (nested elements sharing the same top edge).
    """
    cands = []
    for e in elements:
        r = e.rect
        if e.role not in STANDABLE or getattr(e, "is_password", False):
            continue
        if r.width < min_w or r.height < min_h or r.width > window.width * 0.92:
            continue
        if r.top < window.top + title_bar or r.left < window.left or r.right > window.right or r.top > window.bottom - 20:
            continue
        cands.append(e)
    cands.sort(key=lambda e: -e.rect.width)
    chosen = []
    for e in cands:
        r = e.rect
        dup = any(abs(r.top - c.rect.top) < 8 and r.left < c.rect.right and c.rect.left < r.right for c in chosen)
        if not dup:
            chosen.append(e)
        if len(chosen) >= max_n:
            break
    return chosen


def visible_ledges(elements, occluders: list[Rect]) -> list[tuple[Rect, str]]:
    """The parts of each element's top edge that aren't covered by a window in front of it (the chat window,
    another app...). One element can come out as several pieces, or none. Returns (ledge rect, label)."""
    out = []
    for e in elements:
        r = e.rect
        spans = [(r.left, r.right)]
        for o in occluders:
            if o.top - 2 <= r.top <= o.bottom + 2:  # that window covers the line the ledge is on
                spans = subtract_span(spans, (o.left, o.right))
                if not spans:
                    break
        label = f"{e.role} '{e.name[:30]}'" if getattr(e, "name", "") else e.role
        for a, b in spans:
            if b - a >= MIN_SPAN:
                out.append((Rect(a, r.top, b, r.top + 1), label))
    return out
