"""The lounge: what the chat window turns into when the conversation goes quiet.

A painted room (time-of-day window with curtains, bookshelf, couch, floor lamp, plant, clock, rug and a
door) where the figure lives its own little life: it naps, reads, waters the plant, gazes out of the
window, stretches, dances, juggles, and eventually gets bored and walks out of the door (back onto
your desktop). Talking to it while it's in here doesn't pull it out: its replies appear in a text box
at the bottom of the room.

`LoungeLife` is the pure logic (no painting), so it's testable; `LoungeView` draws it.
"""

from __future__ import annotations

import math
import random
import time
from typing import Callable, Generator

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import QTextBrowser, QWidget

from stickfigure.agent.emotion import Emotion
from stickfigure.config import CONFIG, Config
from stickfigure.figure.animator import Animator
from stickfigure.figure.controller import Activity
from stickfigure.figure.skeleton import Pose, mirror
from stickfigure.overlay.render import draw_figure
from stickfigure.ui.markdown import to_html

# Where things are, as fractions of the room width.
PLANT, WINDOW, PLAY, SHELF, COUCH, LAMP, DOOR = 0.07, 0.2, 0.3, 0.42, 0.63, 0.82, 0.93
WALK_SPEED = 0.13  # room widths per second
Task = Generator[None, None, None]


def sky_colors(hour: float) -> tuple[QColor, QColor, bool]:
    """(top, bottom, is_night) for the window, by local time."""
    if 7 <= hour < 17:
        return QColor("#6fb6ff"), QColor("#cfe8ff"), False
    if 17 <= hour < 19.5 or 5.5 <= hour < 7:
        return QColor("#ff8a5c"), QColor("#ffd29a"), False
    return QColor("#0e1633"), QColor("#2a3566"), True


# -- the figure's body in the room ----------------------------------------------------------------


class _Vec:
    def __init__(self, x: float = 0.0, y: float = 0.0):
        self.x, self.y = x, y

    def __iter__(self):
        return iter((self.x, self.y))


class _Body:
    def __init__(self):
        self.position = _Vec()
        self.velocity = _Vec()


class _Stub:
    """Just enough of controller.Figure for the Animator to read."""

    def __init__(self):
        self.body = _Body()
        self.grabbed = False
        self.knocked = 0.0
        self.knocked_side = 1
        self.grounded = True
        self.tumbling = False
        self.tumble_angle = 0.0
        self.air_time = 0.0
        self.land_timer = 0.0
        self.activity = Activity.NONE
        self.activity_point = None
        self.facing = 1


class LoungeAnimator(Animator):
    """The desktop animator plus a few indoor poses (reading, stretching, watering, dancing, juggling)."""

    def __init__(self, cfg: Config = CONFIG):
        super().__init__(_Stub(), cfg)
        self.mode: str | None = None

    def _classify(self):
        return self.mode if self.mode else super()._classify()

    def _target(self, state) -> Pose:
        if isinstance(state, str):
            canonical = getattr(self, f"_pose_{state}")()
            return mirror(canonical, self.fig.facing)
        return super()._target(state)

    def _standing(self) -> tuple:
        return (0.0, self.P.half - 0.96 * self._leg)

    def _pose_read(self) -> Pose:  # sitting, book held up in front
        H, half, t = self.P.height, self.P.half, self.time
        turn = 0.01 * H * math.sin(t * 0.7)
        pelvis = (0.0, half - 0.015 * H)
        return self._assemble(
            pelvis, -0.06,
            (0.12 * H, half + 0.3 * H), (0.16 * H, half + 0.28 * H),
            (0.2 * H, half - 0.36 * H + turn), (0.25 * H, half - 0.38 * H - turn),
            hands_abs=True, elbow=-1.0, elbow_f=-1.0,
        )

    def _pose_stretch(self) -> Pose:
        H, half, t = self.P.height, self.P.half, self.state_time
        k = min(1.0, t / 0.6)
        sway = math.sin(t * 1.6) * 0.1 * k
        return self._assemble(
            self._standing(), sway,
            (-0.08 * H, half), (0.08 * H, half),
            (-0.04 * H, -self._arm * 0.95 * k + self._arm * 0.9 * (1 - k)),
            (0.04 * H, -self._arm * 0.95 * k + self._arm * 0.9 * (1 - k)),
        )

    def _pose_water(self) -> Pose:  # leaning forward, watering can held out and tipped
        H, half = self.P.height, self.P.half
        return self._assemble(
            self._standing(), 0.18,
            (-0.08 * H, half), (0.1 * H, half),
            (0.02 * H, self._arm * 0.8), (0.3 * H, 0.02 * H),
        )

    def _pose_dance(self) -> Pose:
        H, half, t = self.P.height, self.P.half, self.state_time
        beat = math.sin(t * 6.0)
        up = max(0.0, math.sin(t * 3.0))
        pelvis = (0.03 * H * beat, half - (0.9 + 0.06 * abs(beat)) * self._leg)
        return self._assemble(
            pelvis, 0.12 * beat,
            (-0.1 * H, half - 0.06 * H * max(0.0, beat)), (0.1 * H, half - 0.06 * H * max(0.0, -beat)),
            (-0.2 * H, -self._arm * 0.8 * up + 0.1 * H), (0.2 * H, -self._arm * 0.8 * (1 - up) + 0.1 * H),
        )

    def _pose_juggle(self) -> Pose:
        H, half, t = self.P.height, self.P.half, self.state_time
        a = math.sin(t * 7.0)
        return self._assemble(
            self._standing(), -0.03,
            (-0.08 * H, half), (0.08 * H, half),
            (0.12 * H, 0.1 * H + 0.06 * H * a), (0.2 * H, 0.1 * H - 0.06 * H * a),
            elbow=-1.0, elbow_f=-1.0,
        )


