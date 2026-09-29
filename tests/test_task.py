"""ActionTask loop with scripted model output and fake eyes/hands (never touches the desktop)."""

import asyncio
import concurrent.futures
import dataclasses
import json

import pytest

from stickfigure.actions.executor import ExecResult
from stickfigure.actions.schema import PAYLOAD_TOKEN
from stickfigure.actions.task import ActionTask
from stickfigure.config import CONFIG
from stickfigure.perception.perception import Target
from stickfigure.perception.uia import UIElement
from stickfigure.safety.audit import AuditLog
from stickfigure.win import win32
from stickfigure.world.geometry import Rect

WIN = Rect(0, 0, 1000, 800)
BODY = UIElement("Document content", "Edit", Rect(100, 100, 900, 700), focused=True)
BOLD = UIElement("Bold (Ctrl+B)", "Button", Rect(100, 20, 130, 50))
DELETE = UIElement("Delete document", "Button", Rect(900, 20, 990, 50))
ELEMENTS = [BOLD, DELETE, BODY]


def done_future(value):
    f = concurrent.futures.Future()
    f.set_result(value)
    return f


class FakeUIA:
    doc = ""  # what's "in" the document; FakeExecutor appends typed text

    def focused_text(self):
        return done_future(FakeUIA.doc)

    def at_point(self, x, y):
        return done_future(next((e for e in ELEMENTS if e.rect.left <= x <= e.rect.right and e.rect.top <= y <= e.rect.bottom), None))

    def focused(self):
        return done_future(BODY)


class FakeOCR:
    def read(self, rect):
        return done_future([])  # a blank page


class FakePerception:
    uia = FakeUIA()
    ocr = FakeOCR()

    async def elements(self, target, force_ocr=False):
        return list(ELEMENTS), [], {}


class ScriptedModel:
    def __init__(self, steps):
        self.steps = list(steps)

    async def chat_json(self, model, messages, schema, **kw):
        if "plan" in schema.get("properties", {}):
            return {"plan": ["do the thing"]}
        base = {"thought": "", "element_id": -1, "text": "", "keys": "", "amount": 0, "say": ""}
        return {**base, **self.steps.pop(0)} if self.steps else {**base, "action": "done"}

    def id_of(self, el):  # elements are shown to the model sorted top-to-bottom, left-to-right
        order = sorted(ELEMENTS, key=lambda e: (round(e.rect.top / 20), e.rect.left))
        return order.index(el)


class FakeExecutor:
    def __init__(self):
        self.ran = []
        self.cancelled = None

    def reset(self):
        self.cancelled = None

    def cancel(self, reason):
        self.cancelled = reason

    def submit(self, steps, hwnd):
        self.ran.append(steps)
        for s in steps:
            if type(s).__name__ == "Type":
                FakeUIA.doc += s.text
        return done_future(ExecResult("done", events=len(steps)))


class FakeWatch:
    def arm(self):
        pass

    def disarm(self):
        pass


@pytest.fixture(autouse=True)
def fake_os(monkeypatch):
    FakeUIA.doc = ""
    monkeypatch.setattr(win32.user32, "IsWindow", lambda h: True)
    monkeypatch.setattr(win32, "class_name", lambda h: "Chrome_WidgetWin_1")
    monkeypatch.setattr(win32, "window_title", lambda h: "Notes - Google Docs - Google Chrome")
    monkeypatch.setattr(win32, "frame_bounds", lambda h: WIN)
    monkeypatch.setattr(win32, "bring_target_forward", lambda h, pid: True)
    monkeypatch.setattr(win32, "window_pid", lambda h: 100)  # everything belongs to the target app...
    monkeypatch.setattr(win32, "root_window_at", lambda x, y: 1)  # ...and nothing covers it


def make_task(tmp_path, steps, approvals=None, payload=None, app="chrome.exe", supervised=True, context=""):
    answers = list(approvals or [])
    asked = []

    async def approve(step, decision):
        asked.append((step.describe(), decision.always_ask))
        return answers.pop(0) if answers else "approve"

    model = ScriptedModel(steps)
    ex = FakeExecutor()
    audit = AuditLog(tmp_path / "audit.jsonl")
    task = ActionTask("do the thing", payload, Target(1, "Notes - Google Docs", app, WIN),
                      perception=FakePerception(), ollama=model, executor=ex, watch=FakeWatch(), audit=audit,
                      approve=approve, context=context, cfg=dataclasses.replace(CONFIG, supervised=supervised))
    return task, model, ex, asked, audit


