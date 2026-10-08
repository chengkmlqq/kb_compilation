"""FastAPI application entry point.

Serves the framework layer (auth / RBAC / datasource / metadata / files /
system config) migrated from the legacy Next.js backend.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Cookie, Depends, FastAPI
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.middleware import rbac_guard
from api.routers import agents, auth, chat_sessions, chunking, cron, datasources, datagrid, files, graph, jobs, kbs, mcps, models, ontology, qa, skills, system, system_config, websearch, workers
from api.services.identity import Identity, decode_identity_cookie
from api.services.migrations import run_migrations_on_startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-apply Alembic migrations to the framework store on boot, so model
    # column changes reach the DB without manual migrate steps (create_all
    # never alters existing tables). Fail-fast: refuse to serve on bad schema.
    run_migrations_on_startup()
    yield


app = FastAPI(
    title="KB Compilation API",
    description="Framework layer + WeKnora RAG capabilities (KB wiki platform)",
    version="0.1.0",
    lifespan=lifespan,
)

# RBAC path guard: whitelist + identity cookie + role-menu assignment check
# (source proxy.ts equivalent). Registered before routers so every /api/v1
# request passes through it.
app.middleware("http")(rbac_guard)

_settings = get_settings()

app.include_router(auth.router, prefix="/api/v1")
app.include_router(datasources.router, prefix="/api/v1/open")
app.include_router(qa.router, prefix="/api/v1")
app.include_router(chat_sessions.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(kbs.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(cron.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")
app.include_router(system_config.router, prefix="/api/v1")
app.include_router(models.router, prefix="/api/v1")
app.include_router(ontology.router, prefix="/api/v1")
app.include_router(mcps.router, prefix="/api/v1")
app.include_router(websearch.router, prefix="/api/v1")
app.include_router(skills.router, prefix="/api/v1")
app.include_router(workers.router, prefix="/api/v1")
app.include_router(chunking.router, prefix="/api/v1")  # 2026-10-07: chunking/parsers 路由此前漏挂, 引擎列表 404
app.include_router(files.router, prefix="/api/v1")
app.include_router(datagrid.router, prefix="/api/v1")
app.include_router(graph.router, prefix="/api/v1")


@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {
        "success": True,
        "status": "UP",
        "db_type": _settings.DB_TYPE,
        "version": app.version,
    }


@app.get("/api/v1/open/health")
def open_health() -> dict:
    """Open-API health endpoint (mirrors the source platform's /api/open/health)."""
    return {"success": True, "status": "UP"}


@app.get("/api/v1/me")
def me(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> dict:
    """Decode current identity from the shared AES cookie (frontend compat)."""
    identity: Identity | None = decode_identity_cookie(x_next_identity or "")
    if not identity:
        return {"success": False, "message": "未登录", "data": None}
    return {"success": True, "data": identity.to_payload()}
