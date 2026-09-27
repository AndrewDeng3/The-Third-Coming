"""Companion curiosity (peeking at what the user does) with fakes for everything outside."""

import asyncio
import time

import pytest

from conftest import make
from stickfigure.agent.emotion import Emotion
from stickfigure.companion import Companion
from stickfigure.figure.animator import Anim, Animator
from stickfigure.figure.brain import Brain
from stickfigure.perception.perception import Target
from stickfigure.win import win32
from stickfigure.world.blocks import BlockManager
from stickfigure.world.geometry import Rect

DOC = Target(7, "Thesis draft - Word", "WINWORD.EXE", Rect(0, 0, 1600, 1000))


class FakeAgent:
    busy = False

    def __init__(self):
        self.follow_ups = []

    async def follow_up(self, situation, observation, instruction, on_text):
        self.follow_ups.append((observation, instruction))
        on_text("Ooh, a thesis? What's it about?")
        return "Ooh, a thesis? What's it about?"


class FakePerception:
    def __init__(self):
        self.described = []

    async def describe(self, target):
        self.described.append(target)
        return "Window: 'Thesis draft' (WINWORD.EXE)\nVisible text: Chapter 2: Methods"


@pytest.fixture
def comp(monkeypatch):
    world, fig = make()
    brain = Brain(fig, world, BlockManager(world))
    c = Companion(fig, brain, Animator(fig), world, FakeAgent(), memory=None, emotion=Emotion(),
                  surface_name=lambda key: "the taskbar")
    c.perception = FakePerception()
    c.peek_target = lambda: DOC
    said = []
    c.bubble_stream = said.append
    c.chat_add_assistant = said.append
    c._said = said
    monkeypatch.setattr(win32, "user_idle_seconds", lambda: 2.0)  # the user is busy doing something
    return c


def test_peeks_and_asks_one_question(comp):
    assert comp._peek_candidate(Anim.IDLE) == DOC
    asyncio.run(comp._peek(DOC))
    obs, instr = comp.agent.follow_ups[0]
    assert "Chapter 2: Methods" in obs and "ONE short" in instr
    assert "What's it about?" in comp._said[-1]


def test_does_not_ask_about_the_same_window_twice_soon(comp):
    asyncio.run(comp._peek(DOC))
    assert comp._peek_candidate(Anim.IDLE) is None


def test_no_peeking_when_user_is_away_or_figure_is_busy(comp, monkeypatch):
    monkeypatch.setattr(win32, "user_idle_seconds", lambda: 600.0)  # away from the keyboard
    assert comp._peek_candidate(Anim.IDLE) is None
    monkeypatch.setattr(win32, "user_idle_seconds", lambda: 2.0)
    assert comp._peek_candidate(Anim.SLEEP) is None
    comp.in_lounge = lambda: True
    assert comp._peek_candidate(Anim.IDLE) is None
    comp.in_lounge = lambda: False
    comp.peek_target = lambda: None  # e.g. a password manager in front: the app offers nothing
    assert comp._peek_candidate(Anim.IDLE) is None


def test_what_it_saw_is_context_for_the_reply_then_expires(comp):
    asyncio.run(comp._peek(DOC))
    assert "Chapter 2" in comp._peek_context()
    seen, _ = comp._recent_peek
    comp._recent_peek = (seen, time.monotonic() - 400)
    assert comp._peek_context() is None
