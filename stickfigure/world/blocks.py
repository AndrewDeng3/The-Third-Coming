"""Blocks the figure places: kinds (with physics), temporary vs permanent, persistence.

- Temporary blocks (staircases) fade after `block_lifetime` seconds; at most `max_blocks` exist.
- Permanent blocks (structures) never expire and survive restarts. They go away only via clear_all()
  (the user) or remove() (the figure deciding to knock something down). Capped at max_permanent_blocks.
Rendering and persistence subscribe via callbacks.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable

from stickfigure.config import CONFIG, Config
from stickfigure.world.physics import Block, World

FADE_TIME = 0.35


@dataclass(frozen=True)
class Kind:
    name: str
    friction: float = 1.0
    elasticity: float = 0.0
    walkable: bool = True  # False: slippery/bouncy - fun to land on, bad for planned paths


KINDS = {
    "wood": Kind("wood"),
    "stone": Kind("stone"),
    "brick": Kind("brick"),
    "grass": Kind("grass"),
    "glass": Kind("glass", friction=0.6),
    "gold": Kind("gold"),
    "ice": Kind("ice", friction=0.03, walkable=False),
    "bouncy": Kind("bouncy", elasticity=0.92, walkable=False),
}
STAIR_KINDS = ("wood", "stone", "brick", "grass")  # dependable footing for climbing
BUILD_KINDS = tuple(KINDS)


class BlockManager:
    def __init__(self, world: World, cfg: Config = CONFIG):
        self.world = world
        self.cfg = cfg
        self.age: dict[int, float] = {}  # temporary blocks only
        self.fading: dict[int, float] = {}  # id -> seconds of fade left
        self._structure_ids = itertools.count(1)
        self._stashed: list[dict] | None = None  # permanent blocks put away while the figure lounges
        self.on_spawn: Callable[[Block], None] = lambda b: None
        self.on_fade: Callable[[int, float], None] = lambda bid, alpha: None
        self.on_remove: Callable[[int], None] = lambda bid: None
        self.on_persist: Callable[[list[dict]], None] = lambda data: None  # permanent set changed

    # -- queries -----------------------------------------------------------------------------

    @property
    def available(self) -> int:
        return self.cfg.max_blocks - len(self.age)

    @property
    def permanent(self) -> list[Block]:
        return [b for b in self.world.blocks.values() if b.permanent and b.id not in self.fading]

    @property
    def permanent_room(self) -> int:
        return self.cfg.max_permanent_blocks - len(self.permanent)

    def structures(self) -> dict[int, list[Block]]:
        out: dict[int, list[Block]] = {}
        for b in self.permanent:
            out.setdefault(b.structure, []).append(b)
        return out

    def new_structure_id(self) -> int:
        return next(self._structure_ids)

    # -- changes -------------------------------------------------------------------------------

    def spawn(self, cx: float, top: float, kind: str = "wood", permanent: bool = False,
              structure: int = 0) -> Block | None:
        k = KINDS.get(kind, KINDS["wood"])
        if permanent:
            if self.permanent_room <= 0:
                return None
        elif self.available <= 0:
            self._evict_oldest()
        block = self.world.add_block(cx, top, k.name, k.friction, k.elasticity, permanent, structure, k.walkable)
        if not permanent:
            self.age[block.id] = 0.0
        self.on_spawn(block)
        if permanent:
            self._persist()
        return block

    def remove(self, bid: int) -> None:
        """Fade a block out (the figure knocking it down)."""
        if bid in self.world.blocks and bid not in self.fading:
            self.fading[bid] = FADE_TIME

    def update(self, dt: float, standing_on: tuple | None) -> None:
        for bid in list(self.fading):
            self.fading[bid] -= dt
            if self.fading[bid] <= 0:
                self._remove(bid)
            else:
                self.on_fade(bid, self.fading[bid] / FADE_TIME)
        for bid in list(self.age):
            if bid in self.fading:
                continue
            self.age[bid] += dt
            if self.age[bid] > self.cfg.block_lifetime:
                if standing_on == ("block", bid):
                    self.age[bid] -= 5.0  # don't pull the floor out from under the figure
                else:
                    self.fading[bid] = FADE_TIME

    def clear_temporary(self) -> None:
        for bid in list(self.age):
            self._remove(bid)

    def clear_all(self) -> None:
        """Everything, permanent structures included (only when the user asks)."""
        for bid in list(self.world.blocks):
            self._remove(bid)

    def stash(self) -> int:
        """Tidy the desktop (the figure went to its lounge): temporary blocks go for good, permanent ones
        are taken down without being forgotten - the saved copy stays, and unstash() puts them back."""
        self.clear_temporary()
        self._stashed = self.snapshot()
        for b in self.permanent:
            self.world.remove_block(b.id)
            self.on_remove(b.id)
        for bid in list(self.fading):  # half-demolished ones just finish going
            self._remove(bid)
        return len(self._stashed)

    def unstash(self, on_screen: Callable[[float, float], bool]) -> int:
        data, self._stashed = self._stashed, None
        if not data:
            return 0
        return self.restore(data, on_screen)

    @property
    def stashed(self) -> bool:
        return bool(self._stashed)

    # -- persistence ---------------------------------------------------------------------------

    def snapshot(self) -> list[dict]:
        return [{"x": round(b.body.position.x, 1), "top": round(b.body.position.y, 1), "kind": b.kind,
                 "structure": b.structure} for b in self.permanent]

    def restore(self, data: list[dict], on_screen: Callable[[float, float], bool]) -> int:
        """Rebuild saved permanent blocks (skipping any that are now off-screen). Returns how many."""
        n = 0
        top_sid = 0
        for d in data or []:
            try:
                x, top, kind, sid = float(d["x"]), float(d["top"]), str(d.get("kind", "wood")), int(d.get("structure", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if not on_screen(x, top) or self.permanent_room <= 0:
                continue
            k = KINDS.get(kind, KINDS["wood"])
            block = self.world.add_block(x, top, k.name, k.friction, k.elasticity, True, sid, k.walkable)
            self.on_spawn(block)
            top_sid = max(top_sid, sid)
            n += 1
        self._structure_ids = itertools.count(top_sid + 1)
        return n

    def _persist(self) -> None:
        self.on_persist(self.snapshot())

    # -- internals -----------------------------------------------------------------------------

    def _evict_oldest(self) -> None:
        if self.age:
            self._remove(max(self.age, key=self.age.get))

    def _remove(self, bid: int) -> None:
        block = self.world.blocks.get(bid)
        self.age.pop(bid, None)
        self.fading.pop(bid, None)
        self.world.remove_block(bid)
        self.on_remove(bid)
        if block is not None and block.permanent:
            self._persist()
