from __future__ import annotations

from collections import deque
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QCloseEvent, QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.file_scanner import LibraryNode, flatten_files, scan_source_tree
from core.genre_manager import append_genre, read_genres, remove_genre, rename_genre, replace_genre
from core.profile import ProfileStore
from core.sync_engine import SyncEngine
from ui.about_dialog import AboutDialog
from ui.log_viewer import LogViewerDialog
from ui.mini_player import MiniPlayerDialog
from ui.profile_manager import ProfileManagerDialog
from ui.tree_widget import ROLE_IS_DIR, ROLE_PATH, MusicTreeWidget
from utils.config import load_config, load_sync_state, save_config
from utils.logger import AppLogger
from utils.scan_cache import load_cached_tree, save_cached_tree

# Sentinel stored in QListWidgetItem.data(UserRole) for the "(No Genre)" row.
_NO_GENRE_SENTINEL = '\x00'
_NO_GENRE_DISPLAY  = '(No Genre)'


class LibraryScanThread(QThread):
    scan_finished = pyqtSignal(str, str, object, str)
    scan_progress = pyqtSignal(int, int)  # files_done, files_total

    def __init__(self, kind: str, root_path: Path, force_refresh: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.root_path = root_path
        self.force_refresh = force_refresh

    def run(self) -> None:
        try:
            root = self.root_path.expanduser().resolve()
            cached = None if self.force_refresh else load_cached_tree(root)
            if cached is not None:
                self.scan_finished.emit(self.kind, str(root), cached, "")
                return

            def on_progress(done: int, total: int) -> None:
                self.scan_progress.emit(done, total)

            tree = scan_source_tree(root, progress_callback=on_progress)
            save_cached_tree(root, tree)
            self.scan_finished.emit(self.kind, str(root), tree, "")
        except Exception as exc:
            self.scan_finished.emit(self.kind, str(self.root_path), None, str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('Calliope')
        self.setMinimumSize(1280, 720)

        self.config = load_config()
        self.sync_state = load_sync_state()
        self.profile_store = ProfileStore()
        self.logger = AppLogger(on_log=self._on_new_log_line)

        self.source_path = Path(self.config.get('source_path') or '').expanduser() if self.config.get('source_path') else None
        self.target_path = Path(self.config.get('target_path') or '').expanduser() if self.config.get('target_path') else None
        self.current_engine: SyncEngine | None = None
        self._active_sync_mode = 'sync'
        self._mini_player: MiniPlayerDialog | None = None
        self._scan_thread: LibraryScanThread | None = None
        self._scan_queue: deque[tuple[str, Path, bool, str | None]] = deque()
        self._scan_busy = False
        self._has_any_synced_candidates = False

        self.source_tree_data: LibraryNode | None = None
        self.target_tree_data: LibraryNode | None = None
        self.source_checked_paths: set[str] = set()
        self.genre_index: dict[str, list[Path]] = {}

        self.total_files = 0
        self.processed_files = 0
        self.error_files = 0
        self.sync_batch_total = 0
        self.pending_active = False

        self.log_viewer = LogViewerDialog(self)
        self.log_viewer.clear_btn.clicked.connect(self._clear_logs)
        self.about_dialog = AboutDialog(self)

        self._build_ui()
        self._apply_styles()
        self._load_profiles()
        self._restore_paths()
        self._update_stats_cards()
        self._set_status('Ready', syncing=False)

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName('CentralWidget')
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        # ── Header panel ──────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName('HeaderPanel')
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(10)

        # Row 1: title + profile + action buttons
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        title = QLabel('Calliope')
        title.setObjectName('AppTitle')
        title.setToolTip('O Calliope, inspire me...')

        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        self.profile_combo.setToolTip('Active sync profile')
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)

        self.manage_profiles_btn = QPushButton('Profiles')
        self.manage_profiles_btn.setToolTip('Manage sync profiles')
        self.manage_profiles_btn.clicked.connect(self._open_profile_manager)

        self.about_btn = QPushButton()
        self.about_btn.setToolTip('About')
        self.about_btn.setObjectName('IconButton')
        self.about_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        self.about_btn.clicked.connect(self.about_dialog.exec)

        self.log_btn = QPushButton('Log')
        self.log_btn.setToolTip('Show sync log')
        self.log_btn.clicked.connect(self._open_log_viewer)

        self.select_source_btn = QPushButton('Source…')
        self.select_source_btn.setToolTip('Select source library folder')
        self.select_source_btn.clicked.connect(self._select_source)

        self.select_target_btn = QPushButton('Target…')
        self.select_target_btn.setToolTip('Select target device folder')
        self.select_target_btn.clicked.connect(self._select_target)

        self.resync_btn = QPushButton('Resync')
        self.resync_btn.setToolTip('Re-sync previously synced files')
        self.resync_btn.clicked.connect(self._start_resync)
        self.resync_btn.setEnabled(False)

        self.start_sync_btn = QPushButton('Sync')
        self.start_sync_btn.setObjectName('StartButton')
        self.start_sync_btn.setToolTip('Start syncing selected files')
        self.start_sync_btn.clicked.connect(self._toggle_sync)
        self.start_sync_btn.setEnabled(False)

        row1.addWidget(title)
        row1.addSpacing(6)
        row1.addWidget(self.profile_combo)
        row1.addWidget(self.manage_profiles_btn)
        row1.addStretch(1)
        row1.addWidget(self.about_btn)
        row1.addWidget(self.log_btn)
        row1.addWidget(self.select_source_btn)
        row1.addWidget(self.select_target_btn)
        row1.addWidget(self.resync_btn)
        row1.addWidget(self.start_sync_btn)
        header_layout.addLayout(row1)

        # Row 2: stat cards
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.total_card = self._build_stat_card('TOTAL', '#e8e4dc')
        self.processed_card = self._build_stat_card('SYNCED', '#9b6fd4')
        self.pending_card = self._build_stat_card('PENDING', '#c4a35a')
        self.errors_card = self._build_stat_card('ERRORS', '#c4524f')
        row2.addWidget(self.total_card['frame'])
        row2.addWidget(self.processed_card['frame'])
        row2.addWidget(self.pending_card['frame'])
        row2.addWidget(self.errors_card['frame'])
        header_layout.addLayout(row2)

        # Row 3: selection controls
        self.selection_row = QWidget()
        self.selection_row.setFixedHeight(24)
        selection_layout = QHBoxLayout(self.selection_row)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        selection_layout.setSpacing(12)
        self.selected_count_label = QLabel('0 files selected')
        self.selected_count_label.setObjectName('MutedLabel')
        self.select_all_link = QLabel('<a href="select">Select all</a>')
        self.select_all_link.setObjectName('LinkLabel')
        self.select_all_link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.select_all_link.linkActivated.connect(lambda _: self.source_tree.set_all_checked(True))
        self.deselect_all_link = QLabel('<a href="deselect">Deselect all</a>')
        self.deselect_all_link.setObjectName('LinkLabel')
        self.deselect_all_link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.deselect_all_link.linkActivated.connect(lambda _: self.source_tree.set_all_checked(False))
        selection_layout.addWidget(self.selected_count_label)
        selection_layout.addWidget(self.select_all_link)
        selection_layout.addWidget(self.deselect_all_link)
        selection_layout.addStretch(1)
        header_layout.addWidget(self.selection_row)

        root_layout.addWidget(header)

        # ── Tree panel ────────────────────────────────────────────────────────
        tree_panel = QFrame()
        tree_panel.setObjectName('TreePanel')
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(14, 12, 14, 12)
        tree_layout.setSpacing(8)

        # Tree header: dual path display with refresh buttons
        tree_header_row = QHBoxLayout()
        tree_header_row.setSpacing(6)

        src_tag = QLabel('SOURCE')
        src_tag.setObjectName('SectionTag')
        self.source_path_label = QLabel('(not selected)')
        self.source_path_label.setObjectName('PathLabel')
        self.source_path_label.setMaximumWidth(320)
        self.source_path_label.setToolTip('')

        self.refresh_source_btn = QPushButton('↺')
        self.refresh_source_btn.setObjectName('RefreshButton')
        self.refresh_source_btn.setFixedWidth(26)
        self.refresh_source_btn.setFixedHeight(22)
        self.refresh_source_btn.setToolTip('Refresh source library')
        self.refresh_source_btn.clicked.connect(self._on_refresh_source)
        self.refresh_source_btn.setEnabled(False)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName('VSeparator')
        sep.setFixedHeight(16)

        tgt_tag = QLabel('TARGET')
        tgt_tag.setObjectName('SectionTag')
        self.target_path_label = QLabel('(not selected)')
        self.target_path_label.setObjectName('PathLabel')
        self.target_path_label.setMaximumWidth(320)
        self.target_path_label.setToolTip('')

        self.refresh_target_btn = QPushButton('↺')
        self.refresh_target_btn.setObjectName('RefreshButton')
        self.refresh_target_btn.setFixedWidth(26)
        self.refresh_target_btn.setFixedHeight(22)
        self.refresh_target_btn.setToolTip('Refresh target library')
        self.refresh_target_btn.clicked.connect(self._on_refresh_target)
        self.refresh_target_btn.setEnabled(False)

        self.view_source_btn = QPushButton('Source')
        self.view_source_btn.setObjectName('ViewToggle')
        self.view_source_btn.setFixedHeight(26)
        self.view_source_btn.clicked.connect(lambda: self._switch_tree_view('source'))

        self.view_target_btn = QPushButton('Target')
        self.view_target_btn.setObjectName('ViewToggle')
        self.view_target_btn.setFixedHeight(26)
        self.view_target_btn.setEnabled(False)
        self.view_target_btn.clicked.connect(lambda: self._switch_tree_view('target'))

        tree_header_row.addWidget(src_tag)
        tree_header_row.addWidget(self.source_path_label)
        tree_header_row.addWidget(self.refresh_source_btn)
        tree_header_row.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)
        tree_header_row.addWidget(tgt_tag)
        tree_header_row.addWidget(self.target_path_label)
        tree_header_row.addWidget(self.refresh_target_btn)
        tree_header_row.addStretch(1)
        tree_header_row.addWidget(self.view_source_btn)
        tree_header_row.addWidget(self.view_target_btn)
        tree_layout.addLayout(tree_header_row)

        self.library_stack = QStackedWidget()

        source_page = QWidget()
        source_layout = QVBoxLayout(source_page)
        source_layout.setContentsMargins(0, 0, 0, 0)
        self.source_tree = MusicTreeWidget()
        self.source_tree.checked_files_changed.connect(self._on_source_checked_count_changed)
        self.source_tree.itemDoubleClicked.connect(self._on_source_item_double_clicked)
        source_layout.addWidget(self.source_tree)

        target_page = QWidget()
        target_layout = QHBoxLayout(target_page)
        target_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.target_tree = MusicTreeWidget()
        self.target_tree.itemDoubleClicked.connect(self._on_target_tree_item_double_clicked)
        self.target_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.target_tree.customContextMenuRequested.connect(self._on_target_tree_context_menu)
        splitter.addWidget(self.target_tree)
        self.genre_panel = self._build_genre_panel()
        splitter.addWidget(self.genre_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        target_layout.addWidget(splitter)

        self.library_stack.addWidget(source_page)
        self.library_stack.addWidget(target_page)
        tree_layout.addWidget(self.library_stack, stretch=1)

        root_layout.addWidget(tree_panel, stretch=1)

        # ── Status panel ──────────────────────────────────────────────────────
        status_panel = QFrame()
        status_panel.setObjectName('StatusPanel')
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(14, 7, 14, 7)
        status_layout.setSpacing(10)

        self.status_dot = QLabel('●')
        self.status_dot.setObjectName('StatusDot')
        self.status_text = QLabel('Ready')
        self.status_text.setObjectName('StatusText')

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat('0/0')
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)

        self.progress_label = QLabel('0/0')
        self.progress_label.setObjectName('MutedLabel')

        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_text)
        status_layout.addStretch(1)
        status_layout.addWidget(self.progress_label)
        status_layout.addWidget(self.progress_bar)

        root_layout.addWidget(status_panel)

        self.dot_timer = QTimer(self)
        self.dot_timer.setInterval(600)
        self.dot_timer.timeout.connect(self._pulse_dot)
        self._dot_visible = True

        self._switch_tree_view('source')

    def _build_genre_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName('GenrePanel')
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel('Genre Playlists')
        title.setObjectName('SectionTitle')
        subtitle = QLabel('Playlists via genre tags')
        subtitle.setObjectName('SectionTag')
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.genre_list = QListWidget()
        self.genre_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.genre_list.currentItemChanged.connect(self._on_genre_selected)
        layout.addWidget(self.genre_list, stretch=1)

        self.new_genre_name_input = QLineEdit()
        self.new_genre_name_input.setPlaceholderText('Playlist / genre name')
        layout.addWidget(self.new_genre_name_input)

        helper = QLabel('Double-click a song to add. Right-click to add selection. Double-click here to remove.')
        helper.setObjectName('MutedLabel')
        helper.setWordWrap(True)
        layout.addWidget(helper)

        self.new_genre_files_list = QListWidget()
        self.new_genre_files_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.new_genre_files_list.itemDoubleClicked.connect(self._on_builder_item_double_clicked)
        layout.addWidget(self.new_genre_files_list, stretch=1)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self.replace_genre_radio = QRadioButton('Replace')
        self.replace_genre_radio.setObjectName('GenreMode')
        self.append_genre_radio = QRadioButton('Append')
        self.append_genre_radio.setObjectName('GenreMode')
        self.append_genre_radio.setChecked(True)
        mode_row.addWidget(self.replace_genre_radio)
        mode_row.addWidget(self.append_genre_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        create_btn_row = QHBoxLayout()
        clear_btn = QPushButton('Clear')
        clear_btn.clicked.connect(self.new_genre_files_list.clear)
        clear_btn.clicked.connect(lambda: self.new_genre_name_input.setText(''))
        clear_btn.clicked.connect(self.genre_list.clearSelection)
        rename_btn = QPushButton('Rename Genre')
        rename_btn.setToolTip(
            'Rename the selected genre to the name typed above,\n'
            'updating every file that carries it.'
        )
        rename_btn.clicked.connect(self._rename_genre)
        create_btn = QPushButton('Apply Playlist')
        create_btn.setObjectName('PrimaryButton')
        create_btn.clicked.connect(self._create_genre_playlist)
        create_btn_row.addWidget(clear_btn)
        create_btn_row.addStretch(1)
        create_btn_row.addWidget(rename_btn)
        create_btn_row.addWidget(create_btn)
        layout.addLayout(create_btn_row)

        return panel

    # ------------------------------------------------------------------ styles

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
QMainWindow, QDialog {
    background-color: #1a1917;
}

QWidget#CentralWidget {
    background-color: #1a1917;
}

