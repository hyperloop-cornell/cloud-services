"""WebSocket endpoint for hub connections."""

import json
import logging
from datetime import datetime

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..auth.auth_service import verify_device_token
from ..protocol.bench_v1 import CAP_DEVICE_SNAPSHOT, HubConnectedAck, HubHandshake, HubStatusBroadcast
from ..routing import route_hub_message
from ..storage.memory_store import get_store
from .broadcaster import get_broadcaster

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.utcnow().isoformat()


async def handle_hub_connection(websocket: WebSocket):
    """
    Handle WebSocket connection from RPi hub.

    Expects handshake message with device token authentication.
    Routes incoming messages to appropriate handlers.
    """
    client_host = websocket.client.host if websocket.client else "unknown"
    client_port = websocket.client.port if websocket.client else "unknown"

    await websocket.accept()
    logger.info(f"Hub WebSocket connection accepted from {client_host}:{client_port}")

    hub_id: str = None
    registered = False
    store = get_store()

    try:
        handshake_data = await websocket.receive_text()
        try:
            handshake_dict = json.loads(handshake_data)
            handshake = HubHandshake(**handshake_dict)
        except (json.JSONDecodeError, TypeError, ValidationError) as e:
            logger.warning(f"Invalid handshake from {client_host}: {e}")
            await websocket.close(code=1008, reason="Invalid handshake")
            return

        authenticated_hub_id = verify_device_token(handshake.deviceToken)
        if not authenticated_hub_id:
            logger.warning(f"Invalid device token for claimed hub {handshake.hubId!r} from {client_host}")
            await websocket.close(code=1008, reason="Invalid device token")
            return

        if authenticated_hub_id != handshake.hubId:
            logger.warning(f"Hub ID mismatch: token={authenticated_hub_id}, claimed={handshake.hubId}")
            await websocket.close(code=1008, reason="Hub ID mismatch")
            return

        hub_id = handshake.hubId
        profile = handshake.profile.model_dump() if handshake.profile else None
        logger.info(
            f"Hub connected: {hub_id} (version {handshake.version}, "
            f"capabilities {handshake.capabilities}, profile {profile})"
        )

        if CAP_DEVICE_SNAPSHOT in handshake.capabilities:
            # The hub re-announces every attached device right after connecting
            await store.clear_hub_devices(hub_id)

        previous = await store.add_hub_connection(
            hub_id,
            websocket,
            handshake.version,
            capabilities=handshake.capabilities,
            profile=profile,
        )
        registered = True
        if previous is not None and previous.websocket is not websocket:
            logger.warning(f"Hub {hub_id} reconnected; closing its previous connection")
            try:
                await previous.websocket.close(code=1012, reason="Replaced by new connection")
            except Exception:
                pass

        await websocket.send_text(HubConnectedAck(hubId=hub_id, timestamp=_now()).model_dump_json())
        await get_broadcaster().publish_all(HubStatusBroadcast(hubId=hub_id, connected=True, timestamp=_now()))

        while True:
            message_data = await websocket.receive_text()
            try:
                message_dict = json.loads(message_data)
            except json.JSONDecodeError:
                logger.warning(f"Ignoring non-JSON message from {hub_id}")
                continue
            if not isinstance(message_dict, dict):
                logger.warning(f"Ignoring non-object message from {hub_id}")
                continue

            await store.update_hub_last_seen(hub_id)
            await route_hub_message(hub_id, message_dict)

    except WebSocketDisconnect:
        logger.info(f"Hub disconnected: {hub_id}")

    except Exception as e:
        logger.error(f"Error in hub connection: {e}", exc_info=True)

    finally:
        if registered and await store.remove_hub_connection(hub_id, websocket):
            logger.info(f"Hub connection cleaned up: {hub_id}")
            await get_broadcaster().publish_all(HubStatusBroadcast(hubId=hub_id, connected=False, timestamp=_now()))