class LoungeLife:
    """What the figure does in the lounge, frame by frame. No Qt painting: positions are fractions of
    the room width, `base` says whether it stands on the floor or sits on the couch."""

    def __init__(self, emotion: Emotion | None = None, cfg: Config = CONFIG, rng: random.Random | None = None):
        self.emotion = emotion or Emotion()
        self.rng = rng or random.Random()
        self.anim = LoungeAnimator(cfg)
        self.fig = self.anim.fig
        self.x = DOOR
        self.base = "floor"  # "floor" | "couch"
        self.hop = 0.0  # height above the base, in figure heights
        self.prop: str | None = None  # "book" | "can" | "ball" | None, drawn at the front hand
        self.door = 0.0  # 0 closed .. 1 open
        self.alpha = 1.0
        self.plant_water = 0.6  # droops as it dries out, perks up when watered
        self.activity = "arriving"
        self.units_per_width = 1000.0  # figure units per room width (set by the view each frame)
        self.on_left: Callable[[], None] = lambda: None
        self.gone = False
        self._task: Task | None = None
        self._dt = 0.0
        self._now = 0.0  # its own clock (advanced by update), so behavior is deterministic in tests
        self._talking_until = 0.0
        self._leave_at = 0.0
        self._leaving = False

    # -- control ------------------------------------------------------------------------------

    def enter(self, stay: float | None = None) -> None:
        """Walk in through the door. `stay`: seconds before it gets restless (random by mood if None)."""
        e = self.emotion
        if stay is None:
            restless = 0.5 * e.energy + 0.5 * e.curiosity  # energetic, curious figures don't sit still long
            stay = self.rng.uniform(150, 420) * (1.4 - restless)
        self._leave_at = self._now + stay
        self._leaving = False
        self.gone = False
        self.alpha = 1.0
        self.x, self.base, self.hop, self.prop = DOOR, "floor", 0.0, None
        self.anim.mode = None
        self.fig.activity = Activity.NONE
        self._start("arriving", self._arrive())

    def talk(self, seconds: float = 25.0) -> None:
        """Someone's talking to it: stop and listen (it resumes its life when the chat goes quiet)."""
        was = self._now < self._talking_until
        self._talking_until = self._now + seconds
        self._leave_at = max(self._leave_at, self._now + 60)  # don't walk out mid-conversation
        if not was and not self._leaving:
            self._start("listening", self._listen())

    def poke(self) -> None:
        """Clicked in the room: wake up / wave."""
        if self._leaving:
            return
        self._start("waving", self._greet())

    def leave(self) -> None:
        """Head for the door (then `on_left` fires)."""
        if not self._leaving:
            self._leaving = True
            self._start("leaving", self._exit())

    def do(self, action: str) -> bool:
        """A body action from the conversation, acted out indoors. False if it needs the desktop."""
        makers = {
            "sit": lambda: self._sit(self.rng.uniform(15, 30)),
            "sleep": lambda: self._nap(self.rng.uniform(40, 90)),
            "dance": self._dance,
            "wave": self._greet,
            "hop": lambda: self._hops(),
        }
        if action not in makers or self._leaving:
            return False
        self._start(action, makers[action]())
        return True

    def _hops(self) -> Task:
        if self.base == "couch":
            yield from self._stand_up()
        for _ in range(self.rng.randint(1, 3)):
            yield from self._jump(0.25, 0.45)
            yield from self._wait(0.1)

    @property
    def sleeping(self) -> bool:
        return self.fig.activity == Activity.SLEEP

    @property
    def talking(self) -> bool:
        return self._now < self._talking_until

    # -- per frame -------------------------------------------------------------------------------

    def update(self, dt: float) -> Pose:
        self._dt = dt
        self._now += dt
        self.plant_water = max(0.0, self.plant_water - dt / 900)
        restless = (not self._leaving and not self.talking and self._now >= self._leave_at
                    and self.emotion.energy > 0.3 and self.activity not in ("arriving", "napping"))
        if restless:
            self.leave()  # drops whatever it was doing and heads for the door
        elif self._task is None:
            self._choose()
        try:
            next(self._task)
        except StopIteration:
            self._task = None
        self.fig.body.velocity.y = -1.0 if self.hop > 0 else 0.0
        self.fig.grounded = self.hop <= 0
        return self.anim.update(dt)

    def _start(self, name: str, task: Task) -> None:
        if self._task is not None:
            self._task.close()
        self._reset_pose()
        self.activity = name
        self._task = task

    def _reset_pose(self) -> None:
        self.anim.mode = None
        self.fig.activity = Activity.NONE
        self.fig.activity_point = None
        self.fig.body.velocity.x = 0.0
        self.prop = None
        self.hop = 0.0

    def _choose(self) -> None:
        e = self.emotion
        tired = e.energy < 0.35
        options = [
            (30 if not tired else 8, "sitting", lambda: self._sit(self.rng.uniform(15, 40))),
            (60 * tired + 4, "napping", lambda: self._nap(self.rng.uniform(40, 120))),
            (14 + 12 * e.curiosity, "reading", self._read),
            (12 * (1.2 - self.plant_water), "watering the plant", self._water),
            (10 + 10 * e.curiosity, "looking out the window", self._gaze),
            (8 * (e.energy > 0.3), "stretching", self._stretch),
            (10 * e.happiness * (e.energy > 0.45), "dancing", self._dance),
            (8 * (e.energy > 0.4), "juggling", self._juggle),
            (10, "pacing around", self._wander),
        ]
        total = sum(w for w, *_ in options)
        r = self.rng.uniform(0, total)
        for w, name, make in options:
            r -= w
            if r <= 0:
                self._start(name, make())
                return
        self._start("sitting", self._sit(20))

    # -- building blocks -----------------------------------------------------------------------------

    def _wait(self, seconds: float) -> Task:
        t = seconds
        while t > 0:
            t -= self._dt
            yield

    def _walk(self, x: float) -> Task:
        if self.base == "couch":
            yield from self._stand_up()
        while abs(x - self.x) > 0.004:
            d = 1 if x > self.x else -1
            self.fig.facing = d
            step = min(abs(x - self.x), WALK_SPEED * (0.7 + 0.5 * self.emotion.energy) * self._dt)
            self.x += d * step
            self.fig.body.velocity.x = d * step / max(self._dt, 1e-6) * self.units_per_width
            yield
        self.x = x
        self.fig.body.velocity.x = 0.0
        yield

    def _stand_up(self) -> Task:
        self.fig.activity = Activity.NONE
        self.anim.mode = None
        self.base = "floor"
        yield from self._wait(0.3)

    def _sit_down(self) -> Task:
        yield from self._walk(COUCH)
        self.fig.facing = self.rng.choice((-1, 1))
        self.base = "couch"
        self.fig.activity = Activity.SIT
        yield from self._wait(0.3)

    def _jump(self, height: float = 0.25, duration: float = 0.45) -> Task:
        t = 0.0
        while t < duration:
            t += self._dt
            self.hop = max(0.0, height * math.sin(math.pi * min(1.0, t / duration)))
            yield
        self.hop = 0.0

    # -- activities ------------------------------------------------------------------------------------

    def _arrive(self) -> Task:
        while self.door < 1:
            self.door = min(1.0, self.door + self._dt * 3)
            yield
        yield from self._walk(DOOR - 0.1)
        while self.door > 0:
            self.door = max(0.0, self.door - self._dt * 3)
            yield
        if self.emotion.energy < 0.35:
            yield from self._nap(self.rng.uniform(40, 90))
        else:
            yield from self._sit(self.rng.uniform(8, 20))

    def _sit(self, seconds: float) -> Task:
        yield from self._sit_down()
        yield from self._wait(seconds)

    def _nap(self, seconds: float) -> Task:
        yield from self._sit_down()
        self.fig.activity = Activity.SLEEP
        t = seconds
        while t > 0 and self.emotion.energy < 0.9:
            t -= self._dt
            yield
        self.fig.activity = Activity.SIT  # groggy moment
        yield from self._wait(2.0)

    def _read(self) -> Task:
        yield from self._walk(SHELF)
        self.fig.facing = 1
        self.anim.mode = "water"  # reaching up to the shelf
        yield from self._wait(1.2)
        self.anim.mode = None
        self.prop = "book"
        yield from self._sit_down()
        self.fig.activity = Activity.NONE
        self.anim.mode = "read"
        yield from self._wait(self.rng.uniform(25, 60))
        self.anim.mode = None
        self.fig.activity = Activity.SIT
        yield from self._wait(1.0)

    def _water(self) -> Task:
        yield from self._walk(PLANT + 0.06)
        self.fig.facing = -1
        self.prop = "can"
        self.anim.mode = "water"
        t = 3.5
        while t > 0:
            t -= self._dt
            self.plant_water = min(1.0, self.plant_water + self._dt * 0.25)
            yield
        self.anim.mode = None
        self.prop = None
        yield from self._wait(0.3)
        yield from self._jump(0.15, 0.35)  # happy little hop

    def _gaze(self) -> Task:
        yield from self._walk(WINDOW + 0.02)
        self.fig.facing = -1
        self.fig.activity = Activity.LOOK
        yield from self._wait(self.rng.uniform(6, 14))

    def _stretch(self) -> Task:
        yield from self._walk(PLAY)
        self.anim.mode = "stretch"
        yield from self._wait(3.5)
        self.anim.mode = None
        yield from self._wait(0.5)

    def _dance(self) -> Task:
        yield from self._walk(PLAY)
        self.anim.mode = "dance"
        for _ in range(self.rng.randint(4, 7)):
            yield from self._wait(0.9)
            self.fig.facing *= -1
        self.anim.mode = None
        yield from self._jump(0.3, 0.5)

    def _juggle(self) -> Task:
        yield from self._walk(PLAY + 0.04)
        self.prop = "ball"
        self.anim.mode = "juggle"
        yield from self._wait(self.rng.uniform(6, 12))
        self.anim.mode = None
        self.prop = None

    def _wander(self) -> Task:
        for _ in range(self.rng.randint(1, 3)):
            yield from self._walk(self.rng.uniform(0.15, DOOR - 0.12))
            yield from self._wait(self.rng.uniform(0.5, 2.0))

    def _listen(self) -> Task:
        if self.sleeping:
            self.fig.activity = Activity.SIT  # wakes up
            yield from self._wait(0.6)
        if self.base != "couch":
            self.fig.facing = 1 if self.x < 0.5 else -1
        while self.talking:
            yield

    def _greet(self) -> Task:
        if self.sleeping:
            self.fig.activity = Activity.SIT
            yield from self._wait(0.8)
        if self.base == "couch":
            yield from self._stand_up()
        self.fig.activity = Activity.WAVE
        yield from self._wait(1.6)
        self.fig.activity = Activity.NONE

    def _exit(self) -> Task:
        yield from self._walk(DOOR - 0.1)
        while self.door < 1:
            self.door = min(1.0, self.door + self._dt * 3)
            yield
        self.fig.facing = 1
        while self.alpha > 0:
            self.x = min(DOOR, self.x + WALK_SPEED * self._dt)
            self.fig.body.velocity.x = WALK_SPEED * self.units_per_width
            self.alpha = max(0.0, self.alpha - self._dt * 1.3)
            yield
        self.fig.body.velocity.x = 0.0
        self.door = 0.0
        self.gone = True
        self.on_left()
        while True:
            yield


