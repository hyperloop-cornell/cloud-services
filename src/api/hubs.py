"""Hub management API endpoints."""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import get_current_user, require_operator
from ..config import get_settings
from ..models import (
    CloseConnectionRequest,
    ConnectionInfo,
    ConnectionListResponse,
    FlashFirmwareRequest,
    HubInfo,
    PortInfo,
    PortListResponse,
    RestartDeviceRequest,
    SerialWriteRequest,
    TaskStatusResponse,
    TelemetryEntry,
    TelemetryListResponse,
)
from ..protocol.bench_v1 import HubProfile
from ..services.command_service import send_command_to_hub
from ..storage.memory_store import HubRecord, get_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hubs", tags=["hubs"])


def _known_hub_ids() -> set:
    return set(get_settings().get_valid_device_tokens().values())


def _hub_info(hub_id: str, record: Optional[HubRecord]) -> HubInfo:
    if record is None:
        return HubInfo(hubId=hub_id, connected=False)
    return HubInfo(
        hubId=hub_id,
        connected=record.connected,
        connectedAt=record.connected_at if record.connected else None,
        lastSeen=record.last_seen,
        version=record.version,
        capabilities=record.capabilities,
        profile=HubProfile(**record.profile) if record.profile else None,
    )


async def _require_known_hub(hub_id: str) -> Optional[HubRecord]:
    """Return the hub's record (None if it never connected); 404 for unknown hubs."""
    records = await get_store().get_hub_records()
    if hub_id not in records and hub_id not in _known_hub_ids():
        raise HTTPException(status_code=404, detail=f"Hub not found: {hub_id}")
    return records.get(hub_id)


@router.get("", response_model=List[HubInfo])
async def list_hubs(current_user: dict = Depends(get_current_user)):
    """
    List every configured hub (from DEVICE_TOKENS) plus any hub seen since startup,
    with its connection state.
    """
    records = await get_store().get_hub_records()
    hub_ids = sorted(_known_hub_ids() | set(records))
    return [_hub_info(hub_id, records.get(hub_id)) for hub_id in hub_ids]


@router.get("/{hub_id}", response_model=HubInfo)
async def get_hub(hub_id: str, current_user: dict = Depends(get_current_user)):
    """
    Get hub details.
    """
    return _hub_info(hub_id, await _require_known_hub(hub_id))


@router.get("/{hub_id}/telemetry", response_model=TelemetryListResponse)
async def get_telemetry(
    hub_id: str,
    limit: Optional[int] = Query(None, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
):
    """
    Get recent telemetry for a hub (kept after the hub disconnects).
    """
    await _require_known_hub(hub_id)
    store = get_store()
    telemetry_data = await store.get_telemetry(hub_id, limit=limit)
    stats = await store.get_telemetry_stats(hub_id)

    entries = [
        TelemetryEntry(
            timestamp=entry.timestamp,
            portId=entry.port_id,
            sessionId=entry.session_id,
            data=entry.data,
            dataSizeBytes=entry.data_size_bytes,
        )
        for entry in telemetry_data
    ]

    return TelemetryListResponse(
        hubId=hub_id,
        telemetry=entries,
        count=stats["count"],
        totalBytes=stats["total_bytes"],
    )


@router.get("/{hub_id}/ports", response_model=PortListResponse)
async def get_ports(
    hub_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get ports for a hub. Offline hubs report no ports.
    """
    record = await _require_known_hub(hub_id)
    ports: List[PortInfo] = []
    if record and record.connected:
        ports = [
            PortInfo(
                port_id=port.port_id,
                port=port.port,
                description=port.description,
                manufacturer=port.manufacturer,
                serial_number=port.serial_number,
                vendor_id=port.vendor_id,
                product_id=port.product_id,
                board_profile=port.board_profile,
            )
            for port in await get_store().get_ports(hub_id)
        ]

    return PortListResponse(hubId=hub_id, ports=ports, count=len(ports))


@router.get("/{hub_id}/connections", response_model=ConnectionListResponse)
async def get_connections(
    hub_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get open serial connections for a hub. Offline hubs report none.
    """
    record = await _require_known_hub(hub_id)
    connections: List[ConnectionInfo] = []
    if record and record.connected:
        connections = [
            ConnectionInfo(
                port_id=conn.port_id,
                status=conn.status,
                baud_rate=conn.baud_rate,
                session_id=conn.session_id,
                bytes_read=conn.bytes_read,
                bytes_written=conn.bytes_written,
                connected_at=conn.connected_at,
            )
            for conn in await get_store().get_connections(hub_id)
        ]

    return ConnectionListResponse(hubId=hub_id, connections=connections, count=len(connections))


async def _dispatch_command(
    hub_id: str,
    command_type: str,
    port_id: str,
    params: Dict[str, Any],
    priority: int,
    user: dict,
) -> TaskStatusResponse:
    """Send a command to a connected hub and return its initial (pending) task status."""
    store = get_store()
    if not await store.is_hub_connected(hub_id):
        raise HTTPException(status_code=404, detail=f"Hub not connected: {hub_id}")

    command_id = f"cmd-{uuid.uuid4()}"
    command = {
        "commandId": command_id,
        "commandType": command_type,
        "portId": port_id,
        "params": params,
        "priority": priority,
    }

    if not await send_command_to_hub(hub_id, command):
        raise HTTPException(status_code=500, detail="Failed to send command to hub")

    logger.info(f"Command {command_id} {command_type} -> {hub_id}:{port_id} by {user.get('username')}")

    now = datetime.utcnow()
    return TaskStatusResponse(
        task_id=command_id,
        status="pending",
        timestamp=now,
        command_type=command_type,
        port_id=port_id,
        hub_id=hub_id,
        priority=priority,
        created_at=now,
    )


@router.post("/{hub_id}/commands/write", response_model=TaskStatusResponse)
async def send_serial_write_command(
    hub_id: str,
    request: SerialWriteRequest,
    current_user: dict = Depends(require_operator),
):
    """
    Send serial write command to hub.
    """
    return await _dispatch_command(
        hub_id,
        "serial_write",
        request.portId,
        {"data": request.data, "encoding": request.encoding},
        request.priority,
        current_user,
    )


@router.post("/{hub_id}/commands/flash", response_model=TaskStatusResponse)
async def send_flash_firmware_command(
    hub_id: str,
    request: FlashFirmwareRequest,
    current_user: dict = Depends(require_operator),
):
    """
    Send flash firmware command to hub.
    """
    params: Dict[str, Any] = {"firmwareData": request.firmwareData, "boardFqbn": request.boardFqbn}
    if request.artifactFormat:
        params["artifactFormat"] = request.artifactFormat
    if request.boardProfile:
        params["boardProfile"] = request.boardProfile

    return await _dispatch_command(hub_id, "flash", request.portId, params, request.priority, current_user)


@router.post("/{hub_id}/commands/restart", response_model=TaskStatusResponse)
async def send_restart_device_command(
    hub_id: str,
    request: RestartDeviceRequest,
    current_user: dict = Depends(require_operator),
):
    """
    Send restart device command to hub.
    """
    return await _dispatch_command(hub_id, "restart", request.portId, {}, request.priority, current_user)


@router.post("/{hub_id}/commands/close", response_model=TaskStatusResponse)
async def send_close_connection_command(
    hub_id: str,
    request: CloseConnectionRequest,
    current_user: dict = Depends(require_operator),
):
    """
    Send close connection command to hub.
    """
    return await _dispatch_command(hub_id, "close_connection", request.portId, {}, request.priority, current_user)
