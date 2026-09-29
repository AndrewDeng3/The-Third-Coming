"""Glue between the body (figure, brain, animator) and the mind (agent, memory, emotion).

- Physical events (grab, pet, throw, knockdown) update mood and trigger instant canned reactions.
- Mood feeds back into posture, walking speed, and behavior choice.
- User messages stream through the LLM into the speech bubble and chat window;
  action tags in replies become body commands.
- Screen questions are answered by looking (perception); "do X in this app" requests are vetted
  and handed to the supervised action controller.
- Occasionally the figure says something unprompted.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Callable

from stickfigure.actions.controller import ActionController
from stickfigure.actions.schema import extract_payload
from stickfigure.actions.task import BROWSERS
from stickfigure.agent.agent import Agent
from stickfigure.agent.emotion import Emotion
from stickfigure.agent.memory import Memory
from stickfigure.agent.persona import (
    CLAIMS_ACTION, COMPUTER_COMMAND, STOP_WORDS, YES_WORDS, Situation, where_on_screen,
)
from stickfigure.config import CONFIG, Config, temp_scale
from stickfigure.figure.animator import Anim, Animator
from stickfigure.figure.brain import Brain
from stickfigure.figure.controller import Figure
from stickfigure.perception.perception import Found, Perception, Target
from stickfigure.safety.policy import WindowInfo, window_problem
from stickfigure.win import win32
from stickfigure.world.geometry import Rect
from stickfigure.world.physics import World

log = logging.getLogger(__name__)
WEBBY = re.compile(r"\b(youtube|video|movie|film|trailer|music|song|google|search|look up|website|site|reddit|"
                   r"netflix|twitch|wikipedia|news|weather|browse|online|web|url|tab)\b", re.I)


class Compose:
    """A task payload the figure has to write itself (code, a paragraph...) before typing it."""

    def __init__(self, request: str):
        self.request = request

    def __len__(self) -> int:
        return 0

ACTIVITY_WORDS = {
    Anim.IDLE: "standing around", Anim.WALK: "walking", Anim.RUN: "running", Anim.PREP: "about to jump",
    Anim.AIR: "mid-jump", Anim.FALL: "falling", Anim.LAND: "landing", Anim.SIT: "sitting",
    Anim.SLEEP: "dozing", Anim.PLACE: "building a block staircase", Anim.WAVE: "waving at the user",
    Anim.GRABBED: "dangling from the user's mouse cursor", Anim.TUMBLE: "tumbling through the air after being thrown",
    Anim.KNOCKED: "lying flat after a hard landing",
    Anim.RIDE: "hanging from the user's mouse cursor, riding it around for fun",
}

REACTIONS = {
    "grab": {"happy": ["Whoa!", "Hey there!", "Up we go!"], "grumpy": ["Put me down!", "Hey!", "Ugh, again?"]},
    "grab_asleep": {"any": ["Hey! I was sleeping!", "Wha—? Five more minutes...", "Mmph!"]},
    "pet": {"happy": ["hehe", "♥", "Aww.", "That tickles!"], "grumpy": ["...fine.", "Hmph.", "Okay, okay."]},
    "thrown": {"happy": ["Wheeee!", "Yahoo!", "Woohoo!"], "grumpy": ["AAAAH!", "Not again!", "Whyyy!"]},
    "ride": {"happy": ["Free ride!", "Wheee, giddy up!", "Don't mind me!", "Onward, cursor!"],
             "grumpy": ["I'm just hanging here.", "Carry me.", "Hmph. Taxi!"]},
    "knocked": {"happy": ["Ow... I'm okay!", "Nailed it.", "Ten out of ten landing."], "grumpy": ["Ow.", "Rude.", "That hurt!"]},
}


class Companion:
    def __init__(
        self,
        fig: Figure,
        brain: Brain,
        anim: Animator,
        world: World,
        agent: Agent,
        memory: Memory,
        emotion: Emotion,
        surface_name: Callable[[tuple | None], str],
        cfg: Config = CONFIG,
    ):
        self.fig, self.brain, self.anim, self.world = fig, brain, anim, world
        self.agent, self.memory, self.emotion = agent, memory, emotion
        self.surface_name = surface_name
        self.cfg = cfg
        # UI hooks (set by the app)
        self.bubble_say: Callable[..., None] = lambda text, hold=None, log=True: None
        self.bubble_think: Callable[[], None] = lambda: None
        self.bubble_stream: Callable[[str], None] = lambda text: None
        self.bubble_finish: Callable[[], None] = lambda: None
        self.bubble_busy: Callable[[], bool] = lambda: False
        self.chat_add_assistant: Callable[[str], None] = lambda text: None
        # Perception hooks (set by the app; None = no eyes)
        self.perception: Perception | None = None
        self.choose_target: Callable[[str], Target | None] = lambda hint: None
        self.show_highlight: Callable[[Rect, str], None] = lambda rect, label: None
        # Action hooks (set by the app; None = no hands)
        self.actions: ActionController | None = None
        self.chat_add_note: Callable[[str], None] = lambda text: None
        self._pending_task: tuple[str, str | None, Target, str] | None = None
        self._last_task: dict | None = None
        self.before_task: Callable[[], None] = lambda: None  # e.g. stop any mischief in progress
        self.open_chat: Callable[[], None] = lambda: None
        self.hide_highlight: Callable[[], None] = lambda: None
        self._confirm: asyncio.Future | None = None
        # Curiosity: glance at what the user is doing and ask about it (set by the app)
        self.notice_enabled = cfg.notice_activity
        self.peek_target: Callable[[], Target | None] = lambda: None
        self.in_lounge: Callable[[], bool] = lambda: False
        self._next_peek = time.monotonic() + random.uniform(*cfg.peek_gap) / 2
        self._asked_about: dict[str, float] = {}  # window title -> when we last asked
        self._recent_peek: tuple[str, float] | None = None  # (what we saw, when)
        self._peeking = False
        self._awaiting_answer: dict | None = None

        # Body actions from the conversation (the app reroutes them while the figure is in its lounge).
        self.body_command: Callable[[str], None] = brain.command
        agent.on_action = self._on_agent_action
        # The always-on mind: every half minute or so it decides what to do next (set up by the app).
        self.think_enabled = True
        self.temperature = cfg.temperature  # 1..10: how often it does things on its own
        self._next_think = time.monotonic() + random.uniform(15, 30)
        self._thinking = False
        self._recent_choices: list[str] = []
        self.last_thought = ""
        self.on_thought: Callable[[str], None] = lambda thought: None
        self.adventure = None  # actions.adventure.Adventure
        self.enter_lounge: Callable[[], bool] = lambda: False
        self.user_activity: Callable[[], str] = lambda: "unknown"
        self.places: Callable[[], list[tuple[str, tuple]]] = lambda: []  # (label, surface key) it could go to
        self.growth = None  # agent.growth.Growth
        self._reflecting = False
        self.last_interaction = time.monotonic()
        self._next_chatter = time.monotonic() + random.uniform(*cfg.idle_chatter)
        self._next_zzz = 0.0
        self._save_at = time.monotonic() + 30

    # -- per frame ----------------------------------------------------------------------

    def update(self, dt: float) -> None:
        now = time.monotonic()
        for event in self.fig.events:
            self._on_event(event)
        self.fig.events.clear()

        state = self.anim.state
        if state in (Anim.WALK, Anim.RUN, Anim.AIR, Anim.PREP, Anim.PLACE):
            activity = "moving"
        elif state == Anim.SLEEP:
            activity = "sleeping"
        elif state in (Anim.SIT, Anim.IDLE):
            activity = "resting"
        else:
            activity = "other"
        self.emotion.update(dt, activity, now - self.last_interaction)

        e = self.emotion
        self.anim.droop = min(1.0, max(0.0, 1.0 - e.energy * 1.6) + 0.3 * e.annoyance)
        self.fig.speed_scale = 0.65 + 0.6 * e.energy

        if state == Anim.SLEEP and now > self._next_zzz and not self.bubble_busy():
            self._next_zzz = now + random.uniform(6, 10)
            self.bubble_say(random.choice(["Zzz...", "zzz", "Zzz... mm... blocks..."]))

        if now > self._next_chatter:
            self._next_chatter = now + random.uniform(*self.cfg.idle_chatter) * temp_scale(self.temperature)
            if (
                now - self.last_interaction > 60
                and state not in (Anim.SLEEP, Anim.GRABBED)
                and not self.bubble_busy()
                and not self.agent.busy
            ):
                self.agent.chatter(self.situation(), self._chatter_text, self._chatter_done)

        if now > self._save_at:
            self._save_at = now + 30
            self.save()

        if (self.growth is not None and not self._reflecting and self.growth.due() and not self.agent.busy
                and not self._thinking):
            asyncio.ensure_future(self._reflect())

        if self.think_enabled and now >= self._next_think and not self._thinking:
            self._next_think = now + random.uniform(*self.cfg.think_gap) * temp_scale(self.temperature)
            if self._can_think(state):
                asyncio.ensure_future(self._think())

        if self.notice_enabled and now >= self._next_peek and not self._peeking:
            self._next_peek = now + random.uniform(*self.cfg.peek_gap)
            target = self._peek_candidate(state)
            if target is not None:
                asyncio.ensure_future(self._peek(target))

    # -- the mind ------------------------------------------------------------------------------------

    def _can_think(self, state: Anim) -> bool:
        busy = (self.agent.busy or self.bubble_busy() or self.in_lounge() or self._peeking
                or (self.actions is not None and self.actions.busy)
                or (self.adventure is not None and self.adventure.running)
                or self._confirm is not None or self._pending_task is not None)
        return not busy and state not in (Anim.SLEEP, Anim.GRABBED, Anim.TUMBLE, Anim.KNOCKED, Anim.RIDE)

    async def _think(self) -> None:
        self._thinking = True
        try:
            idle = win32.user_idle_seconds()
            places = self.places()[:14]
            aware = self.agent.awareness()
            context = {"user": aware or (f"away from the computer for {idle:.0f}s" if idle > 30 else
                                         f"active right now ({self.user_activity()})"),
                       "places": "\n".join(f"[{i}] {label}" for i, (label, _) in enumerate(places)),
                       "personality": self.growth.short() if self.growth is not None else "still forming"}
            choice = await self.agent.think(self.situation(), context, self._recent_choices)
            if choice is None or not self._can_think(self.anim.state):
                return
            self.last_thought = choice["thought"]
            self.on_thought(choice["thought"])
            log.info("thought: %s -> %s", choice["thought"], choice["action"])
            if self.growth is not None:
                self.growth.event(f"I thought '{choice['thought'][:80]}' and chose to {choice['action']}")
            done = self._act_on(choice["action"], idle, choice.get("target", -1), places)
            self._recent_choices = (self._recent_choices + [choice["action"] + ("" if done else " (couldn't)")])[-8:]
            if done and choice["say"] and choice["action"] != "chat" and not self.bubble_busy():
                self.bubble_say(choice["say"])
        except Exception as e:  # the mind must never break anything
            log.warning("thinking failed: %s", e)
        finally:
            self._thinking = False

    def _act_on(self, action: str, idle: float, target: int = -1, places: list | None = None) -> bool:
        if action == "go_to":
            places = places or []
            if not 0 <= target < len(places):
                return False
            self.brain.command_goto(places[target][1], None, build=True)
            return True
        body = {"wander": "explore", "follow_cursor": "follow", "ride_cursor": "ride", "chase_cursor": "chase",
                "flip": "flip", "dance": "dance", "climb": "climb", "build": "build", "sit": "sit", "nap": "sleep",
                "climb_element": "climb_element", "reach_cursor": "reach_cursor", "spar": "fight"}
        if action in body:
            if action == "nap" and self.emotion.energy > 0.6:
                return False
            self.brain.command(body[action])
            return True
        if action in ("look_up_something", "peek_tabs"):
            adv = self.adventure
            if adv is None or not adv.enabled or idle < 30 or (action == "peek_tabs" and not self.notice_enabled):
                return False
            asyncio.ensure_future(adv.play("search" if action == "look_up_something" else "tabs", min_idle=30))
            return True
        if action == "look_at_screen":
            if not self.notice_enabled or idle > 60:
                return False
            target = self.peek_target()
            if target is None or self._peeking:
                return False
            self._asked_about[target.title] = time.monotonic()
            asyncio.ensure_future(self._peek(target))
            return True
        if action == "chat":
            self.agent.chatter(self.situation(), self._chatter_text, self._chatter_done)
            return True
        if action == "lounge":
            return self.enter_lounge()
        return action == "nothing"

    # -- curiosity -------------------------------------------------------------------------------

    def _peek_candidate(self, state: Anim) -> Target | None:
        """Only when the user is actively doing something and the figure is free to be curious."""
        if (self.perception is None or self.agent.busy or self.bubble_busy() or self.in_lounge()
                or (self.actions is not None and self.actions.busy)
                or state in (Anim.SLEEP, Anim.GRABBED, Anim.TUMBLE, Anim.KNOCKED, Anim.FALL)
                or win32.user_idle_seconds() > 30):
            return None
        target = self.peek_target()
        if target is None:
            return None
        last = self._asked_about.get(target.title)
        if last is not None and time.monotonic() - last < 1800:
            return None  # already asked about this recently
        return target

    async def _peek(self, target: Target) -> None:
        """Glance at the window (read-only, local) and ask one curious question about it."""
        self._peeking = True
        try:
            self.brain.look(True)
            try:
                seen = await self.perception.describe(target)
            finally:
                self.brain.look(False)
            if self.agent.busy or self.bubble_busy():
                return  # the user started talking to us meanwhile
            self._asked_about[target.title] = time.monotonic()
            self._recent_peek = (seen, time.monotonic())
            observation = ("You just glanced at what the user is doing on their computer (you peek now and then; "
                           "you don't watch constantly). What you saw:\n" + seen)
            instruction = ("(Ask the user ONE short, friendly, curious question about what they're doing, based on "
                           "what you saw. Under 20 words. Don't list or read out what's on screen.)")
            text = await self.agent.follow_up(self.situation(), observation, instruction, self.bubble_stream)
            if text:
                self.bubble_finish()
                self.chat_add_assistant(text)
                log.info("asked about %s", target.label())
        except Exception as e:  # curiosity must never break anything
            log.warning("peek failed: %s", e)
        finally:
            self._peeking = False

    def _peek_context(self) -> str | None:
        """What we saw on the last peek, for answering the user's reply to our question."""
        if self._recent_peek is None:
            return None
        seen, at = self._recent_peek
        if time.monotonic() - at > 300:
            self._recent_peek = None
            return None
        return ("A few minutes ago you glanced at the user's screen and asked them about it. What you saw then:\n"
                + seen)

    # -- events -------------------------------------------------------------------------------

    def _on_event(self, event: tuple) -> None:
        kind = event[0]
        e = self.emotion
        if kind == "grab":
            was_asleep = self.anim.state == Anim.SLEEP
            e.on_grab()
            if was_asleep:
                e.annoyance = min(1.0, e.annoyance + 0.15)
                self._react("grab_asleep", 1.0)
            else:
                self._react("grab", 0.35)
            self.last_interaction = time.monotonic()
        elif kind == "pet":
            e.on_pet()
            self._grow("sass", -0.006, "the user petted me")
            self._react("pet", 0.8)
            if e.happiness > 0.5:
                self.brain.command("hop")
            self.last_interaction = time.monotonic()
        elif kind == "thrown":
            e.on_thrown(event[1])
            self._grow("boldness", 0.006, "the user threw me across the screen")
            self._grow("sass", 0.004)
            self._react("thrown", 0.7)
        elif kind == "spar":
            self._on_spar(event[1], event[2])
        elif kind == "ride":
            self._react("ride", 0.8)
            self._first("first_ride", "The first time I grabbed the Animator's cursor and rode it around!")
            self._grow("boldness", 0.004, "I rode the user's cursor")
        elif kind == "knocked":
            e.on_knocked()
            self._grow("boldness", -0.006, "I got knocked flat after a throw")
            self._react("knocked", 0.6)

    def _on_spar(self, phase: str, who) -> None:
        short = who.name.removeprefix("The ")
        if phase == "start":
            self.bubble_say(random.choice(who.greet) if who.greet else f"{who.name}?! Let's go!")
            self._first("first_fight", f"My first sparring match - against {who.name}!")
        elif phase == "win":
            self.bubble_say(random.choice(["GG!", f"Too easy, {short}.", "And STAY down!", "W. Easy W.",
                                           f"Better luck next time, {short}!"]))
            self._grow("boldness", 0.006, f"I won a sparring match against {who.name}")
        elif phase == "lose":
            self.bubble_say(random.choice(["Ow! Okay, okay, you win this one.", f"Next time, {short}!",
                                           "L... rematch later.", "I let you win. Obviously."]))
            self._grow("playfulness", 0.004, f"I lost a sparring match to {who.name}")
        elif phase in ("hit", "hurt") and random.random() < 0.25 and not self.bubble_busy():
            self.bubble_say(random.choice(["Hah!", "Take that!", "Hyah!"] if phase == "hit"
                                          else ["Oof!", "Hey!", "Cheap shot!"]), log=False)

    # -- growing up ---------------------------------------------------------------------------------

    def _grow(self, trait: str, delta: float, why: str = "") -> None:
        if self.growth is not None:
            self.growth.nudge(trait, delta, why)

    def _first(self, key: str, text: str) -> None:
        if self.growth is not None and self.growth.first(key, text):
            self.chat_add_note(f"✦ A first: {text}")

    def on_sentiment(self, s: float) -> None:
        """How the user talks to it shapes it: warmth makes it sweeter and chattier, snapping makes it sassier."""
        if s > 0:
            self._grow("sass", -0.008 * s, "the user was kind to me")
            self._grow("chattiness", 0.005 * s)
        elif s < 0:
            self._grow("sass", 0.01 * -s, "the user snapped at me")

    async def _reflect(self) -> None:
        self._reflecting = True
        try:
            await self.agent.reflect()
        except Exception as e:  # growing up must never break anything
            log.warning("reflection failed: %s", e)
        finally:
            self._reflecting = False

    def _react(self, kind: str, chance: float) -> None:
        if self.bubble_busy() or random.random() > chance:
            return
        lines = REACTIONS[kind]
        pool = lines.get("any") or (lines["grumpy"] if self.emotion.annoyance > 0.35 else lines["happy"])
        self.bubble_say(random.choice(pool))

    # -- conversation ------------------------------------------------------------------------------

    def situation(self) -> Situation:
        return Situation(
            activity=ACTIVITY_WORDS.get(self.anim.state, "hanging out"),
            surface=self.surface_name(self.world.surface_for_shape(self.fig.ground_shape)),
        )

    async def converse(
        self,
        text: str,
        on_text: Callable[[str], None],
    ) -> list[str]:
        """Handle one user message. Returns the body actions the reply asked for (already dispatched)."""
        self.last_interaction = time.monotonic()
        if self._confirm is not None and not self._confirm.done():
            yes = bool(YES_WORDS.match(text))
            self._confirm.set_result("approve" if yes else "deny")
            reply = "On it!" if yes else "Okay, I won't."
            self.bubble_say(reply, log=False)  # (on_text puts it in the chat)
            on_text(reply)
            return []
        busy = self.actions is not None and self.actions.busy
        if busy and STOP_WORDS.match(text):
            self.actions.stop("you asked me to stop")
            self.bubble_say("Stopping!", log=False)
            on_text("Stopping!")
            return []
        if self.anim.state == Anim.SLEEP:
            self.brain.cancel()  # wake up to listen
        self.bubble_think()

        if self._awaiting_answer is not None and not busy and not STOP_WORDS.match(text):
            observation, found = self._continue_with_answer(text), None
        else:
            if self._awaiting_answer is not None and STOP_WORDS.match(text):
                self._awaiting_answer = None
            observation, found = await self._perceive(text)

        def stream(t: str) -> None:
            if t:
                self.bubble_stream(t)
            on_text(t)

        reply = await self.agent.reply(
            text, self.situation(), stream, observation=observation, suppress_actions=observation is not None
        )
        self.bubble_finish()
        if (self._pending_task is None and observation is None and not busy and self.actions is not None
                and CLAIMS_ACTION.search(reply.text or "")):
            # It said it's doing something ("typing it now!") but no task was started: really do it.
            log.info("the reply claimed an action with no task running: starting one")
            self._plan_task({"screen": "act", "target": text.strip()[:200], "app": "", "content": ""}, text)
        if self._pending_task is not None:
            goal, payload, target, context = self._pending_task
            self._pending_task = None
            asyncio.ensure_future(self._run_task(goal, payload, target, context))
        elif found is not None and found.ok:
            self.brain.command_point(found.element.center)
        else:
            for action in reply.actions:
                log.info("reply requested action: %s", action)
                self.body_command(action)
        self.last_interaction = time.monotonic()
        return reply.actions

    def _plan_task(self, route: dict, text: str) -> str:
        """Vet an action request before anything happens; queue it to start after the reply."""
        if self.actions is None:
            return "The user asked you to do something in an app, but your hands aren't enabled."
        if self.actions.busy:
            # A follow-up while working ("also make it bold"): fold it into the running task.
            self.actions.add_instruction(text)
            return (f"While you're working on a task, the user added: \"{text}\". You've folded it into what "
                    "you're doing. Acknowledge in a few words.")
        target = self.choose_target(route.get("app", ""))
        if not route.get("app") and WEBBY.search(route.get("target", "")) and (
                target is None or target.app.lower() not in BROWSERS):
            target = self.choose_target("chrome") or self.choose_target("edge") or self.choose_target("firefox") or target
        if target is None:
            return "The user asked you to do something, but there's no app window open to do it in."
        win = WindowInfo(target.hwnd, target.app, win32.class_name(target.hwnd), target.title)
        problem = window_problem(win)
        if problem:
            self.actions.audit.write("refused", app=target.app, title=target.title, goal=route["target"], reason=problem)
            return (f"The user asked you to \"{route['target']}\" in {target.label()}, but you refused: {problem}. "
                    "Politely explain you're not allowed to touch that, for safety.")
        payload = extract_payload(text)
        content = str(route.get("content", "")).strip()
        if payload is None and content:
            payload = Compose(content)  # written by the model first, then pasted verbatim
        self._pending_task = (route["target"], payload, target, self._previous_context(target))
        if isinstance(payload, Compose):
            extra = f" You'll write it yourself first ({content}), then put it in."
        else:
            extra = f" They gave you {len(payload)} characters of text to type exactly." if payload else ""
        how = ("You'll ask in chat before each step." if self.cfg.supervised else
               "Each step runs by itself; only risky clicks like Send or Delete will ask first, in chat.")
        return (f"The user asked you to \"{route['target']}\" in {target.label()}.{extra} You're starting now. "
                f"{how} Pressing Esc stops you. Tell them briefly you're on it. Don't claim it's done yet.")

    def reset_conversation(self) -> None:
        """After the chat is cleared: forget the in-flight conversational state too."""
        self._recent_peek = None
        self._last_task = None
        self._awaiting_answer = None
        self._pending_task = None
        if self._confirm is not None and not self._confirm.done():
            self._confirm.set_result("deny")

    def _previous_context(self, target: Target) -> str:
        """What the last task did, so follow-ups like "now make it bold" make sense."""
        last = self._last_task
        if last is None or time.monotonic() - last["at"] > 600 or last["target"].hwnd != target.hwnd:
            return ""
        steps = "; ".join(last["steps"][-6:])
        return f"just before this, you did \"{last['goal']}\" ({last['status']}: {last['message']}). Steps: {steps}"

    def _continue_with_answer(self, answer: str) -> str:
        """The last task stopped to ask the user something; their reply continues it."""
        q = self._awaiting_answer
        self._awaiting_answer = None
        context = f"you paused to ask \"{q['question']}\" and the user answered: \"{answer}\". Continue the task."
        prior = self._previous_context(q["target"])
        self._pending_task = (q["goal"], q["payload"], q["target"], f"{prior}. {context}" if prior else context)
        return (f"You had asked the user \"{q['question']}\" while doing \"{q['goal']}\"; they answered "
                f"\"{answer}\". You're continuing now. Acknowledge in a few words.")

    async def _run_task(self, goal: str, payload, target: Target, context: str = "") -> None:
        self.before_task()
        self.brain.cancel()
        self.chat_add_note(f"▶ Task: {goal}  ({target.app})")
        lessons = await self.agent.lessons_for(goal, target.app)
        if lessons:
            context = (context + ". " if context else "") + "Lessons from past attempts: " + " | ".join(lessons)
        if isinstance(payload, Compose):
            self.bubble_say("Writing it first...", hold=60)
            written = await self.agent.compose(payload.request)
            if not written.strip():
                self.chat_add_note("■ failed: I couldn't write it (my brain didn't answer).")
                return
            self.chat_add_note(f"✎ Wrote {len(written.splitlines())} lines; putting it in now.")
            self.chat_add_assistant(f"Here's what I'm putting in:\n\n```\n{written}\n```")
            payload = written
        result = await self.actions.run(goal, payload, target, context)
        self.chat_add_note(f"■ {result.status}: {result.message}")
        self._last_task = {"goal": goal, "target": target, "status": result.status, "message": result.message,
                           "steps": result.steps, "at": time.monotonic()}
        asyncio.ensure_future(self.agent.learn(goal, target.app, result.status, result.message, result.steps))
        if result.status == "asked":
            self._awaiting_answer = {"question": result.message, "goal": goal, "payload": payload, "target": target}
        if self.growth is not None:
            self.growth.event(f"I did a task for the user: {goal} ({result.status})")
            if result.status == "done":
                self._first("first_task", f"The first job I did on the computer for the user: {goal}")
        if result.status == "done":
            self.emotion.on_explored()
            self.brain.command("hop")
        await self._follow_up(goal, result)

    async def _follow_up(self, goal: str, result) -> None:
        """Report back in the figure's own voice (and into chat history, so the next message has context)."""
        observation = (f"You just finished working on \"{goal}\". Outcome: {result.status} - {result.message} "
                       f"Steps you took: {'; '.join(result.steps[-8:]) or 'none'}.")
        instruction = ("(Tell the user how the task went in one or two short sentences, in your own voice."
                       + (" Ask them your question." if result.status == "asked" else "") + ")")
        text = await self.agent.follow_up(self.situation(), observation, instruction, self.bubble_stream)
        if text:
            self.bubble_finish()
            self.chat_add_assistant(text)
        else:
            self.bubble_say(result.message, log=False)
            self.chat_add_assistant(result.message)

    async def confirm(self, what: str, why: str, destructive: bool, keep_clear, element_rect) -> str:
        """Ask in chat before a risky step (no popup). The user's next message answers yes/no."""
        loop = asyncio.get_running_loop()
        self._confirm = loop.create_future()
        question = f"Should I {what}? ({why}) Say yes or no."
        self.bubble_say(f"Should I {what}?", hold=self.cfg.approval_timeout, log=False)
        self.chat_add_assistant(question)
        self.open_chat()
        if element_rect is not None:
            self.show_highlight(element_rect, "this one?")
        try:
            return await asyncio.wait_for(asyncio.shield(self._confirm), self.cfg.approval_timeout)
        except asyncio.TimeoutError:
            self.chat_add_note("(no answer, so I didn't do it)")
            return "deny"
        finally:
            if not self._confirm.done():
                self._confirm.cancel()
            self._confirm = None
            self.hide_highlight()

    async def ask_yes_no(self, question: str, timeout: float = 120.0) -> bool:
        """A yes/no question asked in chat (never a popup): the user's next message answers it."""
        if self._confirm is not None and not self._confirm.done():
            return False  # already waiting on another answer
        self._confirm = asyncio.get_running_loop().create_future()
        self.open_chat()
        self.chat_add_assistant(f"{question} (say yes or no)")
        self.bubble_say(question, hold=10, log=False)
        try:
            return await asyncio.wait_for(asyncio.shield(self._confirm), timeout) == "approve"
        except asyncio.TimeoutError:
            return False
        finally:
            if not self._confirm.done():
                self._confirm.cancel()
            self._confirm = None

    def on_step(self, step) -> None:
        if step.say:
            self.bubble_say(step.say)  # progress shows in the speech bubble (no popup)
        if step.point is not None:
            self.brain.command_point(step.point, hold=2.5)

    async def _perceive(self, text: str) -> tuple[str | None, Found | None]:
        """If the message is about the screen, look (read-only) and describe what was seen."""
        if self.perception is None:
            return self._peek_context(), None
        route = await self.agent.route(text)
        if route["screen"] == "none" and COMPUTER_COMMAND.search(text):
            # The router called a clear command "just chatting": do it anyway (never role-play doing it).
            log.info("router said none, but this is a command: treating it as a task")
            route = {"screen": "act", "target": text.strip()[:200], "app": route.get("app", ""), "content": ""}
        if route["screen"] == "none":
            return self._peek_context(), None  # e.g. the user answering our question about their screen
        if route["screen"] == "act":
            return self._plan_task(route, text), None
        target = self.choose_target(route.get("app", ""))
        if target is None:
            return "There's no app window open to look at.", None
        self.brain.look(True)
        try:
            if route["screen"] == "describe":
                desc = await self.perception.describe(target)
                log.info("described %s (%d chars)", target.label(), len(desc))
                return desc, None
            found = await self.perception.locate(route["target"], target)
            log.info("locate %r in %s -> %s via %s %s", route["target"], target.label(),
                     found.element.describe() if found.ok else None, found.method,
                     {k: round(v) for k, v in found.timings.items()})
            if not found.ok:
                return (f"You searched the {target.label()} window for \"{route['target']}\" "
                        f"but couldn't find anything like it."), found
            el = found.element
            cx, cy = el.center
            self.show_highlight(el.rect, el.name[:40] or el.role)
            return (f"In the {target.label()} window you found {el.describe()}, at the "
                    f"{where_on_screen(cx, cy, target.rect)} of the window. It's now outlined in orange on "
                    f"screen and you're heading over to point at it."), found
        except Exception as e:  # perception must never break the conversation
            log.exception("perception failed: %s", e)
            return "You tried to look at the screen but your eyes glitched.", None
        finally:
            self.brain.look(False)

    def _on_agent_action(self, action: str) -> None:
        log.info("conversation requested action: %s", action)
        self.body_command(action)

    def _chatter_text(self, text: str) -> None:
        if text:
            self.bubble_stream(text)

    def _chatter_done(self, text: str) -> None:
        self.bubble_finish()
        self.chat_add_assistant(text)

    # -- persistence -------------------------------------------------------------------------------

    def save(self) -> None:
        self.memory.put("emotion", self.emotion.to_dict())
