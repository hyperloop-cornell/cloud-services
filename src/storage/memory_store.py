"""In-memory storage for testing."""

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class HubConnection:
    """Hub connection data."""
    hub_id: str
    connected_at: datetime
    last_seen: datetime
    version: str
    websocket: Any  # WebSocket connection
    capabilities: List[str] = field(default_factory=list)
    profile: Optional[Dict[str, Any]] = None


@dataclass
class HubRecord:
    """What is known about a hub, kept after it disconnects."""
    hub_id: str
    connected: bool
    connected_at: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    version: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    profile: Optional[Dict[str, Any]] = None


@dataclass
class TelemetryData:
    """Telemetry data entry."""
    timestamp: datetime
    port_id: str
    session_id: str
    data: str  # Base64 encoded
    data_size_bytes: int


@dataclass
class HealthData:
    """Health metrics data."""
    timestamp: datetime
    uptime_seconds: int
    system: Dict[str, Any]
    service: Dict[str, Any]
    errors: Dict[str, Any]


@dataclass
class DeviceEvent:
    """Device event data."""
    timestamp: datetime
    event_type: str
    port_id: str
    device_info: Optional[Dict[str, Any]] = None


@dataclass
class PortData:
    """Port data."""
    port_id: str
    port: str
    description: Optional[str] = None
    manufacturer: Optional[str] = None
    serial_number: Optional[str] = None
    vendor_id: Optional[str] = None
    product_id: Optional[str] = None
    board_profile: Optional[Dict[str, Any]] = None


@dataclass
class ConnectionData:
    """Connection data."""
    port_id: str
    status: str
    baud_rate: int
    session_id: str
    bytes_read: int
    bytes_written: int
    connected_at: Optional[datetime] = None


