from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


def _read_art_bytes(path: Path) -> bytes | None:
    """Return raw embedded cover-art bytes from an audio file, or None."""
    ext = path.suffix.lower()
    try:
        if ext == ".flac":
            from mutagen.flac import FLAC
            f = FLAC(path)
            if f.pictures:
                return f.pictures[0].data

        elif ext in (".mp3", ".aiff"):
            from mutagen.id3 import ID3
            frames = ID3(path).getall("APIC")
            if frames and frames[0].data:
                return frames[0].data

        elif ext in (".m4a", ".aac"):
            from mutagen.mp4 import MP4
            tags = MP4(path).tags
            if tags and "covr" in tags and tags["covr"]:
                return bytes(tags["covr"][0])

        elif ext == ".ogg":
            import base64
            from mutagen.flac import Picture
            from mutagen.oggvorbis import OggVorbis
            audio = OggVorbis(path)
            pics = audio.get("metadata_block_picture", [])
            if pics:
                return Picture(base64.b64decode(pics[0])).data

    except Exception:
        pass
    return None


class MiniPlayerDialog(QDialog):
    _ART_SIZE = 260

    def __init__(self, config: dict, save_config_fn, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._save_config = save_config_fn
        self._duration_ms: int = 0
        self._seeking: bool = False

        self.setWindowTitle("Now Playing")
        self.setModal(False)
        self.setFixedSize(self._ART_SIZE + 40, self._ART_SIZE + 160)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        # ── Media backend ────────────────────────────────────────────────────
        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)

        vol = max(0, min(100, int(self._config.get("player_volume", 30))))
        self._audio_out.setVolume(vol / 100.0)

        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)

        # ── Layout ───────────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        # Album art
        self._art_label = QLabel()
        self._art_label.setFixedSize(self._ART_SIZE, self._ART_SIZE)
        self._art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._art_label.setStyleSheet(
            "background:#1e1e1e; border-radius:4px; color:#555; font-size:32px;"
        )
        self._art_label.setText("♪")
        root.addWidget(self._art_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Title / artist
        self._title_lbl = QLabel("—")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet("font-weight:600; font-size:12px;")

        self._artist_lbl = QLabel("")
        self._artist_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._artist_lbl.setStyleSheet("color:#888; font-size:11px;")

        root.addWidget(self._title_lbl)
        root.addWidget(self._artist_lbl)

        # Seek bar
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 1000)
        self._seek.sliderPressed.connect(self._on_seek_pressed)
        self._seek.sliderReleased.connect(self._on_seek_released)
        root.addWidget(self._seek)

        # Controls row: play/pause on left, vol on right
        controls = QHBoxLayout()
        controls.setSpacing(6)

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(36, 28)
        self._play_btn.clicked.connect(self._toggle_playback)
        controls.addWidget(self._play_btn)

        controls.addStretch(1)

        vol_lbl = QLabel("vol")
        vol_lbl.setStyleSheet("color:#888; font-size:10px;")
        self._vol = QSlider(Qt.Orientation.Horizontal)
        self._vol.setRange(0, 100)
        self._vol.setValue(vol)
        self._vol.setFixedWidth(80)
        self._vol.valueChanged.connect(self._on_volume_changed)

        controls.addWidget(vol_lbl)
        controls.addWidget(self._vol)

        root.addLayout(controls)

    # ── Public ───────────────────────────────────────────────────────────────

    def load(self, path: Path) -> None:
        """Load a file and start playing it immediately."""
        self._player.stop()
        self._seek.setValue(0)
        self._duration_ms = 0

        # Art
        art_bytes = _read_art_bytes(path)
        if art_bytes:
            px = QPixmap()
            px.loadFromData(art_bytes)
            self._art_label.setPixmap(
                px.scaled(
                    self._ART_SIZE,
                    self._ART_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self._art_label.setText("")
        else:
            self._art_label.clear()
            self._art_label.setText("♪")

        # Tags
        title = path.stem
        artist = ""
        try:
            from mutagen import File as MutagenFile
            tags = MutagenFile(path, easy=True)
            if tags:
                title = (tags.get("title") or [path.stem])[0]
                artist = (tags.get("artist") or [""])[0]
        except Exception:
            pass

        self._title_lbl.setText(title)
        self._artist_lbl.setText(artist)
        self.setWindowTitle(f"♪  {title}")

        self._player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self._player.play()

    # ── Slots ────────────────────────────────────────────────────────────────

    def _toggle_playback(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self._play_btn.setText("⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

    def _on_duration_changed(self, ms: int) -> None:
        self._duration_ms = ms

    def _on_position_changed(self, ms: int) -> None:
        if not self._seeking and self._duration_ms > 0:
            self._seek.setValue(int(ms * 1000 / self._duration_ms))

    def _on_seek_pressed(self) -> None:
        self._seeking = True

    def _on_seek_released(self) -> None:
        if self._duration_ms > 0:
            self._player.setPosition(int(self._seek.value() * self._duration_ms / 1000))
        self._seeking = False

    def _on_volume_changed(self, value: int) -> None:
        self._audio_out.setVolume(value / 100.0)
        self._config["player_volume"] = value
        self._save_config(self._config)

    def closeEvent(self, event) -> None:
        self._player.stop()
        super().closeEvent(event)
