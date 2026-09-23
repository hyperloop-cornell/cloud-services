"""Hub WebSocket protocol: handshake, registry, routing and broadcast to browsers."""

import base64
import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import HUB_ID, HUB_TOKEN

OTHER_HUB = "rpi-lab-02"


def handshake(**extra):
    message = {
        "type": "hub_connect",
        "hubId": HUB_ID,
        "deviceToken": HUB_TOKEN,
        "timestamp": "2026-09-23T12:00:00Z",
        "version": "1.0.0",
    }
    message.update(extra)
    return json.dumps(message)


def connect_hub(client, path="/hub", **extra):
    ws = client.websocket_connect(path)
    session = ws.__enter__()
    session.send_text(handshake(**extra))
    ack = json.loads(session.receive_text())
    assert ack["type"] == "hub_connected"
    return ws, session


def device_connected(port_id="port-1", **info):
    device_info = {"port": "/dev/ttyACM0", "vendor_id": "2341", "product_id": "0069", "baud_rate": 115200}
    device_info.update(info)
    return json.dumps({
        "type": "device_event",
        "hubId": HUB_ID,
        "timestamp": "2026-09-23T12:00:01Z",
        "eventType": "connected",
        "portId": port_id,
        "deviceInfo": device_info,
    })


def hubs_by_id(client, headers):
    return {hub["hubId"]: hub for hub in client.get("/api/hubs", headers=headers).json()}


def eventually(check, timeout=3.0):
    """Poll until check() returns a truthy value (hub messages are handled on another thread)."""
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result or time.monotonic() > deadline:
            return result
        time.sleep(0.02)


def port_ids(client, headers):
    return [p["port_id"] for p in client.get(f"/api/hubs/{HUB_ID}/ports", headers=headers).json()["ports"]]


def test_known_hubs_listed_when_offline(client, operator_headers):
    hubs = hubs_by_id(client, operator_headers)
    assert set(hubs) == {HUB_ID, OTHER_HUB}
    assert not hubs[HUB_ID]["connected"]
    assert client.get(f"/api/hubs/{OTHER_HUB}", headers=operator_headers).json()["connected"] is False


def test_unknown_hub_404(client, operator_headers):
    assert client.get("/api/hubs/nope", headers=operator_headers).status_code == 404
    assert client.get("/api/hubs/nope/ports", headers=operator_headers).status_code == 404


def test_offline_hub_reports_no_ports(client, operator_headers):
    res = client.get(f"/api/hubs/{OTHER_HUB}/ports", headers=operator_headers)
    assert res.status_code == 200
    assert res.json()["ports"] == []


def test_legacy_handshake_gets_legacy_capabilities(client, operator_headers):
    ws, session = connect_hub(client)
    try:
        hub = hubs_by_id(client, operator_headers)[HUB_ID]
        assert hub["connected"] is True
        assert hub["capabilities"] == ["bench", "flash:ino", "flash:hex"]
        assert hub["profile"] is None
    finally:
        ws.__exit__(None, None, None)

    assert hubs_by_id(client, operator_headers)[HUB_ID]["connected"] is False


def test_new_handshake_reports_capabilities_and_profile(client, operator_headers):
    caps = ["bench", "flash:ino", "flash:hex", "flash:bin", "device_snapshot"]
    ws, _ = connect_hub(client, capabilities=caps, profile={"name": "lab-hub", "mode": "bench", "uplink": "wifi"})
    try:
        hub = hubs_by_id(client, operator_headers)[HUB_ID]
        assert hub["capabilities"] == caps
        assert hub["profile"] == {"name": "lab-hub", "mode": "bench", "uplink": "wifi"}
    finally:
        ws.__exit__(None, None, None)


def test_uplink_alias_path(client, operator_headers):
    ws, _ = connect_hub(client, path="/api/device/ws/uplink")
    try:
        assert hubs_by_id(client, operator_headers)[HUB_ID]["connected"] is True
    finally:
        ws.__exit__(None, None, None)


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"deviceToken": "wrong"}, "Invalid device token"),
        ({"hubId": OTHER_HUB}, "Hub ID mismatch"),
    ],
)
def test_rejected_handshakes(client, overrides, reason):
    with client.websocket_connect("/hub") as session:
        session.send_text(handshake(**overrides))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            session.receive_text()
        assert excinfo.value.code == 1008
        assert excinfo.value.reason == reason


def test_device_event_populates_ports_with_board_profile(client, operator_headers):
    board = {"id": "uno_r4_minima", "name": "Arduino Uno R4 Minima", "fqbn": "arduino:renesas_uno:minima", "artifacts": ["ino", "bin"]}
    ws, session = connect_hub(client)
    try:
        session.send_text(device_connected(board_profile=board, product="UNO R4 Minima"))
        assert eventually(lambda: port_ids(client, operator_headers))
        ports = client.get(f"/api/hubs/{HUB_ID}/ports", headers=operator_headers).json()["ports"]
        assert len(ports) == 1
        assert ports[0]["board_profile"] == board
        assert ports[0]["description"] == "UNO R4 Minima"
        connections = client.get(f"/api/hubs/{HUB_ID}/connections", headers=operator_headers).json()
        assert connections["count"] == 1
    finally:
        ws.__exit__(None, None, None)


