"""Phase 5 exit checks with REAL input, confined to a sandbox window this script launches.

    .venv\\Scripts\\python tools\\live_action_check.py

Moves the mouse and types in the sandbox for ~1 minute; press Esc to stop it early.
Nothing outside the sandbox window can receive input: the executor's window lock refuses it.
The "never touches terminals" guarantees are covered by the unit tests (tests/test_task.py), not here.
"""

import asyncio
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

from stickfigure.actions import input_driver as drv
from stickfigure.actions.executor import Click, Executor, Move, Type
from stickfigure.actions.task import ActionTask
from stickfigure.agent.ollama import Ollama
from stickfigure.config import CONFIG
from stickfigure.perception.perception import Perception, Target
from stickfigure.safety.audit import AuditLog
from stickfigure.safety.override import InputWatch
from stickfigure.safety.policy import DENY, Proposed, WindowInfo, decide
from stickfigure.win import win32

u32 = ctypes.WinDLL("user32")
k32 = ctypes.WinDLL("kernel32")
u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u32.FindWindowW.restype = wintypes.HWND
u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
PARAGRAPH = ("Stick figures are underrated. With just a circle and five lines, an animator can show joy, "
             "panic, and everything in between - which is exactly why this little one lives on your desktop.")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def bring_forward(hwnd: int) -> bool:
    fg = u32.GetForegroundWindow()
    fg_thread = u32.GetWindowThreadProcessId(fg, None)
    me = k32.GetCurrentThreadId()
    u32.AttachThreadInput(me, fg_thread, True)
    u32.SetForegroundWindow(hwnd)
    u32.BringWindowToTop(hwnd)
    u32.AttachThreadInput(me, fg_thread, False)
    time.sleep(0.3)
    return int(u32.GetForegroundWindow() or 0) == hwnd


def inject_untagged(*inputs) -> None:
    """Input WITHOUT our tag: the override hook must treat it exactly like the user's hand."""
    arr = (drv.INPUT * len(inputs))(*inputs)
    for i in arr:
        if i.type == drv.INPUT_KEYBOARD:
            i.u.ki.dwExtraInfo = 0
        else:
            i.u.mi.dwExtraInfo = 0
    drv.user32.SendInput(len(inputs), arr, ctypes.sizeof(drv.INPUT))


def read_state(path: Path) -> dict:
    for _ in range(10):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            time.sleep(0.05)
    return {}


def slow_cfg():
    return type("C", (), {"typing_wpm": 80, "paste_threshold": 10_000, "max_clicks_per_sec": 3.0})()


def latency_test(name: str, hwnd: int, editor_center, trigger) -> None:
    ex = Executor(cfg=slow_cfg())
    stamps = {}

    def user(kind):
        stamps.setdefault("user", time.perf_counter())
        ex.cancel(f"user {kind}")

    def kill():
        stamps.setdefault("kill", time.perf_counter())
        ex.cancel("kill switch")

    watch = InputWatch(user if "override" in name else (lambda k: None), kill)
    watch.arm()
    fut = ex.submit([Move(*editor_center), Click(), Type("x" * 200)], hwnd)
    time.sleep(1.2)
    t_inject = time.perf_counter()
    trigger()
    r = fut.result(10)
    watch.disarm()
    drv.release_all()
    halt_ms = (r.halted_at - t_inject) * 1000  # last event we sent, relative to the trigger
    ok = r.status == "cancelled" and halt_ms < 50
    check(name, ok, f"status={r.status}, last input {halt_ms:.1f} ms after trigger, detail={r.detail!r}")
    ex.close()


async def full_task(goal, payload, target, perception, ollama, audit, approve_all=True):
    ex = Executor()
    asked = []

    async def approve(step, decision):
        asked.append(step.describe())
        return "approve"  # simulate a user who clicks "Do it" on EVERY step (worst case)

    watch = InputWatch(lambda k: task.stop("user input"), lambda: task.stop("kill"))
    task = ActionTask(goal, payload, target, perception=perception, ollama=ollama, executor=ex, watch=watch,
                      audit=audit, approve=approve)
    t0 = time.perf_counter()
    r = await task.run()
    ex.close()
    return r, asked, time.perf_counter() - t0


