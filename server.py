"""
TMOM Deviation Engine — Main Server

Standalone FastAPI service that:
1. Subscribes to backend WebSocket streams (market-state, user-activity, engine-output)
2. Runs deviation supervision logic
3. Exposes REST API for frontend consumption
4. Exposes WebSocket for real-time deviation output

Deployment: independent service on Render/Railway/etc.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional, Dict, Any, Set
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from api.router import router as deviation_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ─── WebSocket Output Registry (for frontend) ────────────────────────

class DeviationOutputRegistry:
    def __init__(self):
        self._global_clients: Set[WebSocket] = set()
        self._session_clients: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, session_id: Optional[str] = None):
        await ws.accept()
        async with self._lock:
            if session_id:
                self._session_clients[session_id].add(ws)
            else:
                self._global_clients.add(ws)

    async def disconnect(self, ws: WebSocket, session_id: Optional[str] = None):
        async with self._lock:
            if session_id and session_id in self._session_clients:
                self._session_clients[session_id].discard(ws)
            else:
                self._global_clients.discard(ws)

    async def broadcast(self, payload: Dict[str, Any], session_id: Optional[str] = None):
        async with self._lock:
            targets = list(self._session_clients.get(session_id, [])) if session_id else list(self._global_clients)
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                async with self._lock:
                    if session_id:
                        self._session_clients[session_id].discard(ws)
                    else:
                        self._global_clients.discard(ws)


deviation_output_registry = DeviationOutputRegistry()


# ─── App Lifecycle ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background WebSocket listeners on startup."""
    from clients.stream_clients import (
        start_market_stream,
        start_activity_stream,
        start_engine_output_stream,
    )

    tasks = [
        asyncio.create_task(start_market_stream()),
        asyncio.create_task(start_activity_stream()),
        asyncio.create_task(start_engine_output_stream()),
    ]
    logger.info(f"[SERVER] Deviation Engine started on {settings.HOST}:{settings.PORT}")
    logger.info(f"[SERVER] Backend: {settings.BACKEND_BASE_URL}")
    logger.info(f"[SERVER] Backend WS: {settings.backend_ws_url}")
    yield
    for t in tasks:
        t.cancel()
    logger.info("[SERVER] Deviation Engine shutting down.")


# ─── FastAPI App ──────────────────────────────────────────────────────

app = FastAPI(
    title="TMOM Deviation Engine",
    description="Standalone deviation supervision and costing service for the TMOM trading platform.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(deviation_router)


@app.get("/")
def root():
    return {
        "service": "tmom-deviation-engine",
        "status": "ok",
        "environment": settings.ENVIRONMENT,
    }


@app.get("/health")
def health():
    from deviation.engine import DeviationEngineRegistry
    engines = DeviationEngineRegistry.get_all()
    return {
        "status": "ok",
        "active_engines": len(engines),
        "environment": settings.ENVIRONMENT,
    }


@app.websocket("/ws/deviation-output")
async def deviation_output_ws(websocket: WebSocket):
    """
    WebSocket endpoint for frontend to receive real-time deviation events.
    The frontend connects here instead of modifying Vallab's endpoints.
    """
    session_id = websocket.query_params.get("session_id")
    await deviation_output_registry.connect(websocket, session_id=session_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await deviation_output_registry.disconnect(websocket, session_id=session_id)


# ─── Entry Point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.ENVIRONMENT == "development",
    )
