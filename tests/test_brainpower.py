"""Markdown replies, long-term episodic memory, composing content, adventures' hard limits, policy tweaks."""

import asyncio

import pytest

from test_mind import FakeOllama, SIT, fake_embed

from stickfigure.actions.adventure import _check_steps, search_url
from stickfigure.actions.executor import Click, Keys, Move, Pause, Type, Wheel
from stickfigure.actions.schema import parse_step
from stickfigure.agent.agent import Agent
from stickfigure.agent.emotion import Emotion
from stickfigure.agent.memory import Memory
from stickfigure.agent.persona import TagFilter, clean_reply, strip_code_fence, system_prompt
from stickfigure.safety.policy import ALLOW, ASK, DENY, Proposed, WindowInfo, decide
from stickfigure.ui.markdown import plain, to_html
from stickfigure.world.geometry import Rect

CHROME = WindowInfo(1, "chrome.exe", "Chrome_WidgetWin_1", "main.py - Replit - Google Chrome")


def run(coro):
    return asyncio.run(coro)


def filtered(text: str) -> tuple[str, list[str]]:
    f = TagFilter()
    out = "".join(f.feed(ch) for ch in text) + f.flush()  # worst case: one character per chunk
    return clean_reply(out), f.actions


# -- markdown ----------------------------------------------------------------------------------------


def test_markdown_survives_the_tag_filter_but_emotes_and_tags_dont():
    text, actions = filtered("*grins* Here you go:\n\n**Steps**\n* one\n* two\n\n```python\nprint(a*b*c) # [sit]\n```\n[wave]")
    assert "grins" not in text
    assert "**Steps**" in text and "* one\n* two" in text
    assert "print(a*b*c) # [sit]" in text  # code is never touched
    assert actions == ["wave"]


def test_markdown_to_html_escapes_and_formats():
    html = to_html("**bold** and `x<y`\n\n```js\nif (a < b) {}\n```\n- item\n<script>alert(1)</script>")
    assert "<b>bold</b>" in html and "x&lt;y" in html and "a &lt; b" in html
    assert "<li>item</li>" in html
    assert "<script>" not in html


def test_unfinished_code_fence_renders_while_streaming():
    assert "print(1)" in to_html("Sure:\n```python\nprint(1)")


def test_plain_text_for_bubble_and_voice():
    p = plain("## Title\n**Bold** point\n```python\nx = 1\n```\n- item")
    assert "```" not in p and "**" not in p and "x = 1" not in p and "code's in the chat" in p
    assert "Title" in p and "• item" in p


def test_strip_code_fence():
    assert strip_code_fence("```python\nprint(1)\n```") == "print(1)"
    assert strip_code_fence("print(1)") == "print(1)"


# -- memory -----------------------------------------------------------------------------------------


def test_episodes_are_recalled_across_restarts(tmp_path):
    db = tmp_path / "m.db"
    m = Memory(db, fake_embed)
    run(m.add_episode("User: my favorite band is Radiohead\nYou: Ooh, great taste!"))
    run(m.add_episode("User: I'm learning the violin\nYou: Fancy!"))
    m.close()
    m2 = Memory(db, fake_embed)
    hits = run(m2.recall_episodes("favorite band", k=1, max_distance=1.0))
    assert hits and "Radiohead" in hits[0].text
    assert run(m2.recall_episodes("band", k=3, before=0.0)) == []  # only ones older than the chat window
    m2.forget_everything()
    assert run(m2.recall_episodes("band", k=3)) == []


def test_reply_includes_old_conversations_and_stores_the_new_one(tmp_path):
    seen = {}

    class Spy(FakeOllama):
        async def chat_stream(self, model, messages, **kw):
            seen["system"] = messages[0]["content"]
            async for c in super().chat_stream(model, messages, **kw):
                yield c

    async def go():
        mem = Memory(tmp_path / "m.db", fake_embed)
        await mem.add_episode("User: my cat is called Pixel\nYou: Cute!")
        agent = Agent(Spy(), mem, Emotion())
        await agent.reply("what is my cat called", SIT, lambda t: None)
        await agent.drain()
        return mem

    mem = run(go())
    assert "Pixel" in seen["system"] and "Older conversations" in seen["system"]
    assert mem.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 2


def test_system_prompt_allows_markdown_for_substantial_answers():
    p = system_prompt("Stick", Emotion(), SIT, [])
    assert "fenced code" in p and "No markdown" not in p


