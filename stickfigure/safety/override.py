"""User-override and kill-switch detection via low-level input hooks.

While actions execute, the user takes control back with a *deliberate* signal:
  - Esc (physical)                  -> stop the task         (swallowed, so the app doesn't also get it)
  - Ctrl+Alt+Pause                  -> emergency stop        (swallowed)
Just moving the mouse or typing doesn't stop anything by default; extra triggers ("click",
"any_key", "mouse_move") can be enabled via Config.override_triggers.

Input carrying our INJECT_TAG is ours and never counts. The kill combo is detected here, on the
hook thread, so an emergency stop never waits on the UI thread.

Hooks run on their own thread with their own message loop and are only installed while armed,
so the rest of the time StickFigure adds nothing to the system's input path.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable, Iterable

from stickfigure.actions.input_driver import INJECT_TAG

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP = 0x0100, 0x0104, 0x0101, 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN = 0x0201, 0x0204, 0x0207
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
VK_ESCAPE = 0x1B
VK_CONTROL_KEYS = {0x11, 0xA2, 0xA3}
VK_ALT_KEYS = {0x12, 0xA4, 0xA5}
VK_KILL = {0x13, 0x03}  # Pause, and Ctrl+Pause's VK_CANCEL
MOVE_THRESHOLD = 40  # px of the user's own mouse travel (only if "mouse_move" is enabled)
TRIGGERS = {"esc", "click", "any_key", "mouse_move"}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = wintypes.LPARAM
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class InputWatch:
    def __init__(
        self,
        on_user_input: Callable[[str], None],
        on_kill: Callable[[], None],
        triggers: Iterable[str] = ("esc",),
    ):
        self.on_user_input = on_user_input
        self.on_kill = on_kill
        self.triggers = set(triggers) & TRIGGERS
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._ctrl = self._alt = False
        self._last_pt: tuple[int, int] | None = None
        self._user_move = 0
        self._user_move_t = 0
        # Keep the ctypes callbacks alive for the life of the hooks.
        self._kb_proc = HOOKPROC(self._keyboard)
        self._ms_proc = HOOKPROC(self._mouse)
        self._hooks: list[int] = []

    @property
    def armed(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def arm(self) -> None:
        if self.armed:
            return
        self._ready.clear()
        self._last_pt = None
        self._user_move = 0
        self._thread = threading.Thread(target=self._run, name="input-watch", daemon=True)
        self._thread.start()
        self._ready.wait(1.0)

    def disarm(self) -> None:
        if self.armed:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(1.0)
        self._thread = None

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        mod = kernel32.GetModuleHandleW(None)
        self._hooks = [user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, mod, 0)]
        if self.triggers & {"click", "mouse_move"}:
            self._hooks.append(user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, mod, 0))
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        for h in self._hooks:
            if h:
                user32.UnhookWindowsHookEx(h)
        self._hooks = []

    # -- hook procedures (keep these fast: Windows drops slow LL hooks) ----------------------

    def _keyboard(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0 and self._on_key(ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents,
                                        w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)):
            return 1  # swallowed
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_key(self, k: KBDLLHOOKSTRUCT, down: bool) -> bool:
        """Returns True if the key should be swallowed."""
        if k.vkCode in VK_CONTROL_KEYS:
            self._ctrl = down
        elif k.vkCode in VK_ALT_KEYS:
            self._alt = down
        ours = bool(k.flags & LLKHF_INJECTED) and k.dwExtraInfo == INJECT_TAG
        if ours:
            return False
        if k.vkCode in VK_KILL and self._ctrl and self._alt:
            if down:
                self._safe(self.on_kill)
            return True
        if k.vkCode == VK_ESCAPE and "esc" in self.triggers:
            if down:
                self._safe(self.on_user_input, "Esc key")
            return True
        if down and "any_key" in self.triggers:
            self._safe(self.on_user_input, "keyboard")
        return False

    def _mouse(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            self._on_mouse(ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents, w_param)
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_mouse(self, m: MSLLHOOKSTRUCT, w_param: int) -> None:
        ours = bool(m.flags & LLMHF_INJECTED) and m.dwExtraInfo == INJECT_TAG
        pt = (m.pt.x, m.pt.y)
        if w_param == WM_MOUSEMOVE:
            # Only the travel the user adds on top of wherever our moves left the cursor.
            if "mouse_move" in self.triggers and not ours and self._last_pt is not None:
                if m.time - self._user_move_t > 500:
                    self._user_move = 0
                self._user_move += abs(pt[0] - self._last_pt[0]) + abs(pt[1] - self._last_pt[1])
                self._user_move_t = m.time
                if self._user_move > MOVE_THRESHOLD:
                    self._safe(self.on_user_input, "mouse")
            self._last_pt = pt
        elif not ours and "click" in self.triggers and w_param in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
            self._safe(self.on_user_input, "mouse click")

    @staticmethod
    def _safe(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:
            pass  # never let an exception escape into the hook chain
