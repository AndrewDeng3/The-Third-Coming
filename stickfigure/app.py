"""Entry point: wires the desktop, physics, body, mind, and UI into one qasync (Qt + asyncio) loop."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import time
from pathlib import Path

# Qt coordinates == physical pixels, matching Win32/DWM. Must be set before QApplication.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Whisper model cache on Windows
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import qasync
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from stickfigure.actions.adventure import Adventure
from stickfigure.actions.controller import ActionController
from stickfigure.actions.mischief import Mischief
from stickfigure.agent.agent import Agent, ollama_embedder
from stickfigure.agent.emotion import Emotion
from stickfigure.agent.memory import Memory
from stickfigure.agent.ollama import Ollama, OllamaError
from stickfigure.companion import Companion
from stickfigure.config import CONFIG, save_settings
from stickfigure.figure.animator import Animator
from stickfigure.figure.brain import Brain
from stickfigure.figure.controller import Figure
from stickfigure.overlay.block_window import BlockViews
from stickfigure.overlay.debug_overlay import DebugOverlay
from stickfigure.overlay.figure_window import FigureWindow
from stickfigure.overlay.highlight import Highlight
from stickfigure.perception.perception import Perception, Target, choose_target
from stickfigure.perception.uia import UIAReader
from stickfigure.overlay.render import COLORS, draw_figure
from stickfigure.overlay.speech_bubble import SpeechBubble
from stickfigure.safety.killswitch import KillSwitch
from stickfigure.figure.controller import Activity
from stickfigure.safety.policy import WindowInfo, window_problem
from stickfigure import firstrun
from stickfigure.ui.build_mode import BuildMode
from stickfigure.ui.setup_window import SetupWindow
from stickfigure.ui.chat_window import ChatWindow
from stickfigure.ui.lounge import LoungeView
from stickfigure.ui.markdown import plain as md_plain
from stickfigure.ui.settings import SettingsPanel
from stickfigure.voice.stt import Listener
from stickfigure.voice.tts import Speaker
from stickfigure.win.hotkeys import VK_SPACE, Hotkeys
from stickfigure.win import win32
from stickfigure.win.tracker import WindowTracker
from stickfigure.world.blocks import BlockManager
from stickfigure.world.elements import pick_element_platforms
from stickfigure.world.physics import World

log = logging.getLogger("stickfigure")


def _app_icon(anim: Animator) -> QIcon:
    """The app icon (packaging/third_coming.ico; bundled next to the code in the packaged app)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    ico = base / "packaging" / "third_coming.ico"
    return QIcon(str(ico)) if ico.exists() else _tray_icon(anim)


def _tray_icon(anim: Animator) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    scale = 56 / anim.P.height
    p.scale(scale, scale)
    draw_figure(p, anim._idle(), (32 / scale, 32 / scale), anim.P, COLORS["Orange"], 9)
    p.end()
    return QIcon(pm)


