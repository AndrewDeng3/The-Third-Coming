import asyncio

import pytest

from stickfigure.actions.executor import Move, Pause, Wheel
from stickfigure.actions.mischief import ALLOWED_STEPS, Mischief
from stickfigure.agent.emotion import Emotion
from stickfigure.perception.perception import Target
from stickfigure.safety.audit import AuditLog
from stickfigure.world.geometry import Rect

TARGET = Target(1, "Some article - Google Chrome", "chrome.exe", Rect(0, 0, 1600, 1000))


@pytest.fixture
def mischief(tmp_path):
    m = Mischief(perception=None, audit=AuditLog(tmp_path / "a.jsonl"), emotion=Emotion())
    yield m
    m.close()


def plan(m, kind, home=(800, 500)):
    return asyncio.run(m._plan(kind, TARGET, home))


@pytest.mark.parametrize("kind", ["tug", "read"])
def test_pranks_never_click_or_type(mischief, kind):
    steps, line, _ = plan(mischief, kind)
    assert steps and line
    assert all(isinstance(s, ALLOWED_STEPS) for s in steps)


def test_tug_returns_the_cursor_home_and_stays_in_window(mischief):
    mischief.figure_x = lambda: 1500  # figure is to the right: tug toward it
    steps, _, pull = plan(mischief, "tug", home=(800, 500))
    moves = [s for s in steps if isinstance(s, Move)]
    assert (moves[-1].x, moves[-1].y) == (800, 500)
    assert pull[0] > 800
    assert all(TARGET.rect.contains(s.x, s.y) for s in moves)


def test_tug_near_edge_is_clamped_inside(mischief):
    mischief.figure_x = lambda: 5000
    steps, _, _ = plan(mischief, "tug", home=(1590, 995))
    assert all(TARGET.rect.contains(s.x, s.y) for s in steps if isinstance(s, Move))


def test_read_scrolls_back_to_where_it_was(mischief):
    steps, _, _ = plan(mischief, "read")
    assert sum(s.clicks for s in steps if isinstance(s, Wheel)) == 0


def test_no_mischief_when_grumpy_tired_or_busy(mischief, monkeypatch):
    from stickfigure.win import win32

    started = []
    monkeypatch.setattr(win32, "user_idle_seconds", lambda: 999)
    monkeypatch.setattr(mischief, "_foreground_target", lambda: TARGET)
    monkeypatch.setattr(asyncio, "ensure_future", lambda coro: (started.append(1), coro.close()))

    def attempt(**mood):
        mischief._next_at = 0
        mischief.emotion = Emotion(**mood)
        mischief.tick()

    attempt(energy=0.9, affection=0.9, annoyance=0.0)
    assert started == [1]
    attempt(energy=0.9, affection=0.9, annoyance=0.8)  # grumpy
    attempt(energy=0.1, affection=0.9)  # tired
    mischief.can_play = lambda: False  # e.g. a task is running
    attempt(energy=0.9, affection=0.9)
    assert started == [1]


def test_requires_user_to_be_idle(mischief, monkeypatch):
    from stickfigure.win import win32

    started = []
    monkeypatch.setattr(win32, "user_idle_seconds", lambda: 3)  # user is active
    monkeypatch.setattr(mischief, "_foreground_target", lambda: TARGET)
    monkeypatch.setattr(asyncio, "ensure_future", lambda coro: (started.append(1), coro.close()))
    mischief._next_at = 0
    mischief.emotion = Emotion(energy=0.9, affection=0.9)
    mischief.tick()
    assert started == []
