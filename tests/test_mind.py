import asyncio
import hashlib
import math

import pytest

from conftest import make, step
from stickfigure.agent.agent import OFFLINE_LINE, Agent
from stickfigure.agent.emotion import Emotion
from stickfigure.agent.memory import Memory
from stickfigure.agent.ollama import OllamaError
from stickfigure.agent.persona import Situation, TagFilter, clean_reply, system_prompt
from stickfigure.figure.brain import Brain
from stickfigure.figure.controller import Activity
from stickfigure.world.blocks import BlockManager

SIT = Situation("sitting", "the taskbar")


def run(coro):
    return asyncio.run(coro)


async def fake_embed(texts, kind):
    """Bag-of-words hashing embedder: texts sharing words are close."""
    out = []
    for t in texts:
        v = [0.0] * 64
        for w in t.lower().replace(".", " ").replace("'", " ").split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1
        out.append([x / n for x in v])
    return out


class FakeOllama:
    def __init__(self, reply_chunks=("Hi ", "there!"), extract=None, fail=False):
        self.reply_chunks = reply_chunks
        self.extract = extract or {"facts": [], "remove_ids": [], "sentiment": 0.0, "action": "none"}
        self.fail = fail
        self.seen_messages = []

    async def chat_stream(self, model, messages, **kw):
        self.seen_messages.append(messages)
        if self.fail:
            raise OllamaError("down")
        for c in self.reply_chunks:
            yield c

    async def chat_json(self, model, messages, schema, **kw):
        return self.extract


# -- tag filter -----------------------------------------------------------------------


def test_tag_filter_strips_actions_across_chunks():
    f = TagFilter()
    out = f.feed("Sure thing! [s") + f.feed("it]") + f.flush()
    assert out == "Sure thing! "
    assert f.actions == ["sit"]


def test_tag_filter_keeps_unknown_brackets():
    f = TagFilter()
    assert f.feed("an [array] of [thi") + f.flush() == "an [array] of [thi"
    assert f.actions == []


def test_tag_filter_drops_roleplay_emotes_across_chunks():
    f = TagFilter()
    out = f.feed("Sure! *bounces up") + f.feed(" and down* Wheee!") + f.flush()
    assert out == "Sure!  Wheee!"
    assert clean_reply(out) == "Sure! Wheee!"


def test_tag_filter_lone_asterisk_eventually_passes_through():
    f = TagFilter()
    out = f.feed("2 * 3 is six, " + "and that's a very long sentence with no closing star at all " * 2) + f.flush()
    assert out.startswith("2 * 3 is six")


def test_structured_action_dispatched_once(tmp_path):
    async def go():
        mem = Memory(tmp_path / "m.db", fake_embed)
        fake = FakeOllama(
            reply_chunks=("Okay!",), extract={"facts": [], "remove_ids": [], "sentiment": 0.2, "action": "dance"}
        )
        agent = Agent(fake, mem, Emotion())
        got = []
        agent.on_action = got.append
        await agent.reply("dance for me", SIT, lambda t: None)
        await agent.drain()
        assert got == ["dance"]

        fake.reply_chunks = ("Okay! [sit]",)  # tag already acted: don't also run the structured action
        await agent.reply("sit down", SIT, lambda t: None)
        await agent.drain()
        assert got == ["dance"]

    run(go())


def test_clean_reply():
    assert clean_reply('  "**Hello**   there"  ') == "**Hello** there"  # markdown is rendered in chat now
    assert clean_reply("I like to climb, just for fun! climb") == "I like to climb, just for fun!"
    assert clean_reply("Time to dance!") == "Time to dance!"  # a real word mid-sentence stays


# -- emotion -----------------------------------------------------------------------------


def test_emotion_decays_to_baseline_and_reacts():
    e = Emotion(annoyance=0.0)
    e.on_thrown(4000)
    assert e.annoyance >= 0.3
    for _ in range(600):
        e.update(1.0, "other", 0)
    assert e.annoyance < 0.05


def test_emotion_energy_drains_when_moving_and_recovers_asleep():
    e = Emotion(energy=0.5)
    for _ in range(60):
        e.update(1.0, "moving", 0)
    tired = e.energy
    for _ in range(60):
        e.update(1.0, "sleeping", 0)
    assert e.energy > tired + 0.5