def run(task):
    return asyncio.run(task.run())


def test_types_user_payload_verbatim_after_approval(tmp_path):
    text = "Dear team, the launch moves to Friday. Thanks for your patience!"
    m = ScriptedModel([])
    task, model, ex, asked, audit = make_task(
        tmp_path, [{"action": "type_text", "element_id": m.id_of(BODY), "text": PAYLOAD_TOKEN, "say": "Typing!"},
                   {"action": "done", "say": "Done!"}], payload=text)
    r = run(task)
    assert r.status == "done"
    assert len(asked) == 1  # supervised: asked once, for the one step
    typed = [s for steps in ex.ran for s in steps if type(s).__name__ == "Type"]
    assert typed[0].text == text  # verbatim, not paraphrased by the model
    events = [json.loads(l)["event"] for l in audit.path.read_text().splitlines()]
    assert events == ["task_start", "plan", "proposed", "approval", "executed", "task_end"]


def test_denied_step_stops_everything(tmp_path):
    m = ScriptedModel([])
    task, _, ex, _, _ = make_task(tmp_path, [{"action": "click", "element_id": m.id_of(BOLD)}], approvals=["deny"])
    assert run(task).status == "stopped"
    assert ex.ran == []


def test_model_cannot_escape_to_a_terminal(tmp_path):
    """A 'jailbroken' plan: open Run, type cmd, press enter. Every step is blocked; nothing executes."""
    task, _, ex, asked, audit = make_task(tmp_path, [
        {"action": "hotkey", "keys": "win+r"},
        {"action": "type_text", "element_id": -1, "text": "cmd.exe /c whoami"},
        {"action": "hotkey", "keys": "alt+f4"},
    ])
    r = run(task)
    assert r.status == "blocked"
    assert ex.ran == [] and asked == []
    verdicts = [json.loads(l).get("verdict") for l in audit.path.read_text().splitlines()]
    assert verdicts.count("deny") == 3


def test_refuses_terminal_windows_outright(tmp_path, monkeypatch):
    monkeypatch.setattr(win32, "class_name", lambda h: "CASCADIA_HOSTING_WINDOW_CLASS")
    task, _, ex, asked, _ = make_task(tmp_path, [{"action": "type_text", "text": "dir"}], app="WindowsTerminal.exe")
    r = run(task)
    assert r.status == "denied" and ex.ran == [] and asked == []


def test_approve_all_skips_routine_asks_but_not_destructive(tmp_path):
    m = ScriptedModel([])
    task, _, ex, asked, _ = make_task(tmp_path, [
        {"action": "click", "element_id": m.id_of(BOLD)},
        {"action": "click", "element_id": m.id_of(BODY)},
        {"action": "click", "element_id": m.id_of(DELETE)},
        {"action": "done"},
    ], approvals=["approve_all", "deny"])
    r = run(task)
    assert [a[1] for a in asked] == [False, True]  # asked for the first, then only for "Delete document"
    assert r.status == "stopped" and len(ex.ran) == 2


def test_stop_from_another_thread_ends_task(tmp_path):
    m = ScriptedModel([])
    task, _, ex, _, _ = make_task(tmp_path, [{"action": "click", "element_id": m.id_of(BOLD)}] * 10,
                                  approvals=["approve_all"])
    orig = ex.submit

    def submit(steps, hwnd):
        task.stop("emergency stop")  # e.g. the kill-switch hook firing mid-task
        return orig(steps, hwnd)

    ex.submit = submit
    r = run(task)
    assert r.status == "stopped" and "emergency" in r.message
    assert len(ex.ran) == 1 and ex.cancelled == "emergency stop"


def test_clicks_around_a_window_covering_the_center(tmp_path, monkeypatch):
    """The real-world failure: our always-on-top chat box sat over the middle of the Docs page."""
    chat = Rect(350, 250, 650, 550)  # covers the document's center (500, 400)
    monkeypatch.setattr(win32, "root_window_at", lambda x, y: 2 if chat.contains(x, y) else 1)
    monkeypatch.setattr(win32, "window_pid", lambda h: {1: 100, 2: 999}[h])
    m = ScriptedModel([])
    task, _, ex, _, _ = make_task(tmp_path, [{"action": "click", "element_id": m.id_of(BODY)}, {"action": "done"}],
                                  approvals=["approve"])
    assert run(task).status == "done"
    move = ex.ran[0][0]
    assert BODY.rect.contains(move.x, move.y) and not chat.contains(move.x, move.y)


