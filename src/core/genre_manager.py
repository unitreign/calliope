from __future__ import annotations

from pathlib import Path

from mutagen import File as MutagenFile


def _normalize_genres(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        if value is None:
            continue
        raw = str(value).strip()
        if not raw:
            continue

        chunks = [raw]
        if ";" in raw:
            chunks = [part.strip() for part in raw.split(";") if part.strip()]

        for chunk in chunks:
            lowered = chunk.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(chunk)

    return normalized


def read_genres(path: Path) -> list[str]:
    try:
        audio = MutagenFile(path, easy=True)
        if not audio:
            return []
        values = audio.get("genre", [])
        return _normalize_genres([str(v) for v in values])
    except Exception:
        return []


def write_genres(path: Path, genres: list[str]) -> bool:
    try:
        audio = MutagenFile(path, easy=True)
        if not audio:
            return False
        normalized = _normalize_genres(genres)
        if normalized:
            audio["genre"] = normalized
        else:
            if "genre" in audio:
                del audio["genre"]
        audio.save()
        return True
    except Exception:
        return False


def replace_genre(path: Path, genre_name: str) -> bool:
    return write_genres(path, [genre_name])


def append_genre(path: Path, genre_name: str) -> bool:
    current = read_genres(path)
    return write_genres(path, current + [genre_name])


def rename_genre(path: Path, old_name: str, new_name: str) -> bool:
    current = read_genres(path)
    if not current:
        return False

    replaced = [new_name if item.lower() == old_name.lower() else item for item in current]
    return write_genres(path, replaced)


def remove_genre(path: Path, genre_name: str) -> bool:
    current = read_genres(path)
    updated = [item for item in current if item.lower() != genre_name.lower()]
    return write_genres(path, updated)
