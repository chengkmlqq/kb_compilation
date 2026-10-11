#!/usr/bin/env python3
"""开发环境初始化（2026-10-12 环境隔离配套）。

一次动作建齐开发环境所需的三样东西（同实例，不新增容器）：
1. MySQL：同实例建开发库（DEV_MYSQL_DATABASE，默认 kb_frame_dev）+ 授权业务账号
2. PG   ：同实例建开发 schema（DEV_SCHEMA_NAME，默认 dev）+ 向量表
3. 灌数据：框架表迁移（alembic）+ 业务表建表 + 种子（框架/菜单/编排组件/编排模板）

生产数据不动；本脚本可重复执行（幂等）。

用法（在 api-server 容器内跑，依赖宿主 .env 的 DB 配置）：
    docker exec kb-api-server python scripts/init_dev_env.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, "/srv/kb")

from api.config import get_settings  # noqa: E402

DEV_MYSQL_DATABASE = os.getenv("DEV_MYSQL_DATABASE", "kb_frame_dev")


def _swap_db_name(url: str, new_db: str) -> str:
    """把 SQLAlchemy URL 里的库名换成 new_db（保留 user/pass/host/port/driver）。"""
    parts = urlsplit(url.replace("mysql+pymysql", "mysql", 1))
    return urlunsplit((parts.scheme, parts.netloc, "/" + new_db, parts.query, parts.fragment)).replace(
        "mysql://", "mysql+pymysql://", 1
    )


def ensure_mysql_dev_db() -> None:
    """同 MySQL 实例建开发库 + 授权给业务账号（幂等）。"""
    import pymysql

    s = get_settings()
    prod_url = s.require_database_url()
    # 从生产 URL 提取账号/密码/host/端口
    plain = prod_url.replace("mysql+pymysql://", "mysql://", 1)
    p = urlsplit(plain)
    user = p.username or ""
    pwd = p.password or ""
    host = p.hostname or "mysql"
    port = p.port or 3306

    conn = pymysql.connect(host=host, port=port, user="root", password=os.getenv("MYSQL_ROOT_PASSWORD", "root_pass"))
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DEV_MYSQL_DATABASE}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            if user:
                # 授权业务账号访问开发库（% 主机，与生产库授权范围一致）
                cur.execute(f"GRANT ALL PRIVILEGES ON `{DEV_MYSQL_DATABASE}`.* TO %s@'%%'", (user,))
                cur.execute("FLUSH PRIVILEGES")
        conn.commit()
    finally:
        conn.close()
    print(f"[mysql] 开发库就绪: {DEV_MYSQL_DATABASE}（同实例 {host}:{port}）")


def ensure_pg_dev_schema() -> None:
    """同 PG 实例建开发 schema + 向量表（幂等）。"""
    from sqlalchemy import create_engine, text

    s = get_settings()
    url = s.require_knowledge_database_url()
    schema = s.DEV_SCHEMA_NAME or "dev"
    eng = create_engine(url)
    try:
        with eng.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        print(f"[pg] 开发 schema 就绪: {schema}")
    finally:
        eng.dispose()


def build_dev_url() -> str:
    """开发库 SQLAlchemy URL（DEV_DATABASE_URL 未配时从生产 URL 换库名推导）。"""
    s = get_settings()
    if s.DEV_DATABASE_URL:
        return s.DEV_DATABASE_URL
    return _swap_db_name(s.require_database_url(), DEV_MYSQL_DATABASE)


def migrate_and_seed() -> None:
    """开发库：框架表迁移 + 业务表建表 + 种子（编排组件/模板）。"""
    from api.db import ENV_DEV, env_scope, get_sessionmaker
    from api.models.knowledge import KnowledgeBase  # noqa: F401 - 注册元数据
    import api.models.framework as _fw  # noqa: F401
    import api.models.knowledge as _kn  # noqa: F401
    import api.models.orchestration as _or  # noqa: F401

    dev_url = build_dev_url()
    s = get_settings()
    s.DEV_DATABASE_URL = dev_url  # 让路由解析指向开发库
    get_sessionmaker(ENV_DEV)  # 预热

    # 1) alembic 迁移（框架表 modo_*）
    env = dict(os.environ)
    env["DATABASE_URL"] = dev_url
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, capture_output=True, text=True)
    print(f"[migrate] rc={r.returncode} {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''}")

    # 2) 业务表建表 + 向量表建表（Base.metadata.create_all 幂等）
    from api.db import Base
    with env_scope(ENV_DEV):
        db = get_sessionmaker(ENV_DEV)()
        try:
            Base.metadata.create_all(db.get_bind(), checkfirst=True)
            print("[tables] 开发库业务表建表完成")
        finally:
            db.close()
    ensure_pg_vector_table()

    # 3) 种子（框架用户/菜单/编排组件/编排模板；幂等 upsert）
    with env_scope(ENV_DEV):
        r2 = subprocess.run(
            [sys.executable, "scripts/seed_framework_db.py"], env=env, capture_output=True, text=True
        )
        print(f"[seed] rc={r2.returncode}")
        for line in (r2.stdout or "").strip().splitlines()[-3:]:
            print(f"       {line}")


def ensure_pg_vector_table() -> None:
    """开发 schema 建向量表 kb_embedding（CREATE TABLE IF NOT EXISTS）。"""
    from sqlalchemy import create_engine, text

    s = get_settings()
    schema = s.DEV_SCHEMA_NAME or "dev"
    eng = create_engine(s.require_knowledge_database_url())
    try:
        with eng.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS vector'))
            conn.execute(
                text(
                    f'CREATE TABLE IF NOT EXISTS "{schema}".kb_embedding ('
                    "id VARCHAR(64) PRIMARY KEY, kb_id VARCHAR(64) NOT NULL, chunk_id VARCHAR(64) NOT NULL, "
                    "dim INTEGER NOT NULL, embedding vector(1024), "
                    "created_at TIMESTAMPTZ DEFAULT now())"
                )
            )
            conn.execute(
                text(
                    f'CREATE INDEX IF NOT EXISTS idx_{schema}_kb_embedding_kb '
                    f'ON "{schema}".kb_embedding (kb_id)'
                )
            )
        print(f"[pg] 开发向量表就绪: {schema}.kb_embedding")
    finally:
        eng.dispose()


if __name__ == "__main__":
    print("=== 开发环境初始化（生产数据不动，幂等）===")
    ensure_mysql_dev_db()
    ensure_pg_dev_schema()
    migrate_and_seed()
    print("=== 完成 ===")