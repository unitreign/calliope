from __future__ import annotations

from datetime import datetime
from typing import Callable


class AppLogger:
    def __init__(self, on_log: Callable[[str], None] | None = None) -> None:
        self.lines: list[str] = []
        self.on_log = on_log

    def bind(self, callback: Callable[[str], None]) -> None:
        self.on_log = callback

    def log(self, message: str) -> str:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        self.lines.append(line)
        if self.on_log:
            self.on_log(line)
        return line

    def clear(self) -> None:
        self.lines.clear()

    def text(self) -> str:
        return "\n".join(self.lines)
