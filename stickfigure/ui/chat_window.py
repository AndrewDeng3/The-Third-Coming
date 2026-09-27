"""Chat window: transcript with streaming assistant messages, input box, and a mood line.

It's a normal top-level window, so the figure can walk on top of it like any other.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QStackedWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from stickfigure.ui.markdown import to_html

USER_BG = "#2f6fdb"
BOT_BG = "#3a3b44"
NOTE_FG = "#9a9aa6"


@dataclass
class _Msg:
    role: str  # "user" | "assistant" | "note"
    text: str
    pending: bool = False


class ChatInput(QPlainTextEdit):
    """Multi-line message box: Enter sends, Shift+Enter adds a line. Grows with its text (up to 8 lines)."""

    send = Signal()
    edited = Signal()

    def __init__(self, placeholder: str):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._programmatic = False
        self.textChanged.connect(self._changed)
        self.document().documentLayout().documentSizeChanged.connect(lambda _: self._fit())
        self._fit()

    def text(self) -> str:
        return self.toPlainText()

    def clear(self) -> None:
        self._programmatic = True
        try:
            super().clear()
        finally:
            self._programmatic = False

    def _changed(self) -> None:
        if not self._programmatic:
            self.edited.emit()

    def _fit(self) -> None:
        line = self.fontMetrics().lineSpacing()
        lines = max(1, min(8, round(self.document().size().height())))  # plain-text layout counts lines
        pad = round(self.document().documentMargin() * 2) + self.frameWidth() * 2 + 14
        self.setFixedHeight(lines * line + pad)
        # Only scroll once it's at its tallest (otherwise a bar flickers in on a single line).
        full = round(self.document().size().height()) > 8
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if full else Qt.ScrollBarAlwaysOff)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(e)


class ChatWindow(QWidget):
    submitted = Signal(str)
    moved = Signal()
    mic_clicked = Signal()
    clear_clicked = Signal()
    activity = Signal()  # the user is typing / clicking here (resets the lounge timer)
    hidden = Signal()
    lounge_toggled = Signal(bool)
    settings_clicked = Signal()
    build_clicked = Signal()

    def __init__(self, name: str, scale: float):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle(f"Chat with {name}")
        self.name = name
        self.s = scale
        self._msgs: list[_Msg] = []
        self.resize(round(380 * scale), round(460 * scale))
        px = lambda v: f"{round(v * scale)}px"  # noqa: E731
        self.setStyleSheet(
            f"""
            QWidget {{ background: #1e1f24; color: #e8e8ee; font-family: 'Segoe UI'; font-size: 10.5pt; }}
            QTextBrowser {{ border: none; padding: {px(6)}; }}
            QLineEdit, QPlainTextEdit {{ background: #2a2b32; border: 1px solid #3a3b44; border-radius: {px(8)};
                         padding: {px(4)} {px(6)}; selection-background-color: {USER_BG}; }}
            QLineEdit:focus, QPlainTextEdit:focus {{ border-color: #f7931e; }}
            QPushButton {{ background: #f7931e; color: #1e1f24; border: none; border-radius: {px(8)};
                           padding: {px(7)} {px(14)}; font-weight: 600; }}
            QPushButton:disabled {{ background: #5a4a36; color: #1e1f24; }}
            QPushButton:checked {{ background: #de2d26; color: white; }}
            QPushButton#clear {{ background: transparent; color: {NOTE_FG}; border: 1px solid #3a3b44;
                                 padding: {px(3)} {px(10)}; font-weight: 400; font-size: 9pt; }}
            QPushButton#clear:hover {{ color: #e8e8ee; border-color: #9a9aa6; }}
            QPushButton#head {{ background: transparent; color: {NOTE_FG}; border: 1px solid #3a3b44;
                                padding: {px(3)} {px(8)}; font-weight: 400; font-size: 10pt; }}
            QPushButton#head:hover {{ color: #e8e8ee; border-color: #9a9aa6; }}
            QPushButton#head:checked {{ background: #3a3b44; color: #f7931e; border-color: #f7931e; }}
            QPushButton#secondary {{ background: #3a3b44; color: #e8e8ee; }}
            QPushButton#icon {{ background: #3a3b44; padding: {px(5)} {px(9)}; }}
            QLabel#mood {{ color: {NOTE_FG}; font-size: 9pt; padding: {px(4)} {px(8)}; }}
            QLabel#section {{ color: #f7931e; font-weight: 700; padding-top: {px(8)}; }}
            QLabel#note {{ color: {NOTE_FG}; font-size: 9pt; padding-top: {px(6)}; }}
            QComboBox, QDoubleSpinBox {{ background: #2a2b32; border: 1px solid #3a3b44; border-radius: {px(6)};
                                         padding: {px(5)} {px(8)}; }}
            QComboBox QAbstractItemView {{ background: #2a2b32; selection-background-color: #3a3b44; }}
            QCheckBox {{ spacing: {px(8)}; }}
            QSlider::groove:horizontal {{ height: {px(4)}; background: #3a3b44; border-radius: {px(2)}; }}
            QSlider::handle:horizontal {{ background: #f7931e; width: {px(14)}; margin: -{px(5)} 0; border-radius: {px(7)}; }}
            QScrollArea {{ border: none; }}
            """
        )
        self.mood = QLabel("", objectName="mood")
        self.mood.setWordWrap(True)  # long thoughts wrap instead of stretching the window
        self.mood.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.mood.setMinimumWidth(1)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._open_link)
        self.input = ChatInput(f"Say something to {name}…  (Shift+Enter: new line)")
        self.send = QPushButton("Send")
        self.mic = QPushButton("🎤")
        self.mic.setToolTip("Talk")
        self.mic.setCheckable(True)
        self.input.send.connect(self._submit)
        self.input.edited.connect(self.activity.emit)
        self.send.clicked.connect(self._submit)
        self.mic.clicked.connect(self.mic_clicked.emit)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.view)
        self.lounge = None

        row = QHBoxLayout()
        row.setContentsMargins(round(8 * scale), 0, round(8 * scale), round(8 * scale))
        row.addWidget(self.input, 1)
        row.addWidget(self.mic, 0, Qt.AlignBottom)
        row.addWidget(self.send, 0, Qt.AlignBottom)
        self.clear = QPushButton("Clear chat", objectName="clear")
        self.clear.setToolTip("Erase this conversation (long-term memories and mood are kept)")
        self.clear.clicked.connect(self.clear_clicked.emit)
        self.lounge_btn = QPushButton("🛋", objectName="head")
        self.lounge_btn.setToolTip("Lounge: let them relax in here (it also happens by itself when chat is quiet)")
        self.lounge_btn.setCheckable(True)
        self.lounge_btn.clicked.connect(lambda on: self.lounge_toggled.emit(on))
        self.build_btn = QPushButton("🧱", objectName="head")
        self.build_btn.setToolTip("Build mode: place and remove blocks yourself")
        self.build_btn.clicked.connect(self.build_clicked.emit)
        self.settings_btn = QPushButton("⚙", objectName="head")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.setCheckable(True)
        self.settings_btn.clicked.connect(lambda on: self.settings_clicked.emit() if on else self.show_chat())
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, round(8 * scale), 0)
        header.setSpacing(round(4 * scale))
        header.addWidget(self.mood, 1)
        header.addWidget(self.lounge_btn)
        header.addWidget(self.build_btn)
        header.addWidget(self.settings_btn)
        header.addWidget(self.clear)
        self.settings_panel = None
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(round(4 * scale))
        col.addLayout(header)
        col.addWidget(self.pages, 1)
        col.addLayout(row)

    # -- lounge -------------------------------------------------------------------------------

    def attach_lounge(self, lounge: QWidget) -> None:
        self.lounge = lounge
        self.pages.addWidget(lounge)
        lounge.poked.connect(self.activity.emit)

    def set_lounge(self, on: bool) -> None:
        if self.lounge is not None:
            self.pages.setCurrentWidget(self.lounge if on else self.view)
        self._sync_header()

    @property
    def in_lounge(self) -> bool:
        return self.lounge is not None and self.pages.currentWidget() is self.lounge

    # -- settings page ----------------------------------------------------------------------------

    def show_settings(self, panel: QWidget) -> None:
        """Swap in a fresh settings panel (it emits `closed` on Back/Save)."""
        if self.settings_panel is not None:
            self.pages.removeWidget(self.settings_panel)
            self.settings_panel.deleteLater()
        self.settings_panel = panel
        self.pages.addWidget(panel)
        panel.closed.connect(self.show_chat)
        self.pages.setCurrentWidget(panel)
        self._sync_header()

    @property
    def in_settings(self) -> bool:
        return self.settings_panel is not None and self.pages.currentWidget() is self.settings_panel

    def show_chat(self) -> None:
        self.pages.setCurrentWidget(self.view)
        self._sync_header()

    def _sync_header(self) -> None:
        self.lounge_btn.setChecked(self.in_lounge)
        self.settings_btn.setChecked(self.in_settings)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.hidden.emit()

    # -- transcript -----------------------------------------------------------------------

    def load_history(self, messages: list[dict]) -> None:
        self._msgs = [_Msg(m["role"], m["content"]) for m in messages if m["role"] in ("user", "assistant")]
        self._render()

    def add_user(self, text: str) -> None:
        self._msgs.append(_Msg("user", text))
        if self.in_lounge:
            self.lounge.talk_user(text)
        self._render()

    def add_note(self, text: str) -> None:
        self._msgs.append(_Msg("note", text))
        self._render()

    def start_assistant(self) -> None:
        self._msgs.append(_Msg("assistant", "", pending=True))
        self.set_busy(True)
        if self.in_lounge:
            self.lounge.talk_reply("", pending=True)
        self._render()

    def update_assistant(self, text: str) -> None:
        if self._msgs and self._msgs[-1].role == "assistant":
            self._msgs[-1].text = text
            if self.in_lounge:
                self.lounge.talk_reply(text, pending=True)
            self._render()

    def finish_assistant(self) -> None:
        if self._msgs and self._msgs[-1].role == "assistant":
            self._msgs[-1].pending = False
            if self.in_lounge:
                self.lounge.talk_reply(self._msgs[-1].text, pending=False)
            if not self._msgs[-1].text:
                self._msgs.pop()
        self.set_busy(False)
        self._render()

    def add_assistant(self, text: str) -> None:
        """A complete message that didn't come from a chat turn (e.g. idle chatter)."""
        self._msgs.append(_Msg("assistant", text))
        if self.in_lounge:
            self.lounge.talk_reply(text, pending=False)
        self._render()

    def set_busy(self, busy: bool) -> None:
        self.send.setEnabled(not busy)

    def set_name(self, name: str) -> None:
        self.name = name
        self.setWindowTitle(f"Chat with {name}")
        self.input.setPlaceholderText(f"Say something to {name}…  (Shift+Enter: new line)")
        self._render()

    def set_listening(self, on: bool) -> None:
        self.mic.setChecked(on)

    def set_mood(self, text: str) -> None:
        self.mood.setText(text)

    def _render(self) -> None:
        pad = round(8 * self.s)
        rows = []
        for m in self._msgs[-80:]:
            if m.role == "note":
                text = html.escape(m.text).replace("\n", "<br>")
                rows.append(f'<p align="center" style="color:{NOTE_FG}; font-size:9pt;">{text}</p>')
                continue
            text = to_html(m.text)
            if m.pending and not text:
                text = '<span style="color:#9a9aa6">…</span>'
            mine = m.role == "user"
            who = "" if mine else (f'<p style="margin:0 0 3px 0; color:#f7931e; font-size:8.5pt; font-weight:600;">'
                                   f'{html.escape(self.name)}</p>')
            wide = "```" in m.text or len(m.text) > 400  # code and long answers get the full width
            gap = "" if wide else "<td width='18%'></td>"
            rows.append(
                f'<table width="100%" cellspacing="0" cellpadding="0" style="margin: {pad//2}px 0;"><tr>'
                + (gap if mine else "")
                + f'<td align="{"right" if mine else "left"}">'
                f'<table cellpadding="{pad}" {"width=100%" if wide else ""} '
                f'style="background:{USER_BG if mine else BOT_BG};">'
                f"<tr><td>{who}{text}</td></tr></table></td>"
                + ("" if mine else gap)
                + "</tr></table>"
            )
        self.view.setHtml("".join(rows))
        self._scroll_to_end()
        QTimer.singleShot(0, self._scroll_to_end)  # again once the new HTML has been laid out
        QTimer.singleShot(60, self._scroll_to_end)

    def _scroll_to_end(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    @staticmethod
    def _open_link(url) -> None:
        if url.scheme() in ("http", "https"):
            import webbrowser

            webbrowser.open(url.toString())

    # -- input ---------------------------------------------------------------------------------

    def _submit(self) -> None:
        self.activity.emit()
        text = self.input.text().strip()
        if not text or not self.send.isEnabled():
            return
        self.input.clear()
        self.submitted.emit(text)

    def open_near(self, x: int, y: int, screen_rect) -> None:
        w, h = self.width(), self.height()
        left, top, right, bottom = screen_rect
        self.move(round(min(max(x - w // 2, left + 8), right - w - 8)), round(min(max(y - h - 40, top + 8), bottom - h - 8)))
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        self.moved.emit()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.moved.emit()
