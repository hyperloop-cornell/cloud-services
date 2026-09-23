"""WebSocket endpoint for client (browser) connections."""

import asyncio
import json
import logging
from datetime import datetime

from fastapi import WebSocket, WebSocketDisconnect, Query, status
from pydantic import ValidationError

from ..auth.dependencies import get_current_user_ws
from ..protocol.bench_v1 import (
    ConnectedMessage,
    PingMessage,
    SubscribeMessage,
    SubscriptionState,
    SubscriptionStatusMessage,
    UnsubscribeMessage,
)
from .broadcaster import bench_topic, get_broadcaster

logger = logging.getLogger(__name__)

# Keepalive ping interval (30 seconds)
PING_INTERVAL = 30


def _now() -> str:
    return datetime.utcnow().isoformat()


async def send_periodic_pings(connection_id: str, username: str):
    """Send periodic ping messages to keep the connection alive."""
    broadcaster = get_broadcaster()
    try:
        while connection_id in broadcaster.clients:
            await asyncio.sleep(PING_INTERVAL)
            await broadcaster.send_to(connection_id, PingMessage(timestamp=_now()))
            logger.debug(f"Sent ping to client {username}")
    except asyncio.CancelledError:
        logger.debug(f"Ping task cancelled for {username}")
        raise


async def handle_client_connection(websocket: WebSocket, token: str = Query(...)):
    """
    Handle WebSocket connection from a browser client.

    Requires JWT token for authentication.
    Supports subscription-based telemetry streaming.
    """
    client_host = websocket.client.host if websocket.client else "unknown"
    client_port = websocket.client.port if websocket.client else "unknown"

    username = await get_current_user_ws(token)
    if not username:
        logger.warning(f"Invalid token from {client_host}:{client_port}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        return

    await websocket.accept()
    logger.info(f"Client WebSocket connected: {username} from {client_host}:{client_port}")

    broadcaster = get_broadcaster()
    connection_id = broadcaster.add_client(websocket, username)
    ping_task = asyncio.create_task(send_periodic_pings(connection_id, username))

    try:
        await broadcaster.send_to(
            connection_id,
            ConnectedMessage(message="Connected to telemetry stream", timestamp=_now()),
        )

        while True:
            message_data = await websocket.receive_text()
            try:
                message_dict = json.loads(message_data)
            except json.JSONDecodeError:
                logger.warning(f"Ignoring non-JSON message from client {username}")
                continue

            message_type = message_dict.get("type")
            if message_type == "pong":
                logger.debug(f"Received pong from client {username}")
            else:
                await route_client_message(connection_id, message_dict)

    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {username}")

    except Exception as e:
        logger.error(f"Error in client connection: {e}", exc_info=True)

    finally:
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass

        broadcaster.remove_client(connection_id)
        logger.info(f"Client connection cleaned up: {username}")


async def route_client_message(connection_id: str, message: dict):
    """Route incoming client message to appropriate handler."""
    message_type = message.get("type")

    try:
        if message_type == "subscribe":
            await handle_subscribe(connection_id, SubscribeMessage(**message))
        elif message_type == "unsubscribe":
            await handle_unsubscribe(connection_id, UnsubscribeMessage(**message))
        else:
            logger.warning(f"Unknown message type from client {connection_id}: {message_type}")

    except ValidationError as e:
        logger.warning(f"Invalid message from client {connection_id}: {e}")
    except Exception as e:
        logger.error(f"Error handling message from client {connection_id}: {e}", exc_info=True)


async def handle_subscribe(connection_id: str, message: SubscribeMessage):
    """Handle subscription request from client."""
    broadcaster = get_broadcaster()
    client = broadcaster.clients.get(connection_id)
    if not client:
        return

    for sub in message.subscriptions:
        broadcaster.subscribe(connection_id, bench_topic(sub.hubId, sub.portId))
        logger.info(f"Client {client.username} subscribed to {sub.hubId}:{sub.portId}")

    active = [
        SubscriptionState(hubId=topic[1], portId=topic[2], status="active")
        for topic in sorted(broadcaster.topics(connection_id), key=str)
        if topic and topic[0] == "bench"
    ]
    await broadcaster.send_to(connection_id, SubscriptionStatusMessage(subscriptions=active, timestamp=_now()))


async def handle_unsubscribe(connection_id: str, message: UnsubscribeMessage):
    """Handle unsubscription request from client."""
    broadcaster = get_broadcaster()
    client = broadcaster.clients.get(connection_id)
    if not client:
        return

    for sub in message.subscriptions:
        broadcaster.unsubscribe(connection_id, bench_topic(sub.hubId, sub.portId))
        logger.info(f"Client {client.username} unsubscribed from {sub.hubId}:{sub.portId}")

    inactive = [
        SubscriptionState(hubId=sub.hubId, portId=sub.portId, status="inactive")
        for sub in message.subscriptions
    ]
    await broadcaster.send_to(connection_id, SubscriptionStatusMessage(subscriptions=inactive, timestamp=_now()))
