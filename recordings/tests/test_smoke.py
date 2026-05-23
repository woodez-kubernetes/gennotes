"""Stage 0 smoke tests: project boots, doctor checks run, home renders."""
from __future__ import annotations

import pytest
from django.test import Client

from recordings.doctor import Report, run_checks


def test_doctor_reports_all_checks() -> None:
    report = run_checks()
    assert isinstance(report, Report)
    names = [c.name for c in report.checks]
    assert "ffmpeg" in names
    assert "whisper.cpp CLI" in names
    assert "llama.cpp CLI" in names
    assert "prompt templates" in names


@pytest.mark.django_db
def test_home_view_renders(authed_client: Client) -> None:
    response = authed_client.get("/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "gennotes" in body
    assert "Upload an audio or video file" in body