def test_emotion_roundtrip():
    e = Emotion(energy=0.1, affection=0.9)
    assert Emotion.from_dict(e.to_dict()) == e
    assert e.label() == "exhausted"


def test_prompt_mentions_mood_situation_and_facts():
    from stickfigure.agent.memory import Fact

    p = system_prompt("Stick", Emotion(annoyance=0.8), SIT, [Fact(1, "The user's name is Sam.", 0)])
    assert "Sam" in p and "taskbar" in p and "grumpy" in p


# -- memory -------------------------------------------------------------------------------


def test_memory_recall_and_persistence(tmp_path):
    db = tmp_path / "m.db"
    m = Memory(db, fake_embed)
    run(m.add_fact("The user's name is Sam."))
    run(m.add_fact("The user has a dog named Biscuit."))
    run(m.add_fact("The user works as a nurse."))
    m.add_message("user", "hello")
    m.put("emotion", {"energy": 0.3})
    m.close()

    m2 = Memory(db, fake_embed)  # "restart"
    hits = run(m2.recall("dog named", k=1))  # fake embedder is bag-of-words, not semantic
    assert hits and "Biscuit" in hits[0].text
    assert m2.recent_messages(5) == [{"role": "user", "content": "hello"}]
    assert m2.get("emotion") == {"energy": 0.3}


def test_memory_dedupes_near_identical_facts(tmp_path):
    m = Memory(tmp_path / "m.db", fake_embed)
    a = run(m.add_fact("The user's name is Sam."))
    b = run(m.add_fact("The user's name is Sam"))
    assert a == b and len(m.all_facts()) == 1


def test_clear_messages_keeps_facts_and_mood(tmp_path):
    m = Memory(tmp_path / "m.db", fake_embed)
    run(m.add_fact("The user likes tea."))
    m.add_message("user", "hello")
    m.add_message("assistant", "hi!")
    m.put("emotion", {"energy": 0.4})
    m.clear_messages()
    assert m.recent_messages(10) == []
    assert [f.text for f in m.all_facts()] == ["The user likes tea."]
    assert m.get("emotion") == {"energy": 0.4}


def test_forget_everything(tmp_path):
    m = Memory(tmp_path / "m.db", fake_embed)
    run(m.add_fact("The user likes tea."))
    m.forget_everything()
    assert m.all_facts() == [] and run(m.recall("tea")) == []
    run(m.add_fact("The user likes coffee."))  # still usable afterwards
    assert len(m.all_facts()) == 1


# -- agent ----------------------------------------------------------------------------------


def test_agent_streams_reply_extracts_facts_and_uses_them_next_turn(tmp_path):
    async def go():
        mem = Memory(tmp_path / "m.db", fake_embed)
        fake = FakeOllama(
            reply_chunks=("Nice to ", "meet you, Sam! [wa", "ve]"),
            extract={"facts": ["The user's name is Sam."], "remove_ids": [], "sentiment": 0.8, "action": "none"},
        )
        emo = Emotion(affection=0.5)
        agent = Agent(fake, mem, emo)
        streamed = []
        reply = await agent.reply("hi, I'm Sam", SIT, streamed.append)
        await agent.drain()
        assert reply.text == "Nice to meet you, Sam!"
        assert reply.actions == ["wave"]
        assert "[" not in "".join(streamed)
        assert [f.text for f in mem.all_facts()] == ["The user's name is Sam."]
        assert emo.affection > 0.55

        fake.reply_chunks = ("Of course, Sam.",)
        await agent.reply("do you remember my name?", SIT, lambda t: None)
        system = fake.seen_messages[-1][0]["content"]
        assert "The user's name is Sam." in system
        history = [m["content"] for m in fake.seen_messages[-1][1:]]
        assert history[-1] == "do you remember my name?" and "hi, I'm Sam" in history

    run(go())


