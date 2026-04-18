from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.profile import (
    DEFAULT_FILENAME_FORMAT,
    ProfileStore,
    SyncProfile,
)


TOKEN_HELP_TEXT = """\
<b>Available tokens</b>
<pre>{track}      - Track number (e.g., 1, 2, 3)
{track:02d}  - Track number, zero-padded (e.g., 01, 02, 03)
{title}      - Song title
{artist}     - Artist name
{album}      - Album name
{year}       - Release year
{format}     - File extension without dot (e.g., flac, mp3)
{folder}     - Relative folder path from source root
{filename}   - Original filename without extension</pre>"""


class ProfileManagerDialog(QDialog):
    profiles_changed = pyqtSignal()

    def __init__(self, store: ProfileStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self._editing_original_name: str | None = None

        self.setWindowTitle("Profiles")
        self.setModal(True)
        self.setMinimumSize(700, 480)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack)

        self.list_page = self._build_list_page()
        self.edit_page = self._build_edit_page()

        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.edit_page)

        self.show_list_mode()

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Profiles")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        hint = QLabel("Profiles store your filename format, art resize settings, and FLAC block size.")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setSpacing(6)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.cards_scroll.setWidget(self.cards_container)
        layout.addWidget(self.cards_scroll, stretch=1)

        create_btn = QPushButton("+ New Profile")
        create_btn.setObjectName("PrimaryButton")
        create_btn.clicked.connect(self.start_create)
        layout.addWidget(create_btn, alignment=Qt.AlignmentFlag.AlignRight)

        return page

    def _build_labeled_row(self, label_text: str, input_widget: QWidget) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)

        label = QLabel(label_text)
        label.setMinimumWidth(160)
        label.setObjectName("MutedLabel")
        row_layout.addWidget(label)
        row_layout.addWidget(input_widget, stretch=1)
        return row

    def _section_divider(self, text: str) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 4)
        lbl = QLabel(text)
        lbl.setObjectName("SectionTag")
        layout.addWidget(lbl)
        layout.addStretch(1)
        return w

    def _build_edit_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        self.edit_title = QLabel("Edit Profile")
        self.edit_title.setObjectName("SectionTitle")
        layout.addWidget(self.edit_title)

        # ── Identity ──────────────────────────────────────────────────────────
        layout.addWidget(self._section_divider("IDENTITY"))

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Profile name")
        layout.addWidget(self._build_labeled_row("Name", self.name_input))

        # ── File naming ───────────────────────────────────────────────────────
        layout.addWidget(self._section_divider("FILE NAMING"))

        self.filename_format_input = QLineEdit()
        self.filename_format_input.setPlaceholderText("{folder}/{filename}.{format}")
        layout.addWidget(self._build_labeled_row("Filename format", self.filename_format_input))

        token_label = QLabel(TOKEN_HELP_TEXT)
        token_label.setObjectName("TokenHelp")
        token_label.setTextFormat(Qt.TextFormat.RichText)
        token_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(token_label)

        # ── Processing ────────────────────────────────────────────────────────
        layout.addWidget(self._section_divider("PROCESSING"))

        art_row = QWidget()
        art_row_layout = QHBoxLayout(art_row)
        art_row_layout.setContentsMargins(0, 0, 0, 0)
        art_row_layout.setSpacing(6)

        self.art_width_input = QSpinBox()
        self.art_width_input.setRange(1, 5000)
        self.art_width_input.setValue(750)
        self.art_width_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.art_width_input.setFixedWidth(80)

        self.art_height_input = QSpinBox()
        self.art_height_input.setRange(1, 5000)
        self.art_height_input.setValue(750)
        self.art_height_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.art_height_input.setFixedWidth(80)

        art_row_layout.addWidget(self.art_width_input)
        art_row_layout.addWidget(QLabel("×"))
        art_row_layout.addWidget(self.art_height_input)
        art_row_layout.addWidget(QLabel("px"))
        art_row_layout.addStretch(1)

        layout.addWidget(self._build_labeled_row("Album art size", art_row))

        art_hint = QLabel("Applies to all formats with embedded art (FLAC, MP3, M4A, OGG).")
        art_hint.setObjectName("MutedLabel")
        art_hint.setWordWrap(True)
        layout.addWidget(art_hint)

        block_row = QWidget()
        block_row_layout = QHBoxLayout(block_row)
        block_row_layout.setContentsMargins(0, 0, 0, 0)
        block_row_layout.setSpacing(8)

        self.apply_block_size_check = QCheckBox("Limit block size to")
        self.apply_block_size_check.setChecked(True)
        self.apply_block_size_check.toggled.connect(self._on_block_size_toggled)

        self.block_size_input = QLineEdit()
        self.block_size_input.setPlaceholderText("4096")
        self.block_size_input.setFixedWidth(80)

        block_row_layout.addWidget(self.apply_block_size_check)
        block_row_layout.addWidget(self.block_size_input)
        block_row_layout.addWidget(QLabel("samples"))
        block_row_layout.addStretch(1)

        layout.addWidget(self._build_labeled_row("FLAC block size", block_row))

        flac_hint = QLabel("When enabled, re-encodes FLAC files whose block size exceeds the limit. Leaves other formats untouched.")
        flac_hint.setObjectName("MutedLabel")
        flac_hint.setWordWrap(True)
        layout.addWidget(flac_hint)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.show_list_mode)
        save_btn = QPushButton("Save Profile")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.save_profile)

        button_row.addStretch(1)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(save_btn)
        layout.addLayout(button_row)

        return page

    # ------------------------------------------------------------------ list

    def refresh_list(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        for profile in self.store.all():
            card = QFrame()
            card.setObjectName("ProfileCard")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(8)

            name_lbl = QLabel(profile.name)
            name_lbl.setObjectName("ProfileName")

            card_layout.addWidget(name_lbl)
            card_layout.addStretch(1)

            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(lambda _, p=profile: self.start_edit(p))
            delete_btn = QPushButton("Delete")
            delete_btn.setObjectName("DangerButton")
            delete_btn.clicked.connect(lambda _, n=profile.name: self.delete_profile(n))

            card_layout.addWidget(edit_btn)
            card_layout.addWidget(delete_btn)

            self.cards_layout.addWidget(card)

        self.cards_layout.addStretch(1)

    def _on_block_size_toggled(self, checked: bool) -> None:
        self.block_size_input.setEnabled(checked)

    # ------------------------------------------------------------------ CRUD

    def show_list_mode(self) -> None:
        self._editing_original_name = None
        self.stack.setCurrentWidget(self.list_page)
        self.refresh_list()

    def start_create(self) -> None:
        self._editing_original_name = None
        self.edit_title.setText("New Profile")
        self.name_input.setText("")
        self.art_width_input.setValue(750)
        self.art_height_input.setValue(750)
        self.filename_format_input.setText(DEFAULT_FILENAME_FORMAT)
        self.apply_block_size_check.setChecked(True)
        self.block_size_input.setText("4096")
        self.block_size_input.setEnabled(True)
        self.stack.setCurrentWidget(self.edit_page)

    def start_edit(self, profile: SyncProfile) -> None:
        self._editing_original_name = profile.name
        self.edit_title.setText("Edit Profile")
        self.name_input.setText(profile.name)
        self.art_width_input.setValue(profile.art_dimensions[0])
        self.art_height_input.setValue(profile.art_dimensions[1])
        self.filename_format_input.setText(profile.filename_format)
        self.apply_block_size_check.setChecked(profile.apply_block_size)
        self.block_size_input.setText(str(profile.max_block_size))
        self.block_size_input.setEnabled(profile.apply_block_size)
        self.stack.setCurrentWidget(self.edit_page)

    def save_profile(self) -> None:
        name = self.name_input.text().strip()
        filename_format = self.filename_format_input.text().strip() or DEFAULT_FILENAME_FORMAT
        block_raw = self.block_size_input.text().strip() or "4096"

        try:
            block_size = int(block_raw)
        except ValueError:
            QMessageBox.warning(self, "Invalid Value", "Max block size must be a number.")
            return

        if not name:
            QMessageBox.warning(self, "Invalid Value", "Profile name is required.")
            return

        profile = SyncProfile(
            name=name,
            album_art_width=self.art_width_input.value(),
            album_art_height=self.art_height_input.value(),
            filename_format=filename_format,
            max_block_size=block_size,
            apply_block_size=self.apply_block_size_check.isChecked(),
        )

        try:
            self.store.upsert(profile, self._editing_original_name)
        except ValueError as exc:
            QMessageBox.warning(self, "Profile Error", str(exc))
            return

        self.profiles_changed.emit()
        self.show_list_mode()

    def delete_profile(self, name: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.store.delete(name)
        self.profiles_changed.emit()
        self.refresh_list()