class StickFigureApp:
    def __init__(self, qapp: QApplication):
        self.qapp = qapp
        self.cfg = CONFIG
        self.ui_scale = qapp.primaryScreen().logicalDotsPerInch() / 96

        # Desktop + body
        self.tracker = WindowTracker(poll_interval=self.cfg.tracker_poll_interval)
        self.world = World()
        self.tracker.update()
        self.world.sync(self.tracker.snapshot, None)
        self.fig = Figure(self.world)
        self.anim = Animator(self.fig)
        self.blocks = BlockManager(self.world)
        self.block_views = BlockViews(self.blocks)

        # Mind
        self.ollama = Ollama(self.cfg.ollama_url)
        self.memory = Memory(Path(self.cfg.data_dir) / "memory.db", ollama_embedder(self.ollama, self.cfg.embed_model))
        self.emotion = Emotion.from_dict(self.memory.get("emotion"))
        self.agent = Agent(self.ollama, self.memory, self.emotion)
        # Permanent structures persist across restarts.
        self.blocks.on_persist = lambda data: self.memory.put("permanent_blocks", data)
        restored = self.blocks.restore(self.memory.get("permanent_blocks", []),
                                       lambda x, top: self.world.on_screen(x, top, margin=0))
        if restored:
            log.info("restored %d permanent blocks", restored)
        self.brain = Brain(self.fig, self.world, self.blocks, self.emotion)
        self.companion = Companion(
            self.fig, self.brain, self.anim, self.world, self.agent, self.memory, self.emotion, self._surface_name
        )
        self.perception = Perception(self.ollama)
        # A separate reader (own thread) for the standable-ledge scanner, so it never delays a task's eyes.
        self.element_reader = UIAReader(max_elements=800)
        self.companion.perception = self.perception
        self.companion.choose_target = self._perception_target

        # UI
        self.menu = self._build_menu()
        self.fig_window = FigureWindow(self.anim, lambda: self.menu.popup(QCursor.pos()), self.open_chat)
        self.bubble = SpeechBubble(round(15 * self.ui_scale), round(300 * self.ui_scale))
        self.chat = ChatWindow(self.agent.name, self.ui_scale)
        self.chat.submitted.connect(lambda text: asyncio.ensure_future(self._on_chat(text)))
        self.chat.moved.connect(self.tracker.mark_dirty)  # our own windows don't fire WinEvents
        self.debug = DebugOverlay(self.world, self._stats, lambda: self.brain.debug_path)
        self.highlight = Highlight(self.ui_scale)
        self.companion.show_highlight = self.highlight.show_rect

        # Hands (supervised actions)
        self.actions = ActionController(self.perception, self.ollama)
        # No popup: progress shows in the speech bubble; risky steps are confirmed in chat.
        self.actions.ask = self.companion.confirm
        self.actions.idle = self._task_idle
        self.companion.open_chat = self.open_chat
        self.companion.hide_highlight = self.highlight.hide
        self.actions.on_status = self.companion.on_step
        self.actions.before_input = lambda: self.fig_window.set_click_through(True)
        self.actions.after_input = lambda: self.fig_window.set_click_through(False)
        self.actions.on_emergency = self.emergency_stop
        self.actions.find_window = self._find_window
        self.actions.list_windows = lambda: [t.label() for t in self._other_windows()]
        self.companion.actions = self.actions
        self.companion.chat_add_note = self.chat.add_note

        # Pet mischief (harmless, unprompted: cursor tugs, a little scrolling, hovering)
        self.mischief = Mischief(self.perception, self.actions.audit, self.emotion)
        self.mischief.can_play = lambda: (not self.actions.busy and not self._hidden_for_fullscreen
                                          and not self.fig.grabbed and not self.brain.sleeping
                                          and not self._in_lounge)
        self.mischief.say = lambda text: None if self.bubble.busy else self._say(text)
        self.mischief.point = lambda pt: self.brain.command_point(pt, hold=2.0)
        self.mischief.figure_x = lambda: self.fig.body.position.x
        self.mischief.before_input = lambda: self.fig_window.set_click_through(True)
        self.mischief.after_input = lambda: self.fig_window.set_click_through(False)
        # Idle adventures: look something up in a new tab, or peek at the user's browser tabs.
        self.adventure = Adventure(self.perception, self.ollama, self.actions.audit, self.emotion)
        self.adventure.can_play = lambda: self.mischief.can_play() and not self.mischief.running
        self.adventure.can_peek_tabs = lambda: self.companion.notice_enabled
        self.adventure.say = lambda text: None if self.bubble.busy else self._say(text)
        self.adventure.look = self.brain.look
        self.adventure.interests = lambda: [f.text for f in self.memory.recent_facts(8)]
        self.adventure.comment = self._adventure_comment
        self.adventure.before_input = lambda: self.fig_window.set_click_through(True)
        self.adventure.after_input = lambda: self.fig_window.set_click_through(False)
        self.mischief.can_play = (lambda base=self.mischief.can_play: base() and not self.adventure.running)
        self.companion.before_task = lambda: (self.mischief.stop(), self.adventure.stop(), self._exit_lounge("task"))
        self.companion.adventure = self.adventure
        self.companion.enter_lounge = self._mind_lounge
        self.companion.user_activity = self._user_activity
        self.companion.on_thought = lambda thought: self._update_mood_line()
        # Voice: everything the figure says in its bubble is also spoken (streamed sentence by sentence).
        self.speaker = Speaker()
        self.listener = Listener()
        self._speech = None  # SpeechStream for the reply currently streaming
        self._spoken = ""
        self._color = self.cfg.color
        self.fig_window.set_color(self.cfg.color)
        c = self.companion
        c.bubble_say = self._say
        c.bubble_think = self._think
        c.bubble_stream = self._stream
        c.bubble_finish = self._finish_speech
        c.bubble_busy = lambda: self.bubble.busy
        c.chat_add_assistant = self.chat.add_assistant
        self.chat.mic_clicked.connect(self.toggle_listen)
        self.chat.clear_clicked.connect(self.clear_chat)

        # Lounge: the chat window becomes a cozy room when the conversation goes quiet. The figure lives
        # its own little life in there, answers chat in a text box, and leaves when it gets bored.
        self.lounge = LoungeView(self.emotion, lambda: self.fig_window.color, lambda: self.speaker.level,
                                 self.agent.name)
        self.lounge.left.connect(lambda: self._exit_lounge("bored"))
        self.chat.attach_lounge(self.lounge)
        self.chat.activity.connect(self._chat_activity)
        self._lounge_cooldown_until = 0.0
        c.body_command = self._body_command
        self.chat.hidden.connect(lambda: self._exit_lounge("chat closed"))
        self.chat.settings_clicked.connect(self.open_settings)
        self.chat.lounge_toggled.connect(self._lounge_button)
        self.chat.build_clicked.connect(self.open_build_mode)
        self.build_mode: BuildMode | None = None
        self._in_lounge = False
        self._after_lounge: str | None = None  # a body action to do once it has walked out of the lounge
        self._brain_was_enabled = True
        self._last_chat_activity = time.monotonic()
        c.in_lounge = lambda: self._in_lounge
        c.peek_target = self._peek_target

        self.killswitch = KillSwitch(self.emergency_stop)
        qapp.installNativeEventFilter(self.killswitch)
        self.killswitch.register()
        self.hotkeys = Hotkeys()
        qapp.installNativeEventFilter(self.hotkeys)

        self.setup_window: SetupWindow | None = None
        self.tray = QSystemTrayIcon(_tray_icon(self.anim))
        qapp.setWindowIcon(_app_icon(self.anim))
        self.tray.setToolTip(f"{self.agent.name} (double-click to chat, Ctrl+Alt+Pause to quit)")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)

        self._timer = QTimer()
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._mood_timer = QTimer()
        self._mood_timer.timeout.connect(self._update_mood_line)
        self._last = time.perf_counter()
        self._acc = 0.0
        self._paused = False
        self._quitting = False
        self._hidden_for_fullscreen = False
        self._riding_click_through = False
        self._step_ms = 0.0
        self._frame_ms = 0.0
        self._debug_next = 0.0

    # -- menu ------------------------------------------------------------------------------

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        self.act_chat = QAction(f"Chat with {self.agent.name}…", menu)
        self.act_chat.triggered.connect(self.open_chat)
        menu.addAction(self.act_chat)
        self.act_talk = QAction("Talk", menu)
        self.act_talk.triggered.connect(self.toggle_listen)
        menu.addAction(self.act_talk)
        self.act_voice = QAction("Speak replies", menu, checkable=True, checked=self.cfg.voice_enabled)
        self.act_voice.toggled.connect(self._set_voice)
        menu.addAction(self.act_voice)
        wander = QAction("Wander", menu, checkable=True, checked=True)
        wander.toggled.connect(lambda on: setattr(self.brain, "enabled", on))
        menu.addAction(wander)
        self.act_mischief = QAction("Mischief (tug cursor / scroll when I'm idle)", menu, checkable=True,
                                    checked=self.cfg.mischief)
        self.act_mischief.toggled.connect(lambda on: setattr(self.mischief, "enabled", on))
        menu.addAction(self.act_mischief)
        build = QAction("Build mode (place blocks)…", menu)
        build.triggered.connect(self.open_build_mode)
        menu.addAction(build)
        self.act_adventure = QAction("Adventures (web / tabs when I'm away)", menu, checkable=True,
                                     checked=self.cfg.adventures)
        self.act_adventure.toggled.connect(lambda on: setattr(self.adventure, "enabled", on))
        menu.addAction(self.act_adventure)
        settings = QAction("Settings…", menu)
        settings.triggered.connect(self.open_settings)
        menu.addAction(settings)

        do = menu.addMenu("Do")
        for label, action in (("Sit down", "sit"), ("Wave", "wave"), ("Dance", "dance"),
                              ("Climb to highest window", "climb"), ("Take a nap", "sleep"),
                              ("Ride my cursor", "ride"), ("Follow my cursor", "follow"), ("Backflip", "flip"),
                              ("Chase my cursor", "chase")):
            a = QAction(label, do)
            a.triggered.connect(lambda _=False, act=action: self.brain.command(act))
            do.addAction(a)
        for label, action in (("Build something", "build"), ("Knock something down", "demolish")):
            a = QAction(label, do)
            a.triggered.connect(lambda _=False, act=action: self.brain.command(act))
            do.addAction(a)
        do.addSeparator()
        clear = QAction("Clear staircase blocks", do)
        clear.triggered.connect(self.blocks.clear_temporary)
        do.addAction(clear)
        clear_all = QAction("Clear ALL blocks (incl. permanent)…", do)
        clear_all.triggered.connect(self._clear_all_blocks)
        do.addAction(clear_all)

        stop_task = QAction("Stop current task  (Ctrl+Alt+Pause)", menu)
        stop_task.triggered.connect(lambda: self.actions.stop("you pressed Stop"))
        menu.addAction(stop_task)
        setup = QAction("Check requirements (Ollama & models)…", menu)
        setup.triggered.connect(lambda: self.open_setup())
        menu.addAction(setup)
        audit = QAction("Open action log", menu)
        audit.triggered.connect(lambda: os.startfile(self.actions.audit.path) if self.actions.audit.path.exists() else None)
        menu.addAction(audit)

        mem = menu.addMenu("Memory")
        show = QAction("What do you remember?", mem)
        show.triggered.connect(self._show_memories)
        clear_chat = QAction("Clear chat…", mem)
        clear_chat.triggered.connect(self.clear_chat)
        forget = QAction("Forget everything…", mem)
        forget.triggered.connect(self._forget_everything)
        mem.addAction(show)
        mem.addAction(clear_chat)
        mem.addAction(forget)

        menu.addSeparator()
        self.act_pause = QAction("Pause physics", menu, checkable=True)
        self.act_pause.toggled.connect(lambda on: setattr(self, "_paused", on))
        self.act_debug = QAction("Show platforms (debug)", menu, checkable=True)
        self.act_debug.toggled.connect(self._toggle_debug)
        respawn = QAction("Respawn", menu)
        respawn.triggered.connect(self._respawn)
        quit_ = QAction("Quit", menu)
        quit_.triggered.connect(self.quit)
        for a in (self.act_pause, self.act_debug, respawn):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(quit_)
        return menu

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.open_chat()

    def _respawn(self) -> None:
        self.brain.cancel()
        self.fig.respawn()

    # -- chat ------------------------------------------------------------------------------

    def open_chat(self) -> None:
        if not self.chat.isVisible():
            self.chat.load_history(self.memory.recent_messages(40))
            self._update_mood_line()
        x, y = self.fig.body.position
        mon = next((m.work for m in self.world.monitors if m.work.left <= x < m.work.right), self.world.monitors[0].work)
        self.chat.open_near(x, y - self.cfg.figure_height, (mon.left, mon.top, mon.right, mon.bottom))

    async def _on_chat(self, text: str) -> None:
        self._chat_activity()
        self.chat.add_user(text)
        self.chat.start_assistant()
        try:
            await self.companion.converse(text, self.chat.update_assistant)
        finally:
            self.chat.finish_assistant()
            self._update_mood_line()

    def _update_mood_line(self) -> None:
        e = self.emotion
        thought = self.companion.last_thought
        self.chat.set_mood(
            f"{self.agent.name} is {e.label()}  ·  energy {e.energy:.0%}  ·  curiosity {e.curiosity:.0%}"
            + (f"  ·  💭 {thought}" if thought else "")
        )

    def _mind_lounge(self) -> bool:
        if not self.chat.isVisible() or self._in_lounge:
            return False
        self._last_chat_activity = time.monotonic() - self.cfg.lounge_after
        self._maybe_enter_lounge(force=True)
        return self._in_lounge

    def _user_activity(self) -> str:
        t = self._peek_target()
        return f"using {t.label()}" if t is not None else "using the computer"

    def _show_memories(self) -> None:
        facts = self.memory.all_facts()
        self.open_chat()
        if facts:
            self.chat.add_note("Things I remember:\n" + "\n".join(f"• {f.text}" for f in facts))
        else:
            self.chat.add_note("I don't remember anything about you yet.")

    def _clear_all_blocks(self) -> None:
        asyncio.ensure_future(self._clear_all_blocks_flow())

    async def _clear_all_blocks_flow(self) -> None:
        n = len(self.blocks.permanent) + len(self.blocks._stashed or [])
        if await self.companion.ask_yes_no(
                f"Clear every block, including {n} permanent one{'s' if n != 1 else ''} I built?"):
            self.blocks._stashed = None
            self.blocks.clear_all()
            self.memory.put("permanent_blocks", [])
            self.chat.add_note("All blocks cleared.")

    def clear_chat(self) -> None:
        asyncio.ensure_future(self._clear_chat_flow())

    async def _clear_chat_flow(self) -> None:
        if not await self.companion.ask_yes_no(
                "Erase our conversation? I'll still remember things about you, just not the chat."):
            return
        self.memory.clear_messages()
        self.companion.reset_conversation()
        self.chat.load_history([])
        self._say("Fresh start!")

    def _forget_everything(self) -> None:
        asyncio.ensure_future(self._forget_everything_flow())

    async def _forget_everything_flow(self) -> None:
        if not await self.companion.ask_yes_no(
                "Really forget EVERYTHING - our chats, all I know about you, and my mood?"):
            return
        self.memory.forget_everything()
        self.emotion.__init__()
        self.chat.load_history([])
        self.bubble.say("Huh? Who are you? ...Hi!")

    # -- actions ------------------------------------------------------------------------------

    def _task_idle(self) -> None:
        self.highlight.hide()
        self.fig_window.set_click_through(False)

    # -- lounge --------------------------------------------------------------------------------

    def _chat_activity(self) -> None:
        """Typing/sending restarts the quiet timer. In the lounge, chatting doesn't pull the figure out:
        it answers in the lounge's text box."""
        self._last_chat_activity = time.monotonic()

    def _body_command(self, action: str) -> None:
        if not self._in_lounge:
            self.brain.command(action)
        elif not self.lounge.life.do(action):
            # Needs the desktop (come here, climb, build...): walk out of the lounge first.
            self._after_lounge = action
            self.lounge.leave()

    def _maybe_enter_lounge(self, force: bool = False) -> None:
        if (self._in_lounge or not self.chat.isVisible() or self.chat.in_settings
                or time.monotonic() - self._last_chat_activity < self.cfg.lounge_after
                or (not force and time.monotonic() < self._lounge_cooldown_until)):
            return
        f = self.fig
        blockers = {"task": self.actions.busy, "talking": self.agent.busy, "listening": self.listener.listening,
                    "prank": self.mischief.running, "held": f.grabbed, "fullscreen": self._hidden_for_fullscreen}
        if not force:
            blockers.update({"in the air": not f.grounded, "knocked over": f.knocked > 0})
        why = [k for k, v in blockers.items() if v]
        if why:
            if force or int(time.monotonic()) % 30 == 0:
                log.info("not lounging yet: %s", ", ".join(why))
            return
        self._in_lounge = True
        self._after_lounge = None
        self.brain.cancel()
        self._brain_was_enabled = self.brain.enabled
        self.brain.enabled = False
        f.activity = Activity.SIT
        self.fig_window.hide()
        self.bubble.hide()
        stashed = self.blocks.stash()  # tidy the desktop; permanent structures come back when it does
        self.lounge.name = self.agent.name
        self.lounge.enter()
        self.chat.set_lounge(True)
        log.info("lounging (put away %d permanent blocks)", stashed)

    def _exit_lounge(self, reason: str) -> None:
        if not self._in_lounge:
            return
        self._in_lounge = False
        self.chat.set_lounge(False)
        self.fig.activity = Activity.NONE
        self.brain.enabled = self._brain_was_enabled
        back = self.blocks.unstash(lambda x, top: self.world.on_screen(x, top, margin=0))
        if back:
            log.info("put back %d permanent blocks", back)
        # Hop out onto the top of the chat window.
        r = win32.frame_bounds(int(self.chat.winId()))
        if r is not None:
            hx, hy = self.fig.half_extents
            self.fig.body.position = ((r.left + r.right) / 2, r.top - hy - 10)
            self.fig.body.velocity = (0, 0)
            self.world.space.reindex_shapes_for_body(self.fig.body)
        self.fig_window.show()
        if reason == "user":
            self._say(random.choice(["I'm back!", "Oh, hi!", "Coming!", "Break's over!"]))
        elif reason == "bored":
            # It chose to leave: don't drag it straight back in, and let it go do something.
            self._lounge_cooldown_until = time.monotonic() + 600
            self._last_chat_activity = time.monotonic()
            action = self._after_lounge
            if action:
                self.brain.command(action)
            else:
                self._say(random.choice(["Okay, break's over!", "I'm bored, adventure time!",
                                         "Time to stretch my legs out here.", "What's going on out here?"]))
                self.brain.command(random.choice(["climb", "hop", "dance", "come"]))
        self._after_lounge = None
        log.info("left the lounge (%s)", reason)

    async def _adventure_comment(self, observation: str, instruction: str) -> None:
        if self.agent.busy or self.bubble.busy:
            return
        text = await self.agent.follow_up(self.companion.situation(), observation, instruction, self._stream)
        if text:
            self._finish_speech()
            self.chat.add_assistant(text)

    async def _scan_elements(self) -> None:
        if self._scanning or self._paused:
            return
        if self._scan_skip > 0:  # the last read was slow (a huge page): give the app a breather
            self._scan_skip -= 1
            return
        fg = int(win32.user32.GetForegroundWindow() or 0)
        if (not fg or win32.window_pid(fg) == os.getpid() or self._in_lounge or self._hidden_for_fullscreen
                or self.actions.busy or fg not in self.world.platforms):
            # Keep the current ones if the figure is standing on one; otherwise drop them.
            key = self.world.surface_for_shape(self.fig.ground_shape)
            if self.world.element_shapes and not (key and key[0] == "elem") and fg != self.world.element_owner:
                self.world.clear_elements()
            return
        rect = win32.frame_bounds(fg)
        info = WindowInfo(fg, win32.process_name(fg), win32.class_name(fg), win32.window_title(fg))
        if rect is None or window_problem(info):
            return
        self._scanning = True
        try:
            t0 = time.perf_counter()
            elements = await asyncio.wait_for(asyncio.wrap_future(self.element_reader.elements(fg, rect)), 4)
            took = time.perf_counter() - t0
            self._scan_skip = 0 if took < 0.4 else min(15, int(took * 4))
            if win32.user32.GetForegroundWindow() != fg:
                return
            rects = pick_element_platforms(elements, rect)
            key = self.world.surface_for_shape(self.fig.ground_shape)
            if key and key[0] == "elem" and self.fig.grounded and fg == self.world.element_owner:
                # Standing on one: only refresh if the one underfoot survives (else it'd drop mid-stride).
                fx, fy = self.fig.feet
                if not any(r.left <= fx <= r.right and abs(r.top - fy) < 8 for r in rects):
                    return
            if self.world.set_element_platforms(fg, rects):
                log.debug("element platforms: %d in %s", len(rects), info.process)
        except (asyncio.TimeoutError, OSError, Exception) as e:  # noqa: BLE001 - optional nicety
            log.debug("element scan failed: %s", e)
        finally:
            self._scanning = False

    def _peek_target(self) -> Target | None:
        """The window the user is working in, if it's okay to glance at (never sensitive ones)."""
        fg = int(win32.user32.GetForegroundWindow() or 0)
        if not fg or win32.window_pid(fg) == os.getpid() or self._hidden_for_fullscreen:
            return None
        info = WindowInfo(fg, win32.process_name(fg), win32.class_name(fg), win32.window_title(fg))
        rect = win32.frame_bounds(fg)
        if window_problem(info) or rect is None or rect.width < 300 or rect.height < 200 or not info.title:
            return None
        return Target(fg, info.title, info.process, rect)

    # -- voice ---------------------------------------------------------------------------------

    def _say(self, text: str, hold: float | None = None) -> None:
        if self._in_lounge:
            self.lounge.say(md_plain(text))
        self.bubble.say(self._bubble_text(text), hold)
        if not text.lower().startswith("zzz"):  # snoring stays in the bubble
            self.speaker.say(md_plain(text)[:700])

    def _think(self) -> None:
        self.speaker.stop()  # a new reply is coming: don't talk over it
        self._speech = self.speaker.stream()
        self.bubble.think()

    @staticmethod
    def _bubble_text(text: str) -> str:
        """Markdown flattened, and long answers trimmed: the full reply is in the chat window."""
        plain = md_plain(text)
        if len(plain) > 240:
            plain = plain[:220].rsplit(" ", 1)[0] + "… (more in chat)"
        return plain

    def _stream(self, text: str) -> None:
        # (In the lounge the chat window mirrors replies into the lounge's text box.)
        self.bubble.stream(self._bubble_text(text))
        if self._speech is None:
            self._speech = self.speaker.stream()
        self._spoken = md_plain(text)[:700]  # read out the gist, not a whole program
        self._speech.feed(self._spoken)

    def _finish_speech(self) -> None:
        self.bubble.finish()
        if self._speech is not None:
            self._speech.finish(self._spoken)
            self._speech = None

    def _set_voice(self, on: bool) -> None:
        self.speaker.enabled = on
        if not on:
            self.speaker.stop()

    def toggle_listen(self) -> None:
        """Push-to-talk: press to start listening (it stops by itself when you pause), press again to finish."""
        if self.listener.listening:
            self.listener.stop()
            return
        self._exit_lounge("user")
        self.speaker.stop()
        loop = asyncio.get_event_loop()
        self.chat.set_listening(True)
        self.bubble.say("Listening…", hold=self.cfg.listen_max)
        self.listener.start(
            on_text=lambda text: loop.call_soon_threadsafe(self._heard, text),
            on_state=lambda state: loop.call_soon_threadsafe(self._listen_state, state),
        )

    def _listen_state(self, state: str) -> None:
        if state == "thinking":
            self.chat.set_listening(False)
            self.bubble.think()
        elif state == "cancelled":
            self.chat.set_listening(False)
            self.bubble.say("…never mind.", hold=2)
        elif state == "error":
            self.chat.set_listening(False)
            self.bubble.say("I can't hear anything. Is a microphone connected?", hold=5)

    def _heard(self, text: str) -> None:
        self.chat.set_listening(False)
        if not text.strip():
            self._say("Hmm? I didn't catch that.")
            return
        log.info("heard: %r (%.0f ms to transcribe)", text, self.listener.last_transcribe_ms)
        asyncio.ensure_future(self._on_chat(text))

    # -- settings ----------------------------------------------------------------------------

    def _set_names(self, buddy: str, user: str) -> None:
        self.agent.name, self.agent.user_name = buddy, user
        self.lounge.name = buddy
        self.chat.set_name(buddy)
        self.act_chat.setText(f"Chat with {buddy}…")
        self.tray.setToolTip(f"{buddy} (double-click to chat, Ctrl+Alt+Pause to quit)")

    def _current_settings(self) -> dict:
        return {
            "buddy_name": self.agent.name, "user_name": self.agent.user_name,
            "color": self._color, "voice_enabled": self.speaker.enabled,
            "tts_voice": self.speaker.voice, "tts_speed": self.speaker.speed, "tts_volume": self.speaker.volume,
            "stt_model": self.cfg.stt_model, "mischief": self.mischief.enabled, "supervised": self.cfg.supervised,
            "notice_activity": self.companion.notice_enabled,
            "adventures": self.adventure.enabled,
            "chat_model": self.cfg.chat_model,
        }

    def open_settings(self) -> None:
        def preview(voice: str, speed: float) -> None:
            self.speaker.stop()
            self.speaker.say(f"Hi! I'm {self.agent.name}.", voice=voice, speed=speed, force=True)

        self._exit_lounge("settings")
        self.open_chat()
        self.chat.show_settings(SettingsPanel(self._current_settings(), self.ui_scale, self._apply_settings, preview))

    def open_build_mode(self) -> None:
        """Place/remove blocks yourself on the monitor the figure is on."""
        if self.build_mode is not None:
            self.build_mode.raise_()
            self.build_mode.activateWindow()
            return
        self._exit_lounge("build mode")
        x, y = self.fig.body.position
        mon = next((m.work for m in self.world.monitors if m.work.left <= x < m.work.right), self.world.monitors[0].work)
        self.build_mode = BuildMode(self.blocks, mon, self.ui_scale)
        self.tracker.ignore(self.build_mode.winId())  # not a window to stand on

        def closed() -> None:
            self.build_mode = None
            self._say(random.choice(["Ooh, nice build!", "I'm gonna climb all over that.", "Neat! Is that for me?"]))

        self.build_mode.closed.connect(closed)
        self.build_mode.show()
        self._say("Build mode! Left-click to place, right-click to remove, Esc when you're done.")

    def _lounge_button(self, on: bool) -> None:
        if on:
            self._last_chat_activity -= self.cfg.lounge_after  # go now
            self._maybe_enter_lounge(force=True)
            self.chat._sync_header()
        else:
            self._chat_activity()
            self._exit_lounge("user")

    def _apply_settings(self, values: dict, needs_restart: bool) -> None:
        save_settings(values)
        self._set_names(self.agent.name, values["user_name"])  # its own name is fixed: The Third Coming
        self._color = values["color"]
        self.fig_window.set_color(values["color"])
        self.speaker.voice, self.speaker.speed = values["tts_voice"], values["tts_speed"]
        self.speaker.volume = values["tts_volume"]
        self.act_voice.setChecked(values["voice_enabled"])
        self.act_mischief.setChecked(values["mischief"])
        self.companion.notice_enabled = values["notice_activity"]
        self.act_adventure.setChecked(values["adventures"])
        if needs_restart:
            self.bubble.say("Some of those changes kick in after a restart!", hold=4)

    def emergency_stop(self) -> None:
        """Kill switch: stop any task (and quit if nothing was running, as it always has)."""
        stopped = self.actions.stop("emergency stop")
        if self.speaker.speaking or self.listener.listening:
            self.speaker.stop()
            self.listener.cancel()
            stopped = True
        if self.mischief.running:
            self.mischief.stop()
            stopped = True
        if self.adventure.running:
            self.adventure.stop()
            stopped = True
        if stopped:
            log.warning("Emergency stop")
            self.bubble.say("Stopped!", hold=3)
        else:
            self.quit()

    def _perception_target(self, hint: str) -> Target | None:
        own = os.getpid()
        windows = [w for w in self.tracker.snapshot.windows if win32.window_pid(w.hwnd) != own]
        return choose_target(windows, self.tracker.last_foreground, hint)

    def _other_windows(self) -> list[Target]:
        own = os.getpid()
        out = []
        for w in self.tracker.snapshot.windows:
            if w.title and win32.window_pid(w.hwnd) != own:
                out.append(Target(w.hwnd, w.title, win32.process_name(w.hwnd), w.rect))
        return out

    def _find_window(self, hint: str) -> Target | None:
        """An open window whose title/app matches `hint` (no fallback: None if nothing matches)."""
        words = [w for w in hint.lower().replace(".exe", "").split() if len(w) > 1]
        for t in self._other_windows():
            hay = f"{t.title} {t.app}".lower()
            if words and all(word in hay for word in words):
                return t
        return None

    def _surface_name(self, key: tuple | None) -> str:
        if key is None:
            return "nothing (you're in the air)"
        if key[0] == "floor":
            return "the taskbar at the bottom of the screen"
        if key[0] == "block":
            return "a block you built"
        if key[0] == "elem":
            return "a text box / button inside the window in front"
        win = next((w for w in self.tracker.snapshot.windows if w.hwnd == key[1]), None)
        if win is None or not win.title:
            return "the top of a window"
        if win.hwnd == int(self.chat.winId()):
            return "the top of your chat window with the user"
        title = win.title if len(win.title) < 60 else win.title[:57] + "..."
        return f"the top of the '{title}' window"

    # -- lifecycle ---------------------------------------------------------------------------------

    def start(self) -> None:
        if not win32.is_per_monitor_dpi_aware():
            log.warning("Process is not per-monitor DPI aware; window coordinates may be scaled")
        self.tracker.ignore(self.fig_window.winId())
        self.tracker.start()
        self.fig_window.show()
        self.tray.show()
        self._timer.start(round(1000 / self.cfg.frame_hz))
        # Stand on text boxes, buttons, images... of the window in front (re-read every couple of seconds).
        self._element_timer = QTimer()
        self._element_timer.timeout.connect(lambda: asyncio.ensure_future(self._scan_elements()))
        self._element_timer.start(2000)
        self._scanning = False
        self._scan_skip = 0
        self._mood_timer.start(2000)
        # Push-to-talk: first free combination wins (Ctrl+Alt+Space is often taken by other apps).
        self.talk_hotkey = None
        for name, vk in (("Ctrl+Alt+Space", VK_SPACE), ("Ctrl+Alt+V", 0x56), ("Ctrl+Alt+T", 0x54)):
            if self.hotkeys.add(win32.MOD_CONTROL | win32.MOD_ALT, vk, self.toggle_listen, name):
                self.talk_hotkey = name
                break
        label = self.talk_hotkey or "no hotkey free - use the 🎤 button"
        self.act_talk.setText(f"Talk  ({label})")
        self.chat.mic.setToolTip(f"Talk ({label})")
        log.info("push-to-talk: %s", label)
        self.speaker.warm_up()  # downloads Kokoro on first run
        self.listener.warm_up()  # downloads Whisper on first run
        asyncio.ensure_future(self._first_run())
        log.info("Running. %d platforms, %d monitors.", len(self.world.platforms), len(self.world.monitors))

    async def _first_run(self) -> None:
        """Make sure Ollama and the models are there (offering to install them), then load the models."""
        try:
            status = await asyncio.to_thread(firstrun.check, self.cfg)
        except Exception:  # noqa: BLE001 - a failed check just means "try warming up anyway"
            log.exception("requirements check failed")
            status = None
        if status is None or status.ready:
            await self._warm_up()
            return
        if status.ollama_installed and not status.ollama_running and not status.missing_models:
            if await asyncio.to_thread(firstrun.start_ollama, self.cfg.ollama_url):  # just needed starting
                await self._warm_up()
                return
        log.info("setup needed: ollama installed=%s running=%s missing=%s", status.ollama_installed,
                 status.ollama_running, status.missing_models)
        self.open_setup(status)
        self.bubble.say("I need my brain installed first! Check the setup window.", hold=8)

    def open_setup(self, status: "firstrun.Status | None" = None) -> None:
        if self.setup_window is not None:
            self.setup_window.raise_()
            self.setup_window.activateWindow()
            return
        self.setup_window = SetupWindow(self.ui_scale, _app_icon(self.anim))

        def done(ready: bool) -> None:
            self.setup_window = None
            if ready:
                self._say("Ahh, I can think again! Hi!")
                asyncio.ensure_future(self._warm_up())

        self.setup_window.finished.connect(done)
        self.setup_window.show()
        if status is not None:
            self.setup_window.show_status(status)
        else:
            async def refresh() -> None:
                st = await asyncio.to_thread(firstrun.check, self.cfg)
                if self.setup_window is not None:
                    self.setup_window.show_status(st)
            asyncio.ensure_future(refresh())

    async def _warm_up(self) -> None:
        try:
            await self.agent.warm_up()
            log.info("Models loaded (%s, %s, %s)", self.cfg.chat_model, self.cfg.embed_model, self.cfg.extract_model)
        except OllamaError as e:
            log.warning("Ollama unavailable: %s", e)
            self.bubble.say("My brain's offline... is Ollama running?", hold=6)
        except Exception:  # never let a startup failure vanish silently inside a task
            log.exception("model warm-up failed")
            self.bubble.say("Something went wrong waking my brain up (see the log).", hold=6)

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._timer.stop()
        self.tracker.stop()
        self.killswitch.unregister()
        self.hotkeys.clear()
        self.speaker.stop()
        self.listener.cancel()
        self.blocks.clear_temporary()  # permanent structures stay (they're saved and restored next time)
        # (Stashed ones, while lounging, were never un-saved, so they come back next time too.)
        self.tray.hide()
        self.fig_window.hide()
        self.bubble.hide()
        self.chat.hide()
        asyncio.ensure_future(self._shutdown())

    async def _shutdown(self) -> None:
        try:
            await asyncio.wait_for(self.agent.drain(), timeout=3)  # finish storing memories
        except asyncio.TimeoutError:
            log.warning("memory extraction still running at shutdown; dropped")
        self.companion.save()
        self.actions.close()
        self.mischief.close()
        self.adventure.close()
        self.memory.close()
        self.perception.close()
        self.element_reader.close()
        await self.ollama.close()
        self.qapp.quit()

    def _toggle_debug(self, on: bool) -> None:
        if on:
            self.debug.show_over_desktop()
            self.tracker.ignore(self.debug.winId())
        else:
            self.debug.hide()

    # -- main loop ------------------------------------------------------------------

    def _tick(self) -> None:
        t0 = time.perf_counter()
        dt = min(t0 - self._last, 0.1)
        self._last = t0

        if self.tracker.update():
            self.world.sync(self.tracker.snapshot, self.fig.body)
            self._handle_fullscreen(self.tracker.snapshot.fullscreen_foreground)

        cursor = win32.cursor_pos()
        if not self._paused:
            h = 1.0 / self.cfg.physics_hz
            self._acc = min(self._acc + dt, h * self.cfg.max_substeps)
            ts = time.perf_counter()
            while self._acc >= h:
                self.fig.pre_step(h, cursor)
                self.world.space.step(h)
                self.fig.post_step(h)
                self._acc -= h
            self._step_ms = (time.perf_counter() - ts) * 1000
            self.fig.update(dt)
            if self._in_lounge:  # its body rests on the desktop side; the lounge animates what it does
                self.fig.activity = Activity.SLEEP if self.lounge.life.sleeping else Activity.SIT
            else:
                self.brain.update(dt, cursor)
                self._maybe_enter_lounge()
            self.companion.update(dt)
            self.blocks.update(dt, self.world.surface_for_shape(self.fig.ground_shape))
            self.block_views.update(dt)
            self.anim.talk = self.speaker.level
            self.anim.update(dt)

        # Riding the cursor: never swallow the user's clicks, and let go when they click something.
        if self.fig.riding != self._riding_click_through:
            self._riding_click_through = self.fig.riding
            self.fig_window.set_click_through(self.fig.riding)
        if self.fig.riding and win32.mouse_down():
            self.fig.stop_ride()
        self.fig_window.sync()
        bx, by = self.fig.body.position
        hx, hy = self.anim.pose["head"]
        self.bubble.follow((bx + hx, by + hy), self.anim.P.head_r, self.world.monitors,
                           not self._hidden_for_fullscreen and not self._in_lounge)
        self.highlight.tick()
        if not self._paused:
            self.mischief.tick()
            self.adventure.tick()
        if self.debug.isVisible() and t0 >= self._debug_next:
            self._debug_next = t0 + 0.1
            self.debug.update()
        self._frame_ms = (time.perf_counter() - t0) * 1000

    def _handle_fullscreen(self, fullscreen: bool) -> None:
        if fullscreen == self._hidden_for_fullscreen:
            return
        self._hidden_for_fullscreen = fullscreen
        self.fig_window.setVisible(not fullscreen)
        for w in self.block_views.windows.values():
            w.setVisible(not fullscreen)

    def _stats(self) -> list[str]:
        f, e = self.fig, self.emotion
        return [
            f"frame {self._frame_ms:5.2f} ms   physics {self._step_ms:5.2f} ms",
            f"tracker refresh {self.tracker.refresh_ms:5.2f} ms",
            f"windows {len(self.tracker.snapshot.windows)}   platforms {len(self.world.platforms)}   blocks {len(self.world.blocks)}",
            f"anim {self.anim.state.name}   task {self.brain.task_name}",
            f"mood {e.label()}  E{e.energy:.2f} C{e.curiosity:.2f} A{e.affection:.2f} N{e.annoyance:.2f}",
            f"pos ({f.body.position.x:.0f}, {f.body.position.y:.0f})   vel ({f.body.velocity.x:.0f}, {f.body.velocity.y:.0f})",
            f"on {self.world.surface_for_shape(f.ground_shape)}",
        ]


def _setup_logging() -> None:
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    if sys.stdout is None or sys.stderr is None:  # pythonw / packaged exe: no console, log to a file
        os.makedirs(CONFIG.data_dir, exist_ok=True)
        from logging.handlers import RotatingFileHandler

        handler = RotatingFileHandler(os.path.join(CONFIG.data_dir, "stickfigure.log"), maxBytes=2_000_000,
                                      backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)
    if os.environ.get("STICKFIGURE_DEBUG"):  # verbose diagnostics, incl. every HTTP connection step
        logging.getLogger().setLevel(logging.DEBUG)
        return
    for noisy in ("httpx", "faster_whisper", "kokoro_onnx", "huggingface_hub", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    _setup_logging()
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName("The Third Coming")
    loop = qasync.QEventLoop(qapp)
    asyncio.set_event_loop(loop)
    app = StickFigureApp(qapp)
    app.start()
    with loop:
        loop.run_forever()
    return 0