def test_device_snapshot_capability_clears_stale_ports(client, operator_headers):
    ws, session = connect_hub(client)
    session.send_text(device_connected("stale-port"))
    assert eventually(lambda: port_ids(client, operator_headers) == ["stale-port"])
    ws.__exit__(None, None, None)

    ws, session = connect_hub(client, capabilities=["bench", "device_snapshot"])
    try:
        session.send_text(device_connected("fresh-port"))
        assert eventually(lambda: port_ids(client, operator_headers) == ["fresh-port"])
    finally:
        ws.__exit__(None, None, None)


def test_legacy_hub_keeps_ports_across_reconnect(client, operator_headers):
    ws, session = connect_hub(client)
    session.send_text(device_connected("port-1"))
    assert eventually(lambda: port_ids(client, operator_headers) == ["port-1"])
    ws.__exit__(None, None, None)

    ws, _ = connect_hub(client)
    try:
        assert port_ids(client, operator_headers) == ["port-1"]
    finally:
        ws.__exit__(None, None, None)


def test_stale_connection_cleanup_keeps_new_connection(client, operator_headers):
    first_ws, first = connect_hub(client)
    second_ws, second = connect_hub(client)

    # The cloud closes the replaced connection; its cleanup must not unregister the new one
    with pytest.raises(WebSocketDisconnect):
        first.receive_text()
    first_ws.__exit__(None, None, None)

    try:
        assert hubs_by_id(client, operator_headers)[HUB_ID]["connected"] is True
        res = client.post(
            f"/api/hubs/{HUB_ID}/commands/restart", json={"portId": "port-1"}, headers=operator_headers
        )
        assert res.status_code == 200
        command = json.loads(second.receive_text())
        assert command["type"] == "command"
        assert command["command"]["commandType"] == "restart"
    finally:
        second_ws.__exit__(None, None, None)


def test_command_response_shape(client, operator_headers):
    ws, session = connect_hub(client)
    try:
        res = client.post(
            f"/api/hubs/{HUB_ID}/commands/flash",
            json={"portId": "port-1", "firmwareData": "AAEC", "artifactFormat": "bin", "boardProfile": "uno_r4_minima"},
            headers=operator_headers,
        )
        body = res.json()
        assert res.status_code == 200
        assert body["command_type"] == "flash"
        assert body["port_id"] == "port-1"
        assert body["hub_id"] == HUB_ID
        assert body["created_at"]

        command = json.loads(session.receive_text())["command"]
        assert command["commandId"] == body["task_id"]
        assert command["params"]["artifactFormat"] == "bin"
        assert command["params"]["boardProfile"] == "uno_r4_minima"
    finally:
        ws.__exit__(None, None, None)


def test_close_command_uses_hub_command_type(client, operator_headers):
    ws, session = connect_hub(client)
    try:
        res = client.post(f"/api/hubs/{HUB_ID}/commands/close", json={"portId": "port-1"}, headers=operator_headers)
        assert res.status_code == 200
        assert json.loads(session.receive_text())["command"]["commandType"] == "close_connection"
    finally:
        ws.__exit__(None, None, None)


class FakeBrowser:
    """Stands in for a browser socket. TestClient runs each WebSocket on its own event loop,
    so delivery across connections is tested through the broadcaster directly."""

    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))

    def of_type(self, message_type):
        return [m for m in self.messages if m["type"] == message_type]


@pytest.fixture
def fake_browser():
    from src.websocket.broadcaster import bench_topic, broadcaster

    browser = FakeBrowser()
    connection_id = broadcaster.add_client(browser, "viewer")
    broadcaster.subscribe(connection_id, bench_topic(HUB_ID, "port-1"))
    return browser


def test_browser_rejects_bad_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/client?token=bad") as session:
            session.receive_text()


def test_browser_subscribe_and_unsubscribe(client, viewer_headers):
    token = viewer_headers["Authorization"].split(" ", 1)[1]
    with client.websocket_connect(f"/ws/client?token={token}") as session:
        assert json.loads(session.receive_text())["type"] == "connected"

        session.send_text(json.dumps({"type": "subscribe", "subscriptions": [{"hubId": HUB_ID, "portId": "port-1"}]}))
        status = json.loads(session.receive_text())
        assert status["type"] == "subscription_status"
        assert status["subscriptions"] == [{"hubId": HUB_ID, "portId": "port-1", "status": "active"}]

        session.send_text(json.dumps({"type": "unsubscribe", "subscriptions": [{"hubId": HUB_ID, "portId": "port-1"}]}))
        status = json.loads(session.receive_text())
        assert status["subscriptions"] == [{"hubId": HUB_ID, "portId": "port-1", "status": "inactive"}]

        # Malformed messages are ignored without closing the socket
        session.send_text("not json")
        session.send_text(json.dumps({"type": "subscribe", "subscriptions": "nope"}))
        session.send_text(json.dumps({"type": "subscribe", "subscriptions": []}))
        assert json.loads(session.receive_text())["subscriptions"] == []


