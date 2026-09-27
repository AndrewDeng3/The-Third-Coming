import math

import pytest

from conftest import FLOOR_FEET, H, make, step
from stickfigure.config import CONFIG
from stickfigure.figure.brain import Brain
from stickfigure.figure.skeleton import two_bone_ik
from stickfigure.world.blocks import BlockManager
from stickfigure.world.geometry import Rect
from stickfigure.world.nav import find_path, plan_staircase, solve_jump


# -- IK -----------------------------------------------------------------------------


def test_ik_reaches_target_and_preserves_lengths():
    mid, end = two_bone_ik((0, 0), (30, 40), 30, 30, -1)
    assert math.dist(end, (30, 40)) < 1e-6
    assert abs(math.dist((0, 0), mid) - 30) < 1e-6
    assert abs(math.dist(mid, end) - 30) < 1e-6


def test_ik_bend_sign_puts_knee_forward():
    knee, _ = two_bone_ik((0, 0), (0, 50), 30, 30, -1)
    assert knee[0] > 0


def test_ik_clamps_unreachable_target():
    _, end = two_bone_ik((0, 0), (0, 500), 30, 30, -1)
    assert abs(math.dist((0, 0), end) - 60) < 0.01


# -- jump solver against real physics --------------------------------------------------


@pytest.mark.parametrize("dx,rise", [(0, 200), (250, 150), (-300, 0.5), (300, -300), (-300, 200)])
def test_jump_lands_where_solver_says(dx, rise):
    # Launch from a narrow raised window so targets off its sides can be below the start height.
    world, fig = make(Rect(860, 600, 1060, 1040))
    fig.body.position = (960, 500)
    step(world, fig, 1)
    x0, y0 = fig.feet
    target = (x0 + dx, y0 - rise)
    fig.jump(*solve_jump(x0, y0, *target))
    # Where do the feet cross the target height on the way down? (interpolated per physics step)
    crossed = None
    for _ in range(240):
        px, py = fig.feet
        fig.pre_step(H, (-9999, -9999))
        world.space.step(H)
        fig.post_step(H)
        fx, fy = fig.feet
        if fig.body.velocity.y > 0 and py <= target[1] <= fy:
            t = (target[1] - py) / (fy - py) if fy != py else 0
            crossed = px + (fx - px) * t
            break
    assert crossed is not None, "never came down through the target height"
    assert abs(crossed - target[0]) < 3


def test_solver_rejects_out_of_reach():
    assert solve_jump(0, 1000, 0, 600) is None  # 400px straight up > max rise
    assert solve_jump(0, 1000, 2000, 1000) is None  # too far sideways


# -- path finding ---------------------------------------------------------------------


def test_path_hops_up_a_window_ladder():
    low = Rect(300, 840, 700, 1040)  # 196 above floor
    mid = Rect(800, 640, 1200, 1040)  # 200 above low
    world, fig = make(low, mid)
    fig.body.position = (150, 900)
    step(world, fig, 1)
    surfaces = world.surfaces()
    start = world.surface_for_shape(fig.ground_shape)
    goal = next(s.key for s in surfaces if s.kind == "window" and s.body.position.y == 640)
    path = find_path(surfaces, start, fig.body.position.x, goal)
    assert path is not None and len(path) == 2
    assert find_path(surfaces, goal, 1000, start) is not None  # and back down


def test_unreachable_window_needs_staircase():
    high = Rect(900, 300, 1400, 700)
    world, fig = make(high)
    fig.body.position = (500, 900)
    step(world, fig, 1)
    surfaces = world.surfaces()
    start = world.surface_for_shape(fig.ground_shape)
    goal = next(s for s in surfaces if s.kind == "window")
    assert find_path(surfaces, start, fig.body.position.x, goal.key) is None
    blocks, land_x = plan_staircase(*fig.feet, goal, (0, 1920))
    assert 2 <= len(blocks) <= CONFIG.max_blocks
    tops = [top for _, top in blocks]
    assert tops == sorted(tops, reverse=True)  # climbing


# -- brain, end to end ----------------------------------------------------------------------


def run_brain(world, fig, brain, seconds):
    blocks = brain.blocks

    def frame(dt):
        brain.update(dt, (-9999, -9999))
        blocks.update(dt, world.surface_for_shape(fig.ground_shape))

    step(world, fig, seconds, frame)


def test_brain_travels_across_windows():
    low = Rect(300, 840, 700, 1040)
    mid = Rect(800, 640, 1200, 1040)
    world, fig = make(low, mid)
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (150, 900)
    step(world, fig, 1)
    goal = next(s.key for s in world.surfaces() if s.kind == "window" and s.body.position.y == 640)
    brain.command_goto(goal, 200, build=False)
    run_brain(world, fig, brain, 15)
    assert world.surface_for_shape(fig.ground_shape) == goal
    assert abs(fig.body.position.x - 1000) < 6


def test_brain_builds_staircase_to_high_window():
    high = Rect(900, 300, 1400, 700)
    world, fig = make(high)
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (500, 900)
    step(world, fig, 1)
    goal = next(s.key for s in world.surfaces() if s.kind == "window")
    brain.command_goto(goal)
    peak = []
    brain.blocks.on_spawn = lambda b: peak.append(len(world.blocks))
    run_brain(world, fig, brain, 20)
    assert world.surface_for_shape(fig.ground_shape) == goal
    assert max(peak) >= 2
    assert abs(fig.feet[1] - (300 - 3)) < 3


def test_brain_drops_back_down():
    win = Rect(700, 600, 1300, 1000)
    world, fig = make(win)
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (1000, 400)
    step(world, fig, 1)
    brain.command_goto(("floor", 0), 1000)
    run_brain(world, fig, brain, 10)
    assert abs(fig.feet[1] - FLOOR_FEET) < 3
