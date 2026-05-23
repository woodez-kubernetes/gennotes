"""Audio ingest helpers: hashing, ffprobe, ffmpeg normalize.

All subprocess calls are isolated here so the views and worker stay focused on
orchestration.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from gennotes.data_dir import media_dir

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".mp4", ".mov", ".mkv", ".webm"}
)

# 2 GiB default; configurable via env so power users can lift it.
DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


def max_upload_bytes() -> int:
    raw = os.environ.get("GENNOTES_MAX_UPLOAD_BYTES")
    if raw:
        return int(raw)
    return DEFAULT_MAX_UPLOAD_BYTES


class IngestError(Exception):
    """Raised when a file cannot be ingested (bad ext, unparseable, etc.)."""


def is_allowed_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def compute_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _ffprobe_bin() -> str:
    return os.environ.get("GENNOTES_FFPROBE", "ffprobe")


def _ffmpeg_bin() -> str:
    return os.environ.get("GENNOTES_FFMPEG", "ffmpeg")


def probe_duration(path: Path) -> float | None:
    """Return duration in seconds, or None if ffprobe can't parse the file."""
    try:
        result = subprocess.run(
            [
                _ffprobe_bin(),
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    try:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def normalize_to_wav(src: Path, dest: Path) -> None:
    """Transcode any supported input into a 16 kHz mono 16-bit PCM WAV.

    Raises IngestError on ffmpeg failure.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_bin(),
        "-nostdin",
        "-y",
        "-i", str(src),
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        "-f", "wav",
        str(dest),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=60 * 60)
    except FileNotFoundError as e:
        raise IngestError(f"ffmpeg binary not found: {e}") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace")[-2000:]
        raise IngestError(f"ffmpeg failed (exit {e.returncode}):\n{stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise IngestError("ffmpeg timed out after 1 hour") from e


def uploads_dir() -> Path:
    d = media_dir() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def wav_dir() -> Path:
    d = media_dir() / "wav"
    d.mkdir(parents=True, exist_ok=True)
    return d
