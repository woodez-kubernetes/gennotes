"""``manage.py serve`` — start the localhost-only production-ish server.

Why not just ``runserver``? Because ``runserver`` happily binds to whatever
host you ask for, including ``0.0.0.0``. We want a single entry point that
*refuses* to bind off-loopback unless the user explicitly opts in.

This uses Django's bundled WSGI server (same as runserver under the hood)
plus a strict address allowlist. It's good enough for a single-user
desktop app and avoids a gunicorn dependency.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser
from typing import Any
from wsgiref.simple_server import make_server

from django.core.handlers.wsgi import WSGIHandler
from django.core.management.base import BaseCommand, CommandError
from django.core.management.commands.runserver import naiveip_re

LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


class Command(BaseCommand):
    help = (
        "Serve gennotes on the loopback interface (127.0.0.1). Refuses to "
        "bind off-loopback unless GENNOTES_ALLOW_LAN=1 is set."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "addrport",
            nargs="?",
            default="127.0.0.1:8765",
            help="host:port to bind. Default 127.0.0.1:8765.",
        )
        parser.add_argument(
            "--open",
            action="store_true",
            help="Open the default browser to the running app.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        addrport: str = options["addrport"]
        match = naiveip_re.match(addrport)
        if match is None:
            raise CommandError(f"invalid address {addrport!r}; use host:port")
        host, _, _, _, port_str = match.groups()
        host = host or "127.0.0.1"
        port = int(port_str)

        if host not in LOOPBACK_HOSTS and os.environ.get("GENNOTES_ALLOW_LAN") != "1":
            raise CommandError(
                f"refusing to bind {host!r}: not on the loopback allowlist. "
                "Set GENNOTES_ALLOW_LAN=1 to override."
            )

        url = f"http://{host}:{port}/"
        self.stdout.write(self.style.SUCCESS(f"gennotes serving on {url}"))
        self.stdout.write("Press Ctrl-C to stop.")

        if options.get("open"):
            threading.Thread(
                target=_open_when_ready,
                args=(host, port, url),
                daemon=True,
            ).start()

        handler = WSGIHandler()
        with make_server(host, port, handler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                self.stdout.write("\nstopping…")


def _open_when_ready(host: str, port: int, url: str) -> None:
    """Wait for the WSGI server to be up, then open the browser."""
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.1)
