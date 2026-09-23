"""Pytest configuration and fixtures for cloud-services tests."""

import os
import sys
from pathlib import Path

import bcrypt
import pytest
from fastapi.testclient import TestClient

TEAM_PASSWORD = "test-team-password"
OPERATOR_NETID = "abc123"
HUB_ID = "rpi-bridge-01"
HUB_TOKEN = "test-token-hub-01-aaaaaaaaaaaaaaaa"

# Configure the app before it is imported; ignore any developer .env file
os.environ["ENV_FILE"] = str(Path(__file__).parent / "does-not-exist.env")
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing-only-0123456789"
os.environ["ALLOWED_NETIDS"] = f"{OPERATOR_NETID},xyz789"
os.environ["TEAM_PASSWORD_HASH"] = bcrypt.hashpw(TEAM_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
os.environ["DEVICE_TOKENS"] = f"{HUB_ID}:{HUB_TOKEN},rpi-lab-02:test-token-hub-02-bbbbbbbbbbbbbbbb"

# Ensure package imports work by adding project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from importlib import import_module  # noqa: E402

main = import_module('src.main')
app = main.app


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    from src.auth.rate_limit import login_rate_limiter

    login_rate_limiter.clear()
    yield
    login_rate_limiter.clear()


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def operator_headers(client):
    res = client.post("/auth/login", json={"username": OPERATOR_NETID, "password": TEAM_PASSWORD})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def viewer_headers(client):
    res = client.post("/auth/login-viewer")
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}
