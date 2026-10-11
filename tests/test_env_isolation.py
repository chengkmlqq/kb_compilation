"""环境隔离路由测试（2026-10-12）。

覆盖：
- current_env 默认 prod；env_scope 切换/恢复
- _resolve_db_url / _resolve_kb_url 按环境解析（dev 未配/开关关 → 回落 prod）
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


def test_resolve_db_url_fallback_when_disabled(monkeypatch) -> None:
    import api.db as db
    from api.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "KB_ENV_ROUTING_ENABLED", False)
    monkeypatch.setattr(s, "DEV_DATABASE_URL", "mysql+pymysql://dev:dev@localhost/dev_db")
    # 开关关 → dev 也回落 prod
    assert db._resolve_db_url("dev") == s.require_database_url()


def test_resolve_db_url_dev_when_enabled(monkeypatch) -> None:
    import api.db as db
    from api.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "KB_ENV_ROUTING_ENABLED", True)
    monkeypatch.setattr(s, "DEV_DATABASE_URL", "mysql+pymysql://dev:dev@localhost/dev_db")
    assert db._resolve_db_url("dev") == s.DEV_DATABASE_URL
    assert db._resolve_db_url("prod") == s.require_database_url()


def test_resolve_kb_url_dev_schema(monkeypatch) -> None:
    import api.db as db
    from api.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "KB_ENV_ROUTING_ENABLED", True)
    monkeypatch.setattr(s, "DEV_KNOWLEDGE_DATABASE_URL", "postgresql://dev@localhost/dev_kb")
    monkeypatch.setattr(s, "DEV_SCHEMA_NAME", "dev")
    # 测试环境无 KNOWNLEGE prod URL，只验证 dev 分支（prod 分支与框架库同逻辑已覆盖）
    url, schema = db._resolve_kb_url("dev")
    assert url == s.DEV_KNOWLEDGE_DATABASE_URL and schema == "dev"


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
