"""Phase 4 exit check: how often does the locator find the element a user asks for?

For every open app window, samples visible named elements (UIA; OCR text lines for apps with
sparse UIA), then queries each one two ways:
  - "exact":   the element's own label
  - "natural": a question an LLM writes for it ("where's the button to go back?")
A hit = the located box contains the target's center (or overlaps it heavily).

    .venv\\Scripts\\python tools\\perception_eval.py [per_window=20]

Read-only: nothing is clicked or typed. Output is aggregate stats + a few truncated failures.
"""

import asyncio
import random
import sys
import time
from collections import Counter

from stickfigure.agent.ollama import Ollama
from stickfigure.config import CONFIG
from stickfigure.perception.locate import _norm
from stickfigure.perception.perception import Perception, SPARSE_UIA, Target
from stickfigure.perception.uia import INTERACTIVE
from stickfigure.win import win32
from stickfigure.win.tracker import WindowTracker

PARAPHRASE_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "q": {"type": "string"}},
                "required": ["n", "q"],
            },
        }
    },
    "required": ["questions"],
}


def hit(found, target) -> bool:
    if found is None:
        return False
    fr, tr = found.rect, target.rect
    cx, cy = target.center
    if fr.left <= cx <= fr.right and fr.top <= cy <= fr.bottom:
        return True
    ix = max(0, min(fr.right, tr.right) - max(fr.left, tr.left))
    iy = max(0, min(fr.bottom, tr.bottom) - max(fr.top, tr.top))
    return ix * iy >= 0.5 * min(fr.width * fr.height, tr.width * tr.height)


async def paraphrase(ollama: Ollama, app: str, items: list) -> list[str]:
    listing = "\n".join(f"{i}. {e.role} labeled \"{e.name[:60]}\"" for i, e in enumerate(items))
    msgs = [
        {"role": "system", "content": (
            "For each numbered UI element, write the short, casual question a user would ask a helper to find it "
            "on screen, e.g. 'where's the back button?' or 'where can I see my downloads?'. Refer to the element by "
            "what it is or does; you may reuse words from its label. Return one {n, q} per element, where n is "
            "the element's number.")},
        {"role": "user", "content": f"App: {app}\n{listing}"},
    ]
    out = await ollama.chat_json(CONFIG.extract_model, msgs, PARAPHRASE_SCHEMA, temperature=0.3)
    by_n = {q.get("n"): q.get("q") for q in out.get("questions", []) if isinstance(q, dict)}
    # Key by number (the model sometimes skips or merges items); fall back to the label itself.
    return [by_n.get(i) or e.name for i, e in enumerate(items)]


def sample_targets(uia, ocr, n: int, rng: random.Random):
    named = [e for e in uia if e.name and e.role in INTERACTIVE and 2 <= len(e.name) <= 40]
    pool, kind = (named, "uia") if len(named) >= SPARSE_UIA else ([e for e in ocr if 3 <= len(e.name) <= 40], "ocr")
    counts = Counter(_norm(e.name) for e in pool)
    unique = [e for e in pool if counts[_norm(e.name)] == 1 and _norm(e.name)]  # ambiguous labels aren't fair tests
    rng.shuffle(unique)
    return unique[:n], kind


async def main(per_window: int) -> None:
    ollama = Ollama(CONFIG.ollama_url)
    perc = Perception(ollama)
    tracker = WindowTracker()
    tracker.update()
    rng = random.Random(7)
    totals = Counter()
    for w in tracker.snapshot.windows:
        target = Target(w.hwnd, w.title, win32.process_name(w.hwnd), w.rect)
        uia, ocr, _ = await perc.elements(target, force_ocr=True)
        items, kind = sample_targets(uia, ocr, per_window, rng)
        if not items:
            continue
        questions = await paraphrase(ollama, target.app, items)
        print(f"\n== {target.app} ({kind} targets, {len(items)} sampled; {len(uia)} UIA elements, {len(ocr)} OCR lines)")
        for mode in ("exact", "natural"):
            ok, methods, ms, fails = 0, Counter(), [], []
            for el, q in zip(items, questions):
                query = el.name if mode == "exact" else q
                t0 = time.perf_counter()
                # Same snapshot the targets came from: apps like YouTube or a streaming chat change
                # between reads, and that would measure app churn rather than the locator.
                found = await perc.locate_in(query, target, uia, ocr)
                ms.append((time.perf_counter() - t0) * 1000)
                good = hit(found.element, el)
                ok += good
                methods[found.method] += 1
                if not good and len(fails) < 4:
                    got = found.element.describe()[:40] if found.element else "nothing"
                    fails.append(f"'{query[:45]}' -> {got} (wanted {el.describe()[:40]})")
            acc = ok / len(items)
            totals[mode + "_ok"] += ok
            totals[mode + "_n"] += len(items)
            ms.sort()
            print(f"  {mode:8s} {ok:2d}/{len(items)} = {acc:4.0%}   median {ms[len(ms)//2]:5.0f} ms  p90 {ms[int(len(ms)*0.9)]:5.0f} ms  via {dict(methods)}")
            for f in fails:
                print(f"      miss: {f}")
    for mode in ("exact", "natural"):
        if totals[mode + "_n"]:
            print(f"\nOVERALL {mode}: {totals[mode + '_ok']}/{totals[mode + '_n']} = {totals[mode + '_ok'] / totals[mode + '_n']:.0%}")
    perc.close()
    await ollama.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20))
