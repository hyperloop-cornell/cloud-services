"""Fan-out of messages to browser WebSocket clients, keyed by subscription topic."""

import asyncio
import json
import logging
from typing import Any, Dict, Hashable, Iterable, List, Set, Tuple

from fastapi import WebSocket
from pydantic import BaseModel

logger = logging.getLogger(__name__)

Topic = Tuple[Hashable, ...]

# A client that cannot take a message within this time is dropped instead of stalling others
SEND_TIMEOUT_SECONDS = 5.0


def bench_topic(hub_id: str, port_id: str) -> Topic:
    """Topic for live serial data from one port on one hub."""
    return ("bench", hub_id, port_id)


class ClientConnection:
    """A connected browser and the topics it subscribed to."""

    def __init__(self, websocket: WebSocket, username: str):
        self.websocket = websocket
        self.username = username
        self.topics: Set[Topic] = set()


class Broadcaster:
    """Tracks browser connections and delivers messages to subscribers."""

    def __init__(self) -> None:
        self.clients: Dict[str, ClientConnection] = {}
        self._connection_counter = 0

    def add_client(self, websocket: WebSocket, username: str) -> str:
        self._connection_counter += 1
        connection_id = f"client_{self._connection_counter}"
        self.clients[connection_id] = ClientConnection(websocket, username)
        return connection_id

    def remove_client(self, connection_id: str) -> None:
        self.clients.pop(connection_id, None)

    def subscribe(self, connection_id: str, topic: Topic) -> None:
        client = self.clients.get(connection_id)
        if client:
            client.topics.add(topic)

    def unsubscribe(self, connection_id: str, topic: Topic) -> None:
        client = self.clients.get(connection_id)
        if client:
            client.topics.discard(topic)

    def topics(self, connection_id: str) -> Set[Topic]:
        client = self.clients.get(connection_id)
        return set(client.topics) if client else set()

    async def publish(self, topic: Topic, message: Any) -> None:
        """Send to clients subscribed to `topic`."""
        targets = [cid for cid, client in self.clients.items() if topic in client.topics]
        await self._send(targets, message)

    async def publish_all(self, message: Any) -> None:
        """Send to every connected client."""
        await self._send(list(self.clients), message)

    async def send_to(self, connection_id: str, message: Any) -> None:
        await self._send([connection_id], message)

    async def _send(self, connection_ids: Iterable[str], message: Any) -> None:
        connection_ids = [cid for cid in connection_ids if cid in self.clients]
        if not connection_ids:
            return

        text = _serialize(message)

        async def deliver(connection_id: str) -> None:
            client = self.clients.get(connection_id)
            if client is None:
                return
            await asyncio.wait_for(client.websocket.send_text(text), timeout=SEND_TIMEOUT_SECONDS)

        results = await asyncio.gather(*(deliver(cid) for cid in connection_ids), return_exceptions=True)
        failed: List[str] = []
        for connection_id, result in zip(connection_ids, results):
            if isinstance(result, BaseException):
                logger.warning(f"Dropping client {connection_id}: {type(result).__name__}: {result}")
                failed.append(connection_id)
        for connection_id in failed:
            self.remove_client(connection_id)


def _serialize(message: Any) -> str:
    if isinstance(message, BaseModel):
        return message.model_dump_json(exclude_none=False)
    return json.dumps(message)


broadcaster = Broadcaster()


def get_broadcaster() -> Broadcaster:
    return broadcaster
