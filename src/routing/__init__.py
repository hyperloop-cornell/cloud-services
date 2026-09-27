"""Routing of hub -> cloud WebSocket messages by their `type` field.

Handlers register themselves with `@hub_message("type")`; adding a message type means adding a
handler module, not editing the endpoint.
"""

import logging
from typing import Awaitable, Callable, Dict

from pydantic import ValidationError

logger = logging.getLogger(__name__)

HubMessageHandler = Callable[[str, dict], Awaitable[None]]

_handlers: Dict[str, HubMessageHandler] = {}


def hub_message(message_type: str) -> Callable[[HubMessageHandler], HubMessageHandler]:
    """Register the decorated coroutine as the handler for `message_type`."""

    def decorator(handler: HubMessageHandler) -> HubMessageHandler:
        if message_type in _handlers:
            raise ValueError(f"Duplicate handler for hub message type {message_type!r}")
        _handlers[message_type] = handler
        return handler

    return decorator


def registered_message_types() -> list:
    return sorted(_handlers)


async def route_hub_message(hub_id: str, message: dict) -> None:
    """Dispatch one message from `hub_id`; errors are logged, never raised."""
    message_type = message.get("type")
    handler = _handlers.get(message_type)
    if handler is None:
        logger.warning(f"Unknown message type from {hub_id}: {message_type}")
        return

    try:
        await handler(hub_id, message)
    except ValidationError as e:
        logger.warning(f"Invalid {message_type} message from {hub_id}: {e}")
    except Exception as e:
        logger.error(f"Error handling {message_type} message from {hub_id}: {e}", exc_info=True)


# Register handlers
from . import bench  # noqa: E402,F401
