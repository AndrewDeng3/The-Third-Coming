"""Finding a UI element from a natural-language description.

1. Lexical scoring over UIA elements + OCR lines (fuzzy name match, role hints like "button").
2. If there's no confident, unambiguous winner, an LLM picks among the top candidates
   (handles semantic queries: "how do I go back?" -> Button 'Back').
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from stickfigure.perception.uia import INTERACTIVE, UIElement

# Words in a query that describe the element's role rather than its label.
ROLE_WORDS = {
    "button": {"Button", "SplitButton"},
    "btn": {"Button", "SplitButton"},
    "icon": {"Button", "SplitButton", "Image"},
    "link": {"Hyperlink"},
    "tab": {"TabItem"},
    "menu": {"MenuItem", "Menu", "SplitButton"},
    "option": {"MenuItem", "ListItem", "RadioButton", "CheckBox"},
    "checkbox": {"CheckBox"},
    "box": {"Edit", "ComboBox", "CheckBox"},
    "field": {"Edit", "ComboBox"},
    "bar": {"Edit", "ComboBox", "ToolBar"},
    "search": {"Edit", "ComboBox"},
    "folder": {"ListItem", "TreeItem"},
    "file": {"ListItem", "DataItem"},
    "dropdown": {"ComboBox", "SplitButton"},
    "slider": {"Slider"},
}
STOP = {"the", "a", "an", "my", "where", "wheres", "where's", "is", "are", "find", "show", "me", "to", "for",
        "on", "in", "of", "can", "you", "i", "how", "do", "does", "please", "that", "this", "it", "at", "there",
        "click", "which", "what", "whats", "thing", "one"}

CONFIDENT = 0.82
MARGIN = 0.08


def _norm(s: str) -> str:
    s = re.sub(r"\(.*?\)", " ", s.lower())  # drop "(Ctrl+S)" style hints
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _tokens(s: str) -> list[str]:
    return [t for t in _norm(s).split() if t not in STOP]


def query_roles(query: str) -> set[str]:
    roles: set[str] = set()
    for w in _norm(query).split():
        roles |= ROLE_WORDS.get(w, set())
    return roles


def score(el: UIElement, query: str) -> float:
    full = _tokens(query)  # keeps role words: "close tab" must prefer 'Close Tab' over 'Close'
    core = [t for t in full if t not in ROLE_WORDS] or full
    if not full or not el.name:
        return 0.0
    name = _norm(el.name)
    n_toks = name.split()

    def present(q: str) -> bool:
        return any(q == n or (len(q) > 3 and (n.startswith(q) or SequenceMatcher(None, q, n).ratio() > 0.85)) for n in n_toks)

    # Share of the label words asked for that the name contains (role words don't have to be in it).
    covered = sum(1 for q in core if present(q)) / len(core)
    # Penalize names with lots of extra words ("Share" vs "Share this video with your friends ...").
    precision = sum(1 for n in n_toks if n in full) / max(1, len(n_toks))
    whole = SequenceMatcher(None, " ".join(full), name).ratio()
    s = 0.55 * covered + 0.25 * whole + 0.2 * precision
    roles = query_roles(query)
    if roles:
        s += 0.08 if el.role in roles else -0.05
    if el.role in INTERACTIVE:
        s += 0.03
    if not el.enabled:
        s -= 0.1
    return max(0.0, min(1.0, s))


@dataclass
class Candidate:
    el: UIElement
    score: float


def merge_sources(uia: list[UIElement], ocr: list[UIElement]) -> list[UIElement]:
    """UIA elements plus OCR lines that aren't already covered by a UIA element with the same text."""
    out = list(uia)
    for line in ocr:
        text = _norm(line.name)
        if not text:
            continue
        cx, cy = line.center
        dup = any(
            e.rect.left <= cx <= e.rect.right and e.rect.top <= cy <= e.rect.bottom and text in _norm(e.name)
            for e in uia
        )
        if not dup:
            out.append(line)
    return out


def rank(elements: list[UIElement], query: str) -> list[Candidate]:
    cands = [Candidate(e, score(e, query)) for e in elements if not e.is_password]
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands


def confident(cands: list[Candidate]) -> bool:
    if not cands or cands[0].score < CONFIDENT:
        return False
    best = cands[0]
    for other in cands[1:]:
        if other.score < best.score - MARGIN:
            break
        # A near-tie is fine if it's the same thing (e.g. UIA + OCR of one button, or nested elements).
        if not _overlap(best.el, other.el):
            return False
    return True


def _overlap(a: UIElement, b: UIElement) -> bool:
    ra, rb = a.rect, b.rect
    return not (ra.right <= rb.left or rb.right <= ra.left or ra.bottom <= rb.top or rb.bottom <= ra.top)


# -- LLM re-ranking ------------------------------------------------------------------------------

PICK_SCHEMA = {
    "type": "object",
    "properties": {"index": {"type": "integer"}, "confidence": {"type": "number"}},
    "required": ["index", "confidence"],
}


def shortlist(cands: list[Candidate], window: tuple[float, float, float, float], n: int = 60) -> list[Candidate]:
    """Top lexical matches, topped up with interactive elements so semantic queries have options."""
    picked = [c for c in cands[: n // 2] if c.el.name]
    seen = {id(c.el) for c in picked}
    for c in cands:
        if len(picked) >= n:
            break
        if id(c.el) not in seen and c.el.name and c.el.role in INTERACTIVE | {"Text"}:
            picked.append(c)
            seen.add(id(c.el))
    return picked


def pick_messages(query: str, cands: list[Candidate], window: tuple[float, float, float, float], app: str) -> list[dict]:
    left, top, right, bottom = window
    w, h = max(1.0, right - left), max(1.0, bottom - top)

    def where(el: UIElement) -> str:
        cx, cy = el.center
        fx, fy = (cx - left) / w, (cy - top) / h
        horiz = "left" if fx < 0.33 else "center" if fx < 0.66 else "right"
        vert = "top" if fy < 0.25 else "middle" if fy < 0.75 else "bottom"
        return f"{vert}-{horiz}"

    lines = [
        f"[{i}] {c.el.role}{' (text on screen)' if c.el.source == 'ocr' else ''}: \"{c.el.name[:80]}\" @ {where(c.el)}"
        for i, c in enumerate(cands)
    ]
    return [
        {
            "role": "system",
            "content": (
                "You help locate user-interface elements. Given the user's request and a numbered list of elements "
                "visible in the app window, return the index of the single element the user means. "
                "Prefer interactive controls (buttons, links, tabs, menu items) over plain text. "
                "If nothing fits, return index -1. confidence is 0..1."
            ),
        },
        {"role": "user", "content": f"App window: {app}\nUser is looking for: {query}\n\nElements:\n" + "\n".join(lines)},
    ]
