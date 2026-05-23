"""First-run setup, login, and logout views."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from . import audit

MIN_PASSPHRASE_LENGTH = 12


def _client(request: HttpRequest) -> str:
    """A safe-to-log client identifier (host:port). No PII beyond the IP."""
    return str(request.META.get("REMOTE_ADDR", "?"))


@require_http_methods(["GET", "POST"])
def setup_view(request: HttpRequest) -> HttpResponse:
    """First-run flow: create the single user account. No-op if one exists."""
    if User.objects.exists():
        return redirect("login")

    if request.method == "POST":
        passphrase = request.POST.get("passphrase", "")
        confirm = request.POST.get("confirm", "")
        if passphrase != confirm:
            messages.error(request, "Passphrases do not match.")
        elif len(passphrase) < MIN_PASSPHRASE_LENGTH:
            messages.error(
                request,
                f"Passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters.",
            )
        else:
            user = User.objects.create_user(username="local", password=passphrase)
            user.is_staff = True
            user.save()
            audit.log_event("setup_complete", client=_client(request))
            authed = authenticate(request, username="local", password=passphrase)
            if authed is not None:
                login(request, authed)
                audit.log_event("login_success", username="local", client=_client(request))
            return redirect("recording-list")

    return render(
        request,
        "recordings/setup.html",
        {"min_length": MIN_PASSPHRASE_LENGTH},
    )


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if not User.objects.exists():
        return redirect("setup")
    if request.user.is_authenticated:
        return redirect("recording-list")

    next_url = request.GET.get("next") or request.POST.get("next") or reverse("recording-list")
    # Only honor relative ``next`` to prevent open-redirect.
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = reverse("recording-list")

    if request.method == "POST":
        passphrase = request.POST.get("passphrase", "")
        user = authenticate(request, username="local", password=passphrase)
        if user is None:
            audit.log_event("login_failure", client=_client(request))
            messages.error(request, "Incorrect passphrase.")
        else:
            login(request, user)
            audit.log_event("login_success", username=user.username, client=_client(request))
            return HttpResponseRedirect(next_url)

    return render(request, "recordings/login.html", {"next": next_url})


def logout_view(request: HttpRequest) -> HttpResponseRedirect:
    if request.user.is_authenticated:
        audit.log_event("logout", username=request.user.username, client=_client(request))
    logout(request)
    return redirect("login")
