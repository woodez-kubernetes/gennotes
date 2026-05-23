"""Append-only audit log.

JSON-lines under ``$GENNOTES_DATA_DIR/logs/events.log``. Each line is a
self-contained record so log rotation, redaction, or export is easy. The
log is *not* a security boundary — anyone with read access to the data dir
sees everything. It exists so the user can answer "what happened?".
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gennotes.data_dir import logs_dir

log = logging.getLogger(__name__)
_lock = threading.Lock()

LOG_FILENAME = "events.log"


def log_path() -> Path:
    return logs_dir() / LOG_FILENAME


def log_event(event_type: str, **fields: Any) -> None:
    """Write a single JSON line to the audit log. Never raises."""
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": event_type,
        **fields,
    }
    line = json.dumps(record, default=str, sort_keys=True)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
    except Exception:
        # Audit logging failures must not crash the app.
        log.exception("audit log write failed: %s", event_type)


def read_events(limit: int = 200) -> list[dict[str, Any]]:
    """Return the last ``limit`` events in chronological order."""
    path = log_path()
    if not path.exists():
        return []
    # For typical sizes (single-user app, weeks of events), reading the
    # whole file is fine. If the log gets huge we'd switch to a tail
    # implementation.
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = lines[-limit:]
    out: list[dict[str, Any]] = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            # Skip corrupt lines; the audit log isn't a security boundary,
            # so silent skip is acceptable.
            continue
    return out


def truncate() -> None:
    """Erase the audit log. Used by ``wipe_all`` and the in-app redact button."""
    path = log_path()
    if path.exists():
        path.unlink()


def iter_lines() -> Iterable[str]:
    """Stream raw JSONL for export."""
    path = log_path()
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        yield from fh
