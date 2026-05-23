from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from recordings import ingest


def test_is_allowed_extension() -> None:
    assert ingest.is_allowed_extension("clip.mp3")
    assert ingest.is_allowed_extension("Recording.WAV")
    assert ingest.is_allowed_extension("meeting.mp4")
    assert not ingest.is_allowed_extension("doc.pdf")
    assert not ingest.is_allowed_extension("noext")


def test_compute_sha256(tmp_path: Path) -> None:
    p = tmp_path / "f.bin"
    payload = b"hello world\n" * 1024
    p.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert ingest.compute_sha256(p) == expected


def test_max_upload_bytes_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GENNOTES_MAX_UPLOAD_BYTES", raising=False)
    assert ingest.max_upload_bytes() == 2 * 1024 * 1024 * 1024


def test_max_upload_bytes_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENNOTES_MAX_UPLOAD_BYTES", "12345")
    assert ingest.max_upload_bytes() == 12345
