"""Background worker for transcription and summarization.

One ``ThreadPoolExecutor`` with one worker thread. Each unit of work is its
own function so retries can be aimed precisely. The two stages chain
automatically — on successful transcription the worker submits a
summarization job for the same recording.

Limitations / intentional tradeoffs:
    * In-process only. If Django reloads (autoreload, gunicorn worker recycle)
      mid-job, the Recording row stays in its transitional state. The detail
      page shows the stuck state; the user clicks "Retry".
    * No multi-job concurrency. v1 is a single-user desktop app; serial is
      simpler and avoids GPU/CPU contention.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from django.db import transaction

from . import audit
from .models import Recording, RecordingState, Summary, Transcript
from .stt import whispercpp
from .summarize import pipeline as summary_pipeline
from .summarize.chunking import Segment as SummarySegment
from .summarize.prompts import DEFAULT_VERSION as DEFAULT_PROMPT_VERSION

log = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="gennotes-worker"
            )
        return _executor


# --- Transcription -----------------------------------------------------------


def submit_transcription(recording_id: int) -> Future[Any]:
    """Queue a transcription job. Idempotent."""
    return _get_executor().submit(_run_transcription, recording_id)


def _claim(recording_id: int, *, from_state: str, to_state: str) -> Recording | None:
    """Atomically transition between states. Returns None if not claimable."""
    with transaction.atomic():
        updated = (
            Recording.objects.select_for_update()
            .filter(id=recording_id, state=from_state)
            .update(state=to_state, error_message="")
        )
        if updated == 0:
            return None
        return Recording.objects.get(id=recording_id)


def _run_transcription(recording_id: int) -> None:
    recording = _claim(
        recording_id,
        from_state=RecordingState.UPLOADED,
        to_state=RecordingState.TRANSCRIBING,
    )
    if recording is None:
        log.info("recording %s not in 'uploaded' state; skipping", recording_id)
        return

    try:
        wav_path = Path(recording.wav_path)
        if not wav_path.exists():
            raise FileNotFoundError(f"normalized WAV missing: {wav_path}")
        model_path = whispercpp.resolve_model_path()
        result = whispercpp.transcribe(wav_path=wav_path, model_path=model_path)
    except Exception as e:
        log.exception("transcription failed for recording %s", recording_id)
        Recording.objects.filter(id=recording_id).update(
            state=RecordingState.FAILED,
            error_message=f"{type(e).__name__}: {e}",
        )
        audit.log_event(
            "transcription_failed",
            recording_id=recording_id,
            error=f"{type(e).__name__}: {e}",
        )
        return

    with transaction.atomic():
        Transcript.objects.update_or_create(
            recording_id=recording_id,
            defaults={
                "text": result.text,
                "segments": [
                    {"start": s.start, "end": s.end, "text": s.text}
                    for s in result.segments
                ],
                "language": result.language,
                "engine": "whisper.cpp",
                "model_name": result.model_name,
            },
        )
        Recording.objects.filter(id=recording_id).update(
            state=RecordingState.TRANSCRIBED, error_message=""
        )

    audit.log_event(
        "transcribed",
        recording_id=recording_id,
        model=result.model_name,
        language=result.language,
        chars=len(result.text),
    )
    # Chain into summarization automatically.
    submit_summarization(recording_id)


# --- Summarization -----------------------------------------------------------


def submit_summarization(
    recording_id: int, *, prompt_version: str = DEFAULT_PROMPT_VERSION
) -> Future[Any]:
    return _get_executor().submit(_run_summarization, recording_id, prompt_version)


def _run_summarization(recording_id: int, prompt_version: str) -> None:
    recording = _claim(
        recording_id,
        from_state=RecordingState.TRANSCRIBED,
        to_state=RecordingState.SUMMARIZING,
    )
    if recording is None:
        log.info("recording %s not in 'transcribed' state; skipping summary", recording_id)
        return

    try:
        transcript = Transcript.objects.get(recording_id=recording_id)
        segments = [
            SummarySegment(
                start=float(s.get("start", 0.0)),
                end=float(s.get("end", 0.0)),
                text=str(s.get("text", "")),
            )
            for s in (transcript.segments or [])
        ]
        result = summary_pipeline.summarize(
            transcript_text=transcript.text,
            segments=segments,
            prompt_version=prompt_version,
        )
    except Exception as e:
        log.exception("summarization failed for recording %s", recording_id)
        Recording.objects.filter(id=recording_id).update(
            state=RecordingState.FAILED,
            error_message=f"{type(e).__name__}: {e}",
        )
        audit.log_event(
            "summarization_failed",
            recording_id=recording_id,
            error=f"{type(e).__name__}: {e}",
        )
        return

    with transaction.atomic():
        Summary.objects.create(
            recording_id=recording_id,
            text=result.text,
            model_name=result.model_name,
            prompt_version=result.prompt_version,
        )
        Recording.objects.filter(id=recording_id).update(
            state=RecordingState.DONE, error_message=""
        )

    audit.log_event(
        "summarized",
        recording_id=recording_id,
        model=result.model_name,
        prompt_version=result.prompt_version,
        n_chunks=result.n_chunks,
    )


# --- Retry / re-summarize ----------------------------------------------------


def retry(recording_id: int) -> Future[Any] | None:
    """Pick up a failed/stuck recording where it broke.

    Decision rule:
        * Has a Transcript row → roll back to TRANSCRIBED, re-summarize.
        * No Transcript row → roll back to UPLOADED, re-transcribe.

    Returns the queued future or None if the recording isn't retryable.
    """
    with transaction.atomic():
        try:
            recording = Recording.objects.select_for_update().get(id=recording_id)
        except Recording.DoesNotExist:
            return None
        if recording.state not in {
            RecordingState.FAILED,
            RecordingState.TRANSCRIBING,
            RecordingState.SUMMARIZING,
        }:
            return None
        has_transcript = Transcript.objects.filter(recording_id=recording_id).exists()
        if has_transcript:
            Recording.objects.filter(id=recording_id).update(
                state=RecordingState.TRANSCRIBED, error_message=""
            )
            return submit_summarization(recording_id)
        Recording.objects.filter(id=recording_id).update(
            state=RecordingState.UPLOADED, error_message=""
        )
        return submit_transcription(recording_id)


def resummarize(
    recording_id: int, *, prompt_version: str = DEFAULT_PROMPT_VERSION
) -> Future[Any] | None:
    """Force a fresh summary even if the recording is already DONE.

    Existing Summary rows are kept (so prompt-version A/B comparison is
    possible). The new Summary row supersedes them for display purposes.
    """
    with transaction.atomic():
        try:
            recording = Recording.objects.select_for_update().get(id=recording_id)
        except Recording.DoesNotExist:
            return None
        if not Transcript.objects.filter(recording_id=recording_id).exists():
            return None
        if recording.state not in {RecordingState.DONE, RecordingState.TRANSCRIBED}:
            return None
        Recording.objects.filter(id=recording_id).update(
            state=RecordingState.TRANSCRIBED, error_message=""
        )
        return submit_summarization(recording_id, prompt_version=prompt_version)
