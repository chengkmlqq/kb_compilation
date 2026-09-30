"""FastAPI application entry point.

Serves the framework layer (auth / RBAC / datasource / metadata / files /
system config) that was extracted from the data-synth Next.js backend.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.config import get_settings

app = FastAPI(
    title="KB Compilation API",
    description="Framework layer extracted from data-synth + WeKnora RAG capabilities",
    version="0.1.0",
)

_settings = get_settings()


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
    """Open-API health endpoint (mirrors data-synth /api/open/health)."""
    return {"success": True, "status": "UP"}
