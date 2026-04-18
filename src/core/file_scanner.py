from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mutagen import File as MutagenFile
from mutagen.flac import FLAC

AUDIO_EXTENSIONS = {".flac", ".mp3", ".ogg", ".m4a", ".wav", ".aac", ".aiff", ".ape"}


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


def _extract_art_dimensions(path: Path) -> str:
    """Extract embedded album art dimensions for any supported audio format."""
    ext = path.suffix.lower()
    try:
        if ext == ".flac":
            flac = FLAC(path)
            if flac.pictures:
                p = flac.pictures[0]
                if p.width > 0 and p.height > 0:
                    return f"{p.width}x{p.height}"
        elif ext in {".mp3", ".aiff"}:
            from io import BytesIO
            from mutagen.id3 import ID3
            from PIL import Image
            tags = ID3(path)
            frames = tags.getall("APIC")
            if frames and frames[0].data:
                img = Image.open(BytesIO(frames[0].data))
                return f"{img.width}x{img.height}"
        elif ext in {".m4a", ".aac"}:
            from io import BytesIO
            from mutagen.mp4 import MP4
            from PIL import Image
            audio = MP4(path)
            if audio.tags and "covr" in audio.tags and audio.tags["covr"]:
                img = Image.open(BytesIO(bytes(audio.tags["covr"][0])))
                return f"{img.width}x{img.height}"
        elif ext in {".ogg", ".opus"}:
            import base64
            from io import BytesIO
            from mutagen.oggvorbis import OggVorbis
            from mutagen.flac import Picture
            from PIL import Image
            audio = OggVorbis(path)
            raw_list = audio.get("metadata_block_picture", [])
            if raw_list:
                pad = "=" * (-len(raw_list[0]) % 4)
                pic = Picture(base64.b64decode(raw_list[0] + pad))
                if pic.data:
                    img = Image.open(BytesIO(pic.data))
                    return f"{img.width}x{img.height}"
    except Exception:
        pass
    return "-"


def read_audio_details(path: Path) -> tuple[float, str, str]:
    duration = 0.0
    block_size = "-"

    try:
        audio = MutagenFile(path)
        if audio and getattr(audio, "info", None) and getattr(audio.info, "length", None):
            duration = float(audio.info.length)
    except Exception:
        duration = 0.0

    if path.suffix.lower() == ".flac":
        try:
            flac = FLAC(path)
            max_block = int(getattr(flac.info, "max_blocksize", 0) or 0)
            if max_block > 0:
                block_size = str(max_block)
        except Exception:
            pass

    art_dimensions = _extract_art_dimensions(path)

    return duration, block_size, art_dimensions


def _count_audio_files(root: Path) -> int:
    """Fast count of audio files under root — no metadata reads."""
    try:
        return sum(1 for p in root.rglob("*") if p.is_file() and is_audio_file(p))
    except Exception:
        return 0


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

    total_files = _count_audio_files(source_root)
    counter = [0]

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

            duration, block_size, art_dimensions = read_audio_details(entry)

            counter[0] += 1
            if progress_callback and total_files > 0:
                progress_callback(counter[0], total_files)

            rel = entry.relative_to(source_root)
            file_node = LibraryNode(
                name=entry.name,
                absolute_path=entry,
                relative_path=rel,
                is_dir=False,
                size_bytes=file_size,
                format_name=(entry.suffix.lower().lstrip(".") or "-"),
                duration_seconds=duration,
                block_size=block_size,
                art_dimensions=art_dimensions,
            )
            folder_children.append(file_node)
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
