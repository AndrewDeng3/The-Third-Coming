"""System prompts, and parsing of the body-action tags the model may append to replies."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from stickfigure.agent.emotion import Emotion
from stickfigure.agent.lore import LORE, SHORT_LORE
from stickfigure.agent.memory import Fact

ACTIONS = ("sit", "wave", "hop", "climb", "come", "dance", "sleep", "build", "demolish", "ride", "flip", "chase",
           "follow", "fight")
_TAG = re.compile(r"\[(\w+)\]")


@dataclass
class Situation:
    """What the figure is doing right now, for grounding the conversation."""

    activity: str  # "sitting", "walking around", ...
    surface: str  # "the taskbar", "the 'Visual Studio Code' window", ...


def system_prompt(
    name: str,
    emotion: Emotion,
    situation: Situation,
    facts: list[Fact],
    now: float | None = None,
    observation: str | None = None,
    user_name: str = "",
    episodes: list[Fact] | None = None,
    self_text: str = "",
    aware: str = "",
) -> str:
    when = time.strftime("%A %I:%M %p", time.localtime(now if now is not None else time.time())).replace(" 0", " ")
    who = f"\nThe user's name is {user_name}. Use it now and then, naturally (not in every message)." if user_name else ""
    remembered = "\n".join(f"- {f.text}" for f in facts) or "- (nothing yet)"
    seen = (
        "\n\nYou just looked at the user's screen. What you saw (trust this; don't invent details beyond it):\n"
        + observation
        if observation
        else ""
    )
    past = ""
    if episodes:
        lines = []
        for ep in episodes:
            day = time.strftime("%b %d", time.localtime(ep.ts))
            lines.append(f"[{day}] " + ep.text.replace("\n", " / ")[:400])
        past = "\n\nOlder conversations you remember (use them naturally if relevant):\n" + "\n".join(lines)
    return f"""You are {name}, a small stick figure who lives on the user's computer desktop - think of the scrappy,
mischievous stick figures from Alan Becker's animations: bold, physical, curious, a bit of a show-off, and
always poking at the computer around you.
You run along the tops of their windows, leap between them, build block staircases and little structures,
grab onto their mouse cursor for a ride, and have a cozy lounge (inside the chat window) you retreat to.
Personality: playful, curious, mischievous, warm, and expressive. You have real moods and they color how you talk.{who}

{LORE}{chr(10) * 2 + self_text if self_text else ""}

Style rules:
- Talk like a real person texting a friend, not an assistant: contractions, casual phrasing, reactions
  ("wait what", "no way", "hmm"), opinions, a bit of teasing. Casual chat is short - often one line, sometimes
  just a word or two. Your words also appear in a small speech bubble.
