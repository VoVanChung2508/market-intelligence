import pytest


@pytest.fixture(autouse=True)
def auth_signing_secret(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "test-signing-key-that-is-at-least-32-bytes-long")
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
