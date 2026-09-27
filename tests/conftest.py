from stickfigure.figure.controller import Figure
from stickfigure.win.tracker import DesktopSnapshot, TrackedWindow
from stickfigure.world.geometry import Monitor, Rect
from stickfigure.world.physics import World

MON = Monitor(Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1040))
FLOOR_FEET = 1040 - 4
H = 1 / 120


def snap(*rects: Rect) -> DesktopSnapshot:
    return DesktopSnapshot([TrackedWindow(i + 1, r, "X") for i, r in enumerate(rects)], [MON], False, 0.0)


def make(*rects: Rect) -> tuple[World, Figure]:
    world = World()
    world.sync(snap(*rects), None)
    return world, Figure(world)


def step(world: World, fig: Figure, seconds: float, per_frame=None) -> None:
    """Simulate at 120 Hz physics / 60 Hz frame logic."""
    for i in range(int(seconds / H)):
        fig.pre_step(H, (-9999, -9999))
        world.space.step(H)
        fig.post_step(H)
        if i % 2:
            fig.update(2 * H)
            if per_frame:
                per_frame(2 * H)


def feet(fig: Figure) -> float:
    return fig.feet[1]
