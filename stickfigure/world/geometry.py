"""Pure geometry for turning desktop windows into walkable platforms.

All coordinates are physical screen pixels, y pointing down.
"""

from __future__ import annotations

from dataclasses import dataclass

Span = tuple[float, float]


@dataclass(frozen=True, slots=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def translated(self, dx: float, dy: float) -> Rect:
        return Rect(self.left + dx, self.top + dy, self.right + dx, self.bottom + dy)


@dataclass(frozen=True, slots=True)
class Monitor:
    bounds: Rect
    work: Rect  # bounds minus taskbar


def subtract_span(spans: list[Span], cut: Span) -> list[Span]:
    """Remove `cut` from every span in `spans`."""
    c0, c1 = cut
    out: list[Span] = []
    for s0, s1 in spans:
        if c1 <= s0 or c0 >= s1:
            out.append((s0, s1))
            continue
        if s0 < c0:
            out.append((s0, c0))
        if c1 < s1:
            out.append((c1, s1))
    return out


def union_spans(spans: list[Span]) -> list[Span]:
    merged: list[Span] = []
    for s0, s1 in sorted(spans):
        if merged and s0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], s1))
        else:
            merged.append((s0, s1))
    return merged


def intersect_spans(a: list[Span], b: list[Span]) -> list[Span]:
    out: list[Span] = []
    for a0, a1 in a:
        for b0, b1 in b:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi > lo:
                out.append((lo, hi))
    return union_spans(out)


def standable_spans(y: float, monitors: list[Monitor], headroom: float) -> list[Span]:
    """Horizontal spans at height `y` where a figure `headroom` tall is fully on-screen.

    A window edge hugging the top of a monitor (e.g. a maximized window) has no
    room above it, and an edge hidden behind the taskbar isn't reachable.
    """
    spans = [
        (m.work.left, m.work.right)
        for m in monitors
        if m.work.top + headroom <= y <= m.work.bottom
    ]
    return union_spans(spans)


def visible_top_edges(
    windows: list[Rect],
    monitors: list[Monitor],
    headroom: float,
    min_len: float = 8.0,
) -> list[list[Span]]:
    """For each window (ordered top-most first), the visible, standable spans of its top edge.

    An edge span is occluded by any window higher in Z-order whose rect covers
    the edge line or the headroom directly above it (a figure standing there
    would be drawn over that window).
    """
    result: list[list[Span]] = []
    for i, w in enumerate(windows):
        spans = intersect_spans([(w.left, w.right)], standable_spans(w.top, monitors, headroom))
        for above in windows[:i]:
            if not spans:
                break
            if above.top <= w.top and above.bottom > w.top - headroom * 0.5:
                spans = subtract_span(spans, (above.left, above.right))
        result.append([s for s in spans if s[1] - s[0] >= min_len])
    return result
