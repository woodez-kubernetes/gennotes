"""whisper.cpp wrapper.

Calls the ``whisper-cli`` binary with JSON output, then parses the resulting
``<basename>.json`` file into a :class:`TranscriptResult`.

The expected JSON layout (whisper.cpp >= 1.6) looks like::

    {
      "result": {"language": "en"},
      "transcription": [
        {
          "timestamps": {"from": "00:00:00,000", "to": "00:00:02,500"},
          "offsets":    {"from": 0,            "to": 2500},
          "text":       "Hello world."
        },
        ...
      ]
    }

We rely on the ``offsets`` field (milliseconds) for timing — it's more robust
than parsing the formatted ``timestamps`` strings.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from gennotes.data_dir import models_dir


class WhisperError(Exception):
    """Raised when whisper-cli fails or its output cannot be parsed."""


@dataclass
class Segment:
    start: float  # seconds
    end: float
    text: str


@dataclass
class TranscriptResult:
    text: str
    language: str
    segments: list[Segment] = field(default_factory=list)
    model_name: str = ""


def _whisper_bin() -> str:
    return os.environ.get("GENNOTES_WHISPER_BIN", "whisper-cli")


def resolve_model_path() -> Path:
    """Return the absolute path of the whisper model to use.

    Resolution order:
      1. ``$GENNOTES_WHISPER_MODEL`` (absolute path OR basename under models/).
      2. First ``ggml-*.bin`` under models/ in alphabetical order.

    Raises FileNotFoundError if nothing matches.
    """
    explicit = os.environ.get("GENNOTES_WHISPER_MODEL")
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = models_dir() / explicit
        if not p.exists():
            raise FileNotFoundError(f"GENNOTES_WHISPER_MODEL points to missing file: {p}")
        return p
    candidates = sorted(models_dir().glob("ggml-*.bin"))
    if not candidates:
        raise FileNotFoundError(
            f"no whisper model (ggml-*.bin) found under {models_dir()}"
        )
    return candidates[0]


def parse_whisper_json(payload: Mapping[str, object]) -> TranscriptResult:
    """Parse a whisper.cpp JSON payload into a TranscriptResult.

    Pure function — no I/O, no subprocess. Exists as its own function so it
    can be tested against golden fixtures without invoking the binary.
    """
    result_obj = payload.get("result")
    language = ""
    if isinstance(result_obj, dict):
        lang = result_obj.get("language")
        if isinstance(lang, str):
            language = lang

    raw_segments = payload.get("transcription", [])
    if not isinstance(raw_segments, list):
        raise WhisperError("malformed whisper JSON: 'transcription' is not a list")

    segments: list[Segment] = []
    text_parts: list[str] = []
    for entry in raw_segments:
        if not isinstance(entry, dict):
            continue
        offsets = entry.get("offsets")
        text = entry.get("text", "")
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not isinstance(offsets, dict):
            continue
        try:
            start = float(offsets["from"]) / 1000.0
            end = float(offsets["to"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        segments.append(Segment(start=start, end=end, text=text))
        if text:
            text_parts.append(text)

    return TranscriptResult(
        text=" ".join(text_parts),
        language=language,
        segments=segments,
    )


def transcribe(
    wav_path: Path,
    model_path: Path | None = None,
    language: str | None = None,
) -> TranscriptResult:
    """Run whisper-cli on a normalized WAV file and return the result.

    The caller is responsible for ensuring ``wav_path`` is 16 kHz mono PCM —
    see ``recordings.ingest.normalize_to_wav``.
    """
    if model_path is None:
        model_path = resolve_model_path()

    with tempfile.TemporaryDirectory(prefix="gennotes-whisper-") as tmp:
        out_basename = Path(tmp) / "out"
        cmd = [
            _whisper_bin(),
            "-m", str(model_path),
            "-f", str(wav_path),
            "-oj",
            "-of", str(out_basename),
            "-nt",  # no timestamps in the text stream (we use JSON timestamps)
        ]
        if language:
            cmd += ["-l", language]

        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=60 * 60 * 4)
        except FileNotFoundError as e:
            raise WhisperError(f"whisper-cli not found: {e}") from e
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or b"").decode("utf-8", errors="replace")[-2000:]
            raise WhisperError(f"whisper-cli exited {e.returncode}:\n{stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise WhisperError("whisper-cli timed out after 4 hours") from e

        json_path = out_basename.with_suffix(".json")
        if not json_path.exists():
            raise WhisperError(f"whisper-cli produced no JSON at {json_path}")

        try:
            payload = json.loads(json_path.read_text())
        except json.JSONDecodeError as e:
            raise WhisperError(f"failed to parse whisper JSON: {e}") from e

    result = parse_whisper_json(payload)
    result.model_name = model_path.name
    return result
