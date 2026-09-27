"""Playful body behaviors: backflips, riding the cursor, the lounge's little life, build-mode grid."""

import random

from conftest import FLOOR_FEET, H, make

from stickfigure.agent.emotion import Emotion
from stickfigure.figure.animator import Anim, Animator
from stickfigure.figure.brain import Brain
from stickfigure.world.blocks import BlockManager
from stickfigure.world.geometry import Rect


def sim(world, fig, brain, seconds, cursor=lambda t: (-9999, -9999)):
    t = 0.0
    for i in range(int(seconds / H)):
        t += H
        c = cursor(t)
        fig.pre_step(H, c)
        world.space.step(H)
        fig.post_step(H)
        if i % 2:
            fig.update(2 * H)
            brain.update(2 * H, c)


def setup():
    world, fig = make()
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (900, 900)
    sim(world, fig, brain, 1)
    return world, fig, brain


def test_backflip_lands_on_its_feet():
    world, fig, brain = setup()
    knocked = []
    brain.command("flip")
    sim(world, fig, brain, 0.3)
    assert fig.tumbling  # spinning in the air
    for _ in range(40):
        sim(world, fig, brain, 0.05)
        knocked.append(fig.knocked > 0)
    assert fig.grounded and not any(knocked)
    assert abs(fig.feet[1] - FLOOR_FEET) < 3


def test_rides_the_cursor_and_lets_go_when_shaken():
    world, fig, brain = setup()
    held = (960, FLOOR_FEET - fig.cfg.figure_height - 90)  # above its head, within a jump
    brain.command("ride")
    sim(world, fig, brain, 3, cursor=lambda t: held)
    assert fig.riding
    ox, oy = fig.ride_offset()
    assert abs(fig.body.position.x - held[0]) < 3 and abs(fig.body.position.y - (held[1] + oy)) < 3
    assert Animator(fig)._classify() == Anim.RIDE
    # Carried along...
    sim(world, fig, brain, 0.5, cursor=lambda t: (held[0] + 200 * t, held[1]))
    assert fig.riding and fig.body.position.x > held[0] + 50
    # ...then flung off by a violent shake.
    base = fig.body.position.x
    sim(world, fig, brain, 0.2, cursor=lambda t: (base + 6000 * t, held[1]))
    assert not fig.riding


def test_ride_gives_up_when_cursor_is_out_of_reach():
    world, fig, brain = setup()
    brain.command("ride")
    sim(world, fig, brain, 3, cursor=lambda t: (960, 100))  # way up high
    assert not fig.riding and fig.grounded


def test_lounge_life_visits_activities_and_leaves_by_itself():
    from stickfigure.ui.lounge import DOOR, LoungeLife

    e = Emotion()
    e.energy, e.curiosity = 0.9, 0.9
    life = LoungeLife(e, rng=random.Random(3))
    left = []
    life.on_left = lambda: left.append(True)
    life.enter(stay=40)
    seen = set()
    for _ in range(int(120 / (1 / 30))):
        life.update(1 / 30)
        seen.add(life.activity)
        assert 0.0 <= life.x <= 1.0
        if left:
            break
    assert "arriving" in seen and len(seen) >= 3
    assert left and life.gone and abs(life.x - DOOR) < 0.03


def test_lounge_listens_instead_of_leaving_mid_conversation():
    from stickfigure.ui.lounge import LoungeLife

    life = LoungeLife(Emotion(), rng=random.Random(1))
    left = []
    life.on_left = lambda: left.append(True)
    life.enter(stay=1)
    for _ in range(30 * 20):
        life.talk(5)
        life.update(1 / 30)
    assert not left and life.activity == "listening"
    assert life.do("dance") and life.activity == "dance"


def test_build_mode_grid_snaps_onto_the_taskbar():
    from stickfigure.ui.build_mode import Grid

    g = Grid(Rect(0, 0, 1920, 1040), 60)
    assert g.cell(10, 1039) == (0, 0)
    cx, top = g.place(0, 0)
    assert (cx, top) == (30, 980)  # bottom row rests on the work-area bottom
    assert g.cell(95, 1040 - 61) == (1, 1)
    assert g.cell(5000, 10) is None


def test_stash_hides_permanent_blocks_without_forgetting_them():
    world, fig = make()
    blocks = BlockManager(world)
    saved = []
    blocks.on_persist = lambda data: saved.append(list(data))
    blocks.spawn(500, 980, "stone", permanent=True, structure=1)
    blocks.spawn(700, 980, "wood")
    persisted = saved[-1]
    assert blocks.stash() == 1
    assert not world.blocks and saved[-1] == persisted  # the saved copy wasn't touched
    assert blocks.unstash(lambda x, y: True) == 1
    assert len(blocks.permanent) == 1 and not blocks.stashed
