from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.file_scanner import LibraryNode
from utils.config import DATA_DIR

SCAN_CACHE_FILE = DATA_DIR / "scan_cache.json"
CACHE_MAX_AGE_SECONDS = 60 * 60 * 24
CACHE_VERSION = 2  # increment to invalidate all cached entries


def _read_cache() -> dict[str, Any]:
    if not SCAN_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(SCAN_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCAN_CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cache_key(root: Path) -> str:
    return str(root.expanduser().resolve()).lower()


def _serialize_node(node: LibraryNode) -> dict[str, Any]:
    return {
        "name": node.name,
        "absolute_path": str(node.absolute_path),
        "relative_path": str(node.relative_path),
        "is_dir": bool(node.is_dir),
        "size_bytes": int(node.size_bytes),
        "format_name": node.format_name,
        "duration_seconds": float(node.duration_seconds),
        "block_size": node.block_size,
        "art_dimensions": node.art_dimensions,
        "children": [_serialize_node(child) for child in node.children],
    }


def _deserialize_node(payload: dict[str, Any]) -> LibraryNode:
    return LibraryNode(
        name=str(payload.get("name", "")),
        absolute_path=Path(str(payload.get("absolute_path", "."))),
        relative_path=Path(str(payload.get("relative_path", "."))),
        is_dir=bool(payload.get("is_dir", False)),
        size_bytes=int(payload.get("size_bytes", 0) or 0),
        format_name=str(payload.get("format_name", "-")),
        duration_seconds=float(payload.get("duration_seconds", 0.0) or 0.0),
        block_size=str(payload.get("block_size", "-")),
        art_dimensions=str(payload.get("art_dimensions", "-")),
        children=[_deserialize_node(child) for child in payload.get("children", [])],
    )


def load_cached_tree(root: Path) -> LibraryNode | None:
    resolved = root.expanduser().resolve()
    key = _cache_key(resolved)
    cache = _read_cache()
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None

    now = time.time()
    cached_at = float(entry.get("cached_at", 0) or 0)
    if now - cached_at > CACHE_MAX_AGE_SECONDS:
        return None

    try:
        current_root_mtime = float(resolved.stat().st_mtime)
    except OSError:
        return None

    if abs(current_root_mtime - float(entry.get("root_mtime", 0) or 0)) > 0.001:
        return None

    if int(entry.get("_version", 0) or 0) != CACHE_VERSION:
        return None

    node_payload = entry.get("tree")
    if not isinstance(node_payload, dict):
        return None

    try:
        return _deserialize_node(node_payload)
    except Exception:
        return None


def save_cached_tree(root: Path, tree: LibraryNode) -> None:
    resolved = root.expanduser().resolve()
    key = _cache_key(resolved)
    cache = _read_cache()

    try:
        root_mtime = float(resolved.stat().st_mtime)
    except OSError:
        root_mtime = 0.0

    cache[key] = {
        "_version": CACHE_VERSION,
        "cached_at": time.time(),
        "root_mtime": root_mtime,
        "tree": _serialize_node(tree),
    }
    _write_cache(cache)
