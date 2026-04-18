from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calliope")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("Calliope")
        title.setObjectName("AboutTitle")
        subtitle = QLabel("Music Sync Manager v1.0.0")
        subtitle.setObjectName("AboutSubtitle")

        description = QLabel(
            "Calliope is a desktop sync manager for portable players that optimizes FLAC files, "
            "resizes embedded album art, and applies metadata-aware file naming during transfer. "
            "Specifically made for Echo and Echo Mini devices from FiiO Snowsky.\n\n"
            "O Calliope, inspire me."
        )
        description.setWordWrap(True)

        tech = QLabel("Built with PyQt6, mutagen, Pillow, pathlib, and subprocess.")
        tech.setWordWrap(True)

        github = QLabel('<a href="https://github.com/unitreign/calliope">GitHub Project</a>')
        github.setOpenExternalLinks(False)
        github.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(description)
        layout.addWidget(tech)
        layout.addWidget(github)
        layout.addLayout(close_row)
