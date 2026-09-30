"""Settings panel, shown inside the chat window. Saves to <data_dir>/settings.json; most changes apply
immediately."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from stickfigure import firstrun, updater
from stickfigure.config import CONFIG
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
                 on_preview_voice: Callable[[str, float], None], on_check_updates: Callable[[], None] = lambda: None):
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
        self.temperature = QSpinBox(minimum=1, maximum=10, value=int(v.get("temperature", 3)))
        self.temperature.setToolTip("How often it does things on its own: pranks, web adventures, thinking. Higher also "
                                    "means it clicks into more search results on its own adventures. 3 is the default.")
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
        self.chat_model = QComboBox()
        self.chat_model.setEditable(True)  # any Ollama model name works too
        for b in firstrun.BRAINS:
            self.chat_model.addItem(f"{b.model}", b.model)
            self.chat_model.setItemData(self.chat_model.count() - 1, f"{b.label}: ~{b.gb:g} GB, {b.note}", Qt.ToolTipRole)
        self.chat_model.setCurrentText(v["chat_model"])
        self.chat_model.setToolTip("Its brain: qwen3:4b (light, ~2.5 GB), qwen3:8b (balanced, ~5.2 GB), qwen3:14b "
                                   "(smartest, ~9.3 GB). A new one downloads after the restart; remove old ones from "
                                   "the tray menu > Check requirements.")
        self.check_updates = QCheckBox("Check for updates")
        self.check_updates.setChecked(v.get("check_updates", True))
        self.auto_update = QCheckBox("Install updates without asking (while I'm away)")
        self.auto_update.setChecked(v.get("auto_update", False))
        version = updater.current_version()
        check_now = QPushButton("Check now", objectName="secondary")
        check_now.clicked.connect(on_check_updates)
        version_row = QHBoxLayout()
        version_row.addWidget(QLabel(f"v{version}" if version else "running from source (update with git)"))
        version_row.addStretch(1)
        version_row.addWidget(check_now)
        check_now.setEnabled(updater.can_update())

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
        form.addRow("Temperature", self.temperature)
        form.addRow("", self.mischief)
        form.addRow("", self.awareness)
        form.addRow("", self.notice)
        form.addRow("", self.adventures)
        form.addRow("", self.supervised)
        form.addRow("", self.autostart)
        form.addRow("Brain (model)", self.chat_model)
        note = QLabel("Listening, brain and ask-every-step apply after a restart.", objectName="note")
        note.setWordWrap(True)
        form.addRow(note)
        form.addRow(self._section("Updates"))
        form.addRow("Version", version_row)
        form.addRow("", self.check_updates)
        form.addRow("", self.auto_update)

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
            "temperature": self.temperature.value(),
            "adventures": self.adventures.isChecked(),
            "supervised": self.supervised.isChecked(),
            "chat_model": self.chat_model.currentText().strip() or CONFIG.chat_model,
            "check_updates": self.check_updates.isChecked(),
            "auto_update": self.auto_update.isChecked(),
        }

    def _save(self) -> None:
        values = self.values()
        if self.autostart.isChecked() != autostart.is_enabled():
            autostart.set_enabled(self.autostart.isChecked())
        needs_restart = any(values[k] != self._initial.get(k) for k in RESTART_KEYS)
        self._on_save(values, needs_restart)
        self.closed.emit()
