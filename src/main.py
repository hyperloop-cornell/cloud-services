"""Main FastAPI application."""

import logging
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .api import auth, hubs
from .websocket.hub_endpoint import handle_hub_connection
from .websocket.client_endpoint import handle_client_connection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="RPi Hub Cloud Service",
    description="Cloud service for testing rpi-hub-service WebSocket communication",
    version="1.0.0",
)

# Refuse to start production with missing or development credentials
settings = get_settings()
settings.validate_for_environment()

# Configure CORS
logger.info(f"CORS origins configured: {settings.cors_origins}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(hubs.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "RPi Hub Cloud Service",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.websocket("/hub")
@app.websocket("/api/device/ws/uplink")
async def hub_websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for RPi hub connections.

    Hubs connect here with device token authentication. `/api/device/ws/uplink` is the
    same endpoint under the name used by the system design documents.
    """
    await handle_hub_connection(websocket)


@app.websocket("/ws/client")
async def client_websocket_endpoint(websocket: WebSocket, token: str):
    """
    WebSocket endpoint for client (browser) connections.
    
    Clients connect here with JWT token authentication.
    Supports subscription-based telemetry streaming.
    """
    await handle_client_connection(websocket, token)


if __name__ == "__main__":
    import uvicorn
    
    logger.info(f"Starting Cloud Service on {settings.host}:{settings.port}")
    logger.info(f"WebSocket endpoint: ws://{settings.host}:{settings.port}/hub")
    logger.info(f"Known hubs: {sorted(set(settings.get_valid_device_tokens().values()))}")
    logger.info(f"Environment: {settings.environment}")

    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
