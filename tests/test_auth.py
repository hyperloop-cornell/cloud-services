"""Tests for NetID + team password login, roles, rate limiting and device tokens."""

from datetime import timedelta

import pytest

from conftest import HUB_ID, HUB_TOKEN, OPERATOR_NETID, TEAM_PASSWORD
from src.auth.auth_service import create_access_token, verify_device_token
from src.config import Settings, get_settings


def login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_login_success(client):
    response = login(client, OPERATOR_NETID, TEAM_PASSWORD)
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_accepts_form_data(client):
    response = client.post("/auth/login", data={"username": OPERATOR_NETID, "password": TEAM_PASSWORD})
    assert response.status_code == 200


def test_login_normalizes_netid(client):
    response = login(client, f"  {OPERATOR_NETID.upper()}@cornell.edu ", TEAM_PASSWORD)
    assert response.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"})
    assert me.json()["username"] == OPERATOR_NETID


def test_login_wrong_password(client):
    assert login(client, OPERATOR_NETID, "wrong-password").status_code == 401


def test_login_netid_not_allowed(client):
    assert login(client, "zzz999", TEAM_PASSWORD).status_code == 401


def test_login_reserved_netid_rejected(client):
    assert login(client, "viewer", TEAM_PASSWORD).status_code == 401


def test_login_missing_credentials(client):
    response = client.post("/auth/login", json={})
    assert response.status_code in [400, 422]


def test_get_current_user_operator(client, operator_headers):
    response = client.get("/auth/me", headers=operator_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == OPERATOR_NETID
    assert data["role"] == "operator"
    assert data["email"] == f"{OPERATOR_NETID}@cornell.edu"


def test_get_current_user_viewer(client, viewer_headers):
    response = client.get("/auth/me", headers=viewer_headers)
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


def test_get_current_user_without_token(client):
    response = client.get("/auth/me")
    assert response.status_code in [401, 403]


def test_get_current_user_invalid_token(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer invalid-token"})
    assert response.status_code == 401


def test_legacy_token_without_role_rejected(client):
    token = create_access_token({"sub": OPERATOR_NETID}, expires_delta=timedelta(minutes=5))
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_removing_netid_revokes_session(client, operator_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "allowed_netids", "xyz789")
    response = client.get("/auth/me", headers=operator_headers)
    assert response.status_code == 401


def test_netid_rate_limit(client):
    settings = get_settings()
    for _ in range(settings.login_max_failures_per_netid):
        assert login(client, OPERATOR_NETID, "wrong-password").status_code == 401

    blocked = login(client, OPERATOR_NETID, TEAM_PASSWORD)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0

    # Other NetIDs from the same client are not blocked by the per-NetID limit
    assert login(client, "xyz789", TEAM_PASSWORD).status_code == 200


def test_ip_rate_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_max_failures_per_ip", 3)
    for netid in ["aaa1", "bbb2", "ccc3"]:
        assert login(client, netid, "wrong-password").status_code == 401
    assert login(client, OPERATOR_NETID, TEAM_PASSWORD).status_code == 429


def test_successful_login_clears_netid_failures(client):
    settings = get_settings()
    for _ in range(settings.login_max_failures_per_netid - 1):
        login(client, OPERATOR_NETID, "wrong-password")
    assert login(client, OPERATOR_NETID, TEAM_PASSWORD).status_code == 200
    assert login(client, OPERATOR_NETID, "wrong-password").status_code == 401
    assert login(client, OPERATOR_NETID, TEAM_PASSWORD).status_code == 200


def test_verify_device_token():
    assert verify_device_token(HUB_TOKEN) == HUB_ID
    assert verify_device_token("not-a-token") is None
    assert verify_device_token("") is None


def test_legacy_device_token_env_still_honored():
    settings = Settings(_env_file=None, device_tokens="", device_token_rpi_bridge_01="legacy-token-value-1234567890")
    assert settings.get_valid_device_tokens() == {"legacy-token-value-1234567890": "rpi-bridge-01"}


def test_malformed_device_token_entries_ignored():
    settings = Settings(_env_file=None, device_tokens="hub-a:tok-a,broken,:nohub,hub-b:")
    assert settings.get_valid_device_tokens() == {"tok-a": "hub-a"}


def test_allowed_netids_file(tmp_path):
    netids = tmp_path / "netids.txt"
    netids.write_text("# team\nABC123\n  def456  # lead\n\nviewer\n", encoding="utf-8")
    settings = Settings(_env_file=None, allowed_netids="", allowed_netids_file=str(netids))
    assert settings.get_allowed_netids() == {"abc123", "def456"}


@pytest.mark.parametrize("quote", ["'", '"', ""])
def test_bcrypt_hash_survives_env_file(tmp_path, monkeypatch, quote):
    monkeypatch.delenv("TEAM_PASSWORD_HASH", raising=False)
    hash_value = "$2b$12$Bsq66/d3TpJAm88m7pUDjOKt9d.zDWL//Ndo.M75MB8U.HUnf28Ue"
    env_file = tmp_path / ".env"
    env_file.write_text(f"TEAM_PASSWORD_HASH={quote}{hash_value}{quote}\n", encoding="utf-8")
    assert Settings(_env_file=str(env_file)).team_password_hash == hash_value


def production_settings(**overrides):
    values = dict(
        _env_file=None,
        environment="production",
        jwt_secret_key="x" * 48,
        team_password_hash="$2b$12$Bsq66/d3TpJAm88m7pUDjOKt9d.zDWL//Ndo.M75MB8U.HUnf28Ue",
        allowed_netids="abc123",
        device_tokens="rpi-bridge-01:" + "t" * 32,
    )
    values.update(overrides)
    return Settings(**values)


def test_production_valid_config_passes():
    production_settings().validate_for_environment()


@pytest.mark.parametrize(
    "overrides",
    [
        {"jwt_secret_key": "your-secret-key-change-this-in-production"},
        {"jwt_secret_key": "short"},
        {"team_password_hash": ""},
        {"allowed_netids": ""},
        {"device_tokens": ""},
        {"device_tokens": "rpi-bridge-01:dev-token-rpi-bridge-01"},
    ],
)
def test_production_rejects_unsafe_config(overrides):
    with pytest.raises(RuntimeError):
        production_settings(**overrides).validate_for_environment()


def test_development_fallbacks():
    settings = Settings(_env_file=None, environment="development", allowed_netids="", device_tokens="")
    assert settings.get_allowed_netids() == {"dev"}
    assert "dev-token-rpi-bridge-01" in settings.get_valid_device_tokens()