# -- composing and new task steps --------------------------------------------------------------------


def test_open_url_and_switch_window_steps_parse():
    r = Rect(0, 0, 1000, 800)
    step = parse_step({"action": "open_url", "text": "https://www.google.com/search?q=cats"}, [], r, None)
    assert step.kind == "open_url" and step.describe().startswith("open https://")
    assert isinstance(parse_step({"action": "open_url", "text": "javascript:alert(1)"}, [], r, None), str)
    assert isinstance(parse_step({"action": "switch_window", "text": ""}, [], r, None), str)


def test_policy_for_urls_hotkeys_and_code():
    assert decide(Proposed("open_url", text="https://example.com"), CHROME, None, True).verdict == ALLOW
    assert decide(Proposed("open_url", text="file:///C:/Windows"), CHROME, None, True).verdict == DENY
    assert decide(Proposed("open_url", text="https://paypal.com/checkout"), CHROME, None, True).verdict == DENY
    assert decide(Proposed("hotkey", keys="ctrl+t"), CHROME, None, True).verdict == ALLOW
    assert decide(Proposed("hotkey", keys="ctrl+enter"), CHROME, None, True).verdict == ASK
    program = "import subprocess\n\nsubprocess.run(['notepad.exe'])\nprint('hi')\n"
    # a whole program in a code editor is just text; the same thing in a single-line field is refused
    assert decide(Proposed("type_text", text=program), CHROME, None, True, multiline=True).verdict == ALLOW
    assert decide(Proposed("type_text", text=program), CHROME, None, True, multiline=False).verdict == DENY
    assert decide(Proposed("type_text", text="cmd.exe /c whoami"), CHROME, None, True, multiline=True).verdict == DENY


def test_compose_returns_clean_content(tmp_path):
    class Writer(FakeOllama):
        async def chat_stream(self, model, messages, **kw):
            for c in ("```python\n", "def hi():\n", "    print('hi')\n", "```"):
                yield c

    agent = Agent(Writer(), Memory(tmp_path / "m.db", fake_embed), Emotion())
    assert run(agent.compose("a hello function")) == "def hi():\n    print('hi')"


# -- adventures ---------------------------------------------------------------------------------------


def test_adventures_can_never_click_type_or_press_other_keys():
    _check_steps([Move(1, 2), Wheel(-3), Pause(1), Keys("ctrl+tab"), Keys("ctrl+shift+tab"), Keys("ctrl+w")])
    for bad in (Click(), Type("hello"), Keys("enter"), Keys("ctrl+a"), Keys("alt+f4")):
        with pytest.raises(AssertionError):
            _check_steps([bad])


def test_search_url_is_escaped():
    assert search_url("why do cats knead & purr") == "https://www.google.com/search?q=why+do+cats+knead+%26+purr"


def test_temperature_scales_how_often_and_how_many_clicks():
    from stickfigure.config import temp_clicks, temp_scale

    assert temp_scale(3) == 1.0 and temp_scale(10) < 0.35 and temp_scale(1) == 3.0
    assert temp_scale(0) == 3.0 and temp_scale(99) == temp_scale(10)  # clamped to 1..10
    assert [temp_clicks(t) for t in (1, 2, 3, 5, 6, 9, 10)] == [0, 0, 1, 1, 2, 3, 3]


def test_adventure_clicks_only_plain_left_clicks_when_allowed_and_never_ads():
    from stickfigure.actions.adventure import SKIP_LINK

    with pytest.raises(AssertionError):
        _check_steps([Click()])  # not unless the result-visiting code asks for it
    _check_steps([Click(), Keys("alt+left")], allow_click=True)
    with pytest.raises(AssertionError):
        _check_steps([Click(button="right")], allow_click=True)
    with pytest.raises(AssertionError):
        _check_steps([Click(count=2)], allow_click=True)
    for bad in ("Sponsored · Buy cheap flights", "Sign in to your account", "Download setup.exe now",
                "Images for octopus"):
        assert SKIP_LINK.search(bad), bad
    assert not SKIP_LINK.search("How Octopuses Change Color - Smithsonian Magazine")


def test_voice_is_human_and_slang_is_allowed():
    p = system_prompt("The Third Coming", Emotion(), SIT, [])
    assert "Slang is welcome" in p and "customer-service bot" in p
