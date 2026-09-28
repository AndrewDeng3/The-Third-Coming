"""Standing on UI elements, following the cursor, the mind, and learning."""

import asyncio
from dataclasses import dataclass

from conftest import FLOOR_FEET, H, make

from stickfigure.agent.agent import Agent
from stickfigure.agent.emotion import Emotion
from stickfigure.agent.memory import Memory
from stickfigure.figure.brain import Brain
from stickfigure.world.blocks import BlockManager
from stickfigure.world.elements import pick_element_platforms
from stickfigure.world.geometry import Rect
from test_mind import SIT, FakeOllama, fake_embed


def sim(world, fig, seconds, cursor=lambda t: (-9999.0, -9999.0), brain=None):
    t = 0.0
    for i in range(int(seconds / H)):
        t += H
        c = cursor(t)
        fig.pre_step(H, c)
        world.space.step(H)
        fig.post_step(H)
        if i % 2:
            fig.update(2 * H)
            if brain is not None:
                brain.update(2 * H, c)


# -- UI elements --------------------------------------------------------------------------------------


@dataclass
class El:
    role: str
    rect: Rect
    is_password: bool = False


def test_element_platform_picking():
    win = Rect(100, 100, 1300, 900)
    els = [El("Edit", Rect(200, 300, 600, 330)), El("Edit", Rect(210, 301, 590, 330)),  # nested dup
           El("Button", Rect(700, 300, 720, 320)),  # tiny
           El("Pane", Rect(100, 100, 1300, 900)),  # the whole window
           El("Button", Rect(300, 110, 500, 130)),  # in the title bar
           El("Edit", Rect(200, 500, 600, 530), is_password=True),
           El("Image", Rect(800, 400, 1100, 700))]
    got = pick_element_platforms(els, win)
    assert [(e.rect.left, e.rect.top) for e in got] == [(200, 300), (800, 400)]


def test_figure_stands_on_a_text_box_and_it_moves_with_the_window():
    from conftest import snap

    win = Rect(400, 500, 1400, 1000)
    world, fig = make(win)
    hwnd = 1
    assert world.set_element_platforms(hwnd, [Rect(600, 700, 1000, 730)])
    assert not world.set_element_platforms(hwnd, [Rect(600, 700, 1000, 730)])  # unchanged
    assert any(s.kind == "element" for s in world.surfaces())
    fig.body.position = (800, 600)
    sim(world, fig, 1.5)
    assert world.surface_for_shape(fig.ground_shape)[0] == "elem"
    assert abs(fig.feet[1] - (700 - world.cfg.platform_thickness)) < 4
    # drag the window 50 px down: the text box (and the figure on it) go along
    world.sync(snap(Rect(400, 550, 1400, 1050)), fig.body)
    sim(world, fig, 0.5)
    assert abs(fig.feet[1] - (750 - world.cfg.platform_thickness)) < 4
    world.sync(snap(), fig.body)  # window closed: its elements go too
    assert not world.element_shapes


# -- following the cursor ------------------------------------------------------------------------------


def test_follows_the_cursor_onto_another_window():
    win = Rect(1100, 850, 1500, 1040)
    world, fig = make(win)
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (400, 900)
    sim(world, fig, 1.0, brain=brain)
    brain.command("follow")
    pointer = (1300.0, 700.0)  # hovering above the window
    sim(world, fig, 12, cursor=lambda t: pointer, brain=brain)
    key = world.surface_for_shape(fig.ground_shape)
    assert key is not None and key[0] == "win"
    assert abs(fig.body.position.x - pointer[0]) < 80
    # then back down to the taskbar
    pointer = (300.0, 1000.0)
    sim(world, fig, 12, cursor=lambda t: pointer, brain=brain)
    assert world.surface_for_shape(fig.ground_shape)[0] == "floor"
    assert abs(fig.body.position.x - pointer[0]) < 80 and abs(fig.feet[1] - FLOOR_FEET) < 3


# -- the mind & learning -------------------------------------------------------------------------------


def test_mind_picks_an_action(tmp_path):
    class Mind(FakeOllama):
        async def chat_json(self, model, messages, schema, **kw):
            if "action" in schema["properties"] and "thought" in schema["properties"]:
                return {"thought": "The cursor looks fun.", "action": "ride_cursor", "say": "Hop on!"}
            return await super().chat_json(model, messages, schema, **kw)

    agent = Agent(Mind(), Memory(tmp_path / "m.db", fake_embed), Emotion())
    r = asyncio.run(agent.think(SIT, {"user": "active"}, ["dance"]))
    assert r == {"thought": "The cursor looks fun.", "action": "ride_cursor", "target": -1, "say": "Hop on!"}


def test_learns_a_lesson_and_recalls_it_for_a_similar_task(tmp_path):
    class Teacher(FakeOllama):
        async def chat_json(self, model, messages, schema, **kw):
            if "lesson" in schema["properties"]:
                return {"lesson": "In chrome, click the New Tab button then type in the address bar."}
            return await super().chat_json(model, messages, schema, **kw)

    async def go():
        agent = Agent(Teacher(), Memory(tmp_path / "m.db", fake_embed), Emotion())
        await agent.learn("open a new tab", "chrome.exe", "done", "ok", ["click Button 'New Tab' -> done"])
        return await agent.lessons_for("open a new tab and search", "chrome.exe"), agent

    lessons, agent = asyncio.run(go())
    assert lessons and "New Tab" in lessons[0]
    # lessons don't leak into the conversation recall
    assert asyncio.run(agent._episodes_for("new tab chrome")) == []


def test_climbs_onto_an_element_of_a_maximized_window():
    """Maximized windows have no top-edge platform, so their text boxes hang off the static body."""
    world, fig = make()
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (600, 900)
    sim(world, fig, 1.0, brain=brain)
    world.set_element_platforms(99, [Rect(900, 820, 1300, 850)], ["Edit 'Search'"])  # ~210 px up: a jump
    brain.command("climb_element")
    sim(world, fig, 10, brain=brain)
    assert world.surface_for_shape(fig.ground_shape)[0] == "elem"


def test_builds_stairs_to_a_high_element():
    world, fig = make()
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (600, 900)
    sim(world, fig, 1.0, brain=brain)
    world.set_element_platforms(99, [Rect(900, 560, 1300, 590)], ["Edit 'Search'"])  # ~480 px up: too high
    brain.command("climb_element")
    sim(world, fig, 25, brain=brain)
    assert world.surface_for_shape(fig.ground_shape)[0] == "elem"
    assert len(world.blocks) >= 1  # it had to build to get there


def test_builds_up_to_a_pointer_hovering_in_the_air_and_grabs_it():
    world, fig = make()
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (600, 900)
    sim(world, fig, 1.0, brain=brain)
    pointer = (800.0, 520.0)  # way above its reach, nothing under it but air
    brain.command("reach_cursor")
    sim(world, fig, 5, cursor=lambda t: pointer, brain=brain)  # (it lets go by itself after 6-20 s)
    assert fig.riding
    assert len(world.blocks) >= 2
