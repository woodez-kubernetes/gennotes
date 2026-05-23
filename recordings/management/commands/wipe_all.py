"""``manage.py wipe_all`` — nuke every recording artifact on disk + in DB.

Useful for handing the laptop back, or before a sensitivity reset. Asks
for confirmation unless ``--yes`` is passed. Models, original audio, the
normalized WAV cache, the audit log, and the SQLite WAL all go.
"""
from __future__ import annotations

import shutil
from typing import Any

from django.core.management.base import BaseCommand
from django.db import connection

from gennotes.data_dir import media_dir
from recordings import audit
from recordings.models import Recording


class Command(BaseCommand):
    help = "Delete every recording, transcript, summary, and audit event."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the interactive confirmation prompt.",
        )
        parser.add_argument(
            "--keep-audit",
            action="store_true",
            help="Preserve the audit log (default: truncate it).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        n_recordings = Recording.objects.count()
        media = media_dir()
        if not options["yes"]:
            self.stdout.write(self.style.WARNING(
                f"About to delete:\n"
                f"  - {n_recordings} recording(s) from the database\n"
                f"  - everything under {media}\n"
                f"  - {'KEEP' if options['keep_audit'] else 'CLEAR'} the audit log\n"
            ))
            answer = input("Type 'wipe' to confirm: ").strip()
            if answer != "wipe":
                self.stdout.write("aborted.")
                return

        # 1) Drop DB rows (cascade nukes Transcripts & Summaries).
        deleted, _ = Recording.objects.all().delete()
        self.stdout.write(f"deleted {deleted} DB row(s).")

        # 2) Wipe media tree.
        for sub in ("uploads", "wav"):
            target = media / sub
            if target.exists():
                shutil.rmtree(target)
        self.stdout.write(f"cleared media tree under {media}.")

        # 3) Audit log.
        if not options["keep_audit"]:
            audit.truncate()
            self.stdout.write("audit log cleared.")
        audit.log_event("wipe_all", deleted=deleted, keep_audit=options["keep_audit"])

        # 4) VACUUM SQLite so the file actually shrinks on disk.
        with connection.cursor() as cur:
            cur.execute("VACUUM")
        self.stdout.write("vacuumed sqlite.")
        self.stdout.write(self.style.SUCCESS("wipe_all complete."))
