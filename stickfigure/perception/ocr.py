"""Screen capture (mss) + Windows' built-in OCR engine (Windows.Media.Ocr), on a worker thread.

Our own overlay windows are excluded from capture (WDA_EXCLUDEFROMCAPTURE), so the figure,
speech bubble and highlights never show up in what we read.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from dataclasses import dataclass

import mss

from stickfigure.perception.uia import UIElement
from stickfigure.world.geometry import Rect


@dataclass(frozen=True)
class Capture:
    rect: Rect  # screen rect captured (physical px)
    width: int
    height: int
    bgra: bytes


def capture(rect: Rect) -> Capture:
    with mss.mss() as sct:
        mon = {"left": int(rect.left), "top": int(rect.top), "width": int(rect.width), "height": int(rect.height)}
        shot = sct.grab(mon)
        return Capture(rect, shot.width, shot.height, bytes(shot.bgra))


async def _recognize(cap: Capture) -> list[UIElement]:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return []
    writer = DataWriter()
    writer.write_bytes(cap.bgra)
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, cap.width, cap.height)
    result = await engine.recognize_async(bitmap)
    ox, oy = cap.rect.left, cap.rect.top
    lines: list[UIElement] = []
    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        l = min(w.bounding_rect.x for w in words)
        t = min(w.bounding_rect.y for w in words)
        r = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
        b = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
        lines.append(UIElement(name=line.text.strip(), role="Text", rect=Rect(ox + l, oy + t, ox + r, oy + b), source="ocr"))
    return lines


class OcrReader:
    def __init__(self) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")
        self.last_ms = 0.0

    def read(self, rect: Rect) -> concurrent.futures.Future:
        """Future -> list[UIElement] (role "Text", source "ocr"), one per text line."""
        return self._pool.submit(self._read, rect)

    def _read(self, rect: Rect) -> list[UIElement]:
        t0 = time.perf_counter()
        cap = capture(rect)
        # WinRT async ops need an event loop; this worker thread gets its own.
        lines = asyncio.run(_recognize(cap))
        self.last_ms = (time.perf_counter() - t0) * 1000
        return lines

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
