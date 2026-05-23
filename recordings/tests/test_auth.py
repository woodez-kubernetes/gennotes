from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client


@pytest.mark.django_db
def test_unauthenticated_redirects_to_setup_when_no_user_exists(anon_client: Client) -> None:
    response = anon_client.get("/")
    assert response.status_code == 302
    assert response["Location"].startswith("/setup/")


@pytest.mark.django_db
def test_unauthenticated_redirects_to_login_when_user_exists(anon_client: Client, local_user: User) -> None:
    response = anon_client.get("/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login/")


@pytest.mark.django_db
def test_setup_creates_user_and_authenticates(anon_client: Client) -> None:
    response = anon_client.post(
        "/setup/",
        {"passphrase": "supersecure-passphrase", "confirm": "supersecure-passphrase"},
        follow=False,
    )
    assert response.status_code == 302
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.username == "local"
    # After setup the user is logged in.
    home = anon_client.get("/")
    assert home.status_code == 200


@pytest.mark.django_db
def test_setup_rejects_short_passphrase(anon_client: Client) -> None:
    response = anon_client.post(
        "/setup/",
        {"passphrase": "short", "confirm": "short"},
        follow=True,
    )
    assert User.objects.count() == 0
    assert "at least" in response.content.decode()


@pytest.mark.django_db
def test_setup_rejects_mismatched_confirm(anon_client: Client) -> None:
    response = anon_client.post(
        "/setup/",
        {"passphrase": "supersecure-passphrase", "confirm": "different-passphrase"},
        follow=True,
    )
    assert User.objects.count() == 0
    assert "do not match" in response.content.decode()


@pytest.mark.django_db
def test_login_redirects_with_safe_next(anon_client: Client, local_user: User) -> None:
    response = anon_client.post(
        "/login/?next=/audit/",
        {"passphrase": "passphrase-1234"},
    )
    assert response.status_code == 302
    assert response["Location"] == "/audit/"


@pytest.mark.django_db
def test_login_strips_open_redirect_target(anon_client: Client, local_user: User) -> None:
    response = anon_client.post(
        "/login/",
        {"passphrase": "passphrase-1234", "next": "//evil.example/"},
    )
    assert response.status_code == 302
    assert not response["Location"].startswith("//")


@pytest.mark.django_db
def test_login_rejects_wrong_passphrase(anon_client: Client, local_user: User) -> None:
    response = anon_client.post(
        "/login/",
        {"passphrase": "wrong-passphrase"},
        follow=True,
    )
    assert "Incorrect passphrase" in response.content.decode()


@pytest.mark.django_db
def test_logout_clears_session(authed_client: Client) -> None:
    response = authed_client.post("/logout/")
    assert response.status_code == 302
    assert response["Location"] == "/login/"
    # Subsequent request should redirect to login again.
    response2 = authed_client.get("/")
    assert response2.status_code == 302
    assert response2["Location"].startswith("/login/")
