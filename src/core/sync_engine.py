from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from string import Formatter

from PyQt6.QtCore import QThread, pyqtSignal

from core.flac_processor import (
    FlacProcessor,
    read_audio_metadata,
    restore_audio_text_metadata,
    snapshot_audio_text_metadata,
)
from core.profile import SyncProfile
from utils.config import save_sync_state


class SafeFormatDict(dict):
    def __missing__(self, key):
        return f"Unknown {key}"


def sanitize_component(value: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in forbidden else ch for ch in value)
    cleaned = cleaned.strip().rstrip(".")
    return cleaned or "untitled"


def build_target_relative_path(
    source_file: Path,
    source_root: Path,
    profile: SyncProfile,
    metadata: dict[str, str | int],
) -> Path:
    # Resolve both so relative_to() comparisons are reliable regardless of
    # how the caller constructed the paths (expanduser-only vs fully resolved).
    source_root = source_root.expanduser().resolve()
    source_file = source_file.expanduser().resolve()

    formatter = Formatter()
    has_folder_token = "{folder}" in profile.filename_format
    has_path_separators = "/" in profile.filename_format or "\\" in profile.filename_format

    safe_values: dict[str, str | int] = {}
    for key, value in metadata.items():
        if isinstance(value, int):
            safe_values[key] = value
        else:
            safe_values[key] = sanitize_component(value)

    # {folder} = relative folder from source root (preserves source structure)
    # {filename} = original file stem without extension
    try:
        rel_parent = source_file.relative_to(source_root).parent
        folder_str = str(rel_parent).replace("\\", "/")
        folder_str = "" if folder_str == "." else folder_str
    except ValueError:
        folder_str = ""
    safe_values["folder"] = folder_str
    safe_values["filename"] = sanitize_component(source_file.stem)

    try:
        formatted = formatter.vformat(profile.filename_format, (), SafeFormatDict(safe_values))
    except Exception:
        fallback_format = str(safe_values.get("format", source_file.suffix.lower().lstrip(".")))
        formatted = f"{safe_values.get('track', 0):02d} - {safe_values.get('title', source_file.stem)}.{fallback_format}"

    formatted = formatted.replace("\\", "/").strip("/")
    formatted_path = Path(formatted) if formatted else Path(source_file.stem)

    suffix = source_file.suffix.lower() or ".flac"
    if formatted_path.suffix == "":
        formatted_path = formatted_path.with_suffix(suffix)

    parts = [sanitize_component(part) for part in formatted_path.parts if part not in (".", "")]
    if not parts:
        parts = [sanitize_component(source_file.stem) + suffix]

    formatted_path = Path(*parts)

    if has_folder_token or has_path_separators:
        return formatted_path

    # Flat format (no path separators, no {folder}): preserve subfolder structure
    # but never prepend the source root's own name.
    try:
        rel_parent = source_file.relative_to(source_root).parent
    except ValueError:
        rel_parent = Path(".")

    if str(rel_parent) in ("", "."):
        return formatted_path
    return rel_parent / formatted_path.name


class SyncEngine(QThread):
    log_emitted = pyqtSignal(str)
    progress_updated = pyqtSignal(int, int)
    file_processed = pyqtSignal(str, bool)
    finished_sync = pyqtSignal(bool, int, int)  # cancelled, processed, errors

    def __init__(
        self,
        source_root: Path,
        target_root: Path,
        files: list[Path],
        profile: SyncProfile,
        sync_state: dict[str, str],
        force_resync: bool = False,
        preserve_target_metadata: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.source_root = source_root
        self.target_root = target_root
        self.files = files
        self.profile = profile
        self.sync_state = sync_state
        self.force_resync = force_resync
        self.preserve_target_metadata = preserve_target_metadata
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        processed = 0
        errors = 0
        total = len(self.files)

        processor = FlacProcessor(logger=self)

        with tempfile.TemporaryDirectory(prefix="calliope_sync_") as tmp_dir_raw:
            tmp_dir = Path(tmp_dir_raw)

            for source_file in self.files:
                if self._stop_requested:
                    self.log(f"Sync cancelled by user after {processed}/{total} files")
                    self.finished_sync.emit(True, processed, errors)
                    return

                source_file = source_file.resolve()
                if not source_file.exists():
                    errors += 1
                    processed += 1
                    self.log(f"Missing file: {source_file}")
                    self.file_processed.emit(str(source_file), False)
                    self.progress_updated.emit(processed, total)
                    continue

                try:
                    metadata = read_audio_metadata(source_file)
                    target_rel = build_target_relative_path(source_file, self.source_root, self.profile, metadata)
                    target_abs = (self.target_root / target_rel).resolve()
                    normalized_rel = str(target_rel).replace("\\", "/")

                    existing = self.sync_state.get(str(source_file))
                    previous_target_abs = (self.target_root / existing).resolve() if existing else None
                    if (not self.force_resync) and existing == normalized_rel and target_abs.exists():
                        processed += 1
                        self.log(f"Skipped already synced: {source_file.name}")
                        self.file_processed.emit(str(source_file), True)
                        self.progress_updated.emit(processed, total)
                        continue

                    metadata_snapshot: dict[str, list[str]] = {}
                    if self.force_resync and self.preserve_target_metadata:
                        metadata_source = None
                        if previous_target_abs and previous_target_abs.exists():
                            metadata_source = previous_target_abs
                        elif target_abs.exists():
                            metadata_source = target_abs
                        if metadata_source is not None:
                            metadata_snapshot = snapshot_audio_text_metadata(metadata_source)

                    prepared = processor.process_for_sync(source_file, self.profile, tmp_dir)
                    target_abs.parent.mkdir(parents=True, exist_ok=True)

                    shutil.copy2(prepared, target_abs)
                    if metadata_snapshot:
                        if not restore_audio_text_metadata(target_abs, metadata_snapshot):
                            self.log(f"Warning: failed to preserve target metadata for {target_abs.name}")

                    if (
                        self.force_resync
                        and previous_target_abs
                        and previous_target_abs.exists()
                        and previous_target_abs != target_abs
                    ):
                        try:
                            previous_target_abs.unlink()
                        except OSError:
                            pass

                    self.sync_state[str(source_file)] = normalized_rel
                    save_sync_state(self.sync_state)

                    processed += 1
                    self.log(f"Synced: {source_file.name} -> {target_rel}")
                    self.file_processed.emit(str(source_file), True)
                except Exception as exc:
                    processed += 1
                    errors += 1
                    self.log(f"Error syncing {source_file.name}: {exc}")
                    self.file_processed.emit(str(source_file), False)

                self.progress_updated.emit(processed, total)

        self.finished_sync.emit(False, processed, errors)

    def log(self, message: str) -> None:
        self.log_emitted.emit(message)
