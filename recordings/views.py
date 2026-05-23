from __future__ import annotations

import contextlib
import io
import zipfile
from pathlib import Path

from django.contrib import messages
from django.db import IntegrityError
from django.http import (
    FileResponse,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from . import audit, ingest, worker
from .doctor import run_checks
from .models import Recording, RecordingState


def _safe_basename(name: str) -> str:
    base = Path(name).stem
    return slugify(base) or "recording"


def doctor(request: HttpRequest) -> HttpResponse:
    report = run_checks()
    return render(
        request,
        "recordings/doctor.html",
        {"ready": report.all_passed, "checks": report.checks},
    )


def recording_list(request: HttpRequest) -> HttpResponse:
    recordings = Recording.objects.all()[:200]
    report = run_checks()
    return render(
        request,
        "recordings/list.html",
        {
            "recordings": recordings,
            "max_upload_mib": ingest.max_upload_bytes() // (1024 * 1024),
            "allowed_extensions": sorted(ingest.ALLOWED_EXTENSIONS),
            "ready": report.all_passed,
        },
    )


@require_POST
def upload(request: HttpRequest) -> HttpResponse:
    upload_file = request.FILES.get("audio")
    if upload_file is None or not upload_file.name:
        messages.error(request, "No file submitted.")
        return redirect("recording-list")

    filename: str = upload_file.name
    if not ingest.is_allowed_extension(filename):
        messages.error(
            request,
            f"Extension not allowed. Permitted: {', '.join(sorted(ingest.ALLOWED_EXTENSIONS))}",
        )
        return redirect("recording-list")

    if upload_file.size is not None and upload_file.size > ingest.max_upload_bytes():
        messages.error(
            request,
            f"File too large ({upload_file.size} bytes); cap is "
            f"{ingest.max_upload_bytes()} bytes.",
        )
        return redirect("recording-list")

    # 1) Stream upload to disk under media/uploads/
    uploads = ingest.uploads_dir()
    suffix = Path(filename).suffix.lower()
    staging_path = uploads / f".staging-{filename}-{id(upload_file)}{suffix}"
    with staging_path.open("wb") as out:
        for chunk in upload_file.chunks():
            out.write(chunk)

    # 2) Hash and rename. ffprobe to verify it's parseable.
    sha = ingest.compute_sha256(staging_path)
    duration = ingest.probe_duration(staging_path)
    if duration is None:
        staging_path.unlink(missing_ok=True)
        messages.error(request, "File could not be parsed as audio/video.")
        return redirect("recording-list")

    final_original = uploads / f"{sha}{suffix}"
    if final_original.exists():
        staging_path.unlink(missing_ok=True)
    else:
        staging_path.replace(final_original)
    final_original.chmod(0o600)

    # 3) Dedupe by sha256.
    existing = Recording.objects.filter(sha256=sha).first()
    if existing is not None:
        messages.info(request, "Already uploaded — redirected to existing recording.")
        return redirect("recording-detail", pk=existing.id)

    # 4) Normalize to WAV.
    wav_path = ingest.wav_dir() / f"{sha}.wav"
    try:
        ingest.normalize_to_wav(final_original, wav_path)
    except ingest.IngestError as e:
        messages.error(request, f"Audio normalization failed: {e}")
        return redirect("recording-list")
    wav_path.chmod(0o600)

    # 5) Create Recording row, enqueue transcription.
    try:
        recording = Recording.objects.create(
            original_filename=filename,
            stored_path=str(final_original),
            wav_path=str(wav_path),
            sha256=sha,
            size_bytes=final_original.stat().st_size,
            duration_seconds=duration,
            state=RecordingState.UPLOADED,
        )
    except IntegrityError:
        existing = Recording.objects.filter(sha256=sha).first()
        if existing is not None:
            return redirect("recording-detail", pk=existing.id)
        raise

    audit.log_event(
        "recording_uploaded",
        recording_id=recording.id,
        sha=sha,
        size_bytes=recording.size_bytes,
        user=request.user.get_username() if request.user.is_authenticated else "",
    )
    worker.submit_transcription(recording.id)
    return redirect("recording-detail", pk=recording.id)


def recording_detail(request: HttpRequest, pk: int) -> HttpResponse:
    recording = get_object_or_404(Recording, pk=pk)
    transcript = getattr(recording, "transcript", None)
    latest_summary = recording.summaries.first()
    return render(
        request,
        "recordings/detail.html",
        {
            "recording": recording,
            "transcript": transcript,
            "summary": latest_summary,
        },
    )


def recording_status(request: HttpRequest, pk: int) -> HttpResponse:
    recording = get_object_or_404(Recording, pk=pk)
    response = render(request, "recordings/_status.html", {"recording": recording})
    if recording.is_terminal:
        response["X-Gennotes-Done"] = "1"
    return response


@require_POST
def recording_retry(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    recording = get_object_or_404(Recording, pk=pk)
    if worker.retry(recording.id) is None:
        messages.warning(request, "Recording is not in a retryable state.")
    return redirect(reverse("recording-detail", args=[recording.id]))


@require_POST
def recording_resummarize(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    recording = get_object_or_404(Recording, pk=pk)
    if worker.resummarize(recording.id) is None:
        messages.warning(
            request, "Re-summarization requires a completed transcript."
        )
    return redirect(reverse("recording-detail", args=[recording.id]))


@require_POST
def recording_delete(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Wipe a recording — files on disk + DB row (cascades transcript & summaries)."""
    recording = get_object_or_404(Recording, pk=pk)
    for raw in (recording.stored_path, recording.wav_path):
        if not raw:
            continue
        with contextlib.suppress(OSError):
            Path(raw).unlink(missing_ok=True)
    audit.log_event(
        "recording_deleted",
        recording_id=recording.id,
        sha=recording.sha256,
        filename=recording.original_filename,
        user=request.user.get_username() if request.user.is_authenticated else "",
    )
    recording.delete()
    messages.success(request, "Recording deleted.")
    return redirect("recording-list")


def recording_export(request: HttpRequest, pk: int) -> FileResponse:
    """Return a zip with the original audio, transcript.md, and summary.md."""
    recording = get_object_or_404(Recording, pk=pk)
    transcript = getattr(recording, "transcript", None)
    summary = recording.summaries.first()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # original audio (skip if missing — e.g. previously deleted)
        original = Path(recording.stored_path) if recording.stored_path else None
        if original and original.exists():
            zf.write(original, arcname=f"original{original.suffix.lower()}")

        # transcript.md
        if transcript is not None:
            md = _transcript_to_markdown(recording, transcript)
            zf.writestr("transcript.md", md)

        # summary.md
        if summary is not None:
            md = _summary_to_markdown(recording, summary)
            zf.writestr("summary.md", md)

        # tiny manifest so the zip is self-describing
        manifest = (
            f"recording_id: {recording.id}\n"
            f"sha256: {recording.sha256}\n"
            f"original_filename: {recording.original_filename}\n"
            f"created_at: {recording.created_at.isoformat()}\n"
            f"duration_seconds: {recording.duration_seconds}\n"
        )
        zf.writestr("manifest.txt", manifest)

    buf.seek(0)
    download_name = f"gennotes-{_safe_basename(recording.original_filename)}-{recording.sha256[:8]}.zip"
    audit.log_event(
        "export_created",
        recording_id=recording.id,
        sha=recording.sha256,
        user=request.user.get_username() if request.user.is_authenticated else "",
    )
    return FileResponse(buf, as_attachment=True, filename=download_name)


def _transcript_to_markdown(recording: Recording, transcript: object) -> str:
    """Pure helper — formats the transcript (incl. segment timecodes) as Markdown."""
    out: list[str] = []
    out.append(f"# Transcript — {recording.original_filename}")
    out.append("")
    out.append(f"- recorded sha256: `{recording.sha256}`")
    out.append(f"- engine: {getattr(transcript, 'engine', '')}")
    out.append(f"- model: {getattr(transcript, 'model_name', '')}")
    lang = getattr(transcript, "language", "")
    if lang:
        out.append(f"- language: {lang}")
    out.append("")
    out.append("---")
    out.append("")
    segments = getattr(transcript, "segments", None) or []
    if segments:
        for s in segments:
            start = float(s.get("start", 0.0))
            end = float(s.get("end", 0.0))
            text = str(s.get("text", "")).strip()
            out.append(f"`[{start:.1f}-{end:.1f}]` {text}")
            out.append("")
    else:
        out.append(getattr(transcript, "text", ""))
    return "\n".join(out)


def _summary_to_markdown(recording: Recording, summary: object) -> str:
    out: list[str] = []
    out.append(f"# Summary — {recording.original_filename}")
    out.append("")
    out.append(f"- model: {getattr(summary, 'model_name', '')}")
    out.append(f"- prompt version: {getattr(summary, 'prompt_version', '')}")
    created = getattr(summary, "created_at", None)
    if created:
        out.append(f"- generated: {created.isoformat()}")
    out.append("")
    out.append("---")
    out.append("")
    out.append(getattr(summary, "text", ""))
    return "\n".join(out)


def audit_log(request: HttpRequest) -> HttpResponse:
    events = list(reversed(audit.read_events(limit=500)))
    return render(request, "recordings/audit_log.html", {"events": events})


@require_POST
def audit_log_truncate(request: HttpRequest) -> HttpResponseRedirect:
    audit.truncate()
    audit.log_event(
        "audit_truncated",
        user=request.user.get_username() if request.user.is_authenticated else "",
    )
    messages.success(request, "Audit log cleared.")
    return redirect("audit-log")
