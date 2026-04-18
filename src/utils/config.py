from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
CONFIG_FILE = DATA_DIR / "config.json"
PROFILES_FILE = DATA_DIR / "profiles.json"
SYNC_STATE_FILE = DATA_DIR / "sync_state.json"


DEFAULT_CONFIG: dict[str, Any] = {
    "source_path": "",
    "target_path": "",
    "selected_profile": "",
    "source_history": [],
    "target_history": [],
}


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Any) -> None:
    ensure_data_dir()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path: Path, default: Any) -> Any:
    ensure_data_dir()
    if not path.exists():
        _write_json(path, default)
        return default

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _write_json(path, default)
        return default


def load_config() -> dict[str, Any]:
    raw = _read_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
    if not isinstance(raw, dict):
        raw = {}
    payload = DEFAULT_CONFIG.copy()
    payload.update(raw)
    return payload


def save_config(config: dict[str, Any]) -> None:
    payload = DEFAULT_CONFIG.copy()
    payload.update(config)
    _write_json(CONFIG_FILE, payload)


def load_sync_state() -> dict[str, str]:
    return _read_json(SYNC_STATE_FILE, {})


def save_sync_state(mapping: dict[str, str]) -> None:
    _write_json(SYNC_STATE_FILE, mapping)
