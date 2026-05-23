from __future__ import annotations

from django.db import models


class RecordingState(models.TextChoices):
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    SUMMARIZING = "summarizing"
    DONE = "done"
    FAILED = "failed"


class Recording(models.Model):
    original_filename = models.CharField(max_length=512)
    stored_path = models.CharField(max_length=1024)
    wav_path = models.CharField(max_length=1024, blank=True, default="")
    sha256 = models.CharField(max_length=64, unique=True)
    size_bytes = models.BigIntegerField(default=0)
    duration_seconds = models.FloatField(null=True, blank=True)
    state = models.CharField(
        max_length=16,
        choices=RecordingState.choices,
        default=RecordingState.UPLOADED,
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["state"])]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.state})"

    @property
    def is_terminal(self) -> bool:
        return self.state in {RecordingState.DONE, RecordingState.FAILED}

    @property
    def is_active(self) -> bool:
        return self.state in {RecordingState.TRANSCRIBING, RecordingState.SUMMARIZING}


class Transcript(models.Model):
    recording = models.OneToOneField(
        Recording, on_delete=models.CASCADE, related_name="transcript"
    )
    text = models.TextField()
    # segments: [{"start": float seconds, "end": float seconds, "text": str}, ...]
    segments = models.JSONField(default=list, blank=True)
    language = models.CharField(max_length=16, blank=True, default="")
    engine = models.CharField(max_length=32, default="whisper.cpp")
    model_name = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Transcript<{self.recording_id}>"


class Summary(models.Model):
    recording = models.ForeignKey(
        Recording, on_delete=models.CASCADE, related_name="summaries"
    )
    text = models.TextField()
    model_name = models.CharField(max_length=128)
    prompt_version = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Summary<{self.recording_id}@{self.prompt_version}>"