QWidget {
    color: #e8e4dc;
    font-family: Consolas, "Courier New", monospace;
    font-size: 13px;
}

QFrame#HeaderPanel, QFrame#TreePanel, QFrame#StatusPanel, QFrame#GenrePanel {
    background-color: #23221f;
    border: 1px solid #33312c;
    border-radius: 5px;
}

QFrame#VSeparator {
    color: #33312c;
    background-color: #33312c;
    border: none;
    max-width: 1px;
}

QLabel#AppTitle {
    font-size: 20px;
    font-weight: 700;
    color: #e8e4dc;
    letter-spacing: -0.5px;
}

QLabel#SectionTitle {
    font-size: 13px;
    font-weight: 600;
    color: #e8e4dc;
}

QLabel#SectionTag {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1.4px;
    color: #8a857b;
}

QLabel#PathLabel {
    color: #8a857b;
    font-size: 12px;
}

QLabel#MutedLabel {
    color: #8a857b;
    font-size: 11px;
}

QLabel#LinkLabel {
    font-size: 11px;
    color: #8a857b;
}

QLabel#LinkLabel a {
    color: #9b6fd4;
    text-decoration: none;
}

QLabel#StatusText {
    color: #e8e4dc;
    font-size: 12px;
}

QLabel#TokenHelp {
    background-color: #1a1917;
    border: 1px solid #33312c;
    border-radius: 5px;
    padding: 8px 10px;
    color: #c8c4bc;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
}

