"""The action vocabulary the model can use, its JSON schema, and the per-step prompt."""

from __future__ import annotations

import re
from dataclasses import dataclass

from stickfigure.perception import locate as L
from stickfigure.perception.uia import INTERACTIVE, UIElement
from stickfigure.world.geometry import Rect

KINDS = ("click", "double_click", "type_text", "hotkey", "scroll", "open_url", "switch_window", "wait", "ask_user",
         "done")
PAYLOAD_TOKEN = "{{TEXT}}"

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": list(KINDS)},
        "element_id": {"type": "integer"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "text": {"type": "string"},
        "keys": {"type": "string"},
        "amount": {"type": "integer"},
        "say": {"type": "string"},
    },
    "required": ["thought", "action", "element_id", "text", "keys", "amount", "say"],
}

SYSTEM = f"""You operate one Windows application window on the user's behalf, one small step at a time.
Each turn you see the goal, what you've done so far, the focused element, and a numbered list of
visible elements. Reply with exactly ONE next action as JSON:

- click / double_click: element_id from the list (or element_id -1 with x, y as 0..1 fractions of the window).
- type_text: types `text` into the focused element. Click the right field first if it isn't focused.
  If the goal says prepared text is ready, use exactly "{PAYLOAD_TOKEN}" as `text` (it's replaced with
  their text verbatim). Otherwise write the words yourself, e.g. "hello".
- hotkey: `keys` like "ctrl+a", "enter", "tab", "ctrl+t" (new browser tab), "ctrl+l" (address bar),
  "ctrl+tab" (next tab). Only simple editing/browsing keys are allowed.
- open_url: `text` = a full https:// URL. Opens it in a new browser tab and continues there (use this to
  visit a site or search: https://www.google.com/search?q=...).
- switch_window: `text` = part of another open window's title or app name (see "Other windows"); work
  continues in that window.
- scroll: `amount` wheel notches, positive = up, negative = down; element_id picks where to scroll (-1 = window).
- wait: let the app catch up.
- ask_user: `say` holds your question; use it if the goal is unclear.
- done: the goal is complete (check the elements to confirm).

Working with text:
- Read "Current text" first. If the goal's text is already there, don't type it again: reply done.
- Type the whole text in ONE type_text action; use \\n inside `text` for line breaks.
- The text goes where the caret is. To start on a new line after existing text, press "ctrl+end"
  then include a leading \\n. To replace text, select it first (e.g. "ctrl+a" selects everything in a field).
- After typing, the step result says whether the text was verified in the field. Never retype
  something that was verified.

Rules: you are ALREADY in the app window below - work here. Only use switch_window if the goal names a
different app, and open_url only if it needs a website that isn't open.
Never open terminals, run commands, or change system settings. Follow the plan, but adapt if the screen
shows something unexpected. If a step was blocked or failed, try a different approach; if truly stuck,
ask_user. Only say done when the elements confirm the goal is complete.
`thought` is one short sentence of reasoning; `say` is a short, friendly status line for the user
("Clicking the Share button!"). Unused fields: element_id -1, text "", keys "", amount 0."""


@dataclass
class Step:
    kind: str
    element: UIElement | None
    point: tuple[float, float] | None  # screen point for clicks/scrolls
    text: str = ""
    keys: str = ""
    amount: int = 0
    say: str = ""
    thought: str = ""

    def describe(self) -> str:
        where = self.element.describe() if self.element else (
            f"point ({round(self.point[0])}, {round(self.point[1])})" if self.point else "")
        if self.kind in ("click", "double_click"):
            return f"{self.kind.replace('_', ' ')} {where}"
        if self.kind == "type_text":
            preview = self.text if len(self.text) <= 60 else self.text[:57] + "..."
            return f"type \"{preview}\" ({len(self.text)} chars)"
        if self.kind == "hotkey":
            return f"press {self.keys}"
        if self.kind == "open_url":
            return f"open {self.text[:80]} in a new tab"
        if self.kind == "switch_window":
            return f"switch to the '{self.text[:40]}' window"
        if self.kind == "scroll":
            return f"scroll {'up' if self.amount > 0 else 'down'} {abs(self.amount)} at {where or 'the window'}"
        return self.kind


def pick_elements(elements: list[UIElement], goal: str, limit: int = 120) -> list[UIElement]:
    """What to show the model: the most goal-relevant elements first, then other controls in reading order."""
    usable = [e for e in elements if (e.name or e.role in ("Edit", "Document", "ComboBox")) and not e.is_password]
    ranked = sorted(usable, key=lambda e: L.score(e, goal), reverse=True)
    chosen = ranked[: limit // 3]
    seen = {id(e) for e in chosen}
    rest = sorted((e for e in usable if id(e) not in seen), key=lambda e: (round(e.rect.top / 20), e.rect.left))
    for e in rest:
        if len(chosen) >= limit:
            break
        if e.role in INTERACTIVE or e.role in ("Edit", "Document", "Text"):
            chosen.append(e)
    return sorted(chosen, key=lambda e: (round(e.rect.top / 20), e.rect.left))


def step_messages(goal: str, has_payload: bool, app: str, window: Rect, focused: UIElement | None,
                  elements: list[UIElement], history: list[str], field_text: str | None = None,
                  plan: list[str] | None = None, windows: list[str] | None = None) -> list[dict]:
    # (Google Docs & co draw text on a canvas: clicking the page puts the caret in a hidden text box, so the
    # "Focused element" may look unrelated - the step results say when the caret is in the text.)
    w, h = max(1.0, window.width), max(1.0, window.height)
    lines = []
    for i, e in enumerate(elements):
        cx, cy = e.center
        flag = " [focused]" if e.focused else ""
        src = " (text)" if e.source == "ocr" else ""
        lines.append(f"[{i}] {e.role}{src} \"{e.name[:70]}\"{flag} @ ({(cx - window.left) / w:.2f}, {(cy - window.top) / h:.2f})")
    done = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(history[-25:])) or "(nothing yet)"
    plan_txt = ("\nPlan:\n" + "\n".join(f"- {p}" for p in plan)) if plan else ""
    others = ("\nOther windows: " + "; ".join(windows[:12])) if windows else ""
    payload = (f"\nPrepared text is ready: to type it, use exactly \"{PAYLOAD_TOKEN}\" as `text`. Never write your "
               "own version of it." if has_payload else "")
    if field_text is None:
        current = "(unknown - this app doesn't expose it)"
    elif not field_text.strip():
        current = "(empty)"
    else:
        current = f'"""\n{field_text}\n"""'
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": (
            f"Goal: {goal}{payload}{plan_txt}\nApp window: {app}{others}\n"
            f"Focused element: {focused.describe() if focused else 'unknown'}\n"
            f"Current text in the field/document being edited (may come from OCR, last 1500 chars):\n{current}\n\n"
            f"Steps so far:\n{done}\n\nVisible elements:\n" + "\n".join(lines)
        )},
    ]


