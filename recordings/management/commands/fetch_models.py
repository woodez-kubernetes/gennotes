"""``manage.py fetch_models`` — copy local model files into the data dir.

This command never touches the network. It only copies (or symlinks) a model
file the user already has on disk into ``GENNOTES_DATA_DIR/models/``. We keep
this explicit and offline so the "no outbound traffic" promise holds even
during setup.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from gennotes.data_dir import models_dir


class Command(BaseCommand):
    help = (
        "Copy or symlink a model file (ggml-*.bin for whisper, *.gguf for "
        "llama) into the gennotes data dir. Source must already exist locally."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("source", help="Path to the model file to import.")
        parser.add_argument(
            "--symlink",
            action="store_true",
            help="Symlink instead of copying (faster, but keeps file on the "
            "original disk).",
        )
        parser.add_argument(
            "--name",
            help="Override the destination filename.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        src = Path(options["source"]).expanduser().resolve()
        if not src.exists() or not src.is_file():
            raise CommandError(f"source file not found: {src}")
        name = options.get("name") or src.name
        suffix = Path(name).suffix.lower()
        if suffix not in {".bin", ".gguf"}:
            raise CommandError(
                f"unsupported extension {suffix!r}; expected .bin (whisper) "
                "or .gguf (llama)"
            )
        if suffix == ".bin" and not name.startswith("ggml-"):
            self.stderr.write(self.style.WARNING(
                f"note: whisper models are usually named 'ggml-*.bin'; you "
                f"asked for {name!r}, which the doctor check won't see."
            ))
        dest_dir = models_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        if dest.exists() or dest.is_symlink():
            raise CommandError(f"destination already exists: {dest}")

        if options["symlink"]:
            dest.symlink_to(src)
            self.stdout.write(self.style.SUCCESS(f"symlinked {src} -> {dest}"))
        else:
            shutil.copy2(src, dest)
            dest.chmod(0o600)
            self.stdout.write(self.style.SUCCESS(f"copied {src} -> {dest}"))