# -- the view ------------------------------------------------------------------------------------


class LoungeView(QWidget):
    poked = Signal()  # the user clicked in the room
    left = Signal()  # the figure walked out of the door by itself

    def __init__(self, emotion: Emotion, get_color: Callable[[], QColor], get_talk_level: Callable[[], float],
                 name: str = "Stick", cfg: Config = CONFIG):
        super().__init__()
        self.life = LoungeLife(emotion, cfg)
        self.life.on_left = self.left.emit
        self.props = self.life.anim.P
        self.get_color = get_color
        self.get_talk_level = get_talk_level
        self.name = name
        self.caption = ""
        self._caption_until = 0.0
        self._last = time.monotonic()
        self._figure_rect = QRectF()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Their lounge. Click to say hi; chat below and they'll answer in here.")

        # Conversation box at the bottom of the room.
        self.talk_box = QTextBrowser(self)
        self.talk_box.setOpenLinks(False)
        self.talk_box.setStyleSheet(
            "QTextBrowser { background: rgba(24, 25, 30, 235); color: #ecebf2; border: 2px solid #f7931e;"
            " border-radius: 10px; padding: 6px; font-size: 10pt; }")
        self.talk_box.hide()
        self._user_line = ""
        self._reply = ""
        self._pending = False
        self._talk_seen = 0.0

    # -- API ----------------------------------------------------------------------------------------

    def enter(self) -> None:
        self._user_line, self._reply, self._pending = "", "", False
        self.talk_box.hide()
        self.caption = ""
        self.life.enter()

    def leave(self) -> None:
        self.life.leave()

    def say(self, text: str, hold: float = 8.0) -> None:
        """A spontaneous thought: a caption bubble over its head (not the conversation box)."""
        self.caption = text
        self._caption_until = time.monotonic() + hold

    def talk_user(self, text: str) -> None:
        self._user_line = text
        self._reply, self._pending = "", True
        self.life.talk()
        self._show_talk()

    def talk_reply(self, text: str, pending: bool) -> None:
        self._reply, self._pending = text, pending
        self.life.talk()
        self._show_talk()

    def _show_talk(self) -> None:
        you = ""
        if self._user_line:
            short = self._user_line if len(self._user_line) < 160 else self._user_line[:157] + "…"
            you = f'<p style="color:#9ec2ff; margin:0 0 4px 0;"><b>You:</b> {to_html(short)}</p>'
        body = to_html(self._reply) if self._reply else '<span style="color:#9a9aa6">…</span>'
        self.talk_box.setHtml(f'{you}<div><b style="color:#f7931e">{self.name}:</b></div>{body}')
        self.talk_box.verticalScrollBar().setValue(self.talk_box.verticalScrollBar().maximum()
                                                   if self._pending else 0)
        self._talk_seen = time.monotonic()
        self._layout_talk()
        self.talk_box.show()

    def _layout_talk(self) -> None:
        w, h = self.width(), self.height()
        doc_h = self.talk_box.document().size().height() + 20
        box_h = round(min(h * 0.5, max(h * 0.2, doc_h)))
        m = 8
        self.talk_box.setGeometry(m, h - box_h - m, w - 2 * m, box_h)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.talk_box.isVisible():
            self._layout_talk()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._last = time.monotonic()
        self._timer.start(33)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()

    def mousePressEvent(self, e) -> None:
        self.life.poke()
        self.poked.emit()

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = min(0.1, now - self._last), now
        self.life.anim.talk = self.get_talk_level()
        self.life.update(dt)
        # The conversation box fades away after a quiet minute.
        if self.talk_box.isVisible() and not self._pending and now - self._talk_seen > 60:
            self.talk_box.hide()
        self.update()

    # -- painting -------------------------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        if self.talk_box.isVisible():
            r.setBottom(self.talk_box.geometry().top() - 4)  # the room shrinks above the talk box
        self.paint_room(p, r, time.localtime())
        p.end()

    def paint_room(self, p: QPainter, r: QRectF, now: time.struct_time) -> None:
        life = self.life
        w, h = r.width(), r.height()
        L, T = r.left(), r.top()
        X = lambda f: L + f * w  # noqa: E731
        hour = now.tm_hour + now.tm_min / 60
        top, bottom, night = sky_colors(hour)
        floor_y = T + h * 0.8
        t = time.monotonic()
        p.save()
        p.setClipRect(r)

        # Wall: warm paper with faint stripes, wainscoting below a chair rail.
        wall = QLinearGradient(r.topLeft(), QPointF(L, floor_y))
        wall.setColorAt(0, QColor("#f1e3c8") if not night else QColor("#4b4160"))
        wall.setColorAt(1, QColor("#e3cfae") if not night else QColor("#3c3450"))
        p.fillRect(QRectF(L, T, w, floor_y - T), wall)
        p.setPen(QPen(QColor(120, 80, 40, 18) if not night else QColor(255, 255, 255, 10), max(2.0, w * 0.012)))
        step = max(12.0, w * 0.045)
        x = L + step / 2
        while x < r.right():
            p.drawLine(QPointF(x, T), QPointF(x, floor_y))
            x += step
        rail = T + h * 0.58
        p.fillRect(QRectF(L, rail, w, floor_y - rail), QColor("#b98a5e") if not night else QColor("#5a4468"))
        p.setPen(QPen(QColor(0, 0, 0, 35), 1.2))
        panel_w = w / 9
        for i in range(9):
            p.drawRect(QRectF(L + i * panel_w + 5, rail + 6, panel_w - 10, floor_y - rail - 12))
        p.fillRect(QRectF(L, rail - 3, w, 6), QColor("#8a5d38") if not night else QColor("#3f2f4a"))

        # Floor: planks with staggered seams, baseboard.
        p.fillRect(QRectF(L, floor_y, w, r.bottom() - floor_y), QColor("#a26f45") if not night else QColor("#5e412c"))
        p.setPen(QPen(QColor(0, 0, 0, 38), 1.2))
        rows = 4
        for i in range(rows):
            y0 = floor_y + (r.bottom() - floor_y) * i / rows
            y1 = floor_y + (r.bottom() - floor_y) * (i + 1) / rows
            p.drawLine(QPointF(L, y1), QPointF(r.right(), y1))
            off = (i % 2) * w * 0.09
            xx = L + off + w * 0.04
            while xx < r.right():
                p.drawLine(QPointF(xx, y0), QPointF(xx, y1))
                xx += w * 0.18
        p.fillRect(QRectF(L, floor_y - 5, w, 7), QColor("#6e4a2c") if not night else QColor("#2f2233"))

        # Window with curtains, sill, sky.
        win = QRectF(X(0.1), T + h * 0.1, w * 0.22, h * 0.36)
        sky = QLinearGradient(win.topLeft(), win.bottomLeft())
        sky.setColorAt(0, top)
        sky.setColorAt(1, bottom)
        p.fillRect(win, sky)
        p.save()
        p.setClipRect(win)
        p.setPen(Qt.NoPen)
        if night:
            p.setBrush(QColor("#fff6cf"))
            p.drawEllipse(QPointF(win.right() - win.width() * 0.28, win.top() + win.height() * 0.28), win.width() * 0.09,
                          win.width() * 0.09)
            p.setBrush(QColor(255, 255, 255, 210))
            for fx, fy in ((0.15, 0.2), (0.35, 0.5), (0.55, 0.15), (0.25, 0.75), (0.75, 0.62), (0.45, 0.35)):
                tw = 1.0 + 0.7 * math.sin(t * 2 + fx * 10)
                p.drawEllipse(QPointF(win.left() + fx * win.width(), win.top() + fy * win.height()), tw, tw)
        else:
            p.setBrush(QColor(255, 236, 150, 230))
            p.drawEllipse(QPointF(win.left() + win.width() * 0.75, win.top() + win.height() * 0.25), win.width() * 0.1,
                          win.width() * 0.1)
            p.setBrush(QColor(255, 255, 255, 215))
            span = win.width() + 80
            for dx, dy, s in ((0, 0.35, 1.0), (span * 0.55, 0.6, 0.7)):
                cx = win.left() - 40 + (t * 5 + dx) % span
                cy = win.top() + dy * win.height()
                for ox, rr in ((0, 10), (11, 13), (24, 9)):
                    p.drawEllipse(QPointF(cx + ox * s, cy), rr * s, rr * s * 0.75)
        # distant hills
        hills = QPainterPath(QPointF(win.left(), win.bottom()))
        for i in range(9):
            fx = i / 8
            hills.lineTo(win.left() + fx * win.width(), win.bottom() - win.height() * (0.12 + 0.06 * math.sin(fx * 7)))
        hills.lineTo(win.right(), win.bottom())
        p.setBrush(QColor("#5c9a58") if not night else QColor("#1d2a3a"))
        p.drawPath(hills)
        p.restore()
        frame_c = QColor("#f7f1e6") if not night else QColor("#9a8cb0")
        p.setPen(QPen(frame_c, 5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(win)
        p.drawLine(QPointF(win.center().x(), win.top()), QPointF(win.center().x(), win.bottom()))
        p.drawLine(QPointF(win.left(), win.center().y()), QPointF(win.right(), win.center().y()))
        p.fillRect(QRectF(win.left() - 8, win.bottom(), win.width() + 16, 7), frame_c.darker(110))
        curtain = QColor("#c0504d") if not night else QColor("#7a3a4a")
        p.setPen(Qt.NoPen)
        for side in (-1, 1):
            base_x = win.left() if side < 0 else win.right()
            path = QPainterPath(QPointF(base_x - side * 4, win.top() - 10))
            path.lineTo(base_x + side * win.width() * 0.16, win.top() - 10)
            path.quadTo(QPointF(base_x + side * win.width() * 0.02, win.center().y()),
                        QPointF(base_x + side * win.width() * 0.14, win.bottom() + 16))
            path.lineTo(base_x - side * 8, win.bottom() + 16)
            path.closeSubpath()
            p.setBrush(curtain)
            p.drawPath(path)
        p.fillRect(QRectF(win.left() - w * 0.05, win.top() - 14, win.width() + w * 0.1, 5), QColor("#5b3b24"))

        # Plant (droops when thirsty).
        pot = QRectF(X(PLANT) - w * 0.035, floor_y - h * 0.11, w * 0.07, h * 0.11)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#c0633c"))
        path = QPainterPath(QPointF(pot.left(), pot.top()))
        path.lineTo(pot.right(), pot.top())
        path.lineTo(pot.right() - pot.width() * 0.15, pot.bottom())
        path.lineTo(pot.left() + pot.width() * 0.15, pot.bottom())
        path.closeSubpath()
        p.drawPath(path)
        p.fillRect(QRectF(pot.left() - 2, pot.top(), pot.width() + 4, pot.height() * 0.2), QColor("#a9512f"))
        perk = 0.45 + 0.55 * life.plant_water
        sway = math.sin(t * 0.8) * 2
        leaf = QColor("#3f8f3a") if life.plant_water > 0.25 else QColor("#8a9a3a")
        for i, (ang, ln) in enumerate(((-0.9, 0.2), (-0.45, 0.26), (0.0, 0.3), (0.45, 0.25), (0.9, 0.19), (-0.2, 0.22), (0.25, 0.23))):
            a = ang * (1.6 - perk)
            L2 = h * ln * (0.7 + 0.3 * perk)
            sx, sy = pot.center().x(), pot.top()
            ex = sx + math.sin(a) * L2 + sway
            ey = sy - math.cos(a) * L2 * perk + (1 - perk) * L2 * 0.4
            p.setPen(QPen(leaf.darker(115), 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(sx, sy), QPointF(ex, ey))
            p.setPen(Qt.NoPen)
            p.setBrush(leaf if i % 2 else leaf.lighter(115))
            p.drawEllipse(QPointF(ex, ey), w * 0.018, h * 0.028)

        # Bookshelf.
        shelf = QRectF(X(SHELF) - w * 0.065, floor_y - h * 0.52, w * 0.13, h * 0.52)
        wood = QColor("#7a4f2e") if not night else QColor("#4a3150")
        p.setPen(QPen(wood.darker(140), 2))
        p.setBrush(wood)
        p.drawRect(shelf)
        inner = shelf.adjusted(5, 5, -5, -5)
        p.fillRect(inner, wood.darker(150))
        spines = ("#d9534f", "#5b8ff9", "#f0ad4e", "#5cb85c", "#9b59b6", "#e8e2d0", "#16a085", "#e67e22")
        rng = random.Random(7)  # the same books every frame
        for row in range(4):
            y1 = inner.top() + inner.height() * (row + 1) / 4
            p.fillRect(QRectF(shelf.left(), y1 - 3, shelf.width(), 5), wood)
            xx = inner.left() + 2
            while True:
                bw = inner.width() * rng.uniform(0.07, 0.13)
                if xx + bw > inner.right() - 2:
                    break
                bh = inner.height() / 4 * rng.uniform(0.6, 0.85)
                missing = life.prop == "book" and row == 1 and abs(xx - inner.left() - inner.width() * 0.4) < bw
                if not missing:
                    p.fillRect(QRectF(xx, y1 - 3 - bh, bw - 1, bh), QColor(spines[rng.randrange(len(spines))]))
                xx += bw
        # a little trophy block on top
        p.fillRect(QRectF(shelf.center().x() - 8, shelf.top() - 16, 16, 16), QColor("#e8b923"))

        # Clock (real time).
        cc = QPointF(X(0.52), T + h * 0.2)
        cr = min(w, h) * 0.07
        p.setPen(QPen(QColor("#5b3b24"), 3))
        p.setBrush(QColor("#fbf6ec"))
        p.drawEllipse(cc, cr, cr)
        for i in range(12):
            a = i / 12 * math.tau
            p.setPen(QPen(QColor("#555"), 1.5))
            p.drawLine(QPointF(cc.x() + math.sin(a) * cr * 0.8, cc.y() - math.cos(a) * cr * 0.8),
                       QPointF(cc.x() + math.sin(a) * cr * 0.92, cc.y() - math.cos(a) * cr * 0.92))
        ha = (now.tm_hour % 12 + now.tm_min / 60) / 12 * math.tau
        ma = now.tm_min / 60 * math.tau
        p.setPen(QPen(QColor("#222"), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(cc, QPointF(cc.x() + math.sin(ha) * cr * 0.5, cc.y() - math.cos(ha) * cr * 0.5))
        p.setPen(QPen(QColor("#222"), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(cc, QPointF(cc.x() + math.sin(ma) * cr * 0.75, cc.y() - math.cos(ma) * cr * 0.75))

        # Portrait above the couch.
        frame = QRectF(X(COUCH) - w * 0.06, T + h * 0.12, w * 0.12, h * 0.2)
        p.setPen(QPen(QColor("#8a5d38"), 5))
        p.setBrush(QColor("#fbf6ec"))
        p.drawRect(frame)
        p.setPen(QPen(self.get_color(), 2.5, Qt.SolidLine, Qt.RoundCap))
        fx, fy, fh = frame.center().x(), frame.center().y(), frame.height()
        p.drawEllipse(QPointF(fx, fy - fh * 0.22), fh * 0.09, fh * 0.09)
        p.drawLine(QPointF(fx, fy - fh * 0.13), QPointF(fx, fy + fh * 0.12))
        p.drawLine(QPointF(fx, fy + fh * 0.12), QPointF(fx - fh * 0.12, fy + fh * 0.32))
        p.drawLine(QPointF(fx, fy + fh * 0.12), QPointF(fx + fh * 0.12, fy + fh * 0.32))
        p.drawLine(QPointF(fx, fy - fh * 0.05), QPointF(fx - fh * 0.15, fy - fh * 0.2))
        p.drawLine(QPointF(fx, fy - fh * 0.05), QPointF(fx + fh * 0.15, fy - fh * 0.2))

        # Door (opens when it comes and goes).
        door = QRectF(X(DOOR) - w * 0.05, floor_y - h * 0.56, w * 0.1, h * 0.56)
        p.setPen(QPen(QColor("#5b3b24"), 4))
        p.setBrush(QColor(20, 16, 24) if life.door > 0 else QColor("#8b5a33"))
        p.drawRect(door)
        if life.door < 1:
            leaf_w = door.width() * (1 - life.door * 0.85)
            dl = QRectF(door.left(), door.top(), leaf_w, door.height())
            p.setBrush(QColor("#9a6639") if not night else QColor("#6a4a5a"))
            p.drawRect(dl)
            p.setPen(QPen(QColor(0, 0, 0, 40), 1.5))
            p.setBrush(Qt.NoBrush)
            if leaf_w > door.width() * 0.5:
                p.drawRect(dl.adjusted(leaf_w * 0.15, door.height() * 0.08, -leaf_w * 0.15, -door.height() * 0.55))
                p.drawRect(dl.adjusted(leaf_w * 0.15, door.height() * 0.52, -leaf_w * 0.15, -door.height() * 0.08))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#e8b923"))
            p.drawEllipse(QPointF(dl.right() - leaf_w * 0.14, door.center().y()), 3.5, 3.5)

        # Floor lamp.
        lamp_x = X(LAMP)
        shade = QRectF(lamp_x - w * 0.045, T + h * 0.24, w * 0.09, h * 0.1)
        lamp_on = night or not (7 <= hour < 17)
        p.setPen(QPen(QColor("#3b3b3b"), 3))
        p.drawLine(QPointF(lamp_x, shade.bottom()), QPointF(lamp_x, floor_y))
        p.drawLine(QPointF(lamp_x - w * 0.03, floor_y - 1), QPointF(lamp_x + w * 0.03, floor_y - 1))
        path = QPainterPath(QPointF(shade.left() + shade.width() * 0.22, shade.top()))
        path.lineTo(shade.right() - shade.width() * 0.22, shade.top())
        path.lineTo(shade.right(), shade.bottom())
        path.lineTo(shade.left(), shade.bottom())
        path.closeSubpath()
        p.setPen(QPen(QColor("#a06a3a"), 2))
        p.setBrush(QColor("#ffe2a0") if lamp_on else QColor("#f0c983"))
        p.drawPath(path)

        # Rug.
        rug_c = QPointF(X(0.48), floor_y + (r.bottom() - floor_y) * 0.5)
        rw, rh = w * 0.3, (r.bottom() - floor_y) * 0.32
        p.setPen(Qt.NoPen)
        for k, c in enumerate(("#8e3b46", "#d9a441", "#8e3b46", "#c95b4a")):
            p.setBrush(QColor(c) if not night else QColor(c).darker(150))
            p.drawEllipse(rug_c, rw * (1 - k * 0.18), rh * (1 - k * 0.18))

        # Couch with cushions.
        couch = QRectF(X(COUCH) - w * 0.13, floor_y - h * 0.2, w * 0.26, h * 0.2)
        cc_ = QColor("#4f7cac") if not night else QColor("#3b5b80")
        p.setPen(QPen(cc_.darker(150), 2))
        p.setBrush(cc_.darker(108))
        p.drawRoundedRect(QRectF(couch.left(), couch.top() - h * 0.1, couch.width(), h * 0.15), 10, 10)  # back
        p.setBrush(cc_.lighter(108))
        for i in range(2):
            p.drawRoundedRect(QRectF(couch.left() + w * 0.02 + i * couch.width() * 0.47, couch.top() - h * 0.08,
                                     couch.width() * 0.44, h * 0.11), 8, 8)
        seat_top = couch.top() + h * 0.02
        p.setBrush(cc_)
        p.drawRoundedRect(QRectF(couch.left(), seat_top, couch.width(), couch.height() * 0.55), 8, 8)  # seat
        for side in (couch.left() - w * 0.025, couch.right() - w * 0.025):  # arms
            p.drawRoundedRect(QRectF(side, couch.top() - h * 0.03, w * 0.05, couch.height() * 0.78), 9, 9)
        p.setBrush(QColor("#f2c14e"))  # throw pillow
        p.drawRoundedRect(QRectF(couch.left() + w * 0.015, seat_top - h * 0.06, w * 0.05, h * 0.07), 6, 6)
        p.setBrush(cc_.darker(140))
        for side in (couch.left() + w * 0.015, couch.right() - w * 0.035):  # legs
            p.drawRect(QRectF(side, seat_top + couch.height() * 0.55, w * 0.02, h * 0.035))

        # Night: dim the room, with a warm pool of lamp light.
        if lamp_on:
            glow = QRadialGradient(QPointF(lamp_x, shade.bottom()), w * 0.45)
            glow.setColorAt(0, QColor(255, 210, 130, 90))
            glow.setColorAt(1, QColor(255, 210, 130, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QPointF(lamp_x, shade.bottom()), w * 0.45, w * 0.45)
        if night:
            p.fillRect(r, QColor(10, 10, 40, 60))

        # The figure.
        self._draw_figure(p, r, floor_y, seat_top, X, h, night, t)
        p.restore()

    def _draw_figure(self, p: QPainter, r: QRectF, floor_y: float, seat_top: float, X, h: float, night: bool,
                     t: float) -> None:
        life = self.life
        if life.gone:
            return
        scale = h * 0.3 / self.props.height
        life.units_per_width = r.width() / scale
        pose = life.anim.pose
        fx = X(life.x)
        base_y = (seat_top if life.base == "couch" else floor_y) - life.hop * h * 0.3
        p.save()
        p.setOpacity(life.alpha)
        p.translate(fx, base_y)
        p.scale(scale, scale)
        draw_figure(p, pose, (0, -self.props.half), self.props, self.get_color(), 8.0)
        p.restore()
        pt = lambda j: QPointF(fx + pose[j][0] * scale, base_y + (pose[j][1] - self.props.half) * scale)  # noqa: E731
        head = pt("head")
        rh = self.props.head_r * scale
        self._figure_rect = QRectF(fx - rh * 3, head.y() - rh * 2, rh * 6, base_y - head.y() + rh * 2)

        hand = pt("hand_f")
        p.setOpacity(life.alpha)
        if life.prop == "book":
            back = pt("hand_b")
            book = QRectF(min(hand.x(), back.x()) - rh * 0.4, min(hand.y(), back.y()) - rh * 1.4,
                          abs(hand.x() - back.x()) + rh * 0.8, rh * 1.6)
            p.setPen(QPen(QColor("#5a1f1f"), 1.5))
            p.setBrush(QColor("#b33a3a"))
            p.drawRect(book)
            p.setPen(QPen(QColor(255, 255, 255, 180), 1))
            p.drawLine(QPointF(book.center().x(), book.top() + 2), QPointF(book.center().x(), book.bottom() - 2))
        elif life.prop == "can":
            can = QRectF(hand.x() - rh * 0.6, hand.y() - rh * 0.2, rh * 1.2, rh * 1.1)
            p.setPen(QPen(QColor("#2d5f7a"), 1.5))
            p.setBrush(QColor("#4aa3d6"))
            p.drawRoundedRect(can, 3, 3)
            spout = QPointF(can.left() if life.fig.facing > 0 else can.right(), can.top())
            tip = QPointF(spout.x() + life.fig.facing * rh * 1.1, spout.y() + rh * 0.2)
            p.drawLine(spout, tip)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(90, 170, 255, 200))
            for i in range(4):
                ph = (t * 2 + i / 4) % 1.0
                p.drawEllipse(QPointF(tip.x() + life.fig.facing * ph * rh * 0.6, tip.y() + ph * h * 0.12), 2, 3)
        elif life.prop == "ball":
            for i, c in enumerate(("#e74c3c", "#3498db", "#f1c40f")):
                ph = (t * 1.4 + i / 3) % 1.0
                bx = hand.x() + (math.cos(ph * math.tau)) * rh * 1.2
                by = hand.y() - abs(math.sin(ph * math.pi)) * rh * 4
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(c))
                p.drawEllipse(QPointF(bx, by), rh * 0.35, rh * 0.35)
        p.setOpacity(1.0)

        if life.sleeping:
            p.setPen(QColor(255, 255, 255, 220) if night else QColor(80, 80, 110, 220))
            f = QFont("Segoe UI")
            f.setPixelSize(max(12, round(h * 0.06)))
            f.setBold(True)
            p.setFont(f)
            for i in range(3):
                phase = (t * 0.5 + i / 3) % 1.0
                p.drawText(QPointF(head.x() + rh * 1.5 + phase * 25, head.y() - rh - phase * h * 0.14), "z")

        if self.caption and time.monotonic() < self._caption_until and not self.talk_box.isVisible():
            f = QFont("Segoe UI")
            f.setPixelSize(max(12, round(h * 0.05)))
            p.setFont(f)
            fm = QFontMetricsF(f)
            text_rect = fm.boundingRect(QRectF(0, 0, r.width() * 0.6, h), Qt.TextWordWrap, self.caption)
            pad = 10
            box = QRectF(head.x() - text_rect.width() / 2 - pad, head.y() - rh * 2.5 - text_rect.height() - 2 * pad,
                         text_rect.width() + 2 * pad, text_rect.height() + 2 * pad)
            box.moveTop(max(box.top(), r.top() + 6))
            box.moveLeft(min(max(box.left(), r.left() + 6), r.right() - box.width() - 6))
            p.setPen(QPen(QColor(30, 30, 36), 1.5))
            p.setBrush(QColor(255, 255, 255, 240))
            p.drawRoundedRect(box, 10, 10)
            p.setPen(QColor(20, 20, 24))
            p.drawText(box.adjusted(pad, pad, -pad, -pad), Qt.TextWordWrap, self.caption)
