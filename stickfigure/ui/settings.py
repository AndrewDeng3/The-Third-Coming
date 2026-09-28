"""Settings panel, shown inside the chat window. Saves to <data_dir>/settings.json; most changes apply
immediately."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from stickfigure.names import NAME
from stickfigure.overlay.render import COLORS
from stickfigure.win import autostart

VOICES = {
    "af_heart": "Heart (US, warm)", "af_bella": "Bella (US)", "af_nicole": "Nicole (US, soft)",
    "af_sky": "Sky (US)", "am_puck": "Puck (US, playful)", "am_michael": "Michael (US)", "am_adam": "Adam (US)",
    "am_echo": "Echo (US)", "bf_emma": "Emma (UK)", "bm_george": "George (UK)", "bm_lewis": "Lewis (UK)",
}
STT_MODELS = {"tiny.en": "Tiny (fastest)", "base.en": "Base (balanced)", "small.en": "Small (most accurate)"}
RESTART_KEYS = {"stt_model", "chat_model", "supervised"}


class SettingsPanel(QWidget):
    closed = Signal()  # Back or Save: return to the conversation

    def __init__(self, current: dict, scale: float, on_save: Callable[[dict, bool], None],
                 on_preview_voice: Callable[[str, float], None]):
        super().__init__()
        self._on_save = on_save
        self._initial = dict(current)
        v = current
        s = scale

        name_row = QLabel(f"<b>{NAME}</b>")
        name_row.setToolTip("The third legendary stick figure, after the Chosen One and the Second Coming. "
                            "That's its name, and it's sticking with it.")
        self.user_name = QLineEdit(v.get("user_name", ""))
        self.user_name.setPlaceholderText("What should it call you?")
        self.color = QComboBox()
        self.color.addItems(list(COLORS))
        self.color.setCurrentText(v["color"])
        self.voice_on = QCheckBox("Speak replies out loud")
        self.voice_on.setChecked(v["voice_enabled"])
        self.voice = QComboBox()
        for key, label in VOICES.items():
            self.voice.addItem(label, key)
        self.voice.setCurrentIndex(max(0, self.voice.findData(v["tts_voice"])))
        self.voice.currentIndexChanged.connect(lambda _: on_preview_voice(self.voice.currentData(), self.speed.value()))
        self.speed = QDoubleSpinBox(minimum=0.7, maximum=1.5, singleStep=0.05, value=v["tts_speed"])
        self.volume = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=round(v["tts_volume"] * 100))
        self.stt = QComboBox()
        for key, label in STT_MODELS.items():
            self.stt.addItem(label, key)
        self.stt.setCurrentIndex(max(0, self.stt.findData(v["stt_model"])))
        self.mischief = QCheckBox("Harmless pranks when I'm idle")
        self.mischief.setToolTip("Cursor tugs, a little scrolling, hovering. Never clicks or types.")
        self.mischief.setChecked(v["mischief"])
        self.notice = QCheckBox("Peek at what I'm doing and ask about it")
        self.notice.setToolTip("Only the window you're using, never password/banking/security windows. "
                               "Stays on this computer and isn't saved to long-term memory.")
        self.notice.setChecked(v.get("notice_activity", True))
        self.awareness = QCheckBox("Keep track of what I'm doing (apps & window titles)")
        self.awareness.setToolTip("Which app and page you're in, switches, typing vs. mouse vs. away, and time per app. "
                                  "No screenshots, never saved to disk, private windows aren't named.")
        self.awareness.setChecked(v.get("awareness", True))
        self.adventures = QCheckBox("Explore the web / peek at my tabs when I'm away")
        self.adventures.setToolTip("Opens a new tab to look up something it's curious about (and closes it), or "
                                   "flips to your next browser tab and back. Never clicks, types, or touches files; "
                                   "any input from you stops it.")
        self.adventures.setChecked(v.get("adventures", True))
        self.supervised = QCheckBox("Ask before every step of a task")
        self.supervised.setChecked(v["supervised"])
        self.autostart = QCheckBox("Start with Windows")
        self.autostart.setChecked(autostart.is_enabled())
        self.chat_model = QLineEdit(v["chat_model"])

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setVerticalSpacing(round(8 * s))
        form.addRow(self._section("Names"))
        form.addRow("Its name", name_row)
        form.addRow("Your name", self.user_name)
        form.addRow("Color", self.color)
        form.addRow(self._section("Voice"))
        form.addRow("", self.voice_on)
        form.addRow("Voice", self.voice)
        form.addRow("Speed", self.speed)
        form.addRow("Volume", self.volume)
        form.addRow("Listening", self.stt)
        form.addRow(self._section("Behavior"))
        form.addRow("", self.mischief)
        form.addRow("", self.awareness)
        form.addRow("", self.notice)
        form.addRow("", self.adventures)
        form.addRow("", self.supervised)
        form.addRow("", self.autostart)
        form.addRow("Chat model", self.chat_model)
        note = QLabel("Listening, chat model and ask-every-step apply after a restart.", objectName="note")
        note.setWordWrap(True)
        form.addRow(note)

        body = QWidget()
        body.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        back = QPushButton("Back", objectName="secondary")
        back.clicked.connect(self.closed.emit)
        save = QPushButton("Save")
        save.clicked.connect(self._save)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(back)
        buttons.addWidget(save)
        col = QVBoxLayout(self)
        col.setContentsMargins(round(10 * s), round(4 * s), round(10 * s), round(6 * s))
        col.addWidget(scroll, 1)
        col.addLayout(buttons)

    @staticmethod
    def _section(title: str) -> QLabel:
        return QLabel(title, objectName="section")

    def values(self) -> dict:
        return {
            "buddy_name": NAME,
            "user_name": self.user_name.text().strip(),
            "color": self.color.currentText(),
            "voice_enabled": self.voice_on.isChecked(),
            "tts_voice": self.voice.currentData(),
            "tts_speed": round(self.speed.value(), 2),
            "tts_volume": self.volume.value() / 100,
            "stt_model": self.stt.currentData(),
            "mischief": self.mischief.isChecked(),
            "notice_activity": self.notice.isChecked(),
            "awareness": self.awareness.isChecked(),
            "adventures": self.adventures.isChecked(),
            "supervised": self.supervised.isChecked(),
            "chat_model": self.chat_model.text().strip() or "llama3.2:3b",
        }

    def _save(self) -> None:
        values = self.values()
        if self.autostart.isChecked() != autostart.is_enabled():
            autostart.set_enabled(self.autostart.isChecked())
        needs_restart = any(values[k] != self._initial.get(k) for k in RESTART_KEYS)
        self._on_save(values, needs_restart)
        self.closed.emit()
