"""Loopback bind enforcement on ``manage.py serve``."""
from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_serve_rejects_non_loopback_without_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GENNOTES_ALLOW_LAN", raising=False)
    with pytest.raises(CommandError, match="not on the loopback allowlist"):
        # The handler validates address before starting the server, so we
        # never actually bind a socket.
        call_command("serve", "0.0.0.0:8765")


def test_serve_rejects_invalid_address() -> None:
    with pytest.raises(CommandError, match="invalid address"):
        call_command("serve", "not-an-address")


def test_serve_accepts_loopback_with_no_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loopback case passes validation without trying to bind a socket."""
    from recordings.management.commands import serve as serve_mod

    monkeypatch.delenv("GENNOTES_ALLOW_LAN", raising=False)
    # Stub out ``make_server`` so the test never opens a port.
    monkeypatch.setattr(serve_mod, "make_server", lambda host, port, handler: _StubServer())
    call_command("serve", "127.0.0.1:0")


class _StubServer:
    def __enter__(self) -> _StubServer:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def serve_forever(self) -> None:
        return None
