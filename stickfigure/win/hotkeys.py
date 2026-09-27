"""Global hotkeys (RegisterHotKey) delivered through Qt's native event filter."""

from __future__ import annotations

import logging
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

from stickfigure.win import win32

log = logging.getLogger(__name__)

MOD_SHIFT = 0x0004
VK_SPACE = 0x20


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, base_id: int = 0x6000):
        super().__init__()
        self._next = base_id
        self._handlers: dict[int, Callable[[], None]] = {}

    def add(self, mods: int, vk: int, handler: Callable[[], None], name: str) -> bool:
        hid = self._next
        self._next += 1
        ok = bool(win32.user32.RegisterHotKey(None, hid, mods | win32.MOD_NOREPEAT, vk))
        if ok:
            self._handlers[hid] = handler
        else:
            log.warning("couldn't register hotkey %s (in use by another app?)", name)
        return ok

    def clear(self) -> None:
        for hid in self._handlers:
            win32.user32.UnregisterHotKey(None, hid)
        self._handlers.clear()

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == win32.WM_HOTKEY and msg.wParam in self._handlers:
                self._handlers[msg.wParam]()
                return True, 0
        return False, 0