QPushButton {
    background-color: #2a2926;
    border: 1px solid #33312c;
    border-radius: 5px;
    padding: 6px 12px;
    color: #e8e4dc;
}

QPushButton:hover {
    background-color: #33312c;
    border-color: #4a4740;
}

QPushButton:disabled {
    color: #5a5652;
    border-color: #2a2926;
    background-color: #23221f;
}

QPushButton#PrimaryButton {
    background-color: #3d2c6b;
    border-color: #9b6fd4;
    color: #e8e4dc;
}

QPushButton#PrimaryButton:hover {
    background-color: #4a3580;
    border-color: #b08ae0;
}

QPushButton#DangerButton {
    background-color: #4d2028;
    border-color: #6c2d38;
}

QPushButton#DangerButton:hover {
    background-color: #5c2530;
}

QPushButton#ViewToggle {
    background-color: #23221f;
    border: 1px solid #33312c;
    border-radius: 5px;
    padding: 4px 14px;
    color: #8a857b;
    font-size: 12px;
}

QPushButton#ViewToggle[selected="true"] {
    background-color: #3d2c6b;
    border-color: #9b6fd4;
    color: #e8e4dc;
}

QPushButton#StartButton {
    background-color: #2a2926;
    border: 1px solid #33312c;
    padding: 6px 16px;
    font-weight: 600;
}

QPushButton#StartButton[active="true"] {
    background-color: #3d2c6b;
    border-color: #9b6fd4;
    color: #e8e4dc;
}

QPushButton#StartButton[stopping="true"] {
    background-color: #4d2028;
    border-color: #6c2d38;
}

QPushButton#RefreshButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 2px 4px;
    color: #8a857b;
    font-size: 14px;
}

QPushButton#RefreshButton:hover {
    background-color: #2a2926;
    border-color: #33312c;
    color: #9b6fd4;
}

QPushButton#RefreshButton:disabled {
    color: #3d3a34;
}

QPushButton#IconButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 4px 6px;
}

QPushButton#IconButton:hover {
    background-color: #2a2926;
    border-color: #33312c;
}

QComboBox {
    background-color: #23221f;
    border: 1px solid #33312c;
    border-radius: 5px;
    padding: 5px 10px;
    color: #e8e4dc;
    selection-background-color: #3d2c6b;
}

QComboBox:hover {
    border-color: #4a4740;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #23221f;
    border: 1px solid #33312c;
    selection-background-color: #3d2c6b;
    color: #e8e4dc;
}

QLineEdit, QSpinBox {
    background-color: #23221f;
    border: 1px solid #33312c;
    border-radius: 5px;
    padding: 5px 10px;
    color: #e8e4dc;
    selection-background-color: #3d2c6b;
}

QLineEdit:focus, QSpinBox:focus {
    border-color: #9b6fd4;
}

QLineEdit:hover, QSpinBox:hover {
    border-color: #4a4740;
}

QRadioButton#GenreMode {
    border: 1px solid #33312c;
    border-radius: 5px;
    padding: 5px 12px;
    background-color: #23221f;
    color: #8a857b;
    min-width: 70px;
}

QRadioButton#GenreMode::indicator {
    width: 0px;
    height: 0px;
}

QRadioButton#GenreMode:checked {
    background-color: #3d2c6b;
    border-color: #9b6fd4;
    color: #e8e4dc;
    font-weight: 600;
}

QTreeWidget, QListWidget {
    background-color: #1a1917;
    border: 1px solid #33312c;
    border-radius: 5px;
    outline: none;
}

QTreeWidget::item, QListWidget::item {
    padding: 2px 0px;
}

QTreeWidget::item:selected, QListWidget::item:selected {
    background-color: #3d2c6b;
    color: #e8e4dc;
}

