"""
Maritime Crew Orchestrator — FastAPI Application Entry Point
"""
import json
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes.crew import router as crew_router
from api.routes.workflow import router as workflow_router
from api.routes.monitoring import router as monitoring_router
from api.routes.decisions import router as decisions_router
from api.websockets.workflow_ws import manager
from config import settings
from database.db import init_db
from services.cache_service import cache_service

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", app=settings.app_name, version=settings.app_version)
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001 - log and continue so the app still boots
        log.error("db_init_failed", error=str(exc))
    yield
    await cache_service.close()
    log.info("shutdown", app=settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Autonomous Maritime Crew Sign-On / Sign-Off Orchestrator using Claude Managed Agents",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(crew_router, prefix="/api/v1")
app.include_router(workflow_router, prefix="/api/v1")
app.include_router(monitoring_router, prefix="/api/v1")
app.include_router(decisions_router, prefix="/api/v1")


# ── WebSocket ──────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_global(websocket: WebSocket):
    """Global WebSocket — receives all agent/workflow events."""
    await manager.connect(websocket, "global")
    try:
        while True:
            data = await websocket.receive_text()
            # Echo-back for ping/pong
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except Exception:
                pass
    except WebSocketDisconnect:
        await manager.disconnect(websocket, "global")


@app.websocket("/ws/{workflow_id}")
async def websocket_workflow(websocket: WebSocket, workflow_id: str):
    """Workflow-scoped WebSocket."""
    await manager.connect(websocket, workflow_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, workflow_id)


# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "healthy", "app": settings.app_name, "version": settings.app_version}


@app.get("/")
async def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
