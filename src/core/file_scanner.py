from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mutagen import File as MutagenFile

AUDIO_EXTENSIONS = {".flac", ".mp3", ".ogg", ".m4a", ".wav", ".aac", ".aiff", ".ape"}

# Metadata reads are I/O-bound (network mounts especially), so a bigger
# pool than the core count still helps. 16 measured best.
SCAN_WORKERS = 16


@dataclass
class LibraryNode:
    name: str
    absolute_path: Path
    relative_path: Path
    is_dir: bool
    size_bytes: int = 0
    format_name: str = "-"
    duration_seconds: float = 0.0
    block_size: str = "-"
    art_dimensions: str = "-"
    children: list["LibraryNode"] = field(default_factory=list)


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def human_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "-"

    size = float(size_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return "-"


def human_length(seconds: float) -> str:
    if seconds <= 0:
        return "-"

    total = int(round(seconds))
    minutes = total // 60
    remainder = total % 60
    return f"{minutes}:{remainder:02d}"


# Python's default file buffer can balloon to match a large filesystem
# blksize (NFS mounts often report ~1MB). mutagen seeks around within a
# file, so a huge buffer means a full re-read on every seek. Keep it small.
READ_BUFFER_BYTES = 8192


def read_audio_details(path: Path) -> tuple[float, str, str]:
    """Read duration, FLAC block size, and art dimensions in one parse."""
    duration = 0.0
    block_size = "-"
    art_dimensions = "-"

    try:
        with open(path, "rb", buffering=READ_BUFFER_BYTES) as fh:
            audio = MutagenFile(fh)
    except Exception:
        audio = None

    if audio is None:
        return duration, block_size, art_dimensions

    info = getattr(audio, "info", None)
    if info and getattr(info, "length", None):
        duration = float(info.length)

    ext = path.suffix.lower()

    try:
        if ext == ".flac":
            max_block = int(getattr(info, "max_blocksize", 0) or 0)
            if max_block > 0:
                block_size = str(max_block)
            if audio.pictures:
                p = audio.pictures[0]
                if p.width > 0 and p.height > 0:
                    art_dimensions = f"{p.width}x{p.height}"
        elif ext in {".mp3", ".aiff"} and audio.tags is not None:
            from io import BytesIO
            from PIL import Image
            frames = audio.tags.getall("APIC")
            if frames and frames[0].data:
                img = Image.open(BytesIO(frames[0].data))
                art_dimensions = f"{img.width}x{img.height}"
        elif ext in {".m4a", ".aac"} and audio.tags is not None:
            from io import BytesIO
            from PIL import Image
            if "covr" in audio.tags and audio.tags["covr"]:
                img = Image.open(BytesIO(bytes(audio.tags["covr"][0])))
                art_dimensions = f"{img.width}x{img.height}"
        elif ext in {".ogg", ".opus"}:
            import base64
            from io import BytesIO
            from mutagen.flac import Picture
            from PIL import Image
            raw_list = audio.get("metadata_block_picture", []) if hasattr(audio, "get") else []
            if raw_list:
                pad = "=" * (-len(raw_list[0]) % 4)
                pic = Picture(base64.b64decode(raw_list[0] + pad))
                if pic.data:
                    img = Image.open(BytesIO(pic.data))
                    art_dimensions = f"{img.width}x{img.height}"
    except Exception:
        pass

    return duration, block_size, art_dimensions


def scan_source_tree(
    source_root: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> LibraryNode:
    source_root = source_root.expanduser().resolve()
    root = LibraryNode(
        name="All Songs",
        absolute_path=source_root,
        relative_path=Path("."),
        is_dir=True,
        children=[],
    )

    pending_files: list[LibraryNode] = []

    def build_dir_node(folder: Path) -> LibraryNode | None:
        folder_children: list[LibraryNode] = []
        total_size = 0

        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (OSError, PermissionError):
            return None

        for entry in entries:
            if entry.is_dir():
                child_dir = build_dir_node(entry)
                if child_dir:
                    folder_children.append(child_dir)
                    total_size += child_dir.size_bytes
                continue

            if not entry.is_file() or not is_audio_file(entry):
                continue

            file_size = 0
            try:
                file_size = entry.stat().st_size
            except OSError:
                pass

            rel = entry.relative_to(source_root)
            file_node = LibraryNode(
                name=entry.name,
                absolute_path=entry,
                relative_path=rel,
                is_dir=False,
                size_bytes=file_size,
                format_name=(entry.suffix.lower().lstrip(".") or "-"),
            )
            folder_children.append(file_node)
            pending_files.append(file_node)
            total_size += file_size

        if not folder_children and folder != source_root:
            return None

        rel_folder = Path(".") if folder == source_root else folder.relative_to(source_root)
        node = LibraryNode(
            name="All Songs" if folder == source_root else folder.name,
            absolute_path=folder,
            relative_path=rel_folder,
            is_dir=True,
            size_bytes=total_size,
            children=folder_children,
        )
        return node

    rebuilt = build_dir_node(source_root)
    if rebuilt:
        root.children = rebuilt.children
        root.size_bytes = rebuilt.size_bytes

    total_files = len(pending_files)
    counter = 0
    counter_lock = threading.Lock()

    def process(node: LibraryNode) -> None:
        nonlocal counter
        node.duration_seconds, node.block_size, node.art_dimensions = read_audio_details(
            node.absolute_path
        )
        if progress_callback:
            with counter_lock:
                counter += 1
                done = counter
            progress_callback(done, total_files)

    if pending_files:
        with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
            list(executor.map(process, pending_files))

    return root


def flatten_files(node: LibraryNode) -> list[LibraryNode]:
    results: list[LibraryNode] = []
    if not node.is_dir:
        return [node]

    for child in node.children:
        if child.is_dir:
            results.extend(flatten_files(child))
        else:
            results.append(child)
    return results
