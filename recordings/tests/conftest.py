"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client


@pytest.fixture
def local_user(db) -> User:  # type: ignore[no-untyped-def]
    return User.objects.create_user(username="local", password="passphrase-1234")


@pytest.fixture
def authed_client(local_user: User) -> Client:
    client = Client()
    assert client.login(username="local", password="passphrase-1234"), "login failed"
    return client


@pytest.fixture
def anon_client(db) -> Client:  # type: ignore[no-untyped-def]
    return Client()
