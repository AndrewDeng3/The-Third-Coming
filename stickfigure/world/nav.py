"""Navigation over walkable surfaces: ballistic jump solver, path search, staircase planner.

Surfaces are one-way platforms, so jumping *up through* a platform is a legal move,
and standing on a window or block you can drop through it.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from stickfigure.config import CONFIG, Config
from stickfigure.world.physics import SurfaceSeg

WALK_COST = 1.0
JUMP_COST = 120.0  # flat penalty so the figure prefers walking over hopping
DROP_COST = 60.0


@dataclass(frozen=True)
class Move:
    kind: str  # "jump" | "drop"
    src: tuple  # surface key
    takeoff_rel: float  # x relative to src body where the move starts
    dst: tuple
    land_rel: float  # x relative to dst body where we expect to land


def inset(cfg: Config = CONFIG) -> float:
    return cfg.body_width / 2 + cfg.body_radius + 2


def usable_span(seg: SurfaceSeg, cfg: Config = CONFIG) -> tuple[float, float, float] | None:
    """World (lo, hi, y) of where the figure's center can stand on `seg`."""
    x0, x1, y = seg.world()
    m = min(inset(cfg), (x1 - x0) / 2 - 1)
    if m < 4:
        return None
    return (x0 + m, x1 - m, y)


def solve_jump(
    x0: float, y0: float, x1: float, y1: float, cfg: Config = CONFIG, h: float | None = None
) -> tuple[float, float] | None:
    """Launch velocity to fly the feet from (x0, y0) to (x1, y1), or None if out of reach.

    Picks the lowest comfortable apex, raising it if needed to keep horizontal speed legal.
    Compensates for the physics step's discrete integration so landings are accurate.
    """
    g = cfg.gravity
    h = h if h is not None else 1.0 / cfg.physics_hz
    fig_h = cfg.figure_height
    if y1 < y0 - 1:  # going up: clear the target by a margin
        apex = y1 - 0.3 * fig_h
    else:  # level or down: small hop
        apex = y0 - 0.25 * fig_h
    for _ in range(12):
        up, down = y0 - apex, y1 - apex
        vy = math.sqrt(2 * g * up)
        if vy > cfg.jump_speed:
            return None
        t = math.sqrt(2 * up / g) + math.sqrt(2 * down / g)
        vx = (x1 - x0) / t
        if abs(vx) <= cfg.max_jump_vx:
            # Chipmunk moves the body with the velocity from *before* this step's gravity
            # (explicit Euler), so the discrete arc matches the continuous one if we launch
            # half a step of gravity slower.
            return (vx, -vy + g * h / 2)
        apex -= 0.25 * fig_h  # higher arc -> longer flight -> slower horizontal speed
    return None


def _edge(a: SurfaceSeg, entry_x: float, b: SurfaceSeg, cfg: Config) -> tuple[str, float, float] | None:
    """Best move from `a` (standing at entry_x) to `b`: (kind, takeoff_x, land_x) in world coords."""
    sa, sb = usable_span(a, cfg), usable_span(b, cfg)
    if sa is None or sb is None:
        return None
    a_lo, a_hi, ya = sa
    b_lo, b_hi, yb = sb
    lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
    if lo <= hi:  # horizontally overlapping
        x = min(max(entry_x, lo), hi)
        if yb < ya - 2:
            return ("jump", x, x) if solve_jump(x, ya, x, yb, cfg) else None
        if yb > ya + 2 and a.kind != "floor":
            return ("drop", x, x)
        return None
    if b_lo > a_hi:
        take, land = a_hi, b_lo
    else:
        take, land = a_lo, b_hi
    return ("jump", take, land) if solve_jump(take, ya, land, yb, cfg) else None


def find_path(
    surfaces: list[SurfaceSeg], start: tuple, start_x: float, goal: tuple, cfg: Config = CONFIG
) -> list[Move] | None:
    """Dijkstra over surfaces. Returns the moves to reach `goal` ([] if already on it), or None."""
    by_key = {s.key: s for s in surfaces}
    if start not in by_key or goal not in by_key:
        return None
    if start == goal:
        return []
    dist = {start: 0.0}
    entry = {start: start_x}
    prev: dict[tuple, Move] = {}
    heap = [(0.0, 0, start)]
    tie = 1
    done: set[tuple] = set()
    while heap:
        d, _, key = heapq.heappop(heap)
        if key in done:
            continue
        done.add(key)
        if key == goal:
            break
        a = by_key[key]
        for b in surfaces:
            if b.key in done or b.key == key:
                continue
            e = _edge(a, entry[key], b, cfg)
            if e is None:
                continue
            kind, take, land = e
            air = JUMP_COST if kind == "jump" else DROP_COST
            nd = d + abs(take - entry[key]) * WALK_COST + abs(land - take) + air
            if nd < dist.get(b.key, math.inf):
                dist[b.key] = nd
                entry[b.key] = land
                prev[b.key] = Move(kind, key, take - a.body.position.x, b.key, land - b.body.position.x)
                heapq.heappush(heap, (nd, tie, b.key))
                tie += 1
    if goal not in prev:
        return None
    path: list[Move] = []
    k = goal
    while k != start:
        m = prev[k]
        path.append(m)
        k = m.src
    return path[::-1]


def reachable(surfaces: list[SurfaceSeg], start: tuple, start_x: float, cfg: Config = CONFIG) -> set[tuple]:
    out = {start}
    for s in surfaces:
        if s.key != start and find_path(surfaces, start, start_x, s.key, cfg) is not None:
            out.add(s.key)
    return out


def plan_staircase(
    start_x: float, start_y: float, goal: SurfaceSeg, bounds: tuple[float, float], cfg: Config = CONFIG
) -> tuple[list[tuple[float, float]], float] | None:
    """Blocks (center x, top y) leading from the feet at (start_x, start_y) up to `goal`.

    Returns (blocks, landing_x on goal) or None if the goal isn't above us or it'd take too many blocks.
    Blocks climb diagonally toward the goal, then zigzag once directly beneath it.
    """
    span = usable_span(goal, cfg)
    if span is None:
        return None
    g_lo, g_hi, gy = span
    if gy >= start_y - 2:
        return None
    left, right = bounds
    half = cfg.block_size / 2
    target_x = min(max(start_x, g_lo), g_hi)
    blocks: list[tuple[float, float]] = []
    cx, cy = start_x, start_y
    zig = 1 if target_x >= start_x else -1
    while solve_jump(cx, cy, min(max(cx, g_lo), g_hi), gy, cfg) is None:
        if len(blocks) >= cfg.max_blocks:
            return None
        remaining = target_x - cx
        if abs(remaining) > cfg.stair_run:
            nx = cx + math.copysign(cfg.stair_run, remaining)
        else:
            nx = cx + zig * cfg.stair_run
            zig = -zig
        if nx - half < left or nx + half > right:  # bounce off the screen edge
            nx = cx - (nx - cx)
            zig = -zig
        ny = max(cy - cfg.stair_rise, gy + 10)
        blocks.append((nx, ny))
        cx, cy = nx, ny
    return blocks, min(max(cx, g_lo), g_hi)