def parse_step(raw: dict, elements: list[UIElement], window: Rect, payload: str | None) -> Step | str:
    """Validate the model's JSON. Returns a Step, or an error string to feed back to the model."""
    kind = raw.get("action")
    if kind not in KINDS:
        return f"unknown action {kind!r}"
    eid = raw.get("element_id")
    eid = -1 if eid is None else int(eid)  # careful: 0 is a valid id (don't use `or -1`)
    element = None
    point = None
    if eid >= 0:
        if eid >= len(elements):
            return f"element_id {eid} doesn't exist"
        element = elements[eid]
        point = element.center
    elif kind in ("click", "double_click"):
        x, y = raw.get("x"), raw.get("y")
        if x is None or y is None or not (0 <= float(x) <= 1 and 0 <= float(y) <= 1):
            return "click needs an element_id or x, y between 0 and 1"
        point = (window.left + float(x) * window.width, window.top + float(y) * window.height)
    elif kind == "scroll":
        point = ((window.left + window.right) / 2, (window.top + window.bottom) / 2)
    text = str(raw.get("text", ""))
    if kind == "open_url":
        url = text.strip()
        if not re.match(r"^https?://[^\s/$.?#][^\s]*$", url, re.I):
            return "open_url needs a full http(s) URL in `text`"
        text = url
    if kind == "switch_window" and not text.strip():
        return "switch_window needs part of a window title or app name in `text`"
    if text.count(PAYLOAD_TOKEN) > 1:
        return f"{PAYLOAD_TOKEN} may appear only ONCE (it already contains all of the prepared text)"
    if PAYLOAD_TOKEN in text and len(text.replace(PAYLOAD_TOKEN, "").strip()) > 3:
        return (f"use {PAYLOAD_TOKEN} on its own (optionally with a leading/trailing newline) - don't add other "
                "words around it")
    if PAYLOAD_TOKEN in text:
        if payload is None:
            return (f"there is no user-supplied text, so {PAYLOAD_TOKEN} can't be used: put the actual words "
                    "to type in `text` (e.g. \"hello\")")
        text = text.replace(PAYLOAD_TOKEN, payload)
    return Step(kind, element, point, text=text, keys=str(raw.get("keys", "")), amount=int(raw.get("amount", 0) or 0),
                say=str(raw.get("say", ""))[:120], thought=str(raw.get("thought", ""))[:200])


_QUOTED = re.compile(r"[\"“”](.{8,}?)[\"“”]", re.S)  # double quotes only: apostrophes are everywhere in prose


def extract_payload(message: str) -> str | None:
    """Verbatim text the user wants typed: a quoted span, or everything after a colon / first newline.

    Done without an LLM so long text is never paraphrased by the model.
    """
    m = _QUOTED.search(message)
    if m and len(m.group(1).strip()) >= 8:
        return m.group(1).strip()
    for sep in (":\n", ":", "\n"):
        if sep in message:
            tail = message.split(sep, 1)[1].strip()
            if len(tail) >= 15:
                return tail
    return None


PLAN_SCHEMA = {
    "type": "object",
    "properties": {"plan": {"type": "array", "items": {"type": "string"}}},
    "required": ["plan"],
}


def plan_messages(goal: str, app: str, has_payload: bool, windows: list[str]) -> list[dict]:
    extra = (" The text is already prepared: ONE type_text step puts all of it in at the caret (no menus, "
             "clipboard or Ctrl+V needed) - usually just click into the right place, then type it."
             if has_payload else "")
    return [
        {"role": "system", "content": (
            "You plan how an assistant will do a task on a Windows PC with the mouse and keyboard, one "
            "window at a time. It can click, type, press simple keys (ctrl+t new tab, ctrl+l address bar), "
            "scroll, open URLs in a new browser tab, and switch to another open window. Write 2-7 short, "
            "concrete steps. Never write out the text to type yourself - just say 'type the prepared text'. "
            "It is ALREADY in the window named below: don't plan to switch to it, and don't "
            "switch to any other app unless the task names it. Never plan to send, submit, or post anything "
            "unless the task asks. No terminals or system settings.")},
        {"role": "user", "content": f"Task: {goal}{extra}\nAlready in: {app}\nOther open windows: {'; '.join(windows[:12])}"},
    ]