def main() -> None:
    state_path = Path(tempfile.mkdtemp()) / "sandbox.json"
    proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("sandbox_target.py")), str(state_path)],
                            creationflags=0x08000000)  # CREATE_NO_WINDOW
    try:
        hwnd = 0
        for _ in range(50):
            hwnd = int(u32.FindWindowW(None, "StickFigure Sandbox") or 0)
            if hwnd:
                break
            time.sleep(0.1)
        assert hwnd, "sandbox window didn't appear"
        time.sleep(0.5)
        print(f"sandbox hwnd={hwnd} pid={win32.window_pid(hwnd)} foreground={bring_forward(hwnd)}")
        perception = Perception(Ollama(CONFIG.ollama_url))
        rect = win32.frame_bounds(hwnd)
        target = Target(hwnd, "StickFigure Sandbox", win32.process_name(hwnd), rect)
        uia = perception.uia.elements(hwnd, rect).result()
        editor = next(e for e in uia if e.name == "Test document" and e.role in ("Edit", "Document"))
        password = next(e for e in uia if e.name == "Password")
        clip_before = drv.get_clipboard_text()
        clip_text_only = drv.clipboard_is_text_only()

        print("\n1. Real typing and pasting")
        ex = Executor()
        r = ex.run([Move(*editor.center), Click(), Type("Hello from Stick! ")], hwnd)
        time.sleep(0.3)
        check("types short text", r.ok and read_state(state_path)["text"] == "Hello from Stick! ", f"{r.status}")
        r = ex.run([Type(PARAGRAPH)], hwnd)
        time.sleep(0.4)
        text = read_state(state_path)["text"]
        check("long text lands verbatim", r.ok and text.endswith(PARAGRAPH),
              "(pasted)" if clip_text_only else "(typed: clipboard had non-text data)")
        check("user's clipboard restored", drv.get_clipboard_text() == clip_before)

        print("\n2. Password field detection (real UIA)")
        ex.run([Move(*password.center), Click()], hwnd)
        time.sleep(0.3)
        focused = perception.uia.focused().result()
        win = WindowInfo(hwnd, target.app, win32.class_name(hwnd), "StickFigure Sandbox")
        d = decide(Proposed("type_text", text="hunter2hunter2"), win, focused, task_approved=True)
        check("password field seen as IsPassword and typing denied", bool(focused and focused.is_password) and d.verdict == DENY,
              f"focused={focused.describe() if focused else None}")
        ex.close()

        print("\n3. Stop latency (target: < 50 ms)")
        bring_forward(hwnd)
        latency_test("user override: Esc", hwnd, editor.center,
                     lambda: inject_untagged(drv._key(drv.VK["esc"]), drv._key(drv.VK["esc"], up=True)))
        bring_forward(hwnd)

        def kill_combo():
            inject_untagged(drv._key(drv.VK["ctrl"]), drv._key(drv.VK["alt"]), drv._key(0x13))
            time.sleep(0.02)
            inject_untagged(drv._key(0x13, up=True), drv._key(drv.VK["alt"], up=True), drv._key(drv.VK["ctrl"], up=True))

        latency_test("kill switch: Ctrl+Alt+Pause", hwnd, editor.center, kill_combo)

        print("\n4. Full supervised task with the real model (approvals auto-granted)")
        bring_forward(hwnd)
        drv.set_clipboard_text(clip_before) if clip_text_only else None
        before = read_state(state_path)["text"]
        audit = AuditLog(state_path.with_name("audit.jsonl"))
        r, asked, secs = asyncio.run(full_task("type the user's paragraph into the test document", PARAGRAPH, target,
                                               perception, perception.ollama, audit))
        after = read_state(state_path)["text"]
        check("task completes and the paragraph is typed exactly once", r.status == "done" and after.count(PARAGRAPH) == before.count(PARAGRAPH) + 1,
              f"status={r.status} steps={r.steps} asked={len(asked)} in {secs:.1f}s")
        perception.close()
    finally:
        proc.terminate()
        drv.release_all()
    print("\n" + ("ALL PASSED" if all(ok for _, ok, _ in results) else "SOME CHECKS FAILED"))


if __name__ == "__main__":
    main()
