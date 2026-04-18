from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from PIL import Image

from core.profile import SyncProfile


class FlacProcessor:
    def __init__(self, logger) -> None:
        self.logger = logger
        self._flac_missing_warned = False

    def process_for_sync(self, source: Path, profile: SyncProfile, temp_dir: Path) -> Path:
        source = source.resolve()
        suffix = source.suffix.lower()

        if suffix == ".flac":
            return self._process_flac(source, profile, temp_dir)

        if suffix in {".mp3", ".aiff", ".m4a", ".aac", ".ogg", ".opus"}:
            if not self._format_has_art(source, suffix):
                return source
            return self._process_non_flac_art(source, profile, suffix, temp_dir)

        return source

    # ------------------------------------------------------------------ FLAC

    def _process_flac(self, source: Path, profile: SyncProfile, temp_dir: Path) -> Path:
        temp_dir.mkdir(parents=True, exist_ok=True)
        file_key = hashlib.md5(str(source).encode("utf-8")).hexdigest()[:10]
        working_path = temp_dir / f"{source.stem}.{file_key}.work.flac"
        shutil.copy2(source, working_path)

        if profile.apply_block_size and self._needs_blocksize_recode(working_path, profile.max_block_size):
            recoded = temp_dir / f"{source.stem}.{file_key}.block.flac"
            if self._recode_blocksize(working_path, recoded, profile.max_block_size):
                working_path = recoded

        if self._has_embedded_art_flac(working_path):
            self._resize_embedded_art_flac(working_path, profile.art_dimensions)

        return working_path

    def _needs_blocksize_recode(self, file_path: Path, max_block_size: int) -> bool:
        try:
            flac = FLAC(file_path)
            current_max = int(getattr(flac.info, "max_blocksize", 0) or 0)
            return current_max > max_block_size
        except Exception:
            return False

    def _recode_blocksize(self, input_path: Path, output_path: Path, block_size: int) -> bool:
        command = [
            "flac",
            f"--blocksize={block_size}",
            "-f",
            str(input_path),
            "-o",
            str(output_path),
        ]
        self.logger.log(f"Re-encoding FLAC block size: {input_path.name} -> {block_size}")
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            if not self._flac_missing_warned:
                self._flac_missing_warned = True
                self.logger.log("Warning: `flac` CLI not found in PATH. Skipping block size reconversion.")
            return False

        if completed.returncode != 0:
            error_message = completed.stderr.strip() or completed.stdout.strip() or "Unknown error"
            self.logger.log(f"Warning: block-size reconversion failed, keeping original FLAC ({error_message})")
            return False

        return True

    def _has_embedded_art_flac(self, file_path: Path) -> bool:
        try:
            flac = FLAC(file_path)
            return bool(flac.pictures)
        except Exception:
            return False

    def _resize_embedded_art_flac(self, file_path: Path, dimensions: tuple[int, int]) -> None:
        width, height = dimensions
        flac = FLAC(file_path)
        if not flac.pictures:
            return

        first_picture = flac.pictures[0]
        if first_picture.width == width and first_picture.height == height:
            return

        with Image.open(BytesIO(first_picture.data)) as image:
            converted = image.convert("RGB")
            resized = converted.resize((width, height), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            resized.save(buffer, format="JPEG", quality=92)
            new_data = buffer.getvalue()

        picture = Picture()
        picture.type = first_picture.type
        picture.mime = "image/jpeg"
        picture.desc = first_picture.desc
        picture.width = width
        picture.height = height
        picture.depth = 24
        picture.data = new_data

        flac.clear_pictures()
        flac.add_picture(picture)
        flac.save()

        self.logger.log(f"Resized album art for {file_path.name} to {width}x{height}")

    # -------------------------------------------------------- non-FLAC art

    def _format_has_art(self, path: Path, suffix: str) -> bool:
        try:
            if suffix in {".mp3", ".aiff"}:
                from mutagen.id3 import ID3
                return bool(ID3(path).getall("APIC"))
            if suffix in {".m4a", ".aac"}:
                from mutagen.mp4 import MP4
                audio = MP4(path)
                return bool(audio.tags and audio.tags.get("covr"))
            if suffix in {".ogg", ".opus"}:
                from mutagen.oggvorbis import OggVorbis
                return bool(OggVorbis(path).get("metadata_block_picture"))
        except Exception:
            pass
        return False

    def _process_non_flac_art(
        self, source: Path, profile: SyncProfile, suffix: str, temp_dir: Path
    ) -> Path:
        temp_dir.mkdir(parents=True, exist_ok=True)
        file_key = hashlib.md5(str(source).encode("utf-8")).hexdigest()[:10]
        working_path = temp_dir / f"{source.stem}.{file_key}.work{source.suffix}"
        shutil.copy2(source, working_path)

        try:
            if suffix in {".mp3", ".aiff"}:
                self._resize_art_id3(working_path, profile.art_dimensions)
            elif suffix in {".m4a", ".aac"}:
                self._resize_art_m4a(working_path, profile.art_dimensions)
            elif suffix in {".ogg", ".opus"}:
                self._resize_art_ogg(working_path, profile.art_dimensions)
        except Exception as exc:
            self.logger.log(f"Warning: art resize failed for {source.name}: {exc}")

        return working_path

    def _resize_art_id3(self, file_path: Path, dimensions: tuple[int, int]) -> None:
        from mutagen.id3 import ID3, APIC
        width, height = dimensions
        tags = ID3(file_path)
        frames = tags.getall("APIC")
        if not frames or not frames[0].data:
            return

        with Image.open(BytesIO(frames[0].data)) as img:
            if img.width == width and img.height == height:
                return
            converted = img.convert("RGB")
            resized = converted.resize((width, height), Image.Resampling.LANCZOS)
            buf = BytesIO()
            resized.save(buf, format="JPEG", quality=92)
            new_data = buf.getvalue()

        first = frames[0]
        tags.delall("APIC")
        tags.add(APIC(
            encoding=first.encoding,
            mime="image/jpeg",
            type=first.type,
            desc=first.desc,
            data=new_data,
        ))
        tags.save(file_path)
        self.logger.log(f"Resized album art for {file_path.name} to {width}x{height}")

    def _resize_art_m4a(self, file_path: Path, dimensions: tuple[int, int]) -> None:
        from mutagen.mp4 import MP4, MP4Cover
        width, height = dimensions
        audio = MP4(file_path)
        if not audio.tags or not audio.tags.get("covr"):
            return

        cover_data = bytes(audio.tags["covr"][0])
        with Image.open(BytesIO(cover_data)) as img:
            if img.width == width and img.height == height:
                return
            converted = img.convert("RGB")
            resized = converted.resize((width, height), Image.Resampling.LANCZOS)
            buf = BytesIO()
            resized.save(buf, format="JPEG", quality=92)
            new_data = buf.getvalue()

        audio.tags["covr"] = [MP4Cover(new_data, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        self.logger.log(f"Resized album art for {file_path.name} to {width}x{height}")

    def _resize_art_ogg(self, file_path: Path, dimensions: tuple[int, int]) -> None:
        from mutagen.oggvorbis import OggVorbis
        width, height = dimensions
        audio = OggVorbis(file_path)
        raw_list = audio.get("metadata_block_picture", [])
        if not raw_list:
            return

        raw = raw_list[0]
        pad = "=" * (-len(raw) % 4)
        pic = Picture(base64.b64decode(raw + pad))
        if not pic.data:
            return

        with Image.open(BytesIO(pic.data)) as img:
            if img.width == width and img.height == height:
                return
            converted = img.convert("RGB")
            resized = converted.resize((width, height), Image.Resampling.LANCZOS)
            buf = BytesIO()
            resized.save(buf, format="JPEG", quality=92)
            new_data = buf.getvalue()

        new_pic = Picture()
        new_pic.type = pic.type
        new_pic.mime = "image/jpeg"
        new_pic.desc = pic.desc
        new_pic.width = width
        new_pic.height = height
        new_pic.depth = 24
        new_pic.data = new_data

        encoded = base64.b64encode(new_pic.write()).decode("ascii")
        audio["metadata_block_picture"] = [encoded]
        audio.save()
        self.logger.log(f"Resized album art for {file_path.name} to {width}x{height}")


# ------------------------------------------------------------------ helpers


def snapshot_audio_text_metadata(path: Path) -> dict[str, list[str]]:
    try:
        audio = MutagenFile(path, easy=True)
        if not audio:
            return {}

        snapshot: dict[str, list[str]] = {}
        for key, values in audio.items():
            key_name = str(key).strip()
            if not key_name:
                continue
            lower_key = key_name.lower()
            if lower_key in {"metadata_block_picture", "apic", "coverart", "covr"}:
                continue
            normalized = [str(v).strip() for v in values if str(v).strip()]
            snapshot[key_name] = normalized
        return snapshot
    except Exception:
        return {}


def restore_audio_text_metadata(path: Path, metadata: dict[str, list[str]]) -> bool:
    try:
        audio = MutagenFile(path, easy=True)
        if not audio:
            return False

        keep = {str(key): [str(v) for v in values if str(v).strip()] for key, values in metadata.items()}

        for existing in list(audio.keys()):
            existing_key = str(existing)
            if existing_key not in keep:
                try:
                    del audio[existing_key]
                except Exception:
                    pass

        for key, values in keep.items():
            if values:
                audio[key] = values

        audio.save()
        return True
    except Exception:
        return False


def read_audio_metadata(path: Path) -> dict[str, str | int]:
    metadata: dict[str, str | int] = {
        "track": 0,
        "title": path.stem,
        "artist": "Unknown Artist",
        "album": "Unknown Album",
        "year": "Unknown Year",
        "format": (path.suffix.lower().lstrip(".") or "flac"),
    }

    try:
        audio = MutagenFile(path, easy=True)
        if not audio:
            return metadata

        def first(tag: str, fallback: str) -> str:
            value = audio.get(tag, [fallback])
            if not value:
                return fallback
            return str(value[0]).strip() or fallback

        track_value = first("tracknumber", "0").split("/", 1)[0]
        try:
            track_num = int(track_value)
        except ValueError:
            track_num = 0

        metadata["track"] = track_num
        metadata["title"] = first("title", path.stem)
        metadata["artist"] = first("artist", "Unknown Artist")
        metadata["album"] = first("album", "Unknown Album")
        metadata["year"] = first("date", "Unknown Year")
        metadata["format"] = path.suffix.lower().lstrip(".") or "flac"
        return metadata
    except Exception:
        return metadata
