import dataclasses

from conftest import FLOOR_FEET, make, step
from stickfigure.config import CONFIG
from stickfigure.figure.brain import Brain
from stickfigure.world.blocks import KINDS, BlockManager
from stickfigure.world.structures import TEMPLATES, find_site, plan


def manager(world, **cfg):
    m = BlockManager(world, dataclasses.replace(CONFIG, **cfg) if cfg else CONFIG)
    saved = []
    m.on_persist = saved.append
    return m, saved


def test_permanent_blocks_never_expire_temporary_ones_do():
    world, fig = make()
    m, saved = manager(world, block_lifetime=1.0)
    temp = m.spawn(300, 900)
    perm = m.spawn(500, 900, kind="gold", permanent=True, structure=1)
    for _ in range(40):
        m.update(0.1, None)
    assert temp.id not in world.blocks
    assert perm.id in world.blocks
    assert saved[-1] == [{"x": 500.0, "top": 900.0, "kind": "gold", "structure": 1}]


def test_permanent_blocks_survive_a_restart_but_offscreen_ones_are_dropped():
    world, _ = make()
    m, saved = manager(world)
    m.spawn(500, 900, kind="brick", permanent=True, structure=3)
    data = saved[-1] + [{"x": 99999, "top": 900, "kind": "wood", "structure": 3}]
    world2, _ = make()
    m2, _ = manager(world2)
    assert m2.restore(data, lambda x, y: world2.on_screen(x, y, margin=0)) == 1
    (b,) = world2.blocks.values()
    assert (b.kind, b.permanent, b.structure) == ("brick", True, 3)
    assert m2.new_structure_id() == 4  # doesn't collide with restored structures


def test_clear_temporary_keeps_permanent_and_clear_all_removes_everything():
    world, _ = make()
    m, saved = manager(world)
    m.spawn(300, 900)
    m.spawn(500, 900, permanent=True)
    m.clear_temporary()
    assert [b.permanent for b in world.blocks.values()] == [True]
    m.clear_all()
    assert world.blocks == {} and saved[-1] == []


def test_permanent_cap():
    world, _ = make()
    m, _ = manager(world, max_permanent_blocks=2)
    assert m.spawn(100, 900, permanent=True) and m.spawn(200, 900, permanent=True)
    assert m.spawn(300, 900, permanent=True) is None


def test_ice_is_slippery_bouncy_bounces_and_planner_avoids_both():
    world, fig = make()
    m, _ = manager(world)
    ice = m.spawn(400, 900, kind="ice", permanent=True)
    bouncy = m.spawn(900, 900, kind="bouncy", permanent=True)
    assert ice.shape.friction < 0.1 and bouncy.shape.elasticity > 0.8
    keys = {s.key for s in world.surfaces()}
    assert ("block", ice.id) not in keys and ("block", bouncy.id) not in keys
    # Drop the figure onto the bouncy block: it should rebound upward at least once.
    fig.body.position = (900, 600)
    ups = []
    step(world, fig, 1.5, lambda dt: ups.append(fig.body.velocity.y < -200))
    assert any(ups)


def test_structure_plans_build_bottom_up_and_sites_avoid_existing_blocks():
    for name in TEMPLATES:
        cells = plan(name, 100, 1000, 60, material="stone", accent="wood")
        tops = [top for _, top, _ in cells]
        assert tops == sorted(tops, reverse=True)  # lower rows first
        assert all(k in KINDS for _, _, k in cells)
    house = plan("house", 100, 1000, 60, material="stone", accent="wood")
    assert {k for _, top, k in house if top < 1000 - 3 * 60} == {"wood"}  # roof in the accent
    occupied = [(300, 880, 360, 1000)]  # a block already standing at x 300..360
    left = find_site((0, 1920), 1000, "wall", 60, occupied, near_x=330)
    assert left is not None and (left + 4 * 60 + 30 <= 300 or left - 30 >= 360)


def test_figure_builds_a_permanent_structure_then_knocks_it_down():
    world, fig = make()
    m, saved = manager(world)
    brain = Brain(fig, world, m)
    brain.enabled = False
    fig.body.position = (900, 900)
    step(world, fig, 1)
    brain._queued = ("build", brain._build_structure("pyramid"))
    step(world, fig, 20, lambda dt: (brain.update(dt, (-999, -999)), m.update(dt, None)))
    assert len(m.permanent) == len(TEMPLATES["pyramid"])
    assert len(m.structures()) == 1
    assert abs(fig.feet[1] - FLOOR_FEET) < 3
    brain._queued = ("demolish", brain._demolish())
    step(world, fig, 20, lambda dt: (brain.update(dt, (-999, -999)), m.update(dt, None)))
    assert m.permanent == [] and saved[-1] == []