QTreeWidget::item:hover, QListWidget::item:hover {
    background-color: #2a2926;
}

QHeaderView::section {
    background-color: #23221f;
    color: #8a857b;
    border: none;
    border-bottom: 1px solid #33312c;
    padding: 5px 8px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}

QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #3d3a34;
    border-radius: 3px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background: #8a857b;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: transparent;
    height: 6px;
}

QScrollBar::handle:horizontal {
    background: #3d3a34;
    border-radius: 3px;
}

QScrollBar::handle:horizontal:hover {
    background: #8a857b;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

QProgressBar {
    border: none;
    border-radius: 3px;
    background-color: #33312c;
    min-height: 6px;
    max-height: 6px;
    min-width: 200px;
}

QProgressBar::chunk {
    background-color: #9b6fd4;
    border-radius: 3px;
}

QFrame#StatCard {
    background-color: #23221f;
    border: 1px solid #33312c;
    border-radius: 5px;
}

QLabel#StatLabel {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: #8a857b;
}

QLabel#StatValue {
    font-size: 22px;
    font-weight: 700;
}

QLabel#StatusDot {
    font-size: 8px;
}

QSplitter::handle {
    background-color: #33312c;
    width: 1px;
}

QFrame#ProfileCard {
    background-color: #23221f;
    border: 1px solid #33312c;
    border-radius: 5px;
}

QLabel#ProfileName {
    color: #e8e4dc;
    font-weight: 500;
}

QLabel#LinkStatusNone    { color: #8a857b; }
QLabel#LinkStatusOffline { color: #c4a35a; }
QLabel#LinkStatusOnline  { color: #7dc47d; }

QDialog {
    background-color: #1a1917;
    color: #e8e4dc;
}

QScrollArea {
    border: none;
    background-color: transparent;
}

QScrollArea > QWidget > QWidget {
    background-color: transparent;
}

QMessageBox {
    background-color: #23221f;
    color: #e8e4dc;
}