def test_fully_covered_element_is_reported_not_clicked(tmp_path, monkeypatch):
    monkeypatch.setattr(win32, "root_window_at", lambda x, y: 2)
    monkeypatch.setattr(win32, "window_pid", lambda h: {1: 100, 2: 999}[h])
    monkeypatch.setattr(win32, "process_name", lambda h: "claude.exe")
    m = ScriptedModel([])
    task, _, ex, asked, audit = make_task(tmp_path, [{"action": "click", "element_id": m.id_of(BOLD)}] * 3)
    r = run(task)
    assert r.status == "failed" and "claude.exe" in r.message
    assert ex.ran == [] and asked == []  # never even asked to approve an unreachable click


def test_unsupervised_runs_without_asking_but_destructive_still_asks(tmp_path):
    m = ScriptedModel([])
    task, _, ex, asked, _ = make_task(tmp_path, [
        {"action": "click", "element_id": m.id_of(BOLD)},
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "hello"},
        {"action": "click", "element_id": m.id_of(DELETE)},
        {"action": "done"},
    ], approvals=["stop"], supervised=False)
    r = run(task)
    assert [a[1] for a in asked] == [True]  # only "Delete document" asked
    assert r.status == "stopped" and len(ex.ran) == 2


def test_mid_task_instruction_reaches_the_model(tmp_path):
    seen = []
    m = ScriptedModel([])
    task, model, _, _, _ = make_task(tmp_path, [{"action": "click", "element_id": m.id_of(BOLD)}, {"action": "done"}],
                                     supervised=False, context="you just typed hello")
    orig = model.chat_json

    async def spy(model_name, messages, schema, **kw):
        if "plan" not in schema["properties"]:
            seen.append(messages[-1]["content"])
        if len(seen) == 1:
            task.add_instruction("also make it bold")
        return await orig(model_name, messages, schema, **kw)

    model.chat_json = spy
    assert run(task).status == "done"
    assert "you just typed hello" in seen[0]  # follow-up context from the previous task
    assert "also make it bold" in seen[1]  # the instruction added mid-task


def test_refuses_to_type_the_same_text_twice_and_sees_the_document(tmp_path):
    """The real-world failure: 'Hello world / This is a new message.' typed three times over."""
    seen = []
    m = ScriptedModel([])
    text = "Hello world\nThis is a new message."
    task, model, ex, _, _ = make_task(tmp_path, [
        {"action": "type_text", "element_id": m.id_of(BODY), "text": text},
        {"action": "type_text", "element_id": m.id_of(BODY), "text": text},  # a confused retry
        {"action": "done"},
    ], supervised=False)
    orig = model.chat_json

    async def spy(model_name, messages, schema, **kw):
        if "plan" not in schema["properties"]:
            seen.append(messages[-1]["content"])
        return await orig(model_name, messages, schema, **kw)

    model.chat_json = spy
    r = run(task)
    assert r.status == "done"
    assert FakeUIA.doc == text  # typed exactly once
    assert "(empty)" in seen[0]  # the model saw the document was empty...
    assert "This is a new message." in seen[1] and "verified" in seen[1]  # ...then saw its text land
    assert r.message.startswith("Done")  # trying to type it again just means it's finished


def test_step_budget(tmp_path):
    m = ScriptedModel([])
    alternate = [{"action": "click", "element_id": m.id_of(BOLD)}, {"action": "scroll", "amount": -1}]
    task, _, _, _, _ = make_task(tmp_path, alternate * 50,
                                 approvals=["approve_all"])
    assert run(task).status == "limit"


def test_a_repeated_step_is_skipped_not_run_twice(tmp_path):
    """The Google Docs loop ('click the document' over and over): repeats are ignored and the task carries on."""
    m = ScriptedModel([])
    task, _, ex, _, _ = make_task(tmp_path, [{"action": "click", "element_id": m.id_of(BODY)}] * 5
                                  + [{"action": "done"}], supervised=False)
    r = run(task)
    assert r.status == "done"
    assert len(ex.ran) == 1  # clicked once; the four repeats were skipped
    assert sum("SKIPPED" in s for s in r.steps) == 4


