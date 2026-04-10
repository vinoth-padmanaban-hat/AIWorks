"""
Control Plane — FastAPI app (port 8000).

Exposes:
  - POST /execute              → Generic Orchestrator → Planner → Execution Engine → agents
  - POST /ingestion/run/{tid}  → Legacy ingestion trigger (backward compat)
  - GET  /admin/...            → Read-only admin API for UI
  - GET  /health

Does NOT contain any domain logic; all work is delegated to agent services
discovered via the Agent Registry.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin_routes import router as admin_router
from app.api.execute_routes import router as execute_router
from app.api.ingestion_routes import router as ingestion_router
from app.core.config import settings
from app.core.langfuse_setup import flush_langfuse, init_langfuse

logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="AIWorks Control Plane",
    version="0.2.0",
    description=(
        "Multi-tenant agentic AI platform. "
        "POST /execute with a goal → Planner → Agent dispatch → Results."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# orchestrator router
app.include_router(execute_router)
app.include_router(ingestion_router)
app.include_router(admin_router)


@app.on_event("startup")
async def _langfuse_startup() -> None:
    init_langfuse()


@app.on_event("shutdown")
async def _langfuse_shutdown() -> None:
    flush_langfuse()


@app.on_event("startup")
async def _log_admin_routes() -> None:
    n = sum(
        1
        for r in app.routes
        if getattr(r, "path", "") and str(r.path).startswith("/admin")
    )
    logging.getLogger(__name__).info(
        "Control plane ready: %d /admin/* routes (restart uvicorn if this is 0 but you expect admin API)",
        n,
    )


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "control-plane"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.control_plane_port,
        reload=True,
    )
