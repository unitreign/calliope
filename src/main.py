from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow

# Resolve icon relative to this file so it works regardless of cwd
_ICON_PATH = Path(__file__).parent.parent / "icon.ico"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Calliope")
    app.setOrganizationName("Calliope")

    if _ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_ICON_PATH)))

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
