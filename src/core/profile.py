from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from utils.config import PROFILES_FILE, ensure_data_dir


DEFAULT_FILENAME_FORMAT = "{folder}/{filename}.{format}"
_LEGACY_FILENAME_FORMATS = {
    "{artist}/{album}/{track:02d} - {title}.{format}",
    "{artist}/{album}/{track:02d} - {title}.flac",
}


@dataclass
class SyncProfile:
    name: str
    album_art_width: int = 750
    album_art_height: int = 750
    filename_format: str = DEFAULT_FILENAME_FORMAT
    max_block_size: int = 4096
    apply_block_size: bool = True  # when False, skip block-size re-encoding entirely

    @property
    def art_dimensions(self) -> tuple[int, int]:
        return max(1, int(self.album_art_width)), max(1, int(self.album_art_height))

    @property
    def album_art_size(self) -> str:
        width, height = self.art_dimensions
        return f"{width}x{height}"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SyncProfile":
        block_size = payload.get("max_block_size", 4096)
        try:
            block_size = int(block_size)
        except (TypeError, ValueError):
            block_size = 4096

        width = payload.get("album_art_width")
        height = payload.get("album_art_height")

        if width is None or height is None:
            raw_size = str(payload.get("album_art_size", "750x750")).strip() or "750x750"
            try:
                width_s, height_s = raw_size.lower().split("x", 1)
                width = int(width_s.strip())
                height = int(height_s.strip())
            except (ValueError, AttributeError):
                width, height = 750, 750

        try:
            width = max(1, int(width))
            height = max(1, int(height))
        except (TypeError, ValueError):
            width, height = 750, 750

        filename_format = str(payload.get("filename_format", DEFAULT_FILENAME_FORMAT)).strip() or DEFAULT_FILENAME_FORMAT
        if filename_format in _LEGACY_FILENAME_FORMATS:
            filename_format = DEFAULT_FILENAME_FORMAT

        return cls(
            name=str(payload.get("name", "")).strip(),
            album_art_width=width,
            album_art_height=height,
            filename_format=filename_format,
            max_block_size=max(256, block_size),
            apply_block_size=bool(payload.get("apply_block_size", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        width, height = self.art_dimensions
        return {
            "name": self.name,
            "album_art_width": width,
            "album_art_height": height,
            "album_art_size": f"{width}x{height}",
            "filename_format": self.filename_format,
            "max_block_size": int(self.max_block_size),
            "apply_block_size": self.apply_block_size,
        }


class ProfileStore:
    def __init__(self) -> None:
        ensure_data_dir()
        profiles_file_exists = PROFILES_FILE.exists()
        self._profiles = self._load()
        if not self._profiles:
            self._profiles = [SyncProfile(name="Default")]
            if not profiles_file_exists:
                self.save()

    def _load(self) -> list[SyncProfile]:
        if not PROFILES_FILE.exists():
            return []

        try:
            raw = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            deduped: list[SyncProfile] = []
            seen: set[str] = set()
            for profile in (SyncProfile.from_dict(item) for item in raw):
                if not profile.name:
                    continue
                key = profile.name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(profile)
            return deduped
        except (OSError, json.JSONDecodeError, ValueError):
            return []

    def save(self) -> None:
        payload = [profile.to_dict() for profile in self._profiles]
        PROFILES_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def all(self) -> list[SyncProfile]:
        return list(self._profiles)

    def names(self) -> list[str]:
        return [p.name for p in self._profiles]

    def get(self, name: str) -> SyncProfile | None:
        wanted = name.casefold()
        for profile in self._profiles:
            if profile.name.casefold() == wanted:
                return profile
        return None

    def upsert(self, profile: SyncProfile, original_name: str | None = None) -> None:
        if not profile.name:
            raise ValueError("Profile name is required")

        replace_name = original_name if original_name is not None else profile.name
        replace_key = replace_name.casefold()
        new_key = profile.name.casefold()
        replaced = False
        for index, existing in enumerate(self._profiles):
            if existing.name.casefold() == replace_key:
                self._profiles[index] = profile
                replaced = True
                break

        if not replaced:
            if any(existing.name.casefold() == new_key for existing in self._profiles):
                raise ValueError("A profile with this name already exists")
            self._profiles.append(profile)

        self._profiles.sort(key=lambda p: p.name.lower())
        self.save()

    def delete(self, name: str) -> None:
        self._profiles = [profile for profile in self._profiles if profile.name != name]
        if not self._profiles:
            self._profiles = [SyncProfile(name="Default")]
        self.save()
