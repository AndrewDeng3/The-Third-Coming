"""Global kill switch: Ctrl+Alt+Pause, registered with the OS so it works regardless of focus.

Phase 1 just quits. Later phases hook this to cancel the agent and flush input first.
"""

from __future__ import annotations

import logging
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

from stickfigure.win import win32

log = logging.getLogger(__name__)

_IDS = {0x5F01: win32.VK_PAUSE, 0x5F02: win32.VK_CANCEL}


class KillSwitch(QAbstractNativeEventFilter):
    def __init__(self, on_trigger: Callable[[], None]):
        super().__init__()
        self._on_trigger = on_trigger
        self.registered = False

    def register(self) -> bool:
        mods = win32.MOD_CONTROL | win32.MOD_ALT | win32.MOD_NOREPEAT
        ok = [bool(win32.user32.RegisterHotKey(None, hid, mods, vk)) for hid, vk in _IDS.items()]
        self.registered = any(ok)
        if not self.registered:
            log.error("Could not register kill switch hotkey Ctrl+Alt+Pause (in use by another app?)")
        return self.registered

    def unregister(self) -> None:
        for hid in _IDS:
            win32.user32.UnregisterHotKey(None, hid)

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == win32.WM_HOTKEY and msg.wParam in _IDS:
                log.warning("Kill switch pressed")
                self._on_trigger()
                return True, 0
        return False, 0