QToolTip {
    background-color: #23221f;
    color: #e8e4dc;
    border: 1px solid #33312c;
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 11px;
}
""")

    def _build_stat_card(self, label: str, value_color: str) -> dict[str, QWidget]:
        frame = QFrame()
        frame.setObjectName('StatCard')
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        frame.setFixedHeight(76)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(2)
        lbl = QLabel(label)
        lbl.setObjectName('StatLabel')
        value = QLabel('0')
        value.setObjectName('StatValue')
        value.setStyleSheet(f'color: {value_color};')
        layout.addWidget(lbl)
        layout.addWidget(value)
        return {'frame': frame, 'value': value}

    # ------------------------------------------------------------------ path helpers

    def _remember_path(self, key: str, path: Path) -> None:
        resolved = str(path.expanduser().resolve())
        existing = [str(item) for item in self.config.get(key, []) if str(item).strip()]
        filtered = [item for item in existing if item != resolved]
        self.config[key] = [resolved] + filtered[:9]
        save_config(self.config)

    def _initial_dialog_path(self, current: Path | None, history_key: str) -> str:
        if current:
            return str(current)
        for candidate in self.config.get(history_key, []):
            try:
                path = Path(str(candidate)).expanduser()
            except Exception:
                continue
            if path.exists():
                return str(path)
        return str(Path.home())

    def _current_profile(self):
        return self.profile_store.get(self.profile_combo.currentText().strip())


    def _clear_target_selection(self) -> None:
        self.target_path = None
        self.target_tree_data = None
        self.config['target_path'] = ''
        save_config(self.config)
        self.view_target_btn.setEnabled(False)
        self.refresh_target_btn.setEnabled(False)
        self._update_target_path_label(None)
        self._recompute_any_synced_candidates()
        if self.library_stack.currentIndex() == 1:
            self._switch_tree_view('source')

    def _update_source_path_label(self, path: Path | None) -> None:
        if path:
            text = path.name or str(path)
            self.source_path_label.setText(text)
            self.source_path_label.setToolTip(str(path))
        else:
            self.source_path_label.setText('(not selected)')
            self.source_path_label.setToolTip('')

    def _update_target_path_label(self, path: Path | None) -> None:
        if path:
            text = path.name or str(path)
            self.target_path_label.setText(text)
            self.target_path_label.setToolTip(str(path))
        else:
            self.target_path_label.setText('(not selected)')
            self.target_path_label.setToolTip('')

    # ------------------------------------------------------------------ profile / init

    def _load_profiles(self) -> None:
        current = self.config.get('selected_profile') or ''
        names = self.profile_store.names()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(names)
        if current and current in names:
            self.profile_combo.setCurrentText(current)
        elif names:
            self.profile_combo.setCurrentText(names[0])
        self.profile_combo.blockSignals(False)
        self._on_profile_changed(self.profile_combo.currentText())

    def _restore_paths(self) -> None:
        if self.source_path and self.source_path.exists():
            self._update_source_path_label(self.source_path)
            self._queue_scan('source', self.source_path)
        else:
            self._update_source_path_label(None)

        if self.target_path and self.target_path.exists():
            self.view_target_btn.setEnabled(True)
            self._update_target_path_label(self.target_path)
            self._queue_scan('target', self.target_path)
        else:
            self._clear_target_selection()

        self._switch_tree_view('source')
        self._refresh_start_button_state()

    # ------------------------------------------------------------------ scanning

    def _queue_scan(self, kind: str, path: Path, force_refresh: bool = False, switch_view: str | None = None) -> None:
        resolved = path.expanduser().resolve()
        request_key = (kind, resolved, force_refresh)

        if self._scan_thread and self._scan_thread.isRunning():
            active_key = (self._scan_thread.kind, self._scan_thread.root_path.expanduser().resolve(), self._scan_thread.force_refresh)
            if active_key == request_key:
                return

        for queued_kind, queued_path, queued_force, _queued_switch in self._scan_queue:
            if (queued_kind, queued_path, queued_force) == request_key:
                return

        self._scan_queue.append((kind, resolved, force_refresh, switch_view))
        self._start_next_scan()

    def _start_next_scan(self) -> None:
        if self._scan_busy or not self._scan_queue:
            return

        kind, path, force_refresh, _switch_view = self._scan_queue[0]
        self._scan_busy = True
        self._refresh_start_button_state()
        self._set_status(self._default_scan_message(kind), syncing=True)
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText('Scanning...')

        self._scan_thread = LibraryScanThread(kind=kind, root_path=path, force_refresh=force_refresh, parent=self)
        self._scan_thread.scan_finished.connect(self._on_scan_finished)
        self._scan_thread.scan_progress.connect(self._on_scan_progress)
        self._scan_thread.start()

    def _default_scan_message(self, kind: str) -> str:
        return 'Scanning source...' if kind == 'source' else 'Scanning target...'

    def _on_scan_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
            self.progress_label.setText(f'{done}/{total}')

    def _on_scan_finished(self, kind: str, path_text: str, tree: object, error: str) -> None:
        switch_view: str | None = None
        if self._scan_queue:
            _, _, _, switch_view = self._scan_queue.popleft()

        if error:
            self.logger.log(f"{kind.title()} scan failed for {path_text}: {error}")
        elif kind == 'source' and isinstance(tree, LibraryNode):
            self._apply_source_scan_result(tree)
        elif kind == 'target' and isinstance(tree, LibraryNode):
            self._apply_target_scan_result(tree, Path(path_text))

        if switch_view:
            self._switch_tree_view(switch_view)

        if self._scan_thread is not None:
            self._scan_thread.deleteLater()
            self._scan_thread = None
        self._scan_busy = False

        self._finalize_loading_indicator()
        self._refresh_start_button_state()
        self._start_next_scan()

    def _finalize_loading_indicator(self) -> None:
        if self.current_engine and self.current_engine.isRunning():
            return
        if self._scan_busy or self._scan_queue:
            return
        self._set_status('Ready', syncing=False)
        total = self.sync_batch_total if self.pending_active else self.total_files
        current = self.processed_files if self.pending_active else 0
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(min(current, max(1, total)))
        label = f'{current}/{total}' if total else '0/0'
        self.progress_label.setText(label)

    def _apply_source_scan_result(self, tree: LibraryNode) -> None:
        self.source_tree_data = tree
        source_files = flatten_files(self.source_tree_data)
        valid_paths = {str(node.absolute_path.resolve()) for node in source_files}
        self.source_checked_paths = {p for p in self.source_checked_paths if p in valid_paths}

        self.total_files = len(source_files)
        self.processed_files = 0
        self.error_files = 0
        self.pending_active = False
        self.sync_batch_total = 0

        self.source_tree.populate(
            self.source_tree_data,
            self.sync_state,
            self.target_path if self.target_path and self.target_path.exists() else None,
            checkboxes_enabled=True,
        )
        self.source_tree.set_paths_drag_enabled(False)
        self.source_tree.set_checked_paths(self.source_checked_paths)
        self._on_source_checked_count_changed(len(self.source_checked_paths))
        self._update_stats_cards()

    def _apply_target_scan_result(self, tree: LibraryNode, target: Path) -> None:
        self.target_tree_data = tree
        self.target_tree.populate(self.target_tree_data, {}, None, checkboxes_enabled=False)
        self.target_tree.set_paths_drag_enabled(False)
        self._refresh_genre_index()
        self._recompute_any_synced_candidates()
        if self.source_tree_data:
            self.source_tree.refresh_synced_markers(self.sync_state, target)

    # ------------------------------------------------------------------ view switching

    def _switch_tree_view(self, view: str) -> None:
        if view == 'target' and not (self.target_path and self.target_path.exists()):
            return
        if view == 'source':
            self.library_stack.setCurrentIndex(0)
        else:
            self.library_stack.setCurrentIndex(1)

        self.view_source_btn.setProperty('selected', view == 'source')
        self.view_target_btn.setProperty('selected', view == 'target')
        self.view_source_btn.style().unpolish(self.view_source_btn)
        self.view_source_btn.style().polish(self.view_source_btn)
        self.view_target_btn.style().unpolish(self.view_target_btn)
        self.view_target_btn.style().polish(self.view_target_btn)

        source_mode = view == 'source'
        self.select_all_link.setEnabled(source_mode)
        self.deselect_all_link.setEnabled(source_mode)

    # ------------------------------------------------------------------ profile events

    def _on_profile_changed(self, name: str) -> None:
        self.config['selected_profile'] = name
        save_config(self.config)
        has_target = bool(self.target_path and self.target_path.exists())
        self.view_target_btn.setEnabled(has_target)
        if has_target and self.target_tree_data is None and self.target_path is not None:
            self._queue_scan('target', self.target_path)
        self._refresh_start_button_state()

    def _open_profile_manager(self) -> None:
        dlg = ProfileManagerDialog(self.profile_store, self)
        dlg.profiles_changed.connect(self._load_profiles)
        dlg.exec()



    def _open_log_viewer(self) -> None:
        self.log_viewer.set_lines(self.logger.lines)
        self.log_viewer.show()
        self.log_viewer.raise_()
        self.log_viewer.activateWindow()

    def _clear_logs(self) -> None:
        self.logger.clear()
        self.log_viewer.clear()

    # ------------------------------------------------------------------ source/target selection

    def _select_source(self) -> None:
        initial = self._initial_dialog_path(self.source_path, 'source_history')
        path = QFileDialog.getExistingDirectory(self, 'Select Source Library', initial)
        if not path:
            return
        chosen = Path(path)
        self.source_path = chosen
        self.config['source_path'] = str(chosen)
        save_config(self.config)
        self._remember_path('source_history', chosen)
        self._update_source_path_label(chosen)
        self._queue_scan('source', chosen, switch_view='source')
        self._switch_tree_view('source')
        self._refresh_start_button_state()

    def _select_target(self) -> None:
        initial = self._initial_dialog_path(self.target_path, 'target_history')
        path = QFileDialog.getExistingDirectory(self, 'Select Target Device Folder', initial)
        if not path:
            return
        chosen = Path(path)
        self.target_path = chosen
        self.config['target_path'] = str(chosen)
        save_config(self.config)
        self._remember_path('target_history', chosen)
        self._update_target_path_label(chosen)
        self.sync_state = load_sync_state()
        self._recompute_any_synced_candidates()
        self.view_target_btn.setEnabled(True)
        self._queue_scan('target', chosen, switch_view='target')
        self._switch_tree_view('target')
        self.logger.log(f'Target selected: {chosen}')
        self._refresh_start_button_state()

    # ------------------------------------------------------------------ refresh handlers

    def _on_refresh_source(self) -> None:
        if self.source_path and self.source_path.exists():
            self._queue_scan('source', self.source_path, force_refresh=True, switch_view='source')

    def _on_refresh_target(self) -> None:
        if self.target_path and self.target_path.exists():
            self._queue_scan('target', self.target_path, force_refresh=True, switch_view='target')

    # ------------------------------------------------------------------ sync

    def _toggle_sync(self) -> None:
        if self.current_engine and self.current_engine.isRunning():
            self.current_engine.request_stop()
            self.start_sync_btn.setEnabled(False)
            self.resync_btn.setEnabled(False)
            return

        self.source_checked_paths = {str(path.resolve()) for path in self.source_tree.get_checked_files()}
        checked_files = [Path(path) for path in sorted(self.source_checked_paths)]
        if not checked_files:
            QMessageBox.warning(self, 'No Files Selected', 'Select one or more files to sync.')
            return

        self._start_sync_job(checked_files, force_resync=False, preserve_target_metadata=False)

    def _start_resync(self) -> None:
        if self.current_engine and self.current_engine.isRunning():
            return

        self.sync_state = load_sync_state()
        self._recompute_any_synced_candidates()
        self.source_checked_paths = {str(path.resolve()) for path in self.source_tree.get_checked_files()}
        files = self._collect_resync_candidates()
        if not files:
            QMessageBox.information(self, 'Nothing To Resync', 'No previously-synced files found for this source/target.')
            return

        prompt = QMessageBox(self)
        prompt.setWindowTitle('Resync Options')
        prompt.setIcon(QMessageBox.Icon.Question)
        prompt.setText(f'Resync {len(files)} file(s) with current profile settings?')
        prompt.setInformativeText(
            'Yes — overwrite target tags from source/profile output.\n'
            'No — keep existing target tags (genre and other metadata).\n'
            'Cancel — do nothing.'
        )
        prompt.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
        )
        prompt.setDefaultButton(QMessageBox.StandardButton.No)
        choice = prompt.exec()
        if choice == QMessageBox.StandardButton.Cancel:
            return

        preserve_target_metadata = choice == QMessageBox.StandardButton.No
        self._start_sync_job(files, force_resync=True, preserve_target_metadata=preserve_target_metadata)

    def _collect_resync_candidates(self) -> list[Path]:
        if not self.source_path or not self.target_path:
            return []

        source_root = self.source_path.expanduser().resolve()
        target_root = self.target_path.expanduser().resolve()
        selected = {str(Path(raw).resolve()) for raw in self.source_checked_paths}

        def is_valid_synced(source_raw: str, rel: str) -> Path | None:
            try:
                src = Path(source_raw).expanduser().resolve()
                target = (target_root / rel).resolve()
            except Exception:
                return None
            if not src.exists() or not target.exists():
                return None
            try:
                src.relative_to(source_root)
            except ValueError:
                return None
            return src

        candidates: set[Path] = set()
        if selected:
            for source_raw in selected:
                mapped = self.sync_state.get(source_raw)
                if not mapped:
                    continue
                candidate = is_valid_synced(source_raw, mapped)
                if candidate:
                    candidates.add(candidate)
        else:
            for source_raw, rel in self.sync_state.items():
                candidate = is_valid_synced(source_raw, rel)
                if candidate:
                    candidates.add(candidate)

        return sorted(candidates)

    def _is_valid_synced_mapping(self, source_raw: str, rel: str) -> bool:
        if not self.source_path or not self.target_path:
            return False
        try:
            source_root = self.source_path.expanduser().resolve()
            target_root = self.target_path.expanduser().resolve()
            src = Path(source_raw).expanduser().resolve()
            target = (target_root / rel).resolve()
        except Exception:
            return False
        if not src.exists() or not target.exists():
            return False
        try:
            src.relative_to(source_root)
        except ValueError:
            return False
        return True

    def _recompute_any_synced_candidates(self) -> None:
        if not self.source_path or not self.target_path:
            self._has_any_synced_candidates = False
            return
        self._has_any_synced_candidates = any(
            self._is_valid_synced_mapping(source_raw, rel)
            for source_raw, rel in self.sync_state.items()
        )

    def _has_resync_candidates_for_selection(self) -> bool:
        if not self.source_checked_paths:
            return self._has_any_synced_candidates
        for source_raw in self.source_checked_paths:
            rel = self.sync_state.get(source_raw)
            if not rel:
                continue
            if self._is_valid_synced_mapping(source_raw, rel):
                return True
        return False

    def _start_sync_job(self, files: list[Path], force_resync: bool, preserve_target_metadata: bool) -> None:
        if self._scan_busy or self._scan_queue:
            QMessageBox.information(self, 'Please Wait', 'Library scan is still in progress. Try again in a moment.')
            return
        if not self.source_path or not self.source_path.exists():
            QMessageBox.warning(self, 'Source Missing', 'Please select a valid source folder.')
            return
        if not self.target_path or not self.target_path.exists():
            QMessageBox.warning(self, 'Target Missing', 'Please select a valid target folder.')
            return

        profile = self.profile_store.get(self.profile_combo.currentText().strip())
        if not profile:
            QMessageBox.warning(self, 'Profile Missing', 'Please select a valid profile.')
            return

        self.sync_state = load_sync_state()
        self._recompute_any_synced_candidates()
        self.processed_files = 0
        self.error_files = 0
        self.sync_batch_total = len(files)
        self.pending_active = True
        self._active_sync_mode = 'resync' if force_resync else 'sync'

        self.current_engine = SyncEngine(
            source_root=self.source_path,
            target_root=self.target_path,
            files=files,
            profile=profile,
            sync_state=self.sync_state,
            force_resync=force_resync,
            preserve_target_metadata=preserve_target_metadata,
            parent=self,
        )
        self.current_engine.log_emitted.connect(lambda message: self.logger.log(message))
        self.current_engine.progress_updated.connect(self._on_sync_progress)
        self.current_engine.file_processed.connect(self._on_file_processed)
        self.current_engine.finished_sync.connect(self._on_sync_finished)

        self.progress_bar.setRange(0, max(1, len(files)))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f'0/{len(files)}')

        self._update_stats_cards()
        self._set_sync_button_state(running=True)
        self._set_status('Syncing...', syncing=True)

        if force_resync:
            mode_text = 'preserving target metadata' if preserve_target_metadata else 'overwriting target metadata'
            self.logger.log(f'Starting resync for {len(files)} file(s) ({mode_text})')
        else:
            self.logger.log(f'Starting sync for {len(files)} file(s)')
        self.current_engine.start()

    def _on_sync_progress(self, current: int, total: int) -> None:
        self.processed_files = current
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(current)
        self.progress_label.setText(f'{current}/{total}')
        self._update_stats_cards()

    def _on_file_processed(self, _source_file: str, success: bool) -> None:
        if not success:
            self.error_files += 1
            self._update_stats_cards()

    def _on_sync_finished(self, cancelled: bool, processed: int, errors: int) -> None:
        self.processed_files = processed
        self.error_files = errors
        self.pending_active = False
        self._update_stats_cards()

        self.sync_state = load_sync_state()
        self._recompute_any_synced_candidates()

        # Defer tree marker refresh to avoid blocking the UI on completion
        if self.source_tree_data and self.target_path and self.target_path.exists():
            snap_state = dict(self.sync_state)
            snap_target = self.target_path
            QTimer.singleShot(0, lambda: self.source_tree.refresh_synced_markers(snap_state, snap_target))
            self._queue_scan('target', self.target_path, force_refresh=True)

        self._set_sync_button_state(running=False)
        self._finalize_loading_indicator()

        if cancelled:
            self.logger.log('Resync cancelled' if self._active_sync_mode == 'resync' else 'Sync cancelled')
        else:
            prefix = 'Resync complete' if self._active_sync_mode == 'resync' else 'Sync complete'
            self.logger.log(f'{prefix}: {processed - errors} succeeded, {errors} errors')

        self.current_engine = None
        self._active_sync_mode = 'sync'
        self._refresh_start_button_state()

    # ------------------------------------------------------------------ mini player

    def _on_source_item_double_clicked(self, item, _column: int) -> None:
        if bool(item.data(0, ROLE_IS_DIR)):
            return
        raw = str(item.data(0, ROLE_PATH) or '')
        if not raw:
            return
        path = Path(raw)
        if not path.exists():
            return
        if self._mini_player is None:
            self._mini_player = MiniPlayerDialog(self.config, save_config, self)
        self._mini_player.load(path)
        self._mini_player.show()
        self._mini_player.raise_()
        self._mini_player.activateWindow()

    # ------------------------------------------------------------------ button state

    def _on_source_checked_count_changed(self, _count: int) -> None:
        self.source_checked_paths = {str(path.resolve()) for path in self.source_tree.get_checked_files()}
        count = len(self.source_checked_paths)
        file_word = 'file' if count == 1 else 'files'
        self.selected_count_label.setText(f'{count} {file_word} selected')
        self._refresh_start_button_state()

    def _refresh_start_button_state(self) -> None:
        has_selection = len(self.source_checked_paths) > 0
        has_paths = bool(self.source_path and self.target_path and self.source_path.exists() and self.target_path.exists())
        running = bool(self.current_engine and self.current_engine.isRunning())
        scan_locked = bool(self._scan_busy or self._scan_queue)
        has_resync = self._has_resync_candidates_for_selection() if has_paths else False
        controls_enabled = not running and not scan_locked

        self.select_source_btn.setEnabled(controls_enabled)
        self.select_target_btn.setEnabled(controls_enabled)
        self.profile_combo.setEnabled(not running)
        self.manage_profiles_btn.setEnabled(not running)

        enabled = has_selection and has_paths and not running and not scan_locked
        self.start_sync_btn.setEnabled(enabled or running)
        self.start_sync_btn.setProperty('active', enabled and not running)
        self.start_sync_btn.setProperty('stopping', running)
        self.start_sync_btn.style().unpolish(self.start_sync_btn)
        self.start_sync_btn.style().polish(self.start_sync_btn)
        self.start_sync_btn.setText('Stop' if running else 'Sync')
        self.resync_btn.setEnabled(has_resync and not running and not scan_locked)

        has_source = bool(self.source_path and self.source_path.exists())
        has_target = bool(self.target_path and self.target_path.exists())
        self.refresh_source_btn.setEnabled(has_source and not running and not scan_locked)
        self.refresh_target_btn.setEnabled(has_target and not running and not scan_locked)

    def _set_sync_button_state(self, running: bool) -> None:
        self.start_sync_btn.setProperty('stopping', running)
        self.start_sync_btn.setProperty('active', not running and len(self.source_checked_paths) > 0)
        self.start_sync_btn.style().unpolish(self.start_sync_btn)
        self.start_sync_btn.style().polish(self.start_sync_btn)
        self.start_sync_btn.setText('Stop' if running else 'Sync')
        self.start_sync_btn.setEnabled(True)
        self.resync_btn.setEnabled(not running and self._has_resync_candidates_for_selection())
        self.select_source_btn.setEnabled(not running)
        self.select_target_btn.setEnabled(not running)
        self.profile_combo.setEnabled(not running)
        self.manage_profiles_btn.setEnabled(not running)

        has_source = bool(self.source_path and self.source_path.exists())
        has_target = bool(self.target_path and self.target_path.exists())
        self.refresh_source_btn.setEnabled(has_source and not running)
        self.refresh_target_btn.setEnabled(has_target and not running)

    # ------------------------------------------------------------------ stats / status

    def _update_stats_cards(self) -> None:
        pending = max(0, self.sync_batch_total - self.processed_files) if self.pending_active else None
        self.total_card['value'].setText(str(self.total_files))
        self.processed_card['value'].setText(str(self.processed_files))
        self.pending_card['value'].setText(str(pending) if pending is not None else '-')
        self.errors_card['value'].setText(str(self.error_files))

    def _set_status(self, text: str, syncing: bool) -> None:
        self.status_text.setText(text)
        if syncing:
            self.dot_timer.start()
            self.status_dot.setStyleSheet('color: #9b6fd4;')
        else:
            self.dot_timer.stop()
            self.status_dot.setStyleSheet('color: #3d3a34;')

    def _pulse_dot(self) -> None:
        self._dot_visible = not self._dot_visible
        # Toggle between accent and dim — never hide, so adjacent text never shifts
        self.status_dot.setStyleSheet(
            'color: #9b6fd4;' if self._dot_visible else 'color: #3d2c6b;'
        )

    def _on_new_log_line(self, line: str) -> None:
        if self.log_viewer.isVisible():
            self.log_viewer.append_line(line)

    # ------------------------------------------------------------------ genre panel

    def _refresh_genre_index(self) -> None:
        self.genre_index.clear()
        self.genre_list.clear()
        self.new_genre_files_list.clear()

        if not self.target_tree_data:
            return

        no_genre_files: list = []
        for file_node in flatten_files(self.target_tree_data):
            path = file_node.absolute_path
            genres = read_genres(path)
            if not genres:
                no_genre_files.append(path)
            else:
                for genre in genres:
                    self.genre_index.setdefault(genre, []).append(path)

        # "(No Genre)" pinned at the top in amber
        if no_genre_files:
            no_genre_item = QListWidgetItem(f'{_NO_GENRE_DISPLAY} ({len(no_genre_files)})')
            no_genre_item.setData(Qt.ItemDataRole.UserRole, _NO_GENRE_SENTINEL)
            no_genre_item.setForeground(QBrush(QColor('#c4a35a')))
            self.genre_list.addItem(no_genre_item)
            self.genre_index[_NO_GENRE_SENTINEL] = no_genre_files

        for genre in sorted(self.genre_index.keys(), key=lambda s: s.lower()):
            if genre == _NO_GENRE_SENTINEL:
                continue
            count = len(self.genre_index[genre])
            item = QListWidgetItem(f'{genre} ({count})')
            item.setData(Qt.ItemDataRole.UserRole, genre)
            self.genre_list.addItem(item)

    def _on_genre_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self.new_genre_files_list.clear()
        if not current:
            return

        genre = str(current.data(Qt.ItemDataRole.UserRole) or '')
        # Don't copy the sentinel into the name field — leave it blank so the
        # user can type the genre they want to assign to these untagged files.
        if genre != _NO_GENRE_SENTINEL:
            self.new_genre_name_input.setText(genre)
        else:
            self.new_genre_name_input.clear()

        for path in sorted(self.genre_index.get(genre, []), key=lambda p: p.name.lower()):
            self._builder_add_paths([str(path.resolve())])

    def _on_target_tree_item_double_clicked(self, item, _column: int) -> None:
        if bool(item.data(0, ROLE_IS_DIR)):
            return
        raw = str(item.data(0, ROLE_PATH) or '')
        if raw:
            self._builder_add_paths([raw])

    def _on_target_tree_context_menu(self, pos) -> None:
        file_items = [
            it for it in self.target_tree.selectedItems()
            if not bool(it.data(0, ROLE_IS_DIR))
        ]
        if not file_items:
            return
        n = len(file_items)
        menu = QMenu(self)
        menu.addAction(
            f'Add {n} file{"s" if n != 1 else ""} to Playlist',
            lambda: self._builder_add_paths(
                [str(it.data(0, ROLE_PATH) or '') for it in file_items]
            ),
        )
        menu.addSeparator()
        menu.addAction(
            f'Delete {n} file{"s" if n != 1 else ""}',
            lambda: self._delete_target_files(list(file_items)),
        )
        menu.exec(self.target_tree.viewport().mapToGlobal(pos))

    def _delete_target_files(self, file_items) -> None:
        n = len(file_items)
        names = '\n'.join(
            Path(str(it.data(0, ROLE_PATH) or '')).name for it in file_items[:10]
        )
        if n > 10:
            names += f'\n... and {n - 10} more'
        answer = QMessageBox.question(
            self,
            'Delete Files',
            f'Permanently delete {n} file{"s" if n != 1 else ""} from the target?\n\n{names}',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted = 0
        failed = 0
        for it in file_items:
            raw = str(it.data(0, ROLE_PATH) or '')
            if not raw:
                continue
            try:
                Path(raw).unlink()
                deleted += 1
            except Exception as exc:
                self.logger.log(f'Delete failed for {Path(raw).name}: {exc}')
                failed += 1
        parts = [f'Deleted {deleted} file{"s" if deleted != 1 else ""} from target']
        if failed:
            parts.append(f'{failed} failed')
        self.logger.log(', '.join(parts))
        if self.target_path and self.target_path.exists():
            self._queue_scan('target', self.target_path, force_refresh=True)

    def _on_builder_item_double_clicked(self, item: QListWidgetItem) -> None:
        self.new_genre_files_list.takeItem(self.new_genre_files_list.row(item))

    def _selected_genre_name(self) -> str:
        item = self.genre_list.currentItem()
        if not item:
            return ''
        val = str(item.data(Qt.ItemDataRole.UserRole) or '')
        # The "(No Genre)" row is a virtual group — treat as no existing genre
        return '' if val == _NO_GENRE_SENTINEL else val

    def _builder_paths(self) -> list[str]:
        return [str(self.new_genre_files_list.item(i).data(Qt.ItemDataRole.UserRole) or '') for i in range(self.new_genre_files_list.count())]

    def _builder_add_paths(self, paths: list[str]) -> None:
        existing = {str(self.new_genre_files_list.item(i).data(Qt.ItemDataRole.UserRole) or '') for i in range(self.new_genre_files_list.count())}
        for raw in paths:
            resolved = str(Path(raw).resolve())
            if not resolved or resolved in existing:
                continue
            existing.add(resolved)
            item = QListWidgetItem(Path(resolved).name)
            item.setToolTip(resolved)
            item.setData(Qt.ItemDataRole.UserRole, resolved)
            self.new_genre_files_list.addItem(item)

    def _rename_genre(self) -> None:
        old_name = self._selected_genre_name()
        new_name = self.new_genre_name_input.text().strip()

        if not old_name:
            QMessageBox.warning(self, 'No Genre Selected',
                                'Select a genre from the list first.')
            return
        if not new_name:
            QMessageBox.warning(self, 'No Name',
                                'Type the new genre name in the field above.')
            return
        if old_name.lower() == new_name.lower():
            return  # nothing to do

        paths = self.genre_index.get(old_name, [])
        if not paths:
            return

        changed = 0
        failed = 0
        for path in paths:
            if rename_genre(path, old_name, new_name):
                changed += 1
            else:
                failed += 1

        parts = [f"Renamed '{old_name}' → '{new_name}': {changed} updated"]
        if failed:
            parts.append(f"{failed} failed")
        self.logger.log(', '.join(parts))
        self._refresh_genre_index()

    def _create_genre_playlist(self) -> None:
        if not self.target_path:
            QMessageBox.warning(self, 'No Target', 'Select a target folder first.')
            return

        genre_name = self.new_genre_name_input.text().strip()
        if not genre_name:
            QMessageBox.warning(self, 'Invalid Name', 'Enter a genre playlist name.')
            return

        selected_existing = self._selected_genre_name()
        target_paths = {str(Path(raw).resolve()) for raw in self._builder_paths()}
        mode_replace = self.replace_genre_radio.isChecked()
        changed = 0
        failed = 0

        detagged = 0
        if selected_existing:
            for path in self.genre_index.get(selected_existing, []):
                resolved = str(path.resolve())
                if resolved not in target_paths:
                    if remove_genre(path, selected_existing):
                        detagged += 1
                    else:
                        failed += 1

        for raw in target_paths:
            path = Path(raw)
            if not path.exists() or path.is_dir():
                failed += 1
                continue
            if selected_existing and selected_existing != genre_name:
                remove_genre(path, selected_existing)
            ok = replace_genre(path, genre_name) if mode_replace else append_genre(path, genre_name)
            if ok:
                changed += 1
            else:
                failed += 1

        parts = [f"Genre playlist '{genre_name}': {changed} tagged"]
        if detagged:
            parts.append(f"{detagged} de-tagged")
        if failed:
            parts.append(f"{failed} failed")
        parts.append('replace mode' if mode_replace else 'append mode')
        self.logger.log(', '.join(parts))

        # Only genre tags changed — no need to re-scan disk, just refresh the index
        self._refresh_genre_index()

    # ------------------------------------------------------------------ close

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.current_engine and self.current_engine.isRunning():
            self.current_engine.request_stop()
            self.current_engine.wait(3000)
        if self._scan_thread and self._scan_thread.isRunning():
            self._scan_thread.wait(3000)
        super().closeEvent(event)
