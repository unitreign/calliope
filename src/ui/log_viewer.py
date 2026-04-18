from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout


class LogViewerDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sync Log")
        self.resize(900, 520)

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.copy_btn = QPushButton("Copy")
        self.clear_btn = QPushButton("Clear")
        self.close_btn = QPushButton("✕")

        top.addWidget(self.copy_btn)
        top.addWidget(self.clear_btn)
        top.addStretch(1)
        top.addWidget(self.close_btn)
        layout.addLayout(top)

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.text_area.setObjectName("LogArea")
        layout.addWidget(self.text_area, stretch=1)

        self.copy_btn.clicked.connect(self.copy_text)
        self.clear_btn.clicked.connect(self.clear)
        self.close_btn.clicked.connect(self.close)

    def set_lines(self, lines: list[str]) -> None:
        self.text_area.setPlainText("\n".join(lines))
        self.text_area.verticalScrollBar().setValue(self.text_area.verticalScrollBar().maximum())

    def append_line(self, line: str) -> None:
        cursor = self.text_area.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        if self.text_area.toPlainText():
            cursor.insertText("\n")
        cursor.insertText(line)
        self.text_area.setTextCursor(cursor)
        self.text_area.verticalScrollBar().setValue(self.text_area.verticalScrollBar().maximum())

    def copy_text(self) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.text_area.toPlainText(), mode=clipboard.Mode.Clipboard)

    def clear(self) -> None:
        self.text_area.clear()
