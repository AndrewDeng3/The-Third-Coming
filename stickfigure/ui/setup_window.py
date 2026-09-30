"""First-run setup: installs what The Third Coming needs to think (Ollama + its AI models), with progress.

Shown at startup only if something is missing (or from the tray menu). Nothing is downloaded until the user
clicks "Set everything up". Ollama's own installer runs visibly, so the user sees exactly what's installed.
"""

from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from dataclasses import replace

from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from stickfigure import firstrun
from stickfigure.config import CONFIG, Config, load_saved_settings, update_settings

log = logging.getLogger(__name__)


class _Row:
    def __init__(self, grid: QGridLayout, r: int, title: str, detail: str):
        self.title = QLabel(f"<b>{title}</b><br><span style='color:#9a9aa6'>{detail}</span>")
        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        grid.addWidget(self.title, 2 * r, 0)
        grid.addWidget(self.status, 2 * r, 1)
        grid.addWidget(self.bar, 2 * r + 1, 0, 1, 2)

    def set(self, text: str, color: str = "#e8e8ee", progress: float | None = None) -> None:
        self.status.setText(f"<span style='color:{color}'>{text}</span>")
        if progress is None:
            self.bar.hide()
        else:
            self.bar.show()
            self.bar.setValue(round(max(0.0, min(1.0, progress)) * 1000))


