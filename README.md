# Cloud Service

FastAPI backend for the Hyperloop telemetry GUI. It exposes the REST API and WebSocket endpoints that
browsers and Raspberry Pi hubs connect to.

- Hubs connect over `WS /hub` and authenticate with a per-hub device token.
- Browsers log in with a NetID and the shared team password, then use JWT bearer tokens for REST and
  `WS /ws/client?token=...` for live telemetry.
- State is kept in memory (`src/storage/memory_store.py`) and resets on restart.

## Quick Start

```bash
cd cloud-services
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

API docs: `http://localhost:8080/docs`

With the defaults in `.env.example` (development), log in as NetID `dev` with password `hyperloop-dev`,
and hubs can connect with `dev-token-rpi-bridge-01` / `dev-token-rpi-bridge-02`. None of these
fallbacks work when `ENVIRONMENT=production`; the service refuses to start until real values are set.

## Authentication

- `POST /auth/login` - NetID + team password (JSON or form). Returns a JWT with role `operator`.
  The NetID must be on the allowlist (`ALLOWED_NETIDS` and/or `ALLOWED_NETIDS_FILE`).
  Failed attempts are rate limited per NetID and per client IP (HTTP 429 with `Retry-After`).
- `POST /auth/login-viewer` - No credentials. Returns a JWT with role `viewer` (read-only).
- `GET /auth/me` - Current user (`username`, `email`, `role`).

Only `operator` tokens can send hub commands; viewers get HTTP 403. Removing a NetID from the allowlist
revokes that person's existing sessions.

Production setup, password rotation and hub token generation: `.claude/auth-setup.md` in the
hyperloop-gui repository.

## API Endpoints

### Hubs
- `GET /api/hubs` - List every configured hub (from `DEVICE_TOKENS`) with `connected`, `lastSeen`,
  `capabilities` and `profile` (offline hubs are included)
- `GET /api/hubs/{hubId}` - Hub details
- `GET /api/hubs/{hubId}/telemetry` - Recent telemetry
- `GET /api/hubs/{hubId}/ports` - Detected serial ports
- `GET /api/hubs/{hubId}/connections` - Open serial connections
- `POST /api/hubs/{hubId}/commands/write` - Serial write (operator only)
- `POST /api/hubs/{hubId}/commands/flash` - Flash firmware (operator only)
- `POST /api/hubs/{hubId}/commands/restart` - Restart device (operator only)
- `POST /api/hubs/{hubId}/commands/close` - Close connection (operator only)

### WebSocket
- `WS /hub` (alias `WS /api/device/ws/uplink`) - Hub connection (device token handshake)
- `WS /ws/client?token=<jwt>` - Browser telemetry stream (subscribe/unsubscribe by hub + port).
  Also carries `health`, `task_status` and `hub_status` (hub online/offline) to every client.

Hubs may add `capabilities` (for example `flash:bin`, `device_snapshot`) and a `profile`
(`{name, mode, uplink}`) to their handshake. Hubs that send neither are treated as bench hubs that
accept `.ino`/`.hex` firmware, so older rpi-hub-server versions keep working.

## Contracts

Message and REST models are defined in `src/protocol/bench_v1.py` and `src/models.py`.
`contracts/openapi.json` is generated from them and consumed by the web client's type generation:

```bash
python -m src.protocol.export
```

`tests/test_contracts.py` fails when the committed file is out of date.

## Testing with rpi-hub-server

1. Start this service (see Quick Start).
2. In `rpi-hub-server/.env`:
   ```env
   HUB_ID=rpi-bridge-01
   SERVER_ENDPOINT=ws://localhost:8080/hub
   DEVICE_TOKEN=dev-token-rpi-bridge-01
   ```
3. Start the hub: `uvicorn src.main:app --host 127.0.0.1 --port 8000`

## Tests

```bash
pytest tests/ -v
```
