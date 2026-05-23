"""Console-script entry point exposed as ``gennotes`` on PATH."""
from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gennotes.settings_dev")
    from django.core.management import execute_from_command_line

    execute_from_command_line(["gennotes", *sys.argv[1:]])
