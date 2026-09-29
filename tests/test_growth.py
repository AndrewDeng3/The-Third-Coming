"""Personality growth: traits drift, a self-journal, firsts, reflection, and it all survives restarts."""

import asyncio

from test_mind import SIT, FakeOllama, fake_embed

from stickfigure.agent.agent import Agent
from stickfigure.agent.emotion import Emotion
from stickfigure.agent.growth import MAX_NOTES, MAX_REFLECT_STEP, Growth
from stickfigure.agent.memory import Memory
from stickfigure.agent.persona import system_prompt


def test_traits_drift_slowly_and_stay_in_bounds(tmp_path):
    g = Growth(Memory(tmp_path / "m.db", fake_embed))
    start = g.traits["sass"]
    g.nudge("sass", 1.0)  # a single event can't swing it much
    assert g.traits["sass"] - start <= 0.05 + 1e-9
    for _ in range(200):
        g.nudge("sass", 0.05)
    assert g.traits["sass"] <= 0.95
    assert 1.4 < g.weight("sass") <= 1.5 and g.weight("nonexistent") == 1.0


def test_growth_survives_a_restart(tmp_path):
    db = tmp_path / "m.db"
    g = Growth(Memory(db, fake_embed))
    g.nudge("boldness", 0.05)
    g.add_note("favorite", "Sitting on top of the Spotify window")
    assert g.first("first_ride", "First cursor ride!")
    assert not g.first("first_ride", "again")  # firsts happen once
    bold = g.traits["boldness"]
    g.memory.close()
    g2 = Growth(Memory(db, fake_embed))
    assert g2.traits["boldness"] == round(bold, 4)
    assert [n["text"] for n in g2.notes] == ["Sitting on top of the Spotify window", "First cursor ride!"]
    assert "first_ride" in g2.firsts


def test_journal_is_bounded_but_keeps_moments(tmp_path):
    g = Growth(Memory(tmp_path / "m.db", fake_embed))
    g.first("f", "A precious moment")
    for i in range(MAX_NOTES + 10):
        g.add_note("opinion", f"Opinion number {i}")
    assert len(g.notes) == MAX_NOTES
    assert any(n["text"] == "A precious moment" for n in g.notes)


def test_it_shows_in_the_system_prompt(tmp_path):
    g = Growth(Memory(tmp_path / "m.db", fake_embed))
    g.add_note("joke", "The user keeps calling me 'noodle'")
    p = system_prompt("The Third Coming", Emotion(), SIT, [], self_text=g.describe())
    assert "Who you've become" in p and "noodle" in p


def test_reflection_adds_notes_and_shifts_traits_within_limits(tmp_path):
    class Reflective(FakeOllama):
        async def chat_json(self, model, messages, schema, **kw):
            if "mood_of_late" in schema["properties"]:
                return {"notes": [{"kind": "opinion", "text": "Being thrown is actually kind of fun."}],
                        "remove": [0], "trait_changes": {"boldness": 0.5, "sass": -0.02},
                        "mood_of_late": "Lots of flying lately, and I'm into it."}
            return await super().chat_json(model, messages, schema, **kw)

    mem = Memory(tmp_path / "m.db", fake_embed)
    agent = Agent(Reflective(), mem, Emotion())
    agent.growth = g = Growth(mem)
    g.add_note("opinion", "Being thrown is scary.")
    for _ in range(3):
        g.event("the user threw me")
    before = dict(g.traits)
    assert asyncio.run(agent.reflect())
    assert [n["text"] for n in g.notes] == ["Being thrown is actually kind of fun."]  # changed its mind
    assert abs(g.traits["boldness"] - before["boldness"] - MAX_REFLECT_STEP) < 1e-9  # clamped
    assert g.mood_of_late.startswith("Lots of flying") and g.events == []


def test_kindness_makes_it_sweeter(tmp_path):
    from stickfigure.companion import Companion

    g = Growth(Memory(tmp_path / "m.db", fake_embed))
    c = Companion.__new__(Companion)  # just the sentiment hook
    c.growth = g
    before = g.traits["sass"]
    c.on_sentiment(0.9)
    assert g.traits["sass"] < before
    c.on_sentiment(-1.0)
    assert g.events[-1].endswith("the user snapped at me")
