from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QMimeData, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QTreeWidget, QTreeWidgetItem

from core.file_scanner import LibraryNode, human_length, human_size

ROLE_PATH = int(Qt.ItemDataRole.UserRole) + 1
ROLE_IS_DIR = int(Qt.ItemDataRole.UserRole) + 2
ROLE_BASE_NAME = int(Qt.ItemDataRole.UserRole) + 3
ROLE_SYNCED_AS = int(Qt.ItemDataRole.UserRole) + 4
MIME_CALLIOPE_PATHS = "application/x-calliope-paths"


class MusicTreeWidget(QTreeWidget):
    checked_files_changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(6)
        self.setHeaderLabels(["Name", "File Size", "Format", "Length", "Block", "Art"])
        self.setRootIsDecorated(True)
        self.setAlternatingRowColors(False)
        self.setExpandsOnDoubleClick(False)
        self.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.setUniformRowHeights(True)

        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)

        self.setColumnWidth(1, 130)
        self.setColumnWidth(2, 90)
        self.setColumnWidth(3, 80)
        self.setColumnWidth(4, 90)
        self.setColumnWidth(5, 100)

        self.folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)

        self._updating_checks = False
        self._checkboxes_enabled = True
        self._drag_paths_enabled = False

        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.itemChanged.connect(self._on_item_changed)

    def set_paths_drag_enabled(self, enabled: bool) -> None:
        self._drag_paths_enabled = enabled
        self.setDragEnabled(enabled)
        self.setDragDropMode(
            QAbstractItemView.DragDropMode.DragOnly if enabled else QAbstractItemView.DragDropMode.NoDragDrop
        )

    def populate(
        self,
        root: LibraryNode,
        sync_state: dict[str, str] | None = None,
        target_root: Path | None = None,
        checkboxes_enabled: bool = True,
    ) -> None:
        self.clear()
        self._checkboxes_enabled = checkboxes_enabled

        root_item = self._make_item(root)
        self.addTopLevelItem(root_item)
        self._add_children(root_item, root.children)
        root_item.setExpanded(True)

        self.refresh_synced_markers(sync_state or {}, target_root)
        self._emit_checked_count()

    def mimeData(self, items: list[QTreeWidgetItem]) -> QMimeData:
        mime = super().mimeData(items)
        if not self._drag_paths_enabled:
            return mime

        paths: list[str] = []
        for item in items:
            if bool(item.data(0, ROLE_IS_DIR)):
                continue
            raw = str(item.data(0, ROLE_PATH) or "")
            if raw:
                paths.append(raw)

        if paths:
            mime.setData(MIME_CALLIOPE_PATHS, "\n".join(paths).encode("utf-8"))
        return mime

    def refresh_synced_markers(self, sync_state: dict[str, str], target_root: Path | None) -> None:
        # First pass: mark individual files
        for item in self._iter_items():
            is_dir = bool(item.data(0, ROLE_IS_DIR))
            base_name = str(item.data(0, ROLE_BASE_NAME) or item.text(0))
            item.setToolTip(0, "")
            item.setData(0, ROLE_SYNCED_AS, "")

            if is_dir:
                item.setText(0, base_name)
                item.setForeground(0, QBrush(QColor("#e8e4dc")))
                continue

            source = str(item.data(0, ROLE_PATH) or "")
            mapped = sync_state.get(source)
            if mapped and target_root and (target_root / mapped).exists():
                item.setText(0, f"\u2713 {base_name}")
                item.setToolTip(0, f"Synced as: {mapped}")
                item.setData(0, ROLE_SYNCED_AS, mapped)
                item.setForeground(0, QBrush(QColor("#7dc47d")))
            else:
                item.setText(0, base_name)
                item.setForeground(0, QBrush(QColor("#e8e4dc")))

        # Second pass: mark directories if all their file descendants are synced
        for item in self._iter_items():
            if not bool(item.data(0, ROLE_IS_DIR)):
                continue
            base_name = str(item.data(0, ROLE_BASE_NAME) or item.text(0))
            if self._all_children_synced(item):
                item.setText(0, f"\u2713 {base_name}")
                item.setForeground(0, QBrush(QColor("#7dc47d")))

    def _all_children_synced(self, item: "QTreeWidgetItem") -> bool:
        """True when all file descendants are synced (empty dirs return False)."""
        has_files = False
        for i in range(item.childCount()):
            child = item.child(i)
            if bool(child.data(0, ROLE_IS_DIR)):
                if not self._all_children_synced(child):
                    return False
            else:
                has_files = True
                if not str(child.data(0, ROLE_SYNCED_AS) or ""):
                    return False
        return has_files

    def get_checked_files(self) -> list[Path]:
        if not self._checkboxes_enabled:
            return []

        results: list[Path] = []
        for item in self._iter_items():
            if bool(item.data(0, ROLE_IS_DIR)):
                continue
            if item.checkState(0) == Qt.CheckState.Checked:
                raw = str(item.data(0, ROLE_PATH) or "")
                if raw:
                    results.append(Path(raw))
        return results

    def set_all_checked(self, checked: bool) -> None:
        if not self._checkboxes_enabled:
            return

        if self.topLevelItemCount() == 0:
            return

        root = self.topLevelItem(0)
        if root is None:
            return

        self._updating_checks = True
        root.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._apply_children_state(root, root.checkState(0))
        self._updating_checks = False
        self._emit_checked_count()

    def set_checked_paths(self, paths: set[str]) -> None:
        if not self._checkboxes_enabled:
            return

        normalized = {str(Path(p).resolve()) for p in paths}
        self._updating_checks = True
        for item in self._iter_items():
            if bool(item.data(0, ROLE_IS_DIR)):
                continue
            raw = str(item.data(0, ROLE_PATH) or "")
            if not raw:
                continue
            resolved = str(Path(raw).resolve())
            item.setCheckState(0, Qt.CheckState.Checked if resolved in normalized else Qt.CheckState.Unchecked)

        items = list(self._iter_items())
        for item in reversed(items):
            if item.childCount() == 0:
                continue
            states = [item.child(i).checkState(0) for i in range(item.childCount())]
            if all(s == Qt.CheckState.Checked for s in states):
                item.setCheckState(0, Qt.CheckState.Checked)
            elif all(s == Qt.CheckState.Unchecked for s in states):
                item.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                item.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self._updating_checks = False
        self._emit_checked_count()

    def _make_item(self, node: LibraryNode) -> QTreeWidgetItem:
        file_size = human_size(node.size_bytes)
        file_format = node.format_name.upper() if not node.is_dir and node.format_name != "-" else "-"
        length = human_length(node.duration_seconds) if not node.is_dir else "-"
        block_size = node.block_size if not node.is_dir else "-"
        art_size = node.art_dimensions if not node.is_dir else "-"

        item = QTreeWidgetItem([node.name, file_size, file_format, length, block_size, art_size])

        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self._checkboxes_enabled:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        # NOTE: do NOT add ItemIsAutoTristate — it causes the tristate cycle
        # (Unchecked→PartiallyChecked→Checked) on user click, which propagates
        # the PartiallyChecked state to file children via _apply_children_state.
        # Folder tristate display is managed manually by _update_parents instead.

        item.setFlags(flags)
        if self._checkboxes_enabled:
            item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setIcon(0, self.folder_icon if node.is_dir else self.file_icon)
        item.setData(0, ROLE_PATH, str(node.absolute_path))
        item.setData(0, ROLE_IS_DIR, node.is_dir)
        item.setData(0, ROLE_BASE_NAME, node.name)

        item.setTextAlignment(1, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
        item.setTextAlignment(2, int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))
        item.setTextAlignment(3, int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))
        item.setTextAlignment(4, int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))
        item.setTextAlignment(5, int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter))
        return item

    def _add_children(self, parent_item: QTreeWidgetItem, children: list[LibraryNode]) -> None:
        for child in children:
            child_item = self._make_item(child)
            parent_item.addChild(child_item)
            if child.is_dir:
                self._add_children(child_item, child.children)

    def _iter_items(self):
        stack: list[QTreeWidgetItem] = []
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            if top is not None:
                stack.append(top)

        while stack:
            current = stack.pop()
            yield current
            for i in range(current.childCount() - 1, -1, -1):
                stack.append(current.child(i))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if not self._checkboxes_enabled:
            return
        if self._updating_checks or column != 0:
            return

        self._updating_checks = True
        state = item.checkState(0)
        self._apply_children_state(item, state)
        self._update_parents(item)
        self._updating_checks = False
        self._emit_checked_count()

    def _apply_children_state(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        # PartiallyChecked is a display-only state for folders; never propagate it.
        # Treat it as Checked so clicking a PartiallyChecked folder checks all children.
        if state == Qt.CheckState.PartiallyChecked:
            state = Qt.CheckState.Checked
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, state)
            self._apply_children_state(child, state)

    def _update_parents(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent:
            states = [parent.child(i).checkState(0) for i in range(parent.childCount())]
            if not states:
                parent = parent.parent()
                continue

            if all(s == Qt.CheckState.Checked for s in states):
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif all(s == Qt.CheckState.Unchecked for s in states):
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            parent = parent.parent()

    def _emit_checked_count(self) -> None:
        self.checked_files_changed.emit(len(self.get_checked_files()))
