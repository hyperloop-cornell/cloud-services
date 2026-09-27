from .client_endpoint import client_manager, get_client_manager
"""WebSocket endpoint for hub connections."""

import json
import logging
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..models import (
    HubHandshake,
    TelemetryMessage,
    HealthMessage,
    DeviceEventMessage,
    TaskStatusMessage,
)
from ..auth.auth_service import verify_device_token
from ..storage.memory_store import get_store

logger = logging.getLogger(__name__)


async def handle_hub_connection(websocket: WebSocket):
    """
    Handle WebSocket connection from RPi hub.
    
    Expects handshake message with device token authentication.
    Routes incoming messages to appropriate handlers.
    """
    client_host = websocket.client.host if websocket.client else "unknown"
    client_port = websocket.client.port if websocket.client else "unknown"
    
    logger.info(
        f"WebSocket connection attempt from {client_host}:{client_port}",
        extra={"client_host": client_host, "client_port": client_port}
    )
    
    await websocket.accept()
    
    logger.info(
        f"WebSocket connection accepted from {client_host}:{client_port}",
        extra={"client_host": client_host, "client_port": client_port}
    )
    
    hub_id: str = None

    try:
        # Wait for handshake
        logger.info("Waiting for handshake message...")
        handshake_data = await websocket.receive_text()
        handshake_dict = json.loads(handshake_data)
        logger.info(f"Received handshake from hub {handshake_dict.get('hubId')!r}")

        # Validate handshake
        try:
            handshake = HubHandshake(**handshake_dict)
        except ValidationError as e:
            logger.warning(f"Invalid handshake: {e}")
            await websocket.close(code=1008, reason="Invalid handshake")
            return

        # Verify device token
        authenticated_hub_id = verify_device_token(handshake.deviceToken)
        if not authenticated_hub_id:
            logger.warning(f"Invalid device token for hub: {handshake.hubId}")
            await websocket.close(code=1008, reason="Invalid device token")
            return

        # Verify hub ID matches token
        if authenticated_hub_id != handshake.hubId:
            logger.warning(
                f"Hub ID mismatch: token={authenticated_hub_id}, claimed={handshake.hubId}"
            )
            await websocket.close(code=1008, reason="Hub ID mismatch")
            return

        hub_id = handshake.hubId
        logger.info(f"Hub connected: {hub_id} (version {handshake.version})")

        # Register connection
        store = get_store()
        await store.add_hub_connection(hub_id, websocket, handshake.version)

        # Send acknowledgment (optional)
        ack = {
            "type": "hub_connected",
            "hubId": hub_id,
            "timestamp": datetime.utcnow().isoformat(),
        }
        await websocket.send_text(json.dumps(ack))

        # Message loop
        while True:
            message_data = await websocket.receive_text()
            message_dict = json.loads(message_data)

            # Update last seen
            await store.update_hub_last_seen(hub_id)

            # Route message
            await route_hub_message(hub_id, message_dict)

    except WebSocketDisconnect:
        logger.info(f"Hub disconnected: {hub_id}")

    except Exception as e:
        logger.error(f"Error in hub connection: {e}", exc_info=True)

    finally:
        # Cleanup
        if hub_id:
            store = get_store()
            await store.remove_hub_connection(hub_id)
            logger.info(f"Hub connection cleaned up: {hub_id}")


async def route_hub_message(hub_id: str, message: dict):
    """
    Route incoming hub message to appropriate handler.
    
    Args:
        hub_id: Hub identifier
        message: Message dictionary
    """
    message_type = message.get("type")

    try:
        if message_type == "telemetry":
            await handle_telemetry(hub_id, message)
        elif message_type == "health":
            await handle_health(hub_id, message)
        elif message_type == "device_event":
            await handle_device_event(hub_id, message)
        elif message_type == "task_status":
            await handle_task_status(hub_id, message)
        else:
            logger.warning(f"Unknown message type from {hub_id}: {message_type}")

    except ValidationError as e:
        logger.warning(f"Invalid message from {hub_id}: {e}")
    except Exception as e:
        logger.error(f"Error handling message from {hub_id}: {e}", exc_info=True)


