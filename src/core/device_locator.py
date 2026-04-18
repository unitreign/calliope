from __future__ import annotations

import json
import string
import uuid
from pathlib import Path

MARKER_FILE_NAME = ".calliope_device.json"


def get_mount_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    anchor = resolved.anchor
    if anchor:
        return Path(anchor)
    return Path("/")


def _marker_path(root: Path) -> Path:
    return root / MARKER_FILE_NAME


def read_marker_device_id(root: Path) -> str:
    marker = _marker_path(root)
    if not marker.exists():
        return ""
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
        return str(raw.get("device_id", "")).strip()
    except Exception:
        return ""


def ensure_marker_device_id_for_path(path: Path) -> str:
    root = get_mount_root(path)
    marker = _marker_path(root)

    existing = read_marker_device_id(root)
    if existing:
        return existing

    device_id = str(uuid.uuid4())
    payload = {
        "device_id": device_id,
        "created_by": "Calliope",
    }
    marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return device_id


def relative_target_path(path: Path) -> str:
    root = get_mount_root(path)
    resolved = path.expanduser().resolve()
    try:
        rel = resolved.relative_to(root)
        rel_text = str(rel).replace("\\", "/")
        return rel_text if rel_text and rel_text != "." else ""
    except ValueError:
        return ""


def _candidate_mount_roots() -> list[Path]:
    roots: list[Path] = []

    if Path("C:\\").exists():
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:\\")
            if root.exists():
                roots.append(root)
        return roots

    roots.append(Path("/"))
    for base in (Path("/media"), Path("/mnt"), Path("/Volumes")):
        if base.exists() and base.is_dir():
            for child in base.iterdir():
                if child.is_dir():
                    roots.append(child)
    return roots


def find_mount_root_by_device_id(device_id: str) -> Path | None:
    wanted = str(device_id or "").strip()
    if not wanted:
        return None

    for root in _candidate_mount_roots():
        if read_marker_device_id(root) == wanted:
            return root
    return None


def resolve_target_for_device_id(device_id: str, relative_path: str) -> Path | None:
    root = find_mount_root_by_device_id(device_id)
    if root is None:
        return None

    rel = str(relative_path or "").strip().replace("\\", "/")
    if not rel or rel == ".":
        return root
    return root / Path(rel)