@dataclass
class TaskStatus:
    """Task status data."""
    timestamp: datetime
    task_id: str
    status: str
    progress: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class MemoryStore:
    """Thread-safe in-memory storage."""

    def __init__(self, max_telemetry_per_hub: int = 1000):
        """Initialize memory store.
        
        Args:
            max_telemetry_per_hub: Maximum telemetry entries per hub
        """
        self._lock = asyncio.Lock()
        self.max_telemetry_per_hub = max_telemetry_per_hub

        # Live hub connections
        self._hubs: Dict[str, HubConnection] = {}

        # Every hub seen since startup (survives disconnects)
        self._hub_records: Dict[str, HubRecord] = {}

        # Telemetry data (hub_id -> list of entries)
        self._telemetry: Dict[str, List[TelemetryData]] = defaultdict(list)

        # Health data (hub_id -> latest entry)
        self._health: Dict[str, HealthData] = {}

        # Device events (hub_id -> list of events)
        self._device_events: Dict[str, List[DeviceEvent]] = defaultdict(list)

        # Ports data (hub_id -> port_id -> PortData)
        self._ports: Dict[str, Dict[str, PortData]] = defaultdict(dict)

        # Connections data (hub_id -> port_id -> ConnectionData)
        self._connections: Dict[str, Dict[str, ConnectionData]] = defaultdict(dict)

        # Task status (hub_id -> task_id -> status)
        self._task_status: Dict[str, Dict[str, TaskStatus]] = defaultdict(dict)

    # Hub Connection Management
    async def add_hub_connection(
        self,
        hub_id: str,
        websocket: Any,
        version: str = "1.0.0",
        capabilities: Optional[List[str]] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> Optional[HubConnection]:
        """Register a hub connection. Returns the connection it replaced, if any."""
        async with self._lock:
            now = datetime.utcnow()
            previous = self._hubs.get(hub_id)
            self._hubs[hub_id] = HubConnection(
                hub_id=hub_id,
                connected_at=now,
                last_seen=now,
                version=version,
                websocket=websocket,
                capabilities=list(capabilities or []),
                profile=profile,
            )
            self._hub_records[hub_id] = HubRecord(
                hub_id=hub_id,
                connected=True,
                connected_at=now,
                last_seen=now,
                version=version,
                capabilities=list(capabilities or []),
                profile=profile,
            )
            return previous

    async def remove_hub_connection(self, hub_id: str, websocket: Any = None) -> bool:
        """Remove a hub connection.

        When `websocket` is given, only remove it if it is still the hub's current
        connection, so a stale handler cannot unregister a newer reconnect.
        """
        async with self._lock:
            current = self._hubs.get(hub_id)
            if current is None or (websocket is not None and current.websocket is not websocket):
                return False
            del self._hubs[hub_id]
            record = self._hub_records.get(hub_id)
            if record:
                record.connected = False
                record.last_seen = datetime.utcnow()
            # Serial sessions do not survive the hub going away
            self._connections.pop(hub_id, None)
            return True

    async def update_hub_last_seen(self, hub_id: str) -> None:
        """Update hub last seen timestamp."""
        async with self._lock:
            now = datetime.utcnow()
            if hub_id in self._hubs:
                self._hubs[hub_id].last_seen = now
            if hub_id in self._hub_records:
                self._hub_records[hub_id].last_seen = now

    async def get_hub_connection(self, hub_id: str) -> Optional[HubConnection]:
        """Get hub connection."""
        async with self._lock:
            return self._hubs.get(hub_id)

    async def get_all_hubs(self) -> List[HubConnection]:
        """Get all hub connections."""
        async with self._lock:
            return list(self._hubs.values())

    async def get_hub_records(self) -> Dict[str, HubRecord]:
        """Every hub seen since startup, connected or not."""
        async with self._lock:
            return {hub_id: HubRecord(**vars(record)) for hub_id, record in self._hub_records.items()}

    async def is_hub_connected(self, hub_id: str) -> bool:
        """Check if hub is connected."""
        async with self._lock:
            return hub_id in self._hubs

    async def clear_hub_devices(self, hub_id: str) -> None:
        """Forget cached ports and connections for a hub (it will re-announce them)."""
        async with self._lock:
            self._ports.pop(hub_id, None)
            self._connections.pop(hub_id, None)

    # Telemetry Management
    async def add_telemetry(
        self,
        hub_id: str,
        port_id: str,
        session_id: str,
        data: str,
        data_size_bytes: int,
    ) -> None:
        """Add telemetry entry."""
        async with self._lock:
            entry = TelemetryData(
                timestamp=datetime.utcnow(),
                port_id=port_id,
                session_id=session_id,
                data=data,
                data_size_bytes=data_size_bytes,
            )
            self._telemetry[hub_id].append(entry)

            # Limit size
            if len(self._telemetry[hub_id]) > self.max_telemetry_per_hub:
                self._telemetry[hub_id] = self._telemetry[hub_id][
                    -self.max_telemetry_per_hub :
                ]

    async def get_telemetry(
        self, hub_id: str, limit: Optional[int] = None
    ) -> List[TelemetryData]:
        """Get telemetry entries for hub."""
        async with self._lock:
            entries = self._telemetry.get(hub_id, [])
            if limit:
                return entries[-limit:]
            return entries.copy()

    async def get_telemetry_stats(self, hub_id: str) -> Dict[str, Any]:
        """Get telemetry statistics."""
        async with self._lock:
            entries = self._telemetry.get(hub_id, [])
            total_bytes = sum(e.data_size_bytes for e in entries)
            return {
                "count": len(entries),
                "total_bytes": total_bytes,
            }

    # Health Management
    async def update_health(
        self,
        hub_id: str,
        uptime_seconds: int,
        system: Dict[str, Any],
        service: Dict[str, Any],
        errors: Dict[str, Any],
    ) -> None:
        """Update health metrics."""
        async with self._lock:
            self._health[hub_id] = HealthData(
                timestamp=datetime.utcnow(),
                uptime_seconds=uptime_seconds,
                system=system,
                service=service,
                errors=errors,
            )

    async def get_health(self, hub_id: str) -> Optional[HealthData]:
        """Get latest health metrics."""
        async with self._lock:
            return self._health.get(hub_id)

    # Device Events
    async def add_device_event(
        self,
        hub_id: str,
        event_type: str,
        port_id: str,
        device_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add device event."""
        async with self._lock:
            event = DeviceEvent(
                timestamp=datetime.utcnow(),
                event_type=event_type,
                port_id=port_id,
                device_info=device_info,
            )
            self._device_events[hub_id].append(event)
            
            # Update ports cache when device connects
            if event_type == "connected" and device_info:
                port_data = PortData(
                    port_id=port_id,
                    port=device_info.get("port", ""),
                    description=device_info.get("description") or device_info.get("product"),
                    manufacturer=device_info.get("manufacturer"),
                    serial_number=device_info.get("serial_number"),
                    vendor_id=device_info.get("vendor_id"),
                    product_id=device_info.get("product_id"),
                    board_profile=device_info.get("board_profile"),
                )
                self._ports[hub_id][port_id] = port_data
            
            # Remove from ports cache when device disconnects
            elif event_type == "disconnected":
                self._ports[hub_id].pop(port_id, None)

            # Keep the event log bounded
            if len(self._device_events[hub_id]) > self.max_telemetry_per_hub:
                self._device_events[hub_id] = self._device_events[hub_id][-self.max_telemetry_per_hub :]

    async def get_device_events(
        self, hub_id: str, limit: Optional[int] = None
    ) -> List[DeviceEvent]:
        """Get device events."""
        async with self._lock:
            events = self._device_events.get(hub_id, [])
            if limit:
                return events[-limit:]
            return events.copy()

    # Ports Management
    async def get_ports(self, hub_id: str) -> List[PortData]:
        """Get all ports for hub."""
        async with self._lock:
            return list(self._ports.get(hub_id, {}).values())

    async def get_port(self, hub_id: str, port_id: str) -> Optional[PortData]:
        """Get specific port."""
        async with self._lock:
            return self._ports.get(hub_id, {}).get(port_id)

    # Connections Management
    async def update_connection(
        self,
        hub_id: str,
        port_id: str,
        status: str,
        baud_rate: int,
        session_id: str,
        bytes_read: int = 0,
        bytes_written: int = 0,
        connected_at: Optional[datetime] = None,
    ) -> None:
        """Update connection data."""
        async with self._lock:
            self._connections[hub_id][port_id] = ConnectionData(
                port_id=port_id,
                status=status,
                baud_rate=baud_rate,
                session_id=session_id,
                bytes_read=bytes_read,
                bytes_written=bytes_written,
                connected_at=connected_at or datetime.utcnow(),
            )

    async def remove_connection(self, hub_id: str, port_id: str) -> None:
        """Remove connection data."""
        async with self._lock:
            self._connections.get(hub_id, {}).pop(port_id, None)

    async def get_connections(self, hub_id: str) -> List[ConnectionData]:
        """Get all connections for hub."""
        async with self._lock:
            return list(self._connections.get(hub_id, {}).values())

    async def get_connection(self, hub_id: str, port_id: str) -> Optional[ConnectionData]:
        """Get specific connection."""
        async with self._lock:
            return self._connections.get(hub_id, {}).get(port_id)

    # Task Status
    async def update_task_status(
        self,
        hub_id: str,
        task_id: str,
        status: str,
        progress: Optional[int] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update task status."""
        async with self._lock:
            self._task_status[hub_id][task_id] = TaskStatus(
                timestamp=datetime.utcnow(),
                task_id=task_id,
                status=status,
                progress=progress,
                result=result,
                error=error,
            )

    async def get_task_status(
        self, hub_id: str, task_id: str
    ) -> Optional[TaskStatus]:
        """Get task status."""
        async with self._lock:
            return self._task_status.get(hub_id, {}).get(task_id)

    async def get_all_task_statuses(self, hub_id: str) -> Dict[str, TaskStatus]:
        """Get all task statuses for hub."""
        async with self._lock:
            return self._task_status.get(hub_id, {}).copy()


# Global instance
_store: Optional[MemoryStore] = None


def get_store() -> MemoryStore:
    """Get memory store singleton."""
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store