class SetupWindow(QWidget):
    finished = Signal(bool)  # True: everything's ready

    def __init__(self, scale: float, icon: QIcon | None = None, cfg: Config = CONFIG):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.started_with = cfg.chat_model  # the model this running copy loaded (a different pick needs a restart)
        saved = load_saved_settings(cfg).get("chat_model")
        chosen = saved or firstrun.recommended_brain().model  # a fresh install: pick what this PC's GPU can run
        self.cfg = self._with_model(cfg, chosen)
        self.setWindowTitle("The Third Coming - setup")
        if icon is not None:
            self.setWindowIcon(icon)
        self.resize(round(560 * scale), round(400 * scale))
        px = lambda v: f"{round(v * scale)}px"  # noqa: E731
        self.setStyleSheet(f"""
            QWidget {{ background: #1e1f24; color: #e8e8ee; font-family: 'Segoe UI'; font-size: 10pt; }}
            QPushButton {{ background: #f7931e; color: #1e1f24; border: none; border-radius: {px(8)};
                           padding: {px(8)} {px(16)}; font-weight: 600; }}
            QPushButton:disabled {{ background: #5a4a36; }}
            QPushButton#secondary {{ background: #3a3b44; color: #e8e8ee; }}
            QProgressBar {{ background: #2a2b32; border: none; border-radius: {px(3)}; max-height: {px(6)}; }}
            QProgressBar::chunk {{ background: #f7931e; border-radius: {px(3)}; }}
            QLabel#head {{ font-size: 14pt; font-weight: 700; }}
            QLabel#note {{ color: #9a9aa6; font-size: 9pt; }}
            QComboBox {{ background: #2a2b32; border: 1px solid #3a3b44; border-radius: {px(6)}; padding: {px(4)} {px(8)}; }}
            QComboBox QAbstractItemView {{ background: #2a2b32; selection-background-color: #f7931e; }}
        """)
        head = QLabel("Let's wake up The Third Coming", objectName="head")
        intro = QLabel("It thinks with local AI (nothing leaves this computer). A few things need downloading once:",
                       objectName="note")
        intro.setWordWrap(True)
        self.brain = QComboBox()
        recommended = firstrun.recommended_brain()
        for b in firstrun.BRAINS:
            tag = "  - recommended for this PC" if b is recommended else ""
            self.brain.addItem(f"{b.label} ({b.model}, ~{b.gb:g} GB){tag}", b.model)
        if firstrun.brain_for(self.cfg.chat_model) is None:  # a custom model from Settings
            self.brain.addItem(f"{self.cfg.chat_model} (your custom model)", self.cfg.chat_model)
        self.brain.setCurrentIndex(max(0, self.brain.findData(self.cfg.chat_model)))
        self.brain.currentIndexChanged.connect(self._brain_changed)
        brain_row = QHBoxLayout()
        brain_row.addWidget(QLabel("<b>Brain size</b>"))
        brain_row.addWidget(self.brain, 1)
        self.brain_note = QLabel("", objectName="note")
        self.brain_note.setWordWrap(True)
        self._show_brain_note()
        self.grid = grid = QGridLayout()
        grid.setVerticalSpacing(round(4 * scale))
        self.ollama_row = _Row(grid, 0, "Ollama", "the local AI engine (from ollama.com, ~1 GB installed)")
        self.model_rows: dict[str, _Row] = {}
        self._build_model_rows()
        voice = QLabel("Voice (speaking/listening, ~0.5 GB) downloads by itself in the background the first time.",
                       objectName="note")
        voice.setWordWrap(True)
        self.go = QPushButton("Set everything up")
        self.go.clicked.connect(lambda: asyncio.ensure_future(self._run()))
        self.skip = QPushButton("Skip for now", objectName="secondary")
        self.skip.clicked.connect(self.close)
        self.free = QPushButton("", objectName="secondary")
        self.free.clicked.connect(lambda: asyncio.ensure_future(self._free_space()))
        self.free.hide()
        buttons = QHBoxLayout()
        buttons.addWidget(self.free)
        buttons.addStretch(1)
        buttons.addWidget(self.skip)
        buttons.addWidget(self.go)
        col = QVBoxLayout(self)
        col.setContentsMargins(round(18 * scale), round(16 * scale), round(18 * scale), round(14 * scale))
        col.addWidget(head)
        col.addWidget(intro)
        col.addSpacing(round(6 * scale))
        col.addLayout(brain_row)
        col.addWidget(self.brain_note)
        col.addSpacing(round(4 * scale))
        col.addLayout(grid)
        col.addStretch(1)
        col.addWidget(voice)
        col.addLayout(buttons)
        self._busy = False
        self._cancel = False
        self._done = False
        self.status: firstrun.Status | None = None

    # -- brain size ------------------------------------------------------------------------------

    @staticmethod
    def _with_model(cfg: Config, model: str) -> Config:
        return replace(cfg, chat_model=model, extract_model=model, action_model=model, code_model=model)

    @property
    def restart_needed(self) -> bool:
        return self.cfg.chat_model != self.started_with

    def _build_model_rows(self) -> None:
        for row in self.model_rows.values():
            for w in (row.title, row.status, row.bar):
                self.grid.removeWidget(w)
                w.deleteLater()
        self.model_rows = {}
        for i, m in enumerate(firstrun.required_models(self.cfg), start=1):
            size = firstrun.MODEL_SIZES.get(m)
            kind = "the brain" if m == self.cfg.chat_model else "memory (finds related things it remembers)"
            self.model_rows[m] = _Row(self.grid, i, m, f"AI model: {kind}{f', ~{size:g} GB' if size else ''}")

    def _show_brain_note(self) -> None:
        b = firstrun.brain_for(self.cfg.chat_model)
        self.brain_note.setText(f"{b.note[0].upper()}{b.note[1:]}. Bigger is smarter but a bigger download; you can "
                                "change it later in Settings." if b else "A custom model you picked in Settings.")

    def _brain_changed(self) -> None:
        model = self.brain.currentData()
        if not model or model == self.cfg.chat_model:
            return
        self.cfg = self._with_model(self.cfg, model)
        update_settings({"chat_model": model}, self.cfg)
        self._show_brain_note()
        self._build_model_rows()
        if self._done:  # it was ready with the old brain: set up the new one
            self._done = False
            self.skip.show()
            try:
                self.go.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.go.clicked.connect(lambda: asyncio.ensure_future(self._run()))
            self.go.setText("Set everything up")
        self.free.hide()

        async def refresh() -> None:
            self.show_status(await asyncio.to_thread(firstrun.check, self.cfg))

        asyncio.ensure_future(refresh())

    async def _show_free_space(self) -> None:
        unused = await asyncio.to_thread(firstrun.unused_brains, self.cfg.chat_model, self.cfg.ollama_url)
        if unused and self._done:
            gb = sum(b.gb for b in unused)
            self.free.setText(f"Remove unused brains (frees ~{gb:g} GB)")
            self.free.setToolTip("Deletes " + ", ".join(b.model for b in unused) + " from Ollama.")
            self.free.show()
        else:
            self.free.hide()

    async def _free_space(self) -> None:
        self.free.setEnabled(False)
        try:
            for b in await asyncio.to_thread(firstrun.unused_brains, self.cfg.chat_model, self.cfg.ollama_url):
                await asyncio.to_thread(firstrun.delete_model, b.model, self.cfg.ollama_url)
            self.free.hide()
        except Exception as e:  # noqa: BLE001 - shown on the button
            log.warning("removing unused models failed: %s", e)
            self.free.setText("Couldn't remove them (see the log)")
        finally:
            self.free.setEnabled(True)

    # -- state ----------------------------------------------------------------------------------

    def show_status(self, st: firstrun.Status) -> None:
        self.status = st
        if st.ollama_running:
            self.ollama_row.set("✓ running", "#7fd48a")
        elif st.ollama_installed:
            self.ollama_row.set("installed, will start it", "#f7c66a")
        else:
            self.ollama_row.set("not installed", "#f7c66a")
        for m, row in self.model_rows.items():
            if not st.ollama_running:
                row.set("waiting for Ollama", "#9a9aa6")
            elif m in st.missing_models:
                row.set("to download", "#f7c66a")
            else:
                row.set("✓ ready", "#7fd48a")
        if st.ready:
            self._finish_ok()

    def _finish_ok(self) -> None:
        self._done = True
        asyncio.ensure_future(self._show_free_space())
        self.go.setText("Let's go!")
        self.go.setEnabled(True)
        self.skip.hide()
        try:
            self.go.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.go.clicked.connect(self.close)

    def closeEvent(self, e) -> None:
        self._cancel = True
        super().closeEvent(e)
        self.finished.emit(self._done)

    # -- doing it ------------------------------------------------------------------------------------

    def _progress(self, row: _Row):
        loop = asyncio.get_running_loop()

        def report(frac: float, text: str) -> None:
            loop.call_soon_threadsafe(lambda: row.set(text[:60], "#e8e8ee", frac))

        return report

    async def _run(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.brain.setEnabled(False)
        self.go.setEnabled(False)
        self.go.setText("Working…")
        cancelled = lambda: self._cancel  # noqa: E731
        current = self.ollama_row  # where to show an error
        try:
            st = await asyncio.to_thread(firstrun.check, self.cfg)
            if not st.ollama_installed:
                installer = await asyncio.to_thread(firstrun.download_ollama_installer,
                                                    self._progress(self.ollama_row), cancelled)
                self.ollama_row.set("Follow the Ollama installer window…", "#f7c66a")
                await asyncio.to_thread(firstrun.run_ollama_installer, installer)
            if not st.ollama_running:
                self.ollama_row.set("starting…", "#f7c66a")
                if not await asyncio.to_thread(firstrun.start_ollama, self.cfg.ollama_url):
                    raise RuntimeError("Ollama didn't start. Try opening the Ollama app, then Retry.")
            self.ollama_row.set("✓ running", "#7fd48a")
            st = await asyncio.to_thread(firstrun.check, self.cfg)
            for m in st.missing_models:
                row = self.model_rows.get(m)
                if row is None:
                    continue
                current = row
                await asyncio.to_thread(firstrun.pull_model, m, self._progress(row), self.cfg.ollama_url, cancelled)
                row.set("✓ ready", "#7fd48a")
            self.show_status(await asyncio.to_thread(firstrun.check, self.cfg))
        except InterruptedError:
            return
        except Exception as e:  # show it; the user can retry
            log.warning("setup failed: %s", e)
            self.go.setText("Retry")
            self.go.setEnabled(True)
            current.set(f"⚠ {(str(e) or e.__class__.__name__)[:70]}", "#ff8a80")
        finally:
            self._busy = False
            self.brain.setEnabled(True)