def test_hub_status_broadcast(client, fake_browser):
    ws, _ = connect_hub(client)
    assert eventually(lambda: fake_browser.of_type("hub_status"))
    assert fake_browser.of_type("hub_status")[0]["connected"] is True
    ws.__exit__(None, None, None)
    assert eventually(lambda: len(fake_browser.of_type("hub_status")) == 2)
    assert fake_browser.of_type("hub_status")[1]["connected"] is False


def test_telemetry_and_task_status_reach_browser(client, fake_browser):
    hub_ws, hub = connect_hub(client)
    try:
        payload = base64.b64encode(b"TEMP: 21.5\n").decode()
        hub.send_text(json.dumps({
            "type": "telemetry", "hubId": HUB_ID, "timestamp": "2026-09-23T12:00:02Z",
            "portId": "port-1", "sessionId": "s1", "data": payload,
        }))
        # Not subscribed to port-2: must not be delivered
        hub.send_text(json.dumps({
            "type": "telemetry", "hubId": HUB_ID, "timestamp": "2026-09-23T12:00:02Z",
            "portId": "port-2", "sessionId": "s2", "data": payload,
        }))
        hub.send_text(json.dumps({
            "type": "task_status", "hubId": HUB_ID, "timestamp": "2026-09-23T12:00:03Z",
            "taskId": "cmd-1", "status": "completed", "commandType": "restart", "portId": "port-1",
        }))
        assert eventually(lambda: fake_browser.of_type("task_status"))

        telemetry = fake_browser.of_type("telemetry_stream")
        assert len(telemetry) == 1
        assert telemetry[0]["portId"] == "port-1"
        assert telemetry[0]["data"] == payload
        assert telemetry[0]["dataSizeBytes"] == len(b"TEMP: 21.5\n")

        task = fake_browser.of_type("task_status")[0]
        assert task["hubId"] == HUB_ID
        assert task["task_id"] == "cmd-1"
        assert task["commandType"] == "restart"
        assert task["portId"] == "port-1"
    finally:
        hub_ws.__exit__(None, None, None)


def test_health_broadcast_includes_uplink(client, fake_browser):
    hub_ws, hub = connect_hub(client)
    try:
        hub.send_text(json.dumps({
            "type": "health", "hubId": HUB_ID, "timestamp": "2026-09-23T12:00:04Z", "uptime_seconds": 5,
            "system": {"cpu": {"percent": 12.5}, "memory": {"percent": 40.0}, "disk": {"percent": 50.0}},
            "service": {}, "errors": {},
            "profile": {"name": "cellular-hub", "mode": "bench"},
            "uplink": {"active": "cellular"},
        }))
        assert eventually(lambda: fake_browser.of_type("health"))
        health = fake_browser.of_type("health")[0]
        assert health["cpu_percent"] == 12.5
        assert health["mode"] == "bench"
        assert health["uplink"] == {"active": "cellular"}
    finally:
        hub_ws.__exit__(None, None, None)


def test_slow_browser_is_dropped():
    import asyncio

    from src.websocket import broadcaster as broadcaster_module
    from src.websocket.broadcaster import broadcaster

    class StuckBrowser:
        async def send_text(self, text):
            await asyncio.sleep(10)

    healthy = FakeBrowser()
    stuck_id = broadcaster.add_client(StuckBrowser(), "stuck")
    healthy_id = broadcaster.add_client(healthy, "healthy")

    original = broadcaster_module.SEND_TIMEOUT_SECONDS
    broadcaster_module.SEND_TIMEOUT_SECONDS = 0.05
    try:
        asyncio.run(broadcaster.publish_all({"type": "ping", "timestamp": "t"}))
    finally:
        broadcaster_module.SEND_TIMEOUT_SECONDS = original

    assert stuck_id not in broadcaster.clients
    assert healthy_id in broadcaster.clients
    assert healthy.messages == [{"type": "ping", "timestamp": "t"}]


def test_invalid_telemetry_does_not_drop_connection(client, operator_headers):
    ws, hub = connect_hub(client)
    try:
        hub.send_text("not json")
        hub.send_text(json.dumps({"type": "telemetry", "hubId": HUB_ID}))
        hub.send_text(json.dumps({"type": "mystery"}))
        hub.send_text(device_connected("port-9"))
        assert eventually(lambda: port_ids(client, operator_headers) == ["port-9"])
    finally:
        ws.__exit__(None, None, None)
