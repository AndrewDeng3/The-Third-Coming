"""Windows UI Automation reader: every visible element of a window (name, role, box) in one call.

Uses a UIA CacheRequest + FindAllBuildCache so the whole subtree comes back in a single
cross-process round trip instead of thousands of per-property COM calls. All COM work
happens on one dedicated MTA thread.

Read-only by design: element snapshots contain names/roles/boxes only, never field *values*.
The one exception is focused_text(), used only inside an action task so the model can see the
text it's editing; it never reads password fields.
"""

from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass

from stickfigure.world.geometry import Rect

CONTROL_TYPES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit",
    50005: "Hyperlink", 50006: "Image", 50007: "ListItem", 50008: "List", 50009: "Menu",
    50010: "MenuBar", 50011: "MenuItem", 50012: "ProgressBar", 50013: "RadioButton",
    50014: "ScrollBar", 50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip", 50023: "Tree",
    50024: "TreeItem", 50025: "Custom", 50026: "Group", 50027: "Thumb", 50028: "DataGrid",
    50029: "DataItem", 50030: "Document", 50031: "SplitButton", 50032: "Window", 50033: "Pane",
    50034: "Header", 50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
    50039: "SemanticZoom", 50040: "AppBar",
}
INTERACTIVE = {
    "Button", "SplitButton", "CheckBox", "RadioButton", "ComboBox", "Edit", "Hyperlink",
    "ListItem", "MenuItem", "TabItem", "TreeItem", "Slider", "DataItem", "HeaderItem",
}
# Containers that rarely help locate anything and bloat the list.
SKIP_TYPES = {"Pane", "Group", "Custom", "Separator", "ScrollBar", "Thumb", "Window", "TitleBar", "ToolTip"}

UIA_BoundingRectangle = 30001
UIA_ControlType = 30003
UIA_Name = 30005
UIA_HasKeyboardFocus = 30008
UIA_IsEnabled = 30010
UIA_AutomationId = 30011
UIA_IsOffscreen = 30022
UIA_IsPassword = 30019
TreeScope_Descendants = 4


@dataclass(frozen=True)
class UIElement:
    name: str
    role: str
    rect: Rect
    enabled: bool = True
    focused: bool = False
    is_password: bool = False
    automation_id: str = ""
    source: str = "uia"  # "uia" | "ocr"

    @property
    def center(self) -> tuple[float, float]:
        return ((self.rect.left + self.rect.right) / 2, (self.rect.top + self.rect.bottom) / 2)

    def describe(self) -> str:
        label = self.name or "(unnamed)"
        return f"{self.role} '{label}'"


class UIAReader:
    def __init__(self, max_elements: int = 3000):
        self.max_elements = max_elements
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="uia",
                                                           initializer=self._init_thread)
        self._uia = None
        self.last_ms = 0.0

    def _init_thread(self) -> None:
        import comtypes
        import comtypes.client as cc

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # importing comtypes on this thread already initialized COM (STA) - fine for queries
        cc.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U

        self._U = U
        self._uia = cc.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        cache = self._uia.CreateCacheRequest()
        for pid in (UIA_Name, UIA_ControlType, UIA_BoundingRectangle, UIA_IsEnabled, UIA_HasKeyboardFocus,
                    UIA_IsPassword, UIA_AutomationId):
            cache.AddProperty(pid)
        self._cache = cache
        self._cond = self._uia.CreateAndCondition(
            self._uia.ControlViewCondition,
            self._uia.CreatePropertyCondition(UIA_IsOffscreen, False),
        )

    def elements(self, hwnd: int, clip: Rect | None = None) -> concurrent.futures.Future:
        """Future -> list[UIElement] for the window's visible subtree."""
        return self._pool.submit(self._elements, hwnd, clip)

    def _elements(self, hwnd: int, clip: Rect | None) -> list[UIElement]:
        t0 = time.perf_counter()
        root = self._uia.ElementFromHandle(hwnd)
        found = root.FindAllBuildCache(TreeScope_Descendants, self._cond, self._cache)
        out: list[UIElement] = []
        n = min(found.Length, self.max_elements)
        for i in range(n):
            el = found.GetElement(i)
            try:
                role = CONTROL_TYPES.get(el.CachedControlType, "Unknown")
                if role in SKIP_TYPES:
                    continue
                r = el.CachedBoundingRectangle
                rect = Rect(r.left, r.top, r.right, r.bottom)
                if rect.width < 3 or rect.height < 3:
                    continue
                if clip is not None and (rect.right <= clip.left or rect.left >= clip.right
                                         or rect.bottom <= clip.top or rect.top >= clip.bottom):
                    continue
                name = (el.CachedName or "").strip()
                if not name and role not in ("Edit", "Document", "ComboBox"):
                    continue
                out.append(UIElement(
                    name=name[:200],
                    role=role,
                    rect=rect,
                    enabled=bool(el.CachedIsEnabled),
                    focused=bool(el.CachedHasKeyboardFocus),
                    is_password=bool(el.CachedIsPassword),
                    automation_id=(el.CachedAutomationId or "")[:80],
                ))
            except Exception:  # elements can vanish mid-read; skip them
                continue
        self.last_ms = (time.perf_counter() - t0) * 1000
        return out

    def focused(self) -> concurrent.futures.Future:
        """Future -> UIElement | None: the element with keyboard focus (system-wide)."""
        return self._pool.submit(self._wrap_single, lambda: self._uia.GetFocusedElementBuildCache(self._cache))

    def at_point(self, x: float, y: float) -> concurrent.futures.Future:
        """Future -> UIElement | None: the element under a screen point (what a click there would hit)."""
        def get():
            pt = self._U.tagPOINT(int(x), int(y))
            return self._uia.ElementFromPointBuildCache(pt, self._cache)
        return self._pool.submit(self._wrap_single, get)

    def focused_text(self, max_chars: int = 4000) -> concurrent.futures.Future:
        """Future -> str | None: the text content of the focused editable element.

        Used only during action tasks so the model can see what it's editing (and not type things
        twice). Never reads password fields. Returns None if the app doesn't expose the text.
        """
        return self._pool.submit(self._focused_text, max_chars)

    def _focused_text(self, max_chars: int) -> str | None:
        U = self._U
        try:
            el = self._uia.GetFocusedElement()
            if el.CurrentIsPassword:
                return None
            try:
                tp = el.GetCurrentPattern(10014).QueryInterface(U.IUIAutomationTextPattern)  # TextPattern
                text = tp.DocumentRange.GetText(max_chars)
                if text is not None:
                    return text
            except Exception:
                pass
            try:
                vp = el.GetCurrentPattern(10002).QueryInterface(U.IUIAutomationValuePattern)  # ValuePattern
                return (vp.CurrentValue or "")[:max_chars]
            except Exception:
                return None
        except Exception:
            return None

    def _wrap_single(self, get) -> UIElement | None:
        try:
            el = get()
            r = el.CachedBoundingRectangle
            return UIElement(
                name=(el.CachedName or "").strip()[:200],
                role=CONTROL_TYPES.get(el.CachedControlType, "Unknown"),
                rect=Rect(r.left, r.top, r.right, r.bottom),
                enabled=bool(el.CachedIsEnabled),
                focused=bool(el.CachedHasKeyboardFocus),
                is_password=bool(el.CachedIsPassword),
                automation_id=(el.CachedAutomationId or "")[:80],
            )
        except Exception:
            return None

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
