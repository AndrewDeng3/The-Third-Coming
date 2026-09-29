"""Sparring with the legends: the match plays out, ends, and cleans up (no knockdowns, no leftover rival)."""

import random

from conftest import FLOOR_FEET, H, make

from stickfigure.figure.animator import Animator
from stickfigure.figure.brain import Brain
from stickfigure.figure.rival import ROSTER, Rival
from stickfigure.world.blocks import BlockManager


def arena(seed):
    random.seed(seed)
    world, fig = make()
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    rivals = []

    def make_rival(who, x, floor_y, facing):
        r = Rival(who, x, floor_y, facing)
        rivals.append(r)
        return r

    brain.make_rival = make_rival
    fig.body.position = (900, 900)
    return world, fig, brain, rivals


def sim(world, fig, brain, rivals, seconds, watch=None):
    for i in range(int(seconds / H)):
        fig.pre_step(H, (-9999, -9999))
        world.space.step(H)
        fig.post_step(H)
        if i % 2:
            fig.update(2 * H)
            brain.update(2 * H, (-9999, -9999))
            for r in rivals:
                r.update(2 * H)
            brain.fx.update(2 * H)
            if watch:
                watch()


def test_a_full_sparring_match_plays_out_and_cleans_up():
    for seed in range(6):
        world, fig, brain, rivals = arena(seed)
        sim(world, fig, brain, rivals, 1.0)
        brain.command("fight")
        seen_poses, knocked = set(), []

        def watch():
            if fig.pose_mode:
                seen_poses.add(fig.pose_mode)
            knocked.append(fig.knocked > 0)

        sim(world, fig, brain, rivals, 25, watch)
        assert len(rivals) == 1
        r = rivals[0]
        assert r.done, f"seed {seed}: the rival should have gone"
        assert brain.task_name == "none" and fig.pose_mode is None
        assert fig.grounded and not any(knocked) and abs(fig.feet[1] - FLOOR_FEET) < 3
        assert seen_poses & {"stance", "guard"} and seen_poses & {"punch", "kick", "block", "hurt", "uppercut", "slash"}
        assert r.who in ROSTER


def test_being_grabbed_mid_fight_sends_the_rival_away():
    world, fig, brain, rivals = arena(1)
    sim(world, fig, brain, rivals, 1.0)
    brain.command("fight")
    sim(world, fig, brain, rivals, 2.0)
    fig.grab((900, 850))  # the user picks it up
    sim(world, fig, brain, rivals, 1.5)
    assert rivals[0].fading and fig.pose_mode is None


def test_every_fight_pose_builds():
    world, fig = make()
    anim = Animator(fig)
    fig.grounded = True
    for mode in ("stance", "punch", "kick", "uppercut", "block", "hurt", "taunt", "victory"):
        fig.pose_mode = mode
        for _ in range(5):
            pose = anim.update(1 / 60)
        assert anim.state == mode and all(abs(x) < 200 and abs(y) < 200 for x, y in pose.values())


def test_blows_line_up_sparks_land_between_the_fighters_at_body_height():
    """Every spark (fist/foot/blade contact) must sit between the two fighters and within body height, and every
    magic burst must happen at a fighter - no hits in thin air."""
    from stickfigure.figure.effects import Effects

    for seed in range(10):
        world, fig, brain, rivals = arena(seed)
        checks = []

        class Spy(Effects):
            def spark(self, x, y, color=(255, 240, 150), size=1.0):
                (fx, fy), r = tuple(fig.body.position), rivals[0]
                rx, ry = tuple(r.puppet.body.position)
                checks.append(("spark", x, y, fx, fy, rx, ry))
                return super().spark(x, y, color, size)

            def pop(self, orb):
                (fx, fy), r = tuple(fig.body.position), rivals[0]
                rx, ry = tuple(r.puppet.body.position)
                checks.append(("burst", orb.x, orb.y, fx, fy, rx, ry))
                return super().pop(orb)

        brain.fx = Spy()
        sim(world, fig, brain, rivals, 1.0)
        brain.command("fight")
        sim(world, fig, brain, rivals, 40)
        assert rivals and rivals[0].done
        H = fig.cfg.figure_height
        from stickfigure.figure.sparring import strike_offset
        longest = max(strike_offset(m, w)[0] for m in ("slash", "thrust") for w in ("sword", "staff", "pickaxe"))
        for kind, x, y, fx, fy, rx, ry in checks:
            lo, hi = min(fx, rx) - 0.2 * H, max(fx, rx) + 0.2 * H
            assert lo <= x <= hi, f"seed {seed}: {kind} at x={x:.0f} is outside the fighters ({fx:.0f}, {rx:.0f})"
            top, bottom = min(fy, ry) - 0.62 * H, max(fy, ry) + 0.62 * H
            assert top <= y <= bottom, f"seed {seed}: {kind} at y={y:.0f} is off the bodies ({fy:.0f}, {ry:.0f})"
            if kind == "spark":  # blows connect at close range: at most two weapon tips meeting in the middle
                assert abs(fx - rx) < 2 * longest + 0.15 * H, f"seed {seed}: spark {abs(fx - rx):.0f}px apart"


def test_strike_reach_makes_sense():
    from stickfigure.figure.sparring import strike_offset

    punch, kick = strike_offset("punch")[0], strike_offset("kick")[0]
    sword, staff = strike_offset("slash", "sword")[0], strike_offset("thrust", "staff")[0]
    assert 20 < punch < 70 and kick > 25
    assert sword > punch and staff > punch  # weapons reach further, so fighters stand further apart
