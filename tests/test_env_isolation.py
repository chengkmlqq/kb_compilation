"""环境隔离路由测试（2026-10-12；2026-10-20 双表改造）。

覆盖：
- current_env 默认 prod；env_scope 切换/恢复
- _resolve_db_url / _resolve_kb_url：双库路由已废弃，dev/prod 恒返回生产 URL
- 双表路由：dev 环境把隔离表（ISOLATED_TABLES）改写为 xxx_dev（env_table_router）
- storage.build_remote 环境前缀（prod//dev/、存量无前缀兼容、不重复加）
"""
from __future__ import annotations

import pytest


def test_current_env_default_prod() -> None:
    import api.db as db

    assert db.current_env() == "prod"


def test_env_scope_switch_and_restore() -> None:
    import api.db as db

    assert db.current_env() == "prod"
    with db.env_scope("dev"):
        assert db.current_env() == "dev"
    assert db.current_env() == "prod"


def test_resolve_db_url_same_for_dev_and_prod(monkeypatch) -> None:
    """双表改造（2026-10-20）：双库路由废弃，dev/prod 恒解析到同一 DATABASE_URL。

    测试环境可能未配 DATABASE_URL，monkeypatch require_database_url 提供
    假值即可验证「dev 与 prod 解析结果恒相等」这一双表语义。
    """
    import api.db as db
    from api.config import get_settings

    s = get_settings()
    # 即便配置了旧 DEV_DATABASE_URL 也不消费（保留字段仅为 .env 兼容）
    monkeypatch.setattr(s, "DEV_DATABASE_URL", "mysql+pymysql://dev:***@localhost/dev_db")
    monkeypatch.setattr(s, "KB_ENV_ROUTING_ENABLED", True)
    monkeypatch.setattr(s, "require_database_url", lambda: "mysql+pymysql://qa:***@db/prod")
    assert db._resolve_db_url("dev") == s.require_database_url()
    assert db._resolve_db_url("prod") == s.require_database_url()


def test_resolve_kb_url_same_for_dev_and_prod(monkeypatch) -> None:
    """双表改造（2026-10-20）：向量库不切 schema/dev 库，dev 回落生产 URL。

    测试环境可能未配 KNOWLEDGE_DATABASE_URL（require_* 会抛），故用
    settings 字段直查（无环境变量时两分支都走空串路径，仍恒相等）。
    """
    import api.db as db
    from api.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "DEV_KNOWLEDGE_DATABASE_URL", "postgresql://dev@localhost/dev_kb")
    monkeypatch.setattr(s, "DEV_SCHEMA_NAME", "dev")
    monkeypatch.setattr(s, "KB_ENV_ROUTING_ENABLED", True)
    monkeypatch.setattr(s, "require_knowledge_database_url",
                        lambda: "postgresql://qa@localhost/kb_embedding")
    url_dev, schema_dev = db._resolve_kb_url("dev")
    url_prod, schema_prod = db._resolve_kb_url("prod")
    assert url_dev == url_prod == s.require_knowledge_database_url()
    assert schema_dev == schema_prod == s.SCHEMA_NAME


def test_dev_table_name_suffix() -> None:
    """白名单表 dev 后缀；白名单外原样。"""
    from api.services.env_table_router import ISOLATED_TABLES, dev_table_name

    assert dev_table_name("kb_tape_run") == "kb_tape_run_dev"
    assert dev_table_name("wiki_page") == "wiki_page_dev"
    assert dev_table_name("kb_tape") == "kb_tape"  # 编排定义表不隔离
    assert "kb_tape_run" in ISOLATED_TABLES
    assert "wiki_page" in ISOLATED_TABLES


def test_dev_sql_rewriter_word_boundary() -> None:
    """词边界改写：正式表名 → _dev；_dev 表名 / 更长标识符不二次改写。"""
    from api.services.env_table_router import _DevSqlRewriter

    r = _DevSqlRewriter()
    assert "INSERT INTO kb_tape_run_dev" in r.rewrite_sql("INSERT INTO kb_tape_run (id) VALUES (%s)")
    # _dev 表名不二次改写（避免 kb_tape_run_dev_dev）
    assert r.rewrite_sql("UPDATE kb_tape_run_dev SET status=%s") == "UPDATE kb_tape_run_dev SET status=%s"
    # 更长的标识符（kb_tape_run_log）不误伤
    assert "kb_tape_run_log" in r.rewrite_sql("SELECT * FROM kb_tape_run_log")
    assert "kb_tape_run" not in r.rewrite_sql("SELECT * FROM kb_tape_run_log").split("kb_tape_run_log")[0]


def test_build_remote_env_prefix() -> None:
    import api.services.storage as st
    from api.db import env_scope

    # 默认 prod 前缀
    assert st.build_remote("kb", "kb_documents/1.txt") == "minio://kb/prod/kb_documents/1.txt"
    # dev 前缀
    with env_scope("dev"):
        assert st.build_remote("kb", "kb_documents/1.txt") == "minio://kb/dev/kb_documents/1.txt"
    # 已带前缀不重复加（避免 dev/prod 嵌套）
    with env_scope("dev"):
        assert st.build_remote("kb", "prod/kb_documents/1.txt") == "minio://kb/prod/kb_documents/1.txt"
    # 日志等其余 key 也带前缀
    with env_scope("prod"):
        assert st.build_remote("kb", "logs/job.log") == "minio://kb/prod/logs/job.log"
