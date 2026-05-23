"""Resolve and initialize the on-disk data directory.

Everything the app reads or writes outside of source code lives under a single
user-visible directory. Path is overridable via the ``GENNOTES_DATA_DIR`` env
var (default: ``~/.gennotes``).

Subdirectories:
    media/    uploaded originals + normalized WAV
    models/   whisper.cpp + llama.cpp model files (GGUF / ggml-*.bin)
    logs/    audit + state-transition logs
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "GENNOTES_DATA_DIR"
DEFAULT = "~/.gennotes"


def data_dir() -> Path:
    raw = os.environ.get(ENV_VAR, DEFAULT)
    return Path(raw).expanduser().resolve()


def media_dir() -> Path:
    return data_dir() / "media"


def models_dir() -> Path:
    return data_dir() / "models"


def logs_dir() -> Path:
    return data_dir() / "logs"


def db_path() -> Path:
    return data_dir() / "db.sqlite3"


def ensure_layout() -> Path:
    """Create the data dir tree if missing. Idempotent."""
    root = data_dir()
    for sub in (root, media_dir(), media_dir() / "uploads", media_dir() / "wav",
                models_dir(), logs_dir()):
        sub.mkdir(parents=True, exist_ok=True)
    return root
