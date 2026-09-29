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
        assert "stance" in seen_poses and seen_poses & {"punch", "kick", "block", "hurt", "uppercut"}
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
