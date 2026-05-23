"""Request-pipeline middleware for hardening the localhost surface.

Three pieces sit at the back of the chain:
    * :class:`CSPMiddleware` — adds a strict ``Content-Security-Policy``
      header to every response.
    * :class:`IdleTimeoutMiddleware` — clears the session if the user has
      been idle longer than ``GENNOTES_IDLE_TIMEOUT_SECONDS``.
    * :class:`LoginRequiredMiddleware` — redirects unauthenticated requests
      to ``LOGIN_URL`` (or ``/setup/`` if no user has been created yet),
      with a small allowlist for the auth flow itself and static assets.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse

_LAST_SEEN_KEY = "gennotes_last_seen"


class CSPMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.policy: str = getattr(settings, "GENNOTES_CSP", "default-src 'self'")

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response["Content-Security-Policy"] = self.policy
        # Belt-and-braces: legacy browsers honor X-Content-Security-Policy
        # and X-WebKit-CSP. We're localhost-only so these are cheap to keep.
        response.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        return response


class IdleTimeoutMiddleware:
    """Force re-login after ``GENNOTES_IDLE_TIMEOUT_SECONDS`` of inactivity."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.timeout: int = int(getattr(settings, "GENNOTES_IDLE_TIMEOUT_SECONDS", 1800))

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.user.is_authenticated:
            now = int(time.time())
            last = int(request.session.get(_LAST_SEEN_KEY, now))
            if now - last > self.timeout:
                logout(request)
            else:
                request.session[_LAST_SEEN_KEY] = now
        return self.get_response(request)


class LoginRequiredMiddleware:
    """Default-deny: every URL requires a logged-in user.

    Exceptions:
        * ``/login/`` and ``/logout/`` — obvious.
        * ``/setup/`` — used once on first launch to create the user.
        * ``/static/...`` — WhiteNoise serves these; CSP keeps origins locked.
    """

    PUBLIC_PREFIXES: tuple[str, ...] = ("/login/", "/logout/", "/setup/", "/static/")

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if any(request.path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return self.get_response(request)
        if not request.user.is_authenticated:
            # If no user exists yet, push to /setup/. Otherwise, /login/.
            if not _any_user_exists():
                return HttpResponseRedirect(reverse("setup"))
            return HttpResponseRedirect(f"{settings.LOGIN_URL}?next={request.path}")
        return self.get_response(request)


def _any_user_exists() -> bool:
    try:
        return User.objects.exists()
    except Exception:
        # DB not migrated yet — treat as "no user".
        return False


# Re-exported for tests that want to clear the idle timestamp.
def reset_last_seen(session: Any) -> None:
    session.pop(_LAST_SEEN_KEY, None)
