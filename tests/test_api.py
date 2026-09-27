"""API tests for cloud-services hub routes."""

import pytest

from conftest import HUB_ID
from src.storage.memory_store import get_store

COMMAND_BODIES = {
    "write": {"portId": "port-1", "data": "hello"},
    "flash": {"portId": "port-1", "firmwareData": "dm9pZCBzZXR1cCgpIHt9", "boardFqbn": "arduino:avr:uno"},
    "restart": {"portId": "port-1"},
    "close": {"portId": "port-1"},
}


class FakeHubSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


@pytest.fixture
def connected_hub():
    import asyncio

    store = get_store()
    socket = FakeHubSocket()
    asyncio.run(store.add_hub_connection(HUB_ID, socket, "1.0.0"))
    yield socket
    asyncio.run(store.remove_hub_connection(HUB_ID))


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


def test_list_hubs_requires_auth(client, operator_headers):
    assert client.get("/api/hubs").status_code in [401, 403]
    res = client.get("/api/hubs", headers=operator_headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_viewer_can_read_hubs(client, viewer_headers):
    assert client.get("/api/hubs", headers=viewer_headers).status_code == 200


@pytest.mark.parametrize("command", sorted(COMMAND_BODIES))
def test_commands_reject_viewers(client, viewer_headers, connected_hub, command):
    res = client.post(f"/api/hubs/{HUB_ID}/commands/{command}", json=COMMAND_BODIES[command], headers=viewer_headers)
    assert res.status_code == 403
    assert connected_hub.sent == []


@pytest.mark.parametrize("command", sorted(COMMAND_BODIES))
def test_commands_reject_anonymous(client, command):
    res = client.post(f"/api/hubs/{HUB_ID}/commands/{command}", json=COMMAND_BODIES[command])
    assert res.status_code == 401


@pytest.mark.parametrize("command", sorted(COMMAND_BODIES))
def test_commands_sent_for_operators(client, operator_headers, connected_hub, command):
    res = client.post(f"/api/hubs/{HUB_ID}/commands/{command}", json=COMMAND_BODIES[command], headers=operator_headers)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "pending"
    assert len(connected_hub.sent) == 1


def test_command_to_disconnected_hub_404(client, operator_headers):
    res = client.post("/api/hubs/rpi-lab-02/commands/write", json=COMMAND_BODIES["write"], headers=operator_headers)
    assert res.status_code == 404
