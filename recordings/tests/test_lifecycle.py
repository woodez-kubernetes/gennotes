"""Delete recording + export-zip + wipe_all coverage."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client

from recordings.models import Recording, RecordingState, Summary, Transcript


def _make_recording_with_files(tmp_path: Path) -> Recording:
    stored = tmp_path / "orig.wav"
    wav = tmp_path / "norm.wav"
    stored.write_bytes(b"\x52\x49\x46\x46" + b"\x00" * 100)
    wav.write_bytes(b"\x52\x49\x46\x46" + b"\x00" * 100)
    return Recording.objects.create(
        original_filename="orig.wav",
        stored_path=str(stored),
        wav_path=str(wav),
        sha256="b" * 64,
        size_bytes=stored.stat().st_size,
        duration_seconds=1.0,
        state=RecordingState.DONE,
    )


@pytest.mark.django_db
def test_delete_removes_files_and_row(authed_client: Client, tmp_path: Path) -> None:
    r = _make_recording_with_files(tmp_path)
    Transcript.objects.create(recording=r, text="t", segments=[], language="en", model_name="m")
    Summary.objects.create(recording=r, text="s", model_name="m", prompt_version="v1")
    response = authed_client.post(f"/r/{r.id}/delete/", follow=True)
    assert response.status_code == 200
    assert not Recording.objects.filter(id=r.id).exists()
    assert not Path(r.stored_path).exists()
    assert not Path(r.wav_path).exists()
    # cascade nukes transcript + summary
    assert not Transcript.objects.filter(recording_id=r.id).exists()
    assert not Summary.objects.filter(recording_id=r.id).exists()


@pytest.mark.django_db
def test_export_zip_contains_expected_entries(authed_client: Client, tmp_path: Path) -> None:
    r = _make_recording_with_files(tmp_path)
    Transcript.objects.create(
        recording=r, text="hello", segments=[{"start": 0.0, "end": 1.0, "text": "hello"}],
        language="en", model_name="ggml-tiny.bin",
    )
    Summary.objects.create(
        recording=r, text="## Decisions\n- ship it", model_name="llama.gguf", prompt_version="v1"
    )
    response = authed_client.get(f"/r/{r.id}/export/")
    assert response.status_code == 200
    blob = b"".join(getattr(response, "streaming_content", []))
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = set(zf.namelist())
    assert "transcript.md" in names
    assert "summary.md" in names
    assert "manifest.txt" in names
    assert "original.wav" in names
    transcript_md = zf.read("transcript.md").decode()
    assert "hello" in transcript_md
    assert "ggml-tiny.bin" in transcript_md
    summary_md = zf.read("summary.md").decode()
    assert "ship it" in summary_md
    manifest = zf.read("manifest.txt").decode()
    assert r.sha256 in manifest


@pytest.mark.django_db(transaction=True)
def test_wipe_all_command_deletes_db_files_and_audit(tmp_path: Path) -> None:
    # transaction=True because the command runs VACUUM, which can't be
    # executed inside the default pytest-django wrapping transaction.
    r = _make_recording_with_files(tmp_path)
    Transcript.objects.create(recording=r, text="t", segments=[], language="en", model_name="m")
    call_command("wipe_all", "--yes")
    assert Recording.objects.count() == 0
    assert Transcript.objects.count() == 0
