import asyncio

from conftest import make, step
from stickfigure.agent.persona import maybe_about_screen, where_on_screen
from stickfigure.figure.brain import Brain
from stickfigure.figure.controller import Activity
from stickfigure.perception import locate as L
from stickfigure.perception.perception import Perception, Target, choose_target
from stickfigure.perception.uia import UIElement
from stickfigure.win.tracker import TrackedWindow
from stickfigure.world.blocks import BlockManager
from stickfigure.world.geometry import Rect


def el(name, role="Button", x=0, y=0, w=40, h=20, source="uia", **kw):
    return UIElement(name=name, role=role, rect=Rect(x, y, x + w, y + h), source=source, **kw)


TOOLBAR = [
    el("Back", x=10), el("Forward", x=60), el("Reload", x=110),
    el("Close", x=900), el("Close Tab", x=300, role="Button"),
    el("Share", x=500, y=400), el("Share this video with your friends and family on social media", role="Text", x=500, y=600, w=300),
    el("Address and search bar", role="Edit", x=200, w=400),
    el("Downloads", role="TreeItem", x=10, y=300),
    el("Downloads", role="Text", x=12, y=301, source="ocr"),
    el("secret", role="Edit", x=10, y=500, is_password=True),
]


def best(query, elements=TOOLBAR):
    return L.rank(elements, query)[0].el


def test_exact_and_casual_queries():
    assert best("Share").name == "Share"
    assert best("where's the share button?").name == "Share"
    assert best("reload").name == "Reload"
    assert best("search bar").name == "Address and search bar"


def test_role_word_in_label_breaks_tie():
    assert best("close tab").name == "Close Tab"
    assert best("close").name == "Close"


def test_short_label_beats_long_sentence_containing_word():
    cands = L.rank(TOOLBAR, "share")
    assert cands[0].el.name == "Share" and cands[0].score > cands[1].score


def test_password_fields_never_candidates():
    assert all(c.el.name != "secret" for c in L.rank(TOOLBAR, "secret"))


def test_ocr_duplicate_of_uia_is_merged():
    merged = L.merge_sources([e for e in TOOLBAR if e.source == "uia"], [e for e in TOOLBAR if e.source == "ocr"])
    assert sum(1 for e in merged if e.name == "Downloads") == 1


def test_confidence_requires_unambiguous_winner():
    twins = [el("Save", x=0), el("Save", x=500)]
    assert not L.confident(L.rank(twins, "save"))
    assert L.confident(L.rank([el("Save"), el("Open", x=100)], "save"))


def test_llm_fallback_used_for_semantic_query():
    class FakeOllama:
        async def chat_json(self, model, messages, schema, **kw):
            listing = messages[1]["content"]
            assert "Back" in listing  # interactive controls are offered even with no lexical match
            idx = next(int(line[1:line.index("]")]) for line in listing.splitlines() if "'Back'" in line or '"Back"' in line)
            return {"index": idx, "confidence": 0.9}

    p = Perception.__new__(Perception)
    p.ollama, p.cfg = FakeOllama(), __import__("stickfigure.config", fromlist=["CONFIG"]).CONFIG
    target = Target(1, "Browser", "chrome.exe", Rect(0, 0, 1000, 800))
    found = asyncio.run(p.locate_in("how do I return to the previous page", target, TOOLBAR, []))
    assert found.ok and found.element.name == "Back" and found.method == "llm"


def test_screen_prefilter():
    assert maybe_about_screen("where's the share button?")
    assert maybe_about_screen("what's on my screen")
    assert maybe_about_screen("maybe try watching a movie?")  # requests without screen words get routed too
    assert not maybe_about_screen("lol")  # only obvious small talk skips the routing call
    assert not maybe_about_screen("thanks!")


def test_where_on_screen_words():
    r = Rect(0, 0, 1000, 1000)
    assert where_on_screen(950, 50, r) == "top, right side"
    assert where_on_screen(500, 500, r) == "center"


def test_choose_target_prefers_named_app_then_last_foreground(monkeypatch):
    from stickfigure.win import win32

    names = {1: "chrome.exe", 2: "explorer.exe", 3: "code.exe"}
    monkeypatch.setattr(win32, "process_name", lambda h: names[h])
    wins = [TrackedWindow(i, Rect(0, 0, 100, 100), "X", t) for i, t in
            [(1, "YouTube - Google Chrome"), (2, "Downloads - File Explorer"), (3, "app.py - Visual Studio Code")]]
    assert choose_target(wins, 3, "").hwnd == 3
    assert choose_target(wins, 3, "file explorer").hwnd == 2
    assert choose_target(wins, 3, "chrome").hwnd == 1
    assert choose_target(wins, None, "").hwnd == 1


def test_brain_walks_toward_target_and_points():
    world, fig = make()
    brain = Brain(fig, world, BlockManager(world))
    brain.enabled = False
    fig.body.position = (300, 900)
    step(world, fig, 1)
    brain.command_point((1500, 200), hold=2.0)
    seen = []
    step(world, fig, 10, lambda dt: (brain.update(dt, (0, 0)), seen.append(fig.activity)))
    assert Activity.POINT in seen
    assert abs(fig.body.position.x - 1500) < 10
