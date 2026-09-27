"""Handlers for bench (USB serial) messages from hubs."""

import base64
import binascii
import logging

from ..protocol.bench_v1 import (
    DeviceEventBroadcast,
    DeviceEventMessage,
    HealthBroadcast,
    HealthMessage,
    TaskStatusBroadcast,
    TaskStatusMessage,
    TelemetryMessage,
    TelemetryStreamMessage,
)
from ..storage.memory_store import get_store
from ..websocket.broadcaster import bench_topic, get_broadcaster
from . import hub_message

logger = logging.getLogger(__name__)


@hub_message("telemetry")
async def handle_telemetry(hub_id: str, message: dict) -> None:
    telemetry = TelemetryMessage(**message)
    store = get_store()

    try:
        data_bytes = base64.b64decode(telemetry.data, validate=True)
    except (binascii.Error, ValueError):
        logger.warning(f"Dropping telemetry from {hub_id}:{telemetry.portId} with invalid base64 data")
        return

    await store.add_telemetry(
        hub_id=hub_id,
        port_id=telemetry.portId,
        session_id=telemetry.sessionId,
        data=telemetry.data,
        data_size_bytes=len(data_bytes),
    )

    # Keep a connection record current (older hubs only announce ports once at startup)
    existing_conn = await store.get_connection(hub_id, telemetry.portId)
    if existing_conn:
        await store.update_connection(
            hub_id=hub_id,
            port_id=telemetry.portId,
            status="connected",
            baud_rate=existing_conn.baud_rate or 0,
            session_id=telemetry.sessionId or existing_conn.session_id,
            bytes_read=existing_conn.bytes_read + len(data_bytes),
            bytes_written=existing_conn.bytes_written,
            connected_at=existing_conn.connected_at,
        )
    else:
        await store.update_connection(
            hub_id=hub_id,
            port_id=telemetry.portId,
            status="connected",
            baud_rate=0,
            session_id=telemetry.sessionId,
            bytes_read=len(data_bytes),
            bytes_written=0,
        )

    await get_broadcaster().publish(
        bench_topic(hub_id, telemetry.portId),
        TelemetryStreamMessage(
            hubId=hub_id,
            portId=telemetry.portId,
            sessionId=telemetry.sessionId,
            timestamp=telemetry.timestamp,
            data=telemetry.data,
            dataSizeBytes=len(data_bytes),
        ),
    )


@hub_message("health")
async def handle_health(hub_id: str, message: dict) -> None:
    health = HealthMessage(**message)
    await get_store().update_health(
        hub_id=hub_id,
        uptime_seconds=health.uptime_seconds,
        system=health.system,
        service=health.service,
        errors=health.errors,
    )

    def percent(section: str):
        value = health.system.get(section)
        return value.get("percent") if isinstance(value, dict) else None

    await get_broadcaster().publish_all(
        HealthBroadcast(
            hubId=hub_id,
            timestamp=health.timestamp,
            cpu_percent=percent("cpu"),
            memory_percent=percent("memory"),
            disk_percent=percent("disk"),
            mode=(health.profile or {}).get("mode"),
            uplink=health.uplink,
        )
    )


@hub_message("device_event")
async def handle_device_event(hub_id: str, message: dict) -> None:
    event = DeviceEventMessage(**message)
    store = get_store()

    await store.add_device_event(
        hub_id=hub_id,
        event_type=event.eventType,
        port_id=event.portId,
        device_info=event.deviceInfo,
    )

    if event.eventType == "connected" and event.deviceInfo:
        await store.update_connection(
            hub_id=hub_id,
            port_id=event.portId,
            status="connected",
            baud_rate=event.deviceInfo.get("baud_rate") or 115200,
            session_id=event.deviceInfo.get("session_id") or "",
            bytes_read=0,
            bytes_written=0,
        )
    elif event.eventType == "disconnected":
        await store.remove_connection(hub_id, event.portId)

    logger.info(f"Device event from {hub_id}: {event.eventType} - {event.portId}")

    await get_broadcaster().publish(
        bench_topic(hub_id, event.portId),
        DeviceEventBroadcast(hubId=hub_id, portId=event.portId, timestamp=event.timestamp, event=event.eventType),
    )


@hub_message("task_status")
async def handle_task_status(hub_id: str, message: dict) -> None:
    task_status = TaskStatusMessage(**message)
    await get_store().update_task_status(
        hub_id=hub_id,
        task_id=task_status.taskId,
        status=task_status.status,
        progress=task_status.progress,
        result=task_status.result,
        error=task_status.error,
    )

    logger.info(f"Task status from {hub_id}: {task_status.taskId} - {task_status.status}")

    await get_broadcaster().publish_all(
        TaskStatusBroadcast(
            hubId=hub_id,
            task_id=task_status.taskId,
            status=task_status.status,
            progress=task_status.progress,
            result=task_status.result,
            error=task_status.error,
            timestamp=task_status.timestamp,
            commandType=task_status.commandType,
            portId=task_status.portId,
        )
    )
