from __future__ import annotations

import json
from pathlib import Path

import pytest

from recordings import audit


@pytest.fixture(autouse=True)
def _isolated_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the audit log to a per-test temp directory."""
    monkeypatch.setattr(audit, "log_path", lambda: tmp_path / "events.log")
    return tmp_path / "events.log"


def test_log_event_writes_jsonl(_isolated_log: Path) -> None:
    audit.log_event("test_event", k=1, v="ok")
    lines = _isolated_log.read_text().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["event"] == "test_event"
    assert obj["k"] == 1
    assert obj["v"] == "ok"
    assert "ts" in obj


def test_read_events_returns_recent_entries(_isolated_log: Path) -> None:
    for i in range(5):
        audit.log_event("e", i=i)
    events = audit.read_events(limit=3)
    assert len(events) == 3
    assert [e["i"] for e in events] == [2, 3, 4]


def test_truncate_clears_log(_isolated_log: Path) -> None:
    audit.log_event("e")
    assert _isolated_log.exists()
    audit.truncate()
    assert not _isolated_log.exists()


def test_log_event_never_raises_on_io_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Point the log at a path inside a missing parent that will fail to create.
    def boom() -> Path:
        raise OSError("disk full")
    monkeypatch.setattr(audit, "log_path", boom)
    # No exception should escape.
    audit.log_event("e", k=1)
