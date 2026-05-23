from __future__ import annotations

import pytest
from django.test import Client


@pytest.mark.django_db
def test_csp_header_present_on_authenticated_page(authed_client: Client) -> None:
    response = authed_client.get("/")
    csp = response["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp


@pytest.mark.django_db
def test_csp_header_present_on_login_page(anon_client: Client) -> None:
    response = anon_client.get("/login/")
    assert "Content-Security-Policy" in response.headers


@pytest.mark.django_db
def test_xframe_options_deny(authed_client: Client) -> None:
    response = authed_client.get("/")
    assert response["X-Frame-Options"] == "DENY"


@pytest.mark.django_db
def test_permissions_policy_locked_down(authed_client: Client) -> None:
    response = authed_client.get("/")
    pp = response["Permissions-Policy"]
    assert "geolocation=()" in pp
    assert "microphone=()" in pp
    assert "camera=()" in pp
