"""``manage.py doctor`` — pass/fail report of binaries and models on disk."""
from __future__ import annotations

import sys
from typing import Any

from django.core.management.base import BaseCommand

from recordings.doctor import run_checks


class Command(BaseCommand):
    help = "Check that ffmpeg, whisper.cpp, llama.cpp and models are available."

    def handle(self, *args: Any, **options: Any) -> None:
        report = run_checks()
        self.stdout.write(report.as_text())
        if not report.all_passed:
            sys.exit(1)
