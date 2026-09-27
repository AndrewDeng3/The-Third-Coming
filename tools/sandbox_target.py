"""A throwaway app for testing real input injection safely. Nothing else on the desktop is touched.

    python tools/sandbox_target.py <state_file>

Writes its editor text + button clicks to <state_file> (JSON) on every change.
Has a password field (UIA IsPassword) and a destructive-sounding button for guard tests.
"""

import json
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget

state_file = sys.argv[1]
state = {"text": "", "password_len": 0, "clicks": []}


def save():
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f)


app = QApplication(sys.argv)
w = QWidget()
w.setWindowTitle("StickFigure Sandbox")
w.setWindowFlag(Qt.WindowStaysOnTopHint)
col = QVBoxLayout(w)
col.addWidget(QLabel("Test document (safe to type in)"))
editor = QTextEdit()
editor.setAccessibleName("Test document")
editor.setObjectName("editor")
col.addWidget(editor, 1)
pw = QLineEdit()
pw.setEchoMode(QLineEdit.Password)
pw.setAccessibleName("Password")
col.addWidget(pw)
for label in ("Bold", "Delete everything"):
    b = QPushButton(label)
    b.clicked.connect(lambda _=False, l=label: (state["clicks"].append(l), save()))
    col.addWidget(b)


def on_text():
    state["text"] = editor.toPlainText()
    save()


editor.textChanged.connect(on_text)
pw.textChanged.connect(lambda t: (state.__setitem__("password_len", len(t)), save()))
w.resize(900, 700)
w.move(200, 200)
save()
w.show()
w.raise_()
w.activateWindow()
editor.setFocus()
sys.exit(app.exec())
