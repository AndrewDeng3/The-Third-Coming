"""Pymunk world: static screen bounds, one-way window platforms, placed blocks."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import pymunk

from stickfigure.config import CONFIG, Config
from stickfigure.win.tracker import DesktopSnapshot
from stickfigure.world.geometry import Monitor, Span, visible_top_edges

# Collision types
CT_SOLID = 0
CT_PLATFORM = 1
CT_FIGURE = 2

# Collision categories (bitmask)
CAT_WORLD = 0b01
CAT_FIGURE = 0b10

FLOOR_RADIUS = 4.0
BLOCK_RADIUS = 2.0


@dataclass
class WindowPlatform:
    hwnd: int
    body: pymunk.Body
    spans: list[Span] = field(default_factory=list)  # relative to body origin (window left, top)
    shapes: list[pymunk.Shape] = field(default_factory=list)


@dataclass
class Block:
    id: int
    body: pymunk.Body  # static, positioned at the block's top-center
    shape: pymunk.Segment
    width: float
    height: float
    kind: str = "wood"
    permanent: bool = False
    structure: int = 0  # blocks built together as one structure share an id (0 = none)
    walkable: bool = True  # False for slippery/bouncy kinds: the path planner avoids them


@dataclass(frozen=True)
class SurfaceSeg:
    """A walkable surface, in coordinates relative to its body so it tracks moving windows."""

    key: tuple
    body: pymunk.Body
    x0: float
    x1: float
    y: float  # feet height relative to body.position.y
    kind: str  # "floor" | "window" | "block" | "element"

    def world(self) -> tuple[float, float, float]:
        ox, oy = self.body.position
        return (ox + self.x0, ox + self.x1, oy + self.y)


class World:
    def __init__(self, cfg: Config = CONFIG):
        self.cfg = cfg
        self.space = pymunk.Space()
        self.space.gravity = (0, cfg.gravity)
        self.space.on_collision(CT_FIGURE, CT_PLATFORM, begin=self._one_way_begin, pre_solve=self._one_way_pre)
        # Platforms on top of visible UI elements (text boxes, buttons...) of the window in front.
        self.element_owner = 0
        self.element_shapes: list[pymunk.Shape] = []
        self._element_rects: list[tuple[int, int, int]] = []
        self.element_labels: list[str] = []  # "Edit 'Search'" etc., for the mind to pick destinations
        self.platforms: dict[int, WindowPlatform] = {}
        self.blocks: dict[int, Block] = {}
        self._block_ids = itertools.count(1)
        self.monitors: list[Monitor] = []
        self._static_shapes: list[pymunk.Shape] = []
        self.shape_keys: dict[pymunk.Shape, tuple] = {}
        self.carry_target: pymunk.Body | None = None  # set by the figure: kinematic body it stands on
        self.passthrough_body: pymunk.Body | None = None  # platform the figure is dropping through
        self.version = 0  # bumps whenever walkable surfaces change shape (not just move)

    # -- one-way platforms ----------------------------------------------------

    def _one_way_begin(self, arbiter: pymunk.Arbiter, _space, _data) -> None:
        # Normal points from the figure (shape a) to the platform (shape b):
        # only accept contacts where the platform is underneath and we aren't rising through it.
        figure, platform = arbiter.shapes
        body = figure.body
        # Feet must be at (or only just past) the surface: walking under a ledge that's a bit
        # higher than our feet shouldn't teleport us on top of it. Allow for fast falls.
        # pymunk BBs are y-up named: bb.top is max y, which is the visual bottom on screen.
        feet = figure.bb.top
        surface = platform.bb.bottom
        tolerance = 12 + max(0.0, body.velocity.y) * _space.current_time_step * 1.5
        if (
            arbiter.normal.y < 0.3
            or body.velocity.y < -1.0
            or feet - surface > tolerance
            or platform.body is self.passthrough_body
        ):
            arbiter.process_collision = False

    # -- UI element platforms ---------------------------------------------------------------

    def set_element_platforms(self, owner: int, rects: list, labels: list[str] | None = None) -> bool:
        """Replace the element platforms with the tops of `rects` (screen Rects of the owner window's
        elements). They hang off the owner's window platform, so they move with the window. Returns True
        if anything changed."""
        plat = self.platforms.get(owner)
        base = plat.body if plat is not None else self.space.static_body
        ox, oy = base.position if plat is not None else (0.0, 0.0)
        key = [(round(r.left - ox), round(r.right - ox), round(r.top - oy)) for r in rects]
        if owner == self.element_owner and key == self._element_rects and (
                not self.element_shapes or self.element_shapes[0].body is base):
            return False
        self.clear_elements()
        r = self.cfg.platform_thickness
        for i, (x0, x1, top) in enumerate(key):
            seg = pymunk.Segment(base, (x0, top), (x1, top), r)
            seg.friction = 1.0
            seg.collision_type = CT_PLATFORM
            seg.filter = pymunk.ShapeFilter(categories=CAT_WORLD)
            self.shape_keys[seg] = ("elem", i)
            self.element_shapes.append(seg)
        if self.element_shapes:
            self.space.add(*self.element_shapes)
        self.element_owner, self._element_rects = owner, key
        self.element_labels = list(labels or [])[: len(key)]
        self.version += 1
        return True

    def clear_elements(self) -> None:
        if self.element_shapes:
            self._forget(self.element_shapes)
            self.space.remove(*self.element_shapes)
            self.version += 1
        self.element_shapes = []
        self._element_rects = []
        self.element_labels = []
        self.element_owner = 0

    def _one_way_pre(self, arbiter: pymunk.Arbiter, _space, _data) -> None:
        if arbiter.shapes[1].body is self.passthrough_body:
            arbiter.process_collision = False

    # -- syncing with the desktop -------------------------------------------------

    def sync(self, snap: DesktopSnapshot, figure_body: pymunk.Body | None) -> None:
        if snap.monitors != self.monitors:
            self._rebuild_bounds(snap.monitors)

        headroom = self.cfg.figure_height
        rects = [w.rect for w in snap.windows]
        edges = visible_top_edges(rects, snap.monitors, headroom)

        seen: set[int] = set()
        for win, spans in zip(snap.windows, edges):
            if not spans:
                continue
            seen.add(win.hwnd)
            rel = [(round(s0 - win.rect.left), round(s1 - win.rect.left)) for s0, s1 in spans]
            plat = self.platforms.get(win.hwnd)
            if plat is None:
                body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
                body.position = (win.rect.left, win.rect.top)
                self.space.add(body)
                plat = self.platforms[win.hwnd] = WindowPlatform(win.hwnd, body)
            else:
                self._move_platform(plat, win.rect.left, win.rect.top, figure_body)
            if rel != plat.spans:
                self._set_spans(plat, rel)

        for hwnd in list(self.platforms.keys() - seen):
            if hwnd == self.element_owner or (self.element_shapes and self.element_shapes[0].body
                                              is self.platforms[hwnd].body):
                self.clear_elements()
            plat = self.platforms.pop(hwnd)
            self._forget(plat.shapes)
            self.space.remove(plat.body, *plat.shapes)
            if self.carry_target is plat.body:
                self.carry_target = None
            self.version += 1

    def _move_platform(self, plat: WindowPlatform, x: float, y: float, figure_body: pymunk.Body | None) -> None:
        dx, dy = x - plat.body.position.x, y - plat.body.position.y
        if dx == 0 and dy == 0:
            return
        plat.body.position = (x, y)
        self.space.reindex_shapes_for_body(plat.body)
        # Kinematic carry: friction can't keep up with a window being flung around,
        # so move the figure with whatever it's standing on.
        if figure_body is not None and self.carry_target is plat.body:
            if abs(dx) <= self.cfg.max_carry_per_frame and abs(dy) <= self.cfg.max_carry_per_frame:
                figure_body.position = (figure_body.position.x + dx, figure_body.position.y + dy)
                self.space.reindex_shapes_for_body(figure_body)
            else:
                self.carry_target = None

    def _set_spans(self, plat: WindowPlatform, spans: list[Span]) -> None:
        if plat.shapes:
            self._forget(plat.shapes)
            self.space.remove(*plat.shapes)
        r = self.cfg.platform_thickness
        plat.shapes = []
        for i, (s0, s1) in enumerate(spans):
            seg = pymunk.Segment(plat.body, (s0, 0), (s1, 0), r)
            seg.friction = 1.0
            seg.collision_type = CT_PLATFORM
            seg.filter = pymunk.ShapeFilter(categories=CAT_WORLD)
            plat.shapes.append(seg)
            self.shape_keys[seg] = ("win", plat.hwnd, i)
        plat.spans = spans
        self.space.add(*plat.shapes)
        self.version += 1

    def _forget(self, shapes: list[pymunk.Shape]) -> None:
        for s in shapes:
            self.shape_keys.pop(s, None)

    def _rebuild_bounds(self, monitors: list[Monitor]) -> None:
        if self._static_shapes:
            self._forget(self._static_shapes)
            self.space.remove(*self._static_shapes)
        sb = self.space.static_body
        shapes: list[pymunk.Shape] = []
        # Floor on top of each monitor's taskbar (work area bottom).
        for i, m in enumerate(monitors):
            seg = pymunk.Segment(sb, (m.work.left, m.work.bottom), (m.work.right, m.work.bottom), FLOOR_RADIUS)
            self.shape_keys[seg] = ("floor", i)
            shapes.append(seg)
        # Walls at the outer left/right of the virtual desktop.
        left = min(m.bounds.left for m in monitors)
        right = max(m.bounds.right for m in monitors)
        top = min(m.bounds.top for m in monitors) - 4000
        bottom = max(m.bounds.bottom for m in monitors)
        shapes.append(pymunk.Segment(sb, (left - 4, top), (left - 4, bottom), 4))
        shapes.append(pymunk.Segment(sb, (right + 4, top), (right + 4, bottom), 4))
        for s in shapes:
            s.friction = 1.0
            s.collision_type = CT_SOLID
            s.filter = pymunk.ShapeFilter(categories=CAT_WORLD)
        self.space.add(*shapes)
        self._static_shapes = shapes
        self.monitors = monitors
        self.version += 1

    # -- blocks ---------------------------------------------------------------------------

    def add_block(
        self,
        cx: float,
        top: float,
        kind: str = "wood",
        friction: float = 1.0,
        elasticity: float = 0.0,
        permanent: bool = False,
        structure: int = 0,
        walkable: bool = True,
    ) -> Block:
        size = self.cfg.block_size
        body = pymunk.Body(body_type=pymunk.Body.STATIC)
        body.position = (cx, top)
        seg = pymunk.Segment(body, (-size / 2, BLOCK_RADIUS), (size / 2, BLOCK_RADIUS), BLOCK_RADIUS)
        seg.friction = friction
        seg.elasticity = elasticity
        seg.collision_type = CT_PLATFORM
        seg.filter = pymunk.ShapeFilter(categories=CAT_WORLD)
        block = Block(next(self._block_ids), body, seg, size, size, kind, permanent, structure, walkable)
        self.space.add(body, seg)
        self.blocks[block.id] = block
        self.shape_keys[seg] = ("block", block.id)
        self.version += 1
        return block

    def remove_block(self, block_id: int) -> None:
        block = self.blocks.pop(block_id, None)
        if block is None:
            return
        self._forget([block.shape])
        self.space.remove(block.body, block.shape)
        if self.passthrough_body is block.body:
            self.passthrough_body = None
        self.version += 1

    # -- queries ------------------------------------------------------------------------

    def surfaces(self) -> list[SurfaceSeg]:
        out: list[SurfaceSeg] = []
        sb = self.space.static_body
        for i, m in enumerate(self.monitors):
            out.append(SurfaceSeg(("floor", i), sb, m.work.left, m.work.right, m.work.bottom - FLOOR_RADIUS, "floor"))
        r = self.cfg.platform_thickness
        for p in self.platforms.values():
            for i, (s0, s1) in enumerate(p.spans):
                out.append(SurfaceSeg(("win", p.hwnd, i), p.body, s0, s1, -r, "window"))
        for b in self.blocks.values():
            if b.walkable:
                out.append(SurfaceSeg(("block", b.id), b.body, -b.width / 2, b.width / 2, 0.0, "block"))
        for i, seg in enumerate(self.element_shapes):
            out.append(SurfaceSeg(("elem", i), seg.body, seg.a.x, seg.b.x, seg.a.y - r, "element"))
        return out

    def surface_for_shape(self, shape: pymunk.Shape | None) -> tuple | None:
        return self.shape_keys.get(shape) if shape is not None else None

    def ground_below(self, x: float, y: float, depth: float) -> bool:
        hit = self.space.segment_query_first((x, y), (x, y + depth), 1, pymunk.ShapeFilter(mask=CAT_WORLD))
        return hit is not None

    def on_screen(self, x: float, y: float, margin: float = 100) -> bool:
        return any(
            m.bounds.left - margin <= x < m.bounds.right + margin
            and m.bounds.top - margin <= y < m.bounds.bottom + margin
            for m in self.monitors
        )

    def spawn_point(self) -> tuple[float, float]:
        m = self.monitors[0]
        return ((m.work.left + m.work.right) / 2, m.work.top + 150)

    def static_segments(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        return [(tuple(s.a), tuple(s.b)) for s in self._static_shapes]

    def platform_segments(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        out = []
        for p in self.platforms.values():
            ox, oy = p.body.position
            out.extend(((ox + s0, oy), (ox + s1, oy)) for s0, s1 in p.spans)
        return out