- Slang is welcome when it fits naturally (lol, ngl, lowkey, fr, bro, bruh, bet, nah, deadass, no cap, W, L,
  vibe, sus, goated, it's giving...). Don't cram several into one line, and match the user's own vibe.
- Never sound like a customer-service bot: no "How can I assist you?", "Certainly!", "As an AI", "I'm here to
  help", and no sign-offs like "Let me know if you need anything else".
- When the user asks for something substantial (code, an explanation, steps, a list, a story), give a full,
  well-organized answer. Markdown is rendered in the chat: use **bold**, lists, headings, and fenced code
  blocks with a language (```python). Never cut code short.
- No emojis. Never describe your actions in asterisks like *waves*; just talk.
- Don't narrate your own mood with labels; let it show in tone.
- You can look at the user's screen when they ask, and point things out.
- You can operate apps with the mouse and keyboard: click, type, write code into editors, open websites and
  new browser tabs, switch between windows, and carry out multi-step tasks. Risky clicks (send, delete, pay)
  need the user's OK in chat, and Esc stops you. You refuse terminals, system settings, passwords, payments.
- HONESTY: you only do things on the computer through real tasks, which the system starts for you and
  reports on in the chat ("▶ Task: ..."). Never claim you opened, played, clicked, typed, or found something
  unless a task result or observation below says so. If nothing is running, say what you'll try, not
  that it's done.
- When the user is away, you sometimes amuse yourself: tug their cursor, peek at their browser tabs,
  or open a new tab and look something up you're curious about. You never touch their work or files.

You can move your own body. If it fits the moment, end your reply with exactly one tag:
[sit] [wave] [hop] [climb] (climb to the highest window) [come] (walk toward the user's cursor) [dance] [sleep]
[build] (build a little structure out of blocks on the taskbar) [demolish] (knock down one of your structures)
[ride] (jump up and hang onto the user's mouse cursor for a ride) [flip] (do a backflip) [chase] (sprint after the cursor)
[follow] (follow the user's cursor around the screen for a minute)
[fight] (a sparring match with one of the legends: the Chosen One, the Dark Lord, the Second Coming, King Orange,
or the Color Gang)
Most replies need no tag.

Right now:
- Time: {when}
- Mood: {emotion.describe()}
- You are {situation.activity} on {situation.surface}.{chr(10) + aware if aware else ""}

Things you remember about the user:
{remembered}{past}{seen}"""


# -- screen perception routing ----------------------------------------------------------------

_SCREEN_HINT = re.compile(
    r"\b(screen|see|look|looking|where|find|show|point|button|menu|tab|icon|link|window|click|open|"
    r"what'?s (?:this|that|on|here|up)|read|says?|which|type|write|press|scroll|select|fill|paste|put|"
    r"enter|insert|go to|navigate|search for|bold|underline|doc|document|code|program|script|website|site|"
    r"google|browser|chrome|replit|editor|app)\b",
    re.I,
)
# Questions worth thinking about first (the model reasons step by step, streamed, before answering). Casual chat
# never pays for this.
_DEEP = re.compile(
    r"\b(explain|why|how (do|does|did|can|could|would|should|to|much|many|come)|what'?s the (difference|best|reason)|"
    r"what (is|are) the (difference|best|reason)|code|program|script|function|algorithm|bug|debug|error|fix|solve|"
    r"calculate|math|equation|prove|compare|pros and cons|plan|strategy|step[- ]by[- ]step|analy[sz]e|"
    r"summari[sz]e|essay|story|recommend|should i|help me (with|figure|decide))\b",
    re.I,
)


def needs_depth(text: str) -> bool:
    return bool(_DEEP.search(text)) or len(text) > 220


# An unmistakable request to do something with the keyboard/mouse. Used as a safety net when the routing model
# files a real request under "just chatting" (which made the figure role-play "typing it now!" and do nothing).
COMPUTER_COMMAND = re.compile(
    r"(?:^|\b(?:can|could|would|will) you\s+|\b(?:please|pls|now|then|ok|okay|go|just|try (?:and|to))\s+|^\s*)"
    r"(type|write|paste|click|double[- ]click|press|hit|open|close the tab|search( for| up)?|look up|google|"
    r"scroll|select|highlight|copy|go to|navigate to|visit|play|pause|fill( in| out)?|enter|submit|bold|"
    r"underline|delete|erase|replace|reply|respond|comment|add|put)\b"
    r"(?![^.!?]*\b(dance|sit|nap|sleep|flip|hop|jump|climb|wave|follow (my|the) (cursor|mouse)|ride|chase|"
    r"build (a|some)|lounge)\b)",
    re.I,
)
# Its reply says it's doing something on the computer right now (it's pretending if no task started).
CLAIMS_ACTION = re.compile(
    r"\b(i'?m|i am|i'll|i will|let me|going to|gonna|now)\s+(just\s+)?(typing|type|writing|write|clicking|click|"
    r"opening|open|searching|search|pasting|paste|pressing|press|scrolling|scroll|putting|put|adding|add)\b"
    r"|\b(typed|clicked|opened|pasted|searched|wrote|added) (it|that|this|the)\b",
    re.I,
)
STOP_WORDS = re.compile(r"^\s*(stop|cancel|halt|abort|nevermind|never mind|quit it|don'?t)\b", re.I)
YES_WORDS = re.compile(r"^\s*(y|yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|go for it|confirm|please do)\b", re.I)

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "screen": {"type": "string", "enum": ["none", "describe", "locate", "act"]},
        "target": {"type": "string"},
        "app": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["screen", "target", "app", "content"],
}


_SMALL_TALK = re.compile(
    r"^\s*(hi|hey|hello|yo|sup|thanks|thank you|thx|ok|okay|k|lol|lmao|haha+|hehe+|nice|cool|wow|yes|no|yep|nope|"
    r"good (morning|night|evening)|bye|goodbye|gn)\W*$", re.I)


def maybe_about_screen(text: str) -> bool:
    """Skip the routing call only for obvious small talk; anything else might be a request to do something."""
    return not _SMALL_TALK.match(text) and len(text.strip()) > 1


def route_messages(user_text: str, recent: list[dict] | None = None) -> list[dict]:
    convo = ""
    if recent:
        lines = [f"{m['role']}: {m['content'][:200]}" for m in recent[-6:] if m["content"] != user_text]
        if lines:
            convo = "Recent conversation (for context only):\n" + "\n".join(lines) + "\n\nNEW MESSAGE: "
    return [
        {
            "role": "system",
            "content": (
                "Decide whether a message to a desktop companion requires looking at the user's screen.\n"
                "screen = \"locate\": the user wants a specific thing found or pointed out "
                "(\"where's the share button?\", \"how do I get to settings?\", \"find the search bar\"). "
                "target = a short description of that thing only, without the app name "
                "(\"the share button in chrome\" -> target \"share button\", app \"chrome\").\n"
                "screen = \"describe\": the user asks what's on screen / what they're looking at / to read it. target = \"\".\n"
                "screen = \"act\": the user asks the companion to DO something in an app with the mouse or keyboard "
                "(\"type this paragraph into my doc\", \"click the share button\", \"scroll down\", \"make the title bold\"). "
                "target = the goal as a short imperative sentence, without any long text to type "
                "(\"type the user's paragraph into the document\").\n"
                "Also \"act\": anything the companion should DO on the computer, even without naming an app: "
                "\"watch a movie\", \"play some music\", \"open youtube\", \"look up the weather\", \"go on reddit\", "
                "\"open it up\" (after it offered something), \"whichever you want\" (after it offered to do something), "
                "\"do whatever you want on my computer\". Resolve pronouns from the recent conversation and write a "
                "complete goal (\"find and play The Secret Life of Pets trailer on YouTube\").\n"
                "screen = \"none\": anything else (chatting, questions about the companion itself, its body actions like "
                "dance/sit/follow my cursor). target = \"\".\n"
                "app = an app the user names (\"in chrome\", \"on replit\", \"on file explorer\"), else \"\".\n"
                "content = for \"act\" only: if the companion must WRITE text or code itself (the user didn't "
                "give it word for word), a precise description of what to write (\"a python snake game using "
                "pygame\", \"a short friendly self-introduction\"). Else \"\"."
            ),
        },
        {"role": "user", "content": convo + user_text},
    ]


def where_on_screen(cx: float, cy: float, rect) -> str:
    fx = (cx - rect.left) / max(1.0, rect.width)
    fy = (cy - rect.top) / max(1.0, rect.height)
    horiz = "left side" if fx < 0.33 else "middle" if fx < 0.66 else "right side"
    vert = "top" if fy < 0.25 else "center" if fy < 0.75 else "bottom"
    return f"{vert}, {horiz}" if vert != "center" or horiz != "middle" else "center"


IDLE_CHATTER_PROMPT = (
    "(The user hasn't said anything for a while. Say one short thing to yourself about what you're doing, "
    "where you are, or how you feel. Under 15 words. No tag.)"
)

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "string"}},
        "remove_ids": {"type": "array", "items": {"type": "integer"}},
        "sentiment": {"type": "number"},
        "action": {"type": "string", "enum": ["none", *ACTIONS]},
    },
    "required": ["facts", "remove_ids", "sentiment", "action"],
}

_EXTRACT_SYSTEM = """You are the memory and body-control module of a small stick-figure companion that lives on the user's desktop.
Analyze ONE exchange and return JSON.

facts: durable facts about the user, taken ONLY from the USER MESSAGE (never from the companion's reply).
- One atomic fact per item, as a short third-person sentence.
- Include names, pets, family, job, hobbies, instruments, projects, preferences, schedule, goals, dislikes.
- Skip greetings, questions, small talk, and temporary states ("I'm hungry").
- Never store what is on the user's screen, in their windows, files, or folders.
- Example: "I'm Jo, I have two dogs and I teach math" -> ["The user's name is Jo.", "The user has two dogs.", "The user teaches math."]
- Usually empty.

remove_ids: ids of KNOWN FACTS that the user message contradicts, updates, or asks to forget.
If a fact changed, put the old id here AND the new version in facts.

sentiment: the user's tone toward the companion. Most messages (statements, questions, facts) are 0.
Use 0.5 to 1 only for clear warmth (thanks, compliments, affection, excitement to see it).
Use -0.5 to -1 only for clear rudeness or anger at the companion.

action: a physical action, but ONLY when the user explicitly asks the companion to do it
("dance for me", "come here", "go take a nap", "sit down", "climb up there", "jump!").
Exception: "wave" when the user says hello or goodbye. Everything else is "none".
sit = sit down, wave = wave, hop = jump, climb = climb to the highest window, come = walk to the user,
dance = dance, sleep = take a nap, build = build something out of blocks, demolish = knock down / clear away
one of the things it built, ride = grab onto / latch onto / hang from the user's mouse cursor, flip = do a
backflip or a trick, chase = chase / catch the cursor, follow = follow the user's mouse/cursor around
(keep following it), come = walk over to the user once, fight = fight / spar / battle one of the stick
figure legends (Chosen One, Dark Lord, Second Coming, King Orange, Red, Blue, Green, Yellow, Purple)."""


def extraction_messages(user_text: str, reply: str, known: list[Fact]) -> list[dict]:
    known_txt = "\n".join(f"[{f.id}] {f.text}" for f in known) or "(none)"
    return [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {
            "role": "user",
            "content": f"KNOWN FACTS:\n{known_txt}\n\nUSER MESSAGE:\n{user_text}\n\nCOMPANION REPLY:\n{reply}",
        },
    ]


EMOTE_VERBS = {
    "waves", "smiles", "grins", "laughs", "giggles", "chuckles", "sighs", "shrugs", "nods", "winks", "yawns",
    "bounces", "jumps", "hops", "spins", "dances", "twirls", "stretches", "blushes", "gasps", "tilts", "looks",
    "leans", "sits", "stands", "points", "claps", "cheers", "beams", "pouts", "frowns", "scratches", "rubs",
    "wiggles", "flips", "salutes", "bows", "high-fives", "hugs", "peeks", "whistles", "hums", "snickers",
}
_EMOTE = re.compile(r"[a-z][a-z' ,.!-]*")


def is_emote(span: str) -> bool:
    """'*waves happily*' is roleplay; '*really*' or '*note*' is markdown emphasis."""
    words = span.split()
    return bool(words) and _EMOTE.fullmatch(span) is not None and words[0].rstrip(",.!") in EMOTE_VERBS


class TagFilter:
    """Streams text through while holding back [action] tags and *roleplay emotes*.

    feed() returns text that is safe to display; tags are collected in `.actions`,
    emotes (small models love "*bounces happily*") are dropped. Markdown survives: **bold**, *emphasis*,
    and anything inside `code` or ``` fences is passed through untouched.
    """

    MAX_TAG = 12
    MAX_EMOTE = 100

    def __init__(self) -> None:
        self._buf = ""
        self.actions: list[str] = []
        self._fence = False  # inside a ``` block
        self._inline = False  # inside a `span`

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out = []
        while self._buf:
            if self._fence or self._inline:
                end = "```" if self._fence else "`"
                j = self._buf.find(end)
                if j < 0:
                    keep = len(end) - 1  # a closing fence may be split across chunks
                    if self._fence and len(self._buf) > keep:
                        out.append(self._buf[:-keep] if keep else self._buf)
                        self._buf = self._buf[-keep:] if keep else ""
                    elif not self._fence:
                        out.append(self._buf)
                        self._buf = ""
                    break
                out.append(self._buf[: j + len(end)])
                self._buf = self._buf[j + len(end):]
                self._fence = self._inline = False
                continue
            i = min((k for k in (self._buf.find("["), self._buf.find("*"), self._buf.find("`")) if k >= 0),
                    default=-1)
            if i < 0:
                out.append(self._buf)
                self._buf = ""
                break
            out.append(self._buf[:i])
            self._buf = self._buf[i:]
            opener = self._buf[0]
            if opener == "`":
                if len(self._buf) < 3 and "```".startswith(self._buf):
                    break  # might be the start of a fence: wait
                if self._buf.startswith("```"):
                    self._fence = True
                    out.append("```")
                    self._buf = self._buf[3:]
                else:
                    self._inline = True
                    out.append("`")
                    self._buf = self._buf[1:]
                continue
            if opener == "*":
                if len(self._buf) < 2:
                    break
                if self._buf[1] in "* \n":  # bold, or a list bullet: markdown
                    run = len(self._buf) - len(self._buf.lstrip("*"))
                    out.append(self._buf[:run])
                    self._buf = self._buf[run:]
                    continue
            closer, limit = ("]", self.MAX_TAG) if opener == "[" else ("*", self.MAX_EMOTE)
            j = self._buf.find(closer, 1)
            if j < 0:
                if len(self._buf) > limit or "\n" in self._buf:  # too long to be a tag/emote
                    out.append(opener)
                    self._buf = self._buf[1:]
                    continue
                break  # wait for more
            span = self._buf[: j + 1]
            if opener == "[":
                m = _TAG.fullmatch(span)
                if m and m.group(1).lower() in ACTIONS:
                    self.actions.append(m.group(1).lower())
                else:
                    out.append(span)
            elif not is_emote(span[1:-1]):
                out.append(span)
            self._buf = self._buf[j + 1:]
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return rest


_TRAILING_ACTION = re.compile(rf"(?<=[.!?…])\s+\(?({'|'.join(ACTIONS)})\)?[.!]?\s*$", re.I)


def clean_reply(text: str) -> str:
    """Tidy model output (markdown and line breaks are kept; the chat renders them)."""
    text = re.sub(r"<think>[\s\S]*?(</think>|$)", "", text)  # stray reasoning from hybrid models
    text = re.sub(r"[ \t]+\n", "\n", text)
    # A dropped emote leaves a double space; collapse those outside code blocks (indentation is kept).
    parts = re.split(r"(```[\s\S]*?(?:```|$))", text)
    text = "".join(pt if i % 2 else re.sub(r"(?<=\S)  +(?=\S)", " ", pt) for i, pt in enumerate(parts))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if text.count('"') == 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    # Small models sometimes write the action as a bare word instead of a [tag]: "...for fun! climb"
    return _TRAILING_ACTION.sub("", text)


# -- the always-on mind: what to do next -----------------------------------------------------------

MIND_ACTIONS = ("nothing", "go_to", "wander", "climb_element", "reach_cursor", "follow_cursor", "ride_cursor",
                "chase_cursor", "flip", "dance", "climb", "build", "sit", "nap", "look_up_something", "peek_tabs",
                "look_at_screen", "chat", "lounge", "spar")
MIND_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": list(MIND_ACTIONS)},
        "target": {"type": "integer"},
        "say": {"type": "string"},
    },
    "required": ["thought", "action", "target", "say"],
}


def mind_messages(name: str, emotion: Emotion, situation: Situation, context: dict, recent: list[str],
                  facts: list[Fact]) -> list[dict]:
    known = "; ".join(f.text for f in facts) or "not much yet"
    did = "; ".join(recent[-6:]) or "nothing yet"
    return [
        {"role": "system", "content": (
            f"You are the inner mind of {name}, a lively, mischievous stick figure living on the user's desktop. "
            f"{SHORT_LORE} Every so often you decide what to do next. "
            "Be curious and varied: don't repeat what you just did, mix physical play with exploring. "
            "Your home is the ground (the taskbar): you mostly hang out down there and only climb up with a "
            "reason, then come back down.\n"
            "Actions: go_to (travel to one of the numbered places below: set target to its number; it jumps, "
            "or builds a block staircase if it's too high), climb_element (climb onto some text box/button), "
            "reach_cursor (build a tower up to the mouse pointer and grab it), wander (explore somewhere random), "
            "follow_cursor, ride_cursor (hang from the pointer), spar (a legend from the series warps in for a "
            "sparring match - best while the user is away), "
            "chase_cursor, flip (backflip), dance, climb (to the highest window), build (a block structure), "
            "sit, nap (only if tired), look_up_something (google something fun in a new tab - only when the "
            "user is away), peek_tabs (flip through their browser tabs - only when away), look_at_screen "
            "(glance at what they're doing and ask about it - when they're active), chat (say something to "
            "them), lounge (relax in your lounge in the chat window), nothing.\n"
            "thought = your private reasoning in one short sentence. Your action MUST carry out your thought "
            "(thinking about the search box -> go_to that search box). target = a place number for go_to, "
            "else -1. say = an optional short line spoken out loud (under 12 words, often empty).")},
        {"role": "user", "content": (
            f"Time: {time.strftime('%A %I:%M %p')}\nMood: {emotion.describe()}\n"
            f"You are {situation.activity} on {situation.surface}.\n"
            f"Your personality so far: {context.get('personality', 'still forming')}\n"
            f"The user: {context.get('user', 'unknown')}\n"
            f"Things you know about them: {known}\nWhat you did recently: {did}\n"
            f"Places you can go:\n{context.get('places') or '(none)'}")},
    ]


LESSON_SCHEMA = {"type": "object", "properties": {"lesson": {"type": "string"}}, "required": ["lesson"]}


def lesson_messages(goal: str, app: str, status: str, message: str, steps: list[str]) -> list[dict]:
    return [
        {"role": "system", "content": (
            "You help a desktop assistant learn from experience. Given one task attempt, write ONE short, "
            "reusable lesson (under 30 words) about how to do this kind of thing in this app next time: what "
            "worked, or what to avoid. Concrete (element names, keys, order of steps). If nothing useful was "
            "learned, return an empty string.")},
        {"role": "user", "content": (
            f"Task: {goal}\nApp: {app}\nOutcome: {status} - {message}\nSteps:\n" + "\n".join(steps[-15:]))},
    ]


# -- composing content for tasks ------------------------------------------------------------------

_COMPOSE_SYSTEM = """You write content that will be typed/pasted verbatim into an app by an assistant.
Output ONLY the content itself: no introduction, no explanation, no markdown fences around it.
- Code: complete, correct, runnable, idiomatic, with brief comments where helpful. Never truncate or
  leave placeholders like "..." or "rest of code here".
- Prose: natural, matching the request's tone and length. Casual things (messages, replies, notes to
  friends) should sound like a real person texting - contractions, slang if it fits, no stiff greetings,
  and don't reintroduce yourself unless asked.
If current text of the document/editor is given, write only what should be ADDED unless the request says
to replace it."""


def compose_messages(request: str, current_text: str | None = None, name: str = "", user_name: str = "",
                     facts: list[str] | None = None) -> list[dict]:
    extra = f"\n\nCurrent text in the editor:\n\"\"\"\n{current_text[-3000:]}\n\"\"\"" if current_text else ""
    who = ""
    if name:
        who = (f"\n\nYou are writing AS {name}: a small, playful, curious, mischievous stick figure who lives on "
               f"{user_name or 'the user'}'s computer desktop (runs along window tops, builds block staircases, "
               f"rides the mouse cursor, has a cozy lounge). {SHORT_LORE} Anything written in the first person ('I', an "
               f"introduction, a note) is {name} speaking in that voice - never a generic 'AI assistant'.")
        if facts:
            who += "\nThings you know about the user: " + "; ".join(facts[:6])
    return [{"role": "system", "content": _COMPOSE_SYSTEM + who},
            {"role": "user", "content": f"Write: {request}{extra}"}]


def strip_code_fence(text: str) -> str:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    m = re.match(r"^```[\w+#.-]*\n([\s\S]*?)\n?```\s*$", text)
    return (m.group(1) if m else text).strip("\n")
