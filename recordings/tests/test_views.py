from __future__ import annotations

import io
import wave
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from recordings.models import Recording, RecordingState


@pytest.mark.django_db
def test_list_renders_empty(authed_client: Client) -> None:
    response = authed_client.get("/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "Upload an audio or video file" in body
    assert "No recordings yet" in body


@pytest.mark.django_db
def test_doctor_endpoint_renders(authed_client: Client) -> None:
    response = authed_client.get("/doctor/")
    assert response.status_code == 200
    assert "System check" in response.content.decode()


@pytest.mark.django_db
def test_upload_rejects_bad_extension(authed_client: Client) -> None:
    bad = SimpleUploadedFile("malware.exe", b"\x00" * 16, content_type="application/octet-stream")
    response = authed_client.post("/upload/", {"audio": bad}, follow=True)
    assert response.status_code == 200
    assert "Extension not allowed" in response.content.decode()


@pytest.mark.django_db
def test_upload_rejects_unparseable_audio(authed_client: Client) -> None:
    junk = SimpleUploadedFile("noise.mp3", b"not actually audio", content_type="audio/mpeg")
    response = authed_client.post("/upload/", {"audio": junk}, follow=True)
    assert response.status_code == 200
    assert "could not be parsed" in response.content.decode()


@pytest.mark.django_db
def test_upload_accepts_valid_file_and_redirects_to_detail(authed_client: Client) -> None:
    """End-to-end through the view layer, but with the worker stubbed.

    ffprobe and ffmpeg run for real — they're cheap on a tiny WAV. The
    transcription worker is mocked so the test doesn't depend on whisper.cpp.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    wav_bytes = buf.getvalue()
    upload = SimpleUploadedFile("test.wav", wav_bytes, content_type="audio/wav")

    with patch("recordings.views.worker.submit_transcription") as submit:
        response = authed_client.post("/upload/", {"audio": upload})
    assert response.status_code == 302
    assert response["Location"].startswith("/r/")
    submit.assert_called_once()

    recording = Recording.objects.get()
    assert recording.state == RecordingState.UPLOADED
    assert recording.duration_seconds is not None
    assert recording.duration_seconds > 0.5
    assert Path(recording.stored_path).exists()
    assert Path(recording.wav_path).exists()


@pytest.mark.django_db
def test_status_endpoint_signals_done_on_terminal_state(authed_client: Client) -> None:
    recording = Recording.objects.create(
        original_filename="x.wav",
        stored_path="/tmp/x.wav",
        wav_path="/tmp/x.wav",
        sha256="a" * 64,
        size_bytes=10,
        state=RecordingState.DONE,
    )
    response = authed_client.get(f"/r/{recording.id}/status/")
    assert response.status_code == 200
    assert response["X-Gennotes-Done"] == "1"
