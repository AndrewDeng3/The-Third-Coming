"""Read-only screen perception, cheapest tier first: UIA -> OCR -> LLM re-rank.

Only runs when the user asks (never continuously), only on one target window, and
everything stays on this machine (local models).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from stickfigure.agent.ollama import Ollama, OllamaError
from stickfigure.config import CONFIG, Config
from stickfigure.perception import locate as L
from stickfigure.perception.ocr import OcrReader
from stickfigure.perception.uia import INTERACTIVE, UIAReader, UIElement
from stickfigure.win import win32
from stickfigure.win.tracker import TrackedWindow
from stickfigure.world.geometry import Rect

log = logging.getLogger(__name__)

SPARSE_UIA = 15  # fewer named interactive elements than this -> the app needs OCR too


@dataclass(frozen=True)
class Target:
    hwnd: int
    title: str
    app: str  # process name, e.g. "chrome.exe"
    rect: Rect

    def label(self) -> str:
        return f"'{self.title[:60]}' ({self.app})"


@dataclass
class Found:
    element: UIElement | None
    confidence: float
    method: str  # "uia" | "ocr" | "llm" | "none"
    target: Target
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.element is not None


def choose_target(windows: list[TrackedWindow], last_foreground: int | None, hint: str = "") -> Target | None:
    """The window the user means: one matching `hint` (app/title words), else the last app they used."""
    def to_target(w: TrackedWindow) -> Target:
        return Target(w.hwnd, w.title, win32.process_name(w.hwnd), w.rect)

    hint = hint.lower().strip()
    if hint:
        words = [w for w in hint.replace(".exe", "").split() if len(w) > 2]
        for w in windows:
            hay = f"{w.title} {win32.process_name(w.hwnd)}".lower()
            if words and all(word in hay for word in words):
                return to_target(w)
        aliases = {"explorer": "explorer.exe", "file explorer": "explorer.exe", "vs code": "code.exe",
                   "vscode": "code.exe", "visual studio code": "code.exe", "chrome": "chrome.exe", "edge": "msedge.exe"}
        exe = aliases.get(hint)
        for w in windows:
            if exe and win32.process_name(w.hwnd).lower() == exe:
                return to_target(w)
    for w in windows:
        if w.hwnd == last_foreground:
            return to_target(w)
    return to_target(windows[0]) if windows else None


UIA_TIMEOUT = 10.0  # seconds; a busy app (huge web page) must not freeze a task


class Perception:
    def __init__(self, ollama: Ollama, cfg: Config = CONFIG):
        self.ollama = ollama
        self.cfg = cfg
        self.uia = UIAReader()
        self.ocr = OcrReader()

    async def elements(self, target: Target, force_ocr: bool = False) -> tuple[list[UIElement], list[UIElement], dict]:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        try:
            uia = await asyncio.wait_for(asyncio.wrap_future(self.uia.elements(target.hwnd, target.rect)), UIA_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("UIA read of %s took over %.0fs; using OCR only", target.label(), UIA_TIMEOUT)
            uia = []
        except Exception as e:  # window closed, access denied (elevated app), ...
            log.warning("UIA read failed for %s: %s", target.label(), e)
            uia = []
        timings["uia_ms"] = (time.perf_counter() - t0) * 1000
        ocr: list[UIElement] = []
        named = sum(1 for e in uia if e.name and e.role in INTERACTIVE)
        if force_ocr or named < SPARSE_UIA:
            t1 = time.perf_counter()
            try:
                ocr = await asyncio.wait_for(asyncio.wrap_future(self.ocr.read(target.rect)), UIA_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning("OCR of %s timed out", target.label())
            timings["ocr_ms"] = (time.perf_counter() - t1) * 1000
        return uia, ocr, timings

    async def locate(self, query: str, target: Target, use_llm: bool = True) -> Found:
        """Take a fresh look at `target` and find the element `query` describes."""
        t0 = time.perf_counter()
        uia, ocr, timings = await self.elements(target)

        async def more_ocr() -> list[UIElement]:
            t1 = time.perf_counter()
            lines = await asyncio.wrap_future(self.ocr.read(target.rect))
            timings["ocr_ms"] = (time.perf_counter() - t1) * 1000
            return lines

        return await self.locate_in(query, target, uia, ocr, more_ocr if not ocr else None, use_llm, timings, t0)

    async def locate_in(
        self,
        query: str,
        target: Target,
        uia: list[UIElement],
        ocr: list[UIElement],
        more_ocr=None,
        use_llm: bool = True,
        timings: dict | None = None,
        t0: float | None = None,
    ) -> Found:
        """Find `query` within an already-captured element snapshot (tiers 1-3)."""
        timings = timings if timings is not None else {}
        t0 = t0 if t0 is not None else time.perf_counter()
        cands = L.rank(L.merge_sources(uia, ocr), query)
        if L.confident(cands):
            return self._found(cands[0], "uia" if cands[0].el.source == "uia" else "ocr", target, timings, t0)

        if more_ocr is not None:  # UIA was rich but didn't have it: the answer may be text only OCR can see
            ocr = await more_ocr()
            cands = L.rank(L.merge_sources(uia, ocr), query)
            if L.confident(cands):
                return self._found(cands[0], "uia" if cands[0].el.source == "uia" else "ocr", target, timings, t0)

        if use_llm and cands:
            r = target.rect
            short = L.shortlist(cands, (r.left, r.top, r.right, r.bottom))
            t2 = time.perf_counter()
            try:
                pick = await self.ollama.chat_json(
                    self.cfg.extract_model, L.pick_messages(query, short, (r.left, r.top, r.right, r.bottom), target.label()),
                    L.PICK_SCHEMA,
                )
                timings["llm_ms"] = (time.perf_counter() - t2) * 1000
                i, conf = int(pick.get("index", -1)), float(pick.get("confidence", 0))
                if 0 <= i < len(short) and conf >= 0.35:
                    return self._found(L.Candidate(short[i].el, conf), "llm", target, timings, t0)
            except (OllamaError, ValueError, TypeError) as e:
                log.warning("LLM element pick failed: %s", e)
        timings["total_ms"] = (time.perf_counter() - t0) * 1000
        return Found(None, cands[0].score if cands else 0.0, "none", target, timings)

    def _found(self, cand: L.Candidate, method: str, target: Target, timings: dict, t0: float) -> Found:
        timings["total_ms"] = (time.perf_counter() - t0) * 1000
        return Found(cand.el, cand.score, method, target, timings)

    async def read_document(self, target: Target) -> str:
        """The text of the window's main document/editor, top to bottom: from UIA if the app exposes it,
        otherwise by OCR of the text area (Google Docs draws its text as pixels)."""
        uia, _, _ = await self.elements(target)
        bodies = [e for e in uia if e.role in ("Document", "Edit") and not e.is_password]
        body = max(bodies, key=lambda e: e.rect.width * e.rect.height, default=None)
        r = target.rect
        if body is not None and body.rect.width * body.rect.height >= 0.15 * r.width * r.height:
            region = Rect(max(body.rect.left, r.left), max(body.rect.top, r.top),
                          min(body.rect.right, r.right), min(body.rect.bottom, r.bottom))
        else:  # no obvious text area: read the window below its toolbars
            region = Rect(r.left, r.top + r.height * 0.18, r.right, r.bottom)
        if region.width < 20 or region.height < 20:
            return ""
        try:
            lines = await asyncio.wait_for(asyncio.wrap_future(self.ocr.read(region)), UIA_TIMEOUT)
        except asyncio.TimeoutError:
            return ""
        lines.sort(key=lambda e: (round(e.rect.top / 12), e.rect.left))
        return "\n".join(e.name for e in lines)

    async def describe(self, target: Target) -> str:
        """A compact text description of the window for the chat model."""
        uia, ocr, _ = await self.elements(target, force_ocr=True)
        parts = [f"Window: {target.label()}"]
        focused = next((e for e in uia if e.focused and e.name), None)
        if focused:
            parts.append(f"Keyboard focus: {focused.describe()}")
        by_role: dict[str, list[str]] = {}
        for e in uia:
            if e.name and e.role in ("TabItem", "Button", "MenuItem", "Hyperlink", "ListItem", "TreeItem"):
                names = by_role.setdefault(e.role, [])
                if e.name not in names and len(names) < 12:
                    names.append(e.name[:50])
        for role, names in by_role.items():
            parts.append(f"{role}s: " + "; ".join(names))
        if ocr:
            ocr_sorted = sorted(ocr, key=lambda e: (round(e.rect.top / 20), e.rect.left))
            text = " | ".join(e.name for e in ocr_sorted)
            parts.append("Visible text (OCR, top to bottom): " + text[:1800])
        return "\n".join(parts)

    def close(self) -> None:
        self.uia.close()
        self.ocr.close()
