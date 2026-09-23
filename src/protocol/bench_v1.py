"""Bench (USB serial) WebSocket protocol, version 1.

Hub -> cloud messages are validated with these models; cloud -> browser messages are built
from them so the exported schema (contracts/) matches what is actually sent. New fields must
be optional so hubs and browsers running older code keep working.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# Capabilities assumed for hubs whose handshake predates the `capabilities` field
LEGACY_HUB_CAPABILITIES = ["bench", "flash:ino", "flash:hex"]

# Capability: the hub re-sends a device_event for every attached device after each connect,
# so the cloud may drop its cached ports for that hub at handshake time.
CAP_DEVICE_SNAPSHOT = "device_snapshot"


class HubProfile(BaseModel):
    """What kind of hub this is, as reported in the handshake."""

    name: Optional[str] = None  # config profile, e.g. "lab-hub" or "cellular-hub"
    mode: str = "bench"  # "bench" (USB MCUs); "pod" is reserved for the EtherCAT master
    uplink: Optional[str] = None  # "wifi" | "cellular" when known


class BoardProfileInfo(BaseModel):
    """Board resolved by the hub from the USB VID/PID registry (config/boards.yaml)."""

    id: str
    name: str
    fqbn: Optional[str] = None
    artifacts: List[str] = Field(default_factory=list)


class OutboundModel(BaseModel):
    """A message the cloud sends. Every field is always serialized, so the exported schema
    marks defaulted fields (such as `type`) as required."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


# ---------------------------------------------------------------------------
# Hub -> cloud
# ---------------------------------------------------------------------------


class HubHandshake(BaseModel):
    """Hub connection handshake message."""

    type: str = "hub_connect"
    hubId: str
    deviceToken: str
    timestamp: str
    version: str = "1.0.0"
    capabilities: List[str] = Field(default_factory=lambda: list(LEGACY_HUB_CAPABILITIES))
    profile: Optional[HubProfile] = None


class TelemetryMessage(BaseModel):
    """Telemetry data from hub."""

    type: str = "telemetry"
    hubId: str
    timestamp: str
    portId: str
    sessionId: str
    data: str  # Base64 encoded


class HealthMessage(BaseModel):
    """Health metrics from hub."""

    type: str = "health"
    hubId: str
    timestamp: str
    uptime_seconds: int
    system: Dict[str, Any]
    service: Dict[str, Any]
    errors: Dict[str, Any]
    profile: Optional[Dict[str, Any]] = None
    uplink: Optional[Dict[str, Any]] = None


class DeviceEventMessage(BaseModel):
    """Device event from hub."""

    type: str = "device_event"
    hubId: str
    timestamp: str
    eventType: str  # connected, disconnected
    portId: str
    deviceInfo: Optional[Dict[str, Any]] = None


class TaskStatusMessage(BaseModel):
    """Task status update from hub."""

    type: str = "task_status"
    hubId: str
    timestamp: str
    taskId: str
    status: str  # pending, running, completed, failed, cancelled
    progress: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    commandType: Optional[str] = None
    portId: Optional[str] = None


# ---------------------------------------------------------------------------
# Cloud -> hub
# ---------------------------------------------------------------------------


class Command(OutboundModel):
    """Command details."""

    commandId: str
    commandType: str  # serial_write, flash, restart, close_connection
    portId: str
    params: Dict[str, Any]
    priority: int = 5


class CommandEnvelope(OutboundModel):
    """Command envelope sent to hub."""

    type: Literal["command"] = "command"
    command: Command


class HubConnectedAck(OutboundModel):
    type: Literal["hub_connected"] = "hub_connected"
    hubId: str
    timestamp: str


# ---------------------------------------------------------------------------
# Browser -> cloud
# ---------------------------------------------------------------------------


class DeviceSubscription(BaseModel):
    hubId: str
    portId: str


class SubscribeMessage(BaseModel):
    type: Literal["subscribe"] = "subscribe"
    subscriptions: List[DeviceSubscription]


class UnsubscribeMessage(BaseModel):
    type: Literal["unsubscribe"] = "unsubscribe"
    subscriptions: List[DeviceSubscription]


class PongMessage(BaseModel):
    type: Literal["pong"] = "pong"
    timestamp: Optional[str] = None


# ---------------------------------------------------------------------------
# Cloud -> browser
# ---------------------------------------------------------------------------


class ConnectedMessage(OutboundModel):
    type: Literal["connected"] = "connected"
    message: str
    timestamp: str


class PingMessage(OutboundModel):
    type: Literal["ping"] = "ping"
    timestamp: str


class TelemetryStreamMessage(OutboundModel):
    type: Literal["telemetry_stream"] = "telemetry_stream"
    hubId: str
    portId: str
    sessionId: str
    timestamp: str
    data: str  # Base64 encoded
    dataSizeBytes: int


class HealthBroadcast(OutboundModel):
    type: Literal["health"] = "health"
    hubId: str
    timestamp: str
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    mode: Optional[str] = None
    uplink: Optional[Dict[str, Any]] = None


class DeviceEventBroadcast(OutboundModel):
    type: Literal["device_event"] = "device_event"
    hubId: str
    portId: str
    timestamp: str
    event: str  # connected, disconnected


class TaskStatusBroadcast(OutboundModel):
    type: Literal["task_status"] = "task_status"
    hubId: str
    task_id: str
    status: str
    progress: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: str
    commandType: Optional[str] = None
    portId: Optional[str] = None


class SubscriptionState(OutboundModel):
    hubId: str
    portId: str
    status: Literal["active", "inactive"]


class SubscriptionStatusMessage(OutboundModel):
    type: Literal["subscription_status"] = "subscription_status"
    subscriptions: List[SubscriptionState]
    timestamp: str


class HubStatusBroadcast(OutboundModel):
    """Sent to every browser when a hub connects or disconnects."""

    type: Literal["hub_status"] = "hub_status"
    hubId: str
    connected: bool
    timestamp: str


ClientBoundMessage = Union[
    ConnectedMessage,
    PingMessage,
    TelemetryStreamMessage,
    HealthBroadcast,
    DeviceEventBroadcast,
    TaskStatusBroadcast,
    SubscriptionStatusMessage,
    HubStatusBroadcast,
]

HUB_TO_CLOUD_MODELS = [HubHandshake, TelemetryMessage, HealthMessage, DeviceEventMessage, TaskStatusMessage]
CLOUD_TO_HUB_MODELS = [HubConnectedAck, CommandEnvelope]
CLIENT_TO_CLOUD_MODELS = [SubscribeMessage, UnsubscribeMessage, PongMessage]
CLOUD_TO_CLIENT_MODELS = [
    ConnectedMessage,
    PingMessage,
    TelemetryStreamMessage,
    HealthBroadcast,
    DeviceEventBroadcast,
    TaskStatusBroadcast,
    SubscriptionStatusMessage,
    HubStatusBroadcast,
]
