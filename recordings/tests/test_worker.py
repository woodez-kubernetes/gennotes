"""Worker orchestration tests with the binary wrappers mocked.

We exercise the state machine end-to-end: claim, chain, retry, and
resummarize — verifying the right Recording / Transcript / Summary rows
exist at each step.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from recordings import worker
from recordings.models import Recording, RecordingState, Summary, Transcript
from recordings.stt.whispercpp import Segment as STTSegment
from recordings.stt.whispercpp import TranscriptResult
from recordings.summarize.pipeline import SummaryResult


def _make_recording() -> Recording:
    return Recording.objects.create(
        original_filename="x.wav",
        stored_path="/tmp/x",
        wav_path="/tmp/x.wav",
        sha256="a" * 64,
        size_bytes=10,
        state=RecordingState.UPLOADED,
    )


def _fake_transcript() -> TranscriptResult:
    return TranscriptResult(
        text="Alice: Hello. Bob: Goodbye.",
        language="en",
        segments=[
            STTSegment(0.0, 1.5, "Alice: Hello."),
            STTSegment(1.5, 3.0, "Bob: Goodbye."),
        ],
        model_name="ggml-base.bin",
    )


def _fake_summary() -> SummaryResult:
    return SummaryResult(
        text="## Decisions\n- None.",
        model_name="llama.gguf",
        prompt_version="v1",
        n_chunks=1,
    )


@pytest.mark.django_db
def test_transcription_success_chains_into_summarization() -> None:
    recording = _make_recording()
    with patch("recordings.worker.whispercpp.transcribe", return_value=_fake_transcript()), \
         patch("recordings.worker.whispercpp.resolve_model_path"), \
         patch("recordings.worker.Path.exists", return_value=True), \
         patch("recordings.worker.summary_pipeline.summarize", return_value=_fake_summary()):
        worker._run_transcription(recording.id)
        # Chained call would have been scheduled on the executor, but we
        # bypass the executor by invoking the summary runner directly.
        worker._run_summarization(recording.id, "v1")

    recording.refresh_from_db()
    assert recording.state == RecordingState.DONE
    assert Transcript.objects.filter(recording_id=recording.id).exists()
    summary = Summary.objects.get(recording_id=recording.id)
    assert summary.prompt_version == "v1"


@pytest.mark.django_db
def test_transcription_failure_marks_failed() -> None:
    recording = _make_recording()
    with patch("recordings.worker.whispercpp.resolve_model_path", side_effect=FileNotFoundError("nope")):
        worker._run_transcription(recording.id)
    recording.refresh_from_db()
    assert recording.state == RecordingState.FAILED
    assert "FileNotFoundError" in recording.error_message


@pytest.mark.django_db
def test_summarization_failure_marks_failed_with_transcript_intact() -> None:
    recording = _make_recording()
    Recording.objects.filter(id=recording.id).update(state=RecordingState.TRANSCRIBED)
    Transcript.objects.create(
        recording=recording,
        text="content",
        segments=[],
        language="en",
        model_name="ggml-base.bin",
    )
    with patch(
        "recordings.worker.summary_pipeline.summarize",
        side_effect=RuntimeError("model crash"),
    ):
        worker._run_summarization(recording.id, "v1")
    recording.refresh_from_db()
    assert recording.state == RecordingState.FAILED
    assert "RuntimeError" in recording.error_message
    # Transcript survives so a retry can pick up from summarization.
    assert Transcript.objects.filter(recording_id=recording.id).exists()


@pytest.mark.django_db
def test_retry_resummarizes_when_transcript_present() -> None:
    recording = _make_recording()
    Recording.objects.filter(id=recording.id).update(
        state=RecordingState.FAILED, error_message="something"
    )
    Transcript.objects.create(
        recording=recording,
        text="content",
        segments=[],
        language="en",
        model_name="ggml-base.bin",
    )
    with patch("recordings.worker.submit_summarization") as sub:
        result = worker.retry(recording.id)
    assert result is not None
    sub.assert_called_once_with(recording.id)
    recording.refresh_from_db()
    assert recording.state == RecordingState.TRANSCRIBED


@pytest.mark.django_db
def test_retry_retranscribes_when_no_transcript() -> None:
    recording = _make_recording()
    Recording.objects.filter(id=recording.id).update(state=RecordingState.FAILED)
    with patch("recordings.worker.submit_transcription") as sub:
        result = worker.retry(recording.id)
    assert result is not None
    sub.assert_called_once_with(recording.id)
    recording.refresh_from_db()
    assert recording.state == RecordingState.UPLOADED


@pytest.mark.django_db
def test_resummarize_creates_new_summary_keeping_old() -> None:
    recording = _make_recording()
    Recording.objects.filter(id=recording.id).update(state=RecordingState.DONE)
    Transcript.objects.create(
        recording=recording,
        text="content",
        segments=[],
        language="en",
        model_name="ggml-base.bin",
    )
    Summary.objects.create(
        recording=recording, text="old summary", model_name="llama.gguf", prompt_version="v1"
    )
    with patch("recordings.worker.submit_summarization") as sub:
        result = worker.resummarize(recording.id)
    assert result is not None
    sub.assert_called_once()
    recording.refresh_from_db()
    assert recording.state == RecordingState.TRANSCRIBED
    # old summary still around
    assert Summary.objects.filter(recording_id=recording.id, text="old summary").exists()