def test_agent_corrects_facts(tmp_path):
    async def go():
        mem = Memory(tmp_path / "m.db", fake_embed)
        old = await mem.add_fact("The user's favorite color is blue.")
        fake = FakeOllama(extract={"facts": ["The user's favorite color is green."], "remove_ids": [old], "sentiment": 0, "action": "none"})
        agent = Agent(fake, mem, Emotion())
        await agent.reply("actually my favorite color is green now", SIT, lambda t: None)
        await agent.drain()
        assert [f.text for f in mem.all_facts()] == ["The user's favorite color is green."]

    run(go())


def test_screen_turn_hides_reply_from_memory_extractor(tmp_path):
    async def go():
        mem = Memory(tmp_path / "m.db", fake_embed)
        fake = FakeOllama(reply_chunks=("I see your Secret-Project folder!",))
        seen = []
        orig = fake.chat_json

        async def spy(model, messages, schema, **kw):
            seen.append(messages[-1]["content"])
            return await orig(model, messages, schema, **kw)

        fake.chat_json = spy
        agent = Agent(fake, mem, Emotion())
        await agent.reply("what's in explorer?", SIT, lambda t: None, observation="Window: Explorer ... Secret-Project")
        await agent.drain()
        assert seen and "Secret-Project" not in seen[-1]

    run(go())


def test_follow_up_message_is_stored_as_companion_turn(tmp_path):
    async def go():
        mem = Memory(tmp_path / "m.db", fake_embed)
        fake = FakeOllama(reply_chunks=("Done! I typed hello.",))
        agent = Agent(fake, mem, Emotion())
        text = await agent.follow_up(SIT, "You finished typing hello.", "(tell the user)", lambda t: None)
        assert text == "Done! I typed hello."
        assert mem.recent_messages(1) == [{"role": "assistant", "content": "Done! I typed hello."}]
        assert "You finished typing hello." in fake.seen_messages[-1][0]["content"]

    run(go())


def test_restated_fact_is_not_deleted(tmp_path):
    async def go():
        mem = Memory(tmp_path / "m.db", fake_embed)
        old = await mem.add_fact("The user loves hiking.")
        fake = FakeOllama(extract={"facts": ["The user loves hiking."], "remove_ids": [old], "sentiment": 0, "action": "none"})
        agent = Agent(fake, mem, Emotion())
        await agent.reply("I love hiking", SIT, lambda t: None)
        await agent.drain()
        assert [f.text for f in mem.all_facts()] == ["The user loves hiking."]

    run(go())


def test_agent_offline_is_graceful(tmp_path):
    async def go():
        mem = Memory(tmp_path / "m.db", fake_embed)
        agent = Agent(FakeOllama(fail=True), mem, Emotion())
        seen = []
        reply = await agent.reply("hello?", SIT, seen.append)
        assert reply.error and seen[-1] == OFFLINE_LINE
        assert mem.recent_messages(5)[-1]["role"] == "user"  # no fake assistant turn stored

    run(go())


# -- body commands & mood-driven behavior -----------------------------------------------------


@pytest.mark.parametrize("action", ["sit", "wave", "hop", "dance", "come", "sleep"])
def test_brain_commands_run(action):
    world, fig = make()
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (500, 900)
    step(world, fig, 1)
    brain.command(action)
    seen = set()

    def frame(dt):
        brain.update(dt, (900, 1000))
        seen.add(fig.activity)

    step(world, fig, 3, frame)
    expected = {"sit": Activity.SIT, "wave": Activity.WAVE, "sleep": Activity.SLEEP}.get(action)
    if expected:
        assert expected in seen
    if action == "come":
        assert fig.body.position.x > 700


def test_exhausted_figure_falls_asleep_on_its_own():
    import random

    random.seed(1)
    world, fig = make()
    emo = Emotion(energy=0.05)
    brain = Brain(fig, world, BlockManager(world), emo)
    fig.body.position = (500, 900)
    step(world, fig, 1)
    asleep = []
    step(world, fig, 10, lambda dt: (brain.update(dt, (-999, -999)), asleep.append(fig.activity == Activity.SLEEP)))
    assert any(asleep)


def test_pet_is_detected_on_quick_click():
    world, fig = make()
    fig.body.position = (500, 900)
    step(world, fig, 1)
    fig.grab((500, 900))
    fig.release()
    assert ("pet",) in fig.events
    assert fig.body.velocity.length == 0  # a pat doesn't drop or fling the figure