async def handle_telemetry(hub_id: str, message: dict):
    """Handle telemetry message."""
    from .client_endpoint import get_client_manager
    
    telemetry = TelemetryMessage(**message)
    store = get_store()

    # Decode data size
    import base64
    data_bytes = base64.b64decode(telemetry.data)

    await store.add_telemetry(
        hub_id=hub_id,
        port_id=telemetry.portId,
        session_id=telemetry.sessionId,
        data=telemetry.data,
        data_size_bytes=len(data_bytes),
    )

    logger.debug(f"Telemetry from {hub_id}: {len(data_bytes)} bytes")

    # Ensure a connection record exists (mark as connected) and update bytes read
    try:
        existing_conn = await store.get_connection(hub_id, telemetry.portId)
        if existing_conn:
            # Update bytes_read and session_id if needed
            new_bytes_read = existing_conn.bytes_read + len(data_bytes)
            baud_rate = existing_conn.baud_rate or 0
            # Preserve connected_at
            await store.update_connection(
                hub_id=hub_id,
                port_id=telemetry.portId,
                status="connected",
                baud_rate=baud_rate,
                session_id=telemetry.sessionId or existing_conn.session_id,
                bytes_read=new_bytes_read,
                bytes_written=existing_conn.bytes_written,
                connected_at=existing_conn.connected_at,
            )
        else:
            # Create a minimal connection record based on telemetry
            # Note: Port will only appear in port list after proper device_event with full metadata
            await store.update_connection(
                hub_id=hub_id,
                port_id=telemetry.portId,
                status="connected",
                baud_rate=0,
                session_id=telemetry.sessionId,
                bytes_read=len(data_bytes),
                bytes_written=0,
            )
    except Exception as e:
        logger.error(f"Error updating connection from telemetry: {e}", exc_info=True)
    
    # Broadcast to subscribed clients
    client_message = {
        "type": "telemetry_stream",
        "hubId": hub_id,
        "portId": telemetry.portId,
        "sessionId": telemetry.sessionId,
        "timestamp": telemetry.timestamp,
        "data": telemetry.data,
        "dataSizeBytes": len(data_bytes),
    }
    
    client_manager = get_client_manager()
    await client_manager.broadcast_telemetry(hub_id, telemetry.portId, client_message)


async def handle_health(hub_id: str, message: dict):
    """Handle health message."""
    from .client_endpoint import get_client_manager
    
    health = HealthMessage(**message)
    store = get_store()

    await store.update_health(
        hub_id=hub_id,
        uptime_seconds=health.uptime_seconds,
        system=health.system,
        service=health.service,
        errors=health.errors,
    )

    logger.info(
        f"Health from {hub_id}: uptime={health.uptime_seconds}s, "
        f"cpu={health.system.get('cpu', {}).get('percent')}%"
    )
    
    # Broadcast to all clients (not subscription-filtered)
    client_message = {
        "type": "health",
        "hubId": hub_id,
        "timestamp": health.timestamp,
        "cpu_percent": health.system.get('cpu', {}).get('percent'),
        "memory_percent": health.system.get('memory', {}).get('percent'),
        "disk_percent": health.system.get('disk', {}).get('percent'),
    }
    
    client_manager = get_client_manager()
    await client_manager.broadcast_health(hub_id, client_message)


async def handle_device_event(hub_id: str, message: dict):
    """Handle device event message."""
    from .client_endpoint import get_client_manager
    
    event = DeviceEventMessage(**message)
    store = get_store()

    await store.add_device_event(
        hub_id=hub_id,
        event_type=event.eventType,
        port_id=event.portId,
        device_info=event.deviceInfo,
    )
    
    # Update connection status if it's a connection event
    if event.eventType == "connected" and event.deviceInfo:
        # Extract connection info from device_info
        baud_rate = event.deviceInfo.get("baud_rate", 115200)
        session_id = event.deviceInfo.get("session_id", "")
        
        await store.update_connection(
            hub_id=hub_id,
            port_id=event.portId,
            status="connected",
            baud_rate=baud_rate,
            session_id=session_id,
            bytes_read=0,
            bytes_written=0,
        )
    elif event.eventType == "disconnected":
        await store.remove_connection(hub_id, event.portId)

    logger.info(f"Device event from {hub_id}: {event.eventType} - {event.portId}")
    
    # Broadcast to subscribed clients
    client_message = {
        "type": "device_event",
        "hubId": hub_id,
        "portId": event.portId,
        "timestamp": event.timestamp,
        "event": event.eventType,
    }
    
    client_manager = get_client_manager()
    await client_manager.broadcast_device_event(hub_id, event.portId, client_message)


async def handle_task_status(hub_id: str, message: dict):
    """Handle task status message."""
    task_status = TaskStatusMessage(**message)
    store = get_store()

    await store.update_task_status(
        hub_id=hub_id,
        task_id=task_status.taskId,
        status=task_status.status,
        progress=task_status.progress,
        result=task_status.result,
        error=task_status.error,
    )

    logger.info(
        f"Task status from {hub_id}: {task_status.taskId} - {task_status.status}"
    )

    # Broadcast to all connected clients
    client_manager = get_client_manager()
    client_message = {
        "type": "task_status",
        "hubId": hub_id,
        "task_id": task_status.taskId,
        "status": task_status.status,
        "progress": task_status.progress,
        "result": task_status.result,
        "error": task_status.error,
        "timestamp": task_status.timestamp,
    }

    await client_manager.broadcast_task_status(hub_id, client_message)