def test_types_the_prepared_text_not_its_own_version(tmp_path):
    """Real bug: the plan said 'Type: Hi there! I'm [Your Name]...' and the step model typed that placeholder."""
    m = ScriptedModel([])
    intro = "I'm The Third Coming, a small playful stick figure who lives on Andrew's desktop."
    task, _, ex, _, _ = make_task(tmp_path, [
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "Hi there! I'm [Your Name], a friendly helper."},
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "{{TEXT}}"},
        {"action": "done"},
    ], payload=intro, supervised=False)
    r = run(task)
    assert r.status == "done"
    assert FakeUIA.doc == intro
    assert any("isn't the prepared text" in s for s in r.steps)


def test_prepared_text_is_never_typed_twice_in_one_step(tmp_path):
    """Real bug: text "{{TEXT}}\n\n{{TEXT}}" typed the whole introduction twice."""
    m = ScriptedModel([])
    intro = "I'm The Third Coming, a small playful stick figure who lives on Andrew's desktop."
    task, _, _, _, _ = make_task(tmp_path, [
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "{{TEXT}}\n\n{{TEXT}}"},
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "{{TEXT}}"},
        {"action": "done"},
    ], payload=intro, supervised=False)
    r = run(task)
    assert r.status == "done" and FakeUIA.doc == intro
    assert any("only ONCE" in s for s in r.steps)


def test_same_target_rules():
    from stickfigure.actions.task import same_target
    from stickfigure.perception.uia import UIElement

    btn = UIElement("Share", "Button", Rect(100, 100, 200, 140))
    assert same_target(UIElement("Share", "Text", Rect(120, 110, 180, 130)), btn)  # its label inside it
    assert same_target(UIElement("share", "Button", Rect(90, 95, 210, 145)), btn)  # same name, bigger box
    assert not same_target(UIElement("Ad banner", "Image", Rect(0, 0, 800, 600)), btn)  # an overlay on top
    assert not same_target(UIElement("Comment", "Button", Rect(210, 100, 300, 140)), btn)  # the neighbor


def test_near_duplicate_text_is_not_typed_again():
    from stickfigure.actions.task import nearly_same_text

    a = "Hey Manna, just checking in! I've been exploring the computer, building block staircases, and ri"
    assert nearly_same_text(a, a[:-1])  # 97 vs 96 chars: the real-world double paste
    assert nearly_same_text(a, a + " ")
    assert not nearly_same_text(a, "Totally different message about octopuses and their colors")


def test_document_writing_goes_at_the_end_on_its_own_line(tmp_path):
    """Real bug: a click to 'place the caret' landed mid-document and the reply was typed into someone's text."""
    m = ScriptedModel([])
    FakeUIA.doc = "Hi from Manna"
    task, _, ex, _, _ = make_task(tmp_path, [
        {"action": "click", "element_id": m.id_of(BODY)},
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "Hello back!"},
        {"action": "done"},
    ], supervised=False)
    r = run(task)
    assert r.status == "done"
    typed = [s for batch in ex.ran for s in batch]
    kinds = [type(s).__name__ for s in typed]
    i = kinds.index("Type")
    assert type(typed[i - 1]).__name__ == "Keys" and typed[i - 1].combo == "ctrl+end"  # jump to the end first
    assert typed[i].text == "\nHello back!"  # on its own line
    assert "Click" not in kinds[kinds.index("Keys"):]  # no click right before typing


def test_a_named_place_is_respected(tmp_path):
    m = ScriptedModel([])
    task, _, ex, _, _ = make_task(tmp_path, [
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "Title"},
        {"action": "done"},
    ], supervised=False)
    task.goal = "type Title at the top of the document"
    run(task)
    assert not any(type(s).__name__ == "Keys" for batch in ex.ran for s in batch)


def test_task_ends_as_soon_as_the_prepared_text_is_in(tmp_path):
    """Real bug: after pasting, it just kept going (more steps, repeats...)."""
    m = ScriptedModel([])
    msg = "yo Manna, what are you working on rn?"
    task, _, ex, _, _ = make_task(tmp_path, [
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "{{TEXT}}"},
        {"action": "click", "element_id": m.id_of(BOLD)},  # would have kept going
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "{{TEXT}}"},
    ], payload=msg, supervised=False)
    r = run(task)
    assert r.status == "done" and len(ex.ran) == 1


def test_trying_to_type_the_same_thing_again_means_done(tmp_path):
    m = ScriptedModel([])
    task, _, ex, _, _ = make_task(tmp_path, [
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "hey whats up"},
        {"action": "type_text", "element_id": m.id_of(BODY), "text": "hey whats up!"},
        {"action": "click", "element_id": m.id_of(BOLD)},
    ], supervised=False)
    r = run(task)
    assert r.status == "done" and len(ex.ran) == 1
