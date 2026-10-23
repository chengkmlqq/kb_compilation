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
from api.middleware import normalize_content_type, rbac_guard
from api.routers import agents, auth, chat_sessions, chat_traces, chunking, cron, datasources, datagrid, feedback, files, graph, jobs, kb_categories, kbs, mcps, metadata, models, ontology, orchestrations, overview, qa, quality, quality_build_check, skills, system, system_config, usage, websearch, workers
from api.services.identity import Identity, decode_identity_cookie
from api.services.migrations import run_migrations_on_startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-apply Alembic migrations to the framework store on boot, so model
    # column changes reach the DB without manual migrate steps (create_all
    # never alters existing tables). Fail-fast: refuse to serve on bad schema.
    run_migrations_on_startup()
    # 2026-10-23：启动后后台预热聚合缓存（admin 视角 + 默认参数）——重启/冷启动后
    # 首个页面请求不支付远端库冷缓冲池的秒级聚合（看板/知识库卡片计数），
    # 预热线程在后台跑，失败不影响启动。
    _warm_aggregate_cache()
    yield


def _warm_aggregate_cache() -> None:
    """后台预热聚合缓存（stale-while-revalidate 的冷启动补位）。

    每个聚合独立线程并行跑（冷缓冲下单个聚合 5-20s，串行会拖到 1 分多钟，
    用户在预热完成前访问仍会同步计算）；list_kbs 排最前（最高频页面）。
    预热失败不影响启动（单个失败只少一个缓存项，请求走同步/stale 兜底）。
    """
    import threading
    import time as _time
    from typing import Callable

    def _job(fn: Callable[[], None]) -> None:
        def _run() -> None:
            try:
                fn()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True, name="kb-warm-agg").start()

    # 高频/看板项并行预热（每个都自建 session，含 1s 启动延迟）
    def _make(fn: Callable[[], None]) -> Callable[[], None]:
        def _wrapped() -> None:
            _time.sleep(1)
            fn()
        return _wrapped

    from api.db import get_sessionmaker
    from api.routers.overview import overview_kb_stats, overview_summary, overview_trend
    from api.services.identity import Identity
    from api.services.kb_admin import list_kbs
    from api.routers.metadata import list_metadata_datasources
    from api.routers.usage import summary as usage_summary
    from api.routers.usage import trend as usage_trend

    def _warm_list_kbs() -> None:
        s = get_sessionmaker()()
        try:
            list_kbs(
                s, 1, 30,
                caller_user_id="admin", caller_team_name="ROOT", is_sys_admin=True,
            )
        finally:
            s.close()

    def _warm_overview_summary() -> None:
        s = get_sessionmaker()()
        try:
            overview_summary(db=s, caller={"user_id": "admin", "team_name": "ROOT", "is_admin": True})
        finally:
            s.close()

    def _warm_overview_kb_stats() -> None:
        s = get_sessionmaker()()
        try:
            overview_kb_stats(db=s, caller={"user_id": "admin", "team_name": "ROOT", "is_admin": True})
        finally:
            s.close()

    def _warm_overview_trend() -> None:
        s = get_sessionmaker()()
        try:
            overview_trend(days=7, db=s, caller={"user_id": "admin", "team_name": "ROOT", "is_admin": True})
        finally:
            s.close()

    def _warm_usage_summary() -> None:
        s = get_sessionmaker()()
        try:
            idn = Identity(login_id="admin", user_id="admin", user_name="系统管理员", team_name="ROOT")
            usage_summary(preset="24h", start="", end="", kb_id="", model="", operation="", db=s, idn=idn)
        finally:
            s.close()

    def _warm_usage_trend_day() -> None:
        s = get_sessionmaker()()
        try:
            idn = Identity(login_id="admin", user_id="admin", user_name="系统管理员", team_name="ROOT")
            usage_trend(granularity="day", preset="24h", start="", end="", kb_id="", model="", operation="", db=s, idn=idn)
        finally:
            s.close()

    def _warm_usage_trend_hour() -> None:
        s = get_sessionmaker()()
        try:
            idn = Identity(login_id="admin", user_id="admin", user_name="系统管理员", team_name="ROOT")
            usage_trend(granularity="hour", preset="24h", start="", end="", kb_id="", model="", operation="", db=s, idn=idn)
        finally:
            s.close()

    def _warm_metadata_dss() -> None:
        s = get_sessionmaker()()
        try:
            idn = Identity(login_id="admin", user_id="admin", user_name="系统管理员", team_name="ROOT")
            list_metadata_datasources(identity=idn, db=s)
        finally:
            s.close()

    def _warm_jobs_statistics() -> None:
        from api.routers.jobs import job_statistics

        s = get_sessionmaker()()
        try:
            job_statistics(_user_id="admin", db=s)
        finally:
            s.close()

    _job(_make(_warm_list_kbs))
    _job(_make(_warm_overview_summary))
    _job(_make(_warm_overview_kb_stats))
    _job(_make(_warm_overview_trend))
    _job(_make(_warm_usage_summary))
    _job(_make(_warm_usage_trend_day))
    _job(_make(_warm_usage_trend_hour))
    _job(_make(_warm_metadata_dss))
    _job(_make(_warm_jobs_statistics))


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
# content-type 归一化（逗号拼接重复值 → 单值），注册在 rbac_guard 之后使其最先执行
app.middleware("http")(normalize_content_type)

_settings = get_settings()

app.include_router(auth.router, prefix="/api/v1")
app.include_router(datasources.router, prefix="/api/v1/open")
app.include_router(qa.router, prefix="/api/v1")
app.include_router(chat_sessions.router, prefix="/api/v1")
app.include_router(chat_traces.router, prefix="/api/v1")
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
app.include_router(metadata.router, prefix="/api/v1")  # 2026-10-10 元数据采集
app.include_router(graph.router, prefix="/api/v1")
app.include_router(orchestrations.router, prefix="/api/v1")
app.include_router(quality.router, prefix="/api/v1")
app.include_router(quality_build_check.router, prefix="/api/v1")  # 2026-10-22 构建任务完成检查
app.include_router(kb_categories.router, prefix="/api/v1")  # 2026-10-19 知识库分类树 CRUD
app.include_router(feedback.router, prefix="/api/v1")  # 2026-10-14 问答反馈统计
app.include_router(usage.router, prefix="/api/v1")  # 2026-10-16 LLM 使用记录与性能监控看板
app.include_router(overview.router, prefix="/api/v1")  # 2026-10-20 首页总览统计看板


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
