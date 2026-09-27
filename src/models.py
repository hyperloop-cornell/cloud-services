"""Pydantic models for API and WebSocket messages."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

# WebSocket message models live in src/protocol/bench_v1.py; re-exported for existing imports
from .protocol.bench_v1 import (  # noqa: F401
    BoardProfileInfo,
    Command,
    CommandEnvelope,
    DeviceEventMessage,
    HealthMessage,
    HubHandshake,
    HubProfile,
    TaskStatusMessage,
    TelemetryMessage,
)


# Auth Models
class LoginRequest(BaseModel):
    """Login request."""
    username: str
    password: str


class TokenResponse(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"


class UserInfo(BaseModel):
    """User information."""
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None


class SerialWriteParams(BaseModel):
    """Parameters for serial write command."""
    data: str
    encoding: str = "utf-8"


class FlashParams(BaseModel):
    """Parameters for flash command."""
    firmwareData: str  # Base64 encoded .ino source or .hex binary
    boardFqbn: Optional[str] = None


class RestartParams(BaseModel):
    """Parameters for restart command."""
    pass


# API Request Models
class SerialWriteRequest(BaseModel):
    """Request to send serial write command."""
    portId: str = Field(..., description="Target port ID")
    data: str = Field(..., description="Data to write")
    encoding: str = Field("utf-8", description="Data encoding")
    priority: int = Field(5, ge=1, le=10, description="Command priority")


class FlashFirmwareRequest(BaseModel):
    """Request to send flash firmware command."""
    portId: str = Field(..., description="Target port ID")
    firmwareData: str = Field(..., description="Base64 encoded firmware (.ino source, .hex, .bin or .elf)")
    boardFqbn: Optional[str] = Field(None, description="Board FQBN (required for .ino source)")
    artifactFormat: Optional[Literal["ino", "hex", "bin", "elf"]] = Field(
        None, description="Firmware format; inferred as ino/hex when omitted (older hubs only accept those)"
    )
    boardProfile: Optional[str] = Field(None, description="Board profile id from the hub's board registry")
    priority: int = Field(3, ge=1, le=10, description="Command priority")


class RestartDeviceRequest(BaseModel):
    """Request to send restart device command."""
    portId: str = Field(..., description="Target port ID")
    priority: int = Field(2, ge=1, le=10, description="Command priority")


class CloseConnectionRequest(BaseModel):
    """Request to close a connection."""
    portId: str = Field(..., description="Target port ID")
    priority: int = Field(1, ge=1, le=10, description="Command priority")


class CommandResponse(BaseModel):
    """Response after sending command."""
    commandId: str
    hubId: str
    status: str
    message: str


# API Response Models
class HubInfo(BaseModel):
    """Hub information."""
    hubId: str
    connected: bool
    connectedAt: Optional[datetime] = None
    lastSeen: Optional[datetime] = None
    version: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    profile: Optional[HubProfile] = None


class HubListResponse(BaseModel):
    """List of hubs."""
    hubs: List[HubInfo]
    count: int


class TelemetryEntry(BaseModel):
    """Telemetry data entry."""
    timestamp: datetime
    portId: str
    sessionId: str
    data: str  # Base64 encoded
    dataSizeBytes: int


class TelemetryListResponse(BaseModel):
    """List of telemetry entries."""
    hubId: str
    telemetry: List[TelemetryEntry]
    count: int
    totalBytes: int


class PortInfo(BaseModel):
    """Port information."""
    port_id: str
    port: str
    description: Optional[str] = None
    manufacturer: Optional[str] = None
    serial_number: Optional[str] = None
    vendor_id: Optional[str] = None
    product_id: Optional[str] = None
    board_profile: Optional[BoardProfileInfo] = None


class PortListResponse(BaseModel):
    """List of ports."""
    hubId: str
    ports: List[PortInfo]
    count: int


class ConnectionInfo(BaseModel):
    """Connection information."""
    port_id: str
    status: str
    baud_rate: int
    session_id: str
    bytes_read: int
    bytes_written: int
    connected_at: Optional[datetime] = None


class ConnectionListResponse(BaseModel):
    """List of connections."""
    hubId: str
    connections: List[ConnectionInfo]
    count: int


class TaskStatusResponse(BaseModel):
    """Task status response."""
    task_id: str
    status: str
    progress: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: datetime
    command_type: Optional[str] = None
    port_id: Optional[str] = None
    hub_id: Optional[str] = None
    priority: Optional[int] = None
    created_at: Optional[datetime] = None
