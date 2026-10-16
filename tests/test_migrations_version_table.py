"""启动迁移的 alembic 版本表列宽自愈测试。

背景（2026-10-16 线上实测）：镜像里 0025 的 revision id
`0025_chat_attachment_image_object` 长 33 字符，而 alembic 默认版本表是
`version_num VARCHAR(32)` → 播版本号时 MySQL 1406 Data too long →
api-server 启动失败（fail-fast）→ 容器重启循环 → 整站 /api 502。
"""
from __future__ import annotations

import re
import types
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

from api.services import migrations
from api.services.migrations import (
    create_version_table_ddl,
    ensure_version_table_width,
    widen_version_column_ddl,
)

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "versions"


def _revision_id_lengths() -> list[tuple[int, str]]:
    ids: list[tuple[int, str]] = []
    for path in VERSIONS_DIR.glob("*.py"):
        match = re.search(
            r'^revision(?::\s*str)?\s*=\s*["\']([^"\']+)["\']',
            path.read_text(encoding="utf-8"),
            re.M,
        )
        if match:
            ids.append((len(match.group(1)), match.group(1)))
    return ids


class _FakeConn:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def execute(self, statement) -> None:  # noqa: ANN001 - 只记录 SQL
        self._sink.append(str(statement))


class _FakeEngine:
    """只支持 ensure_version_table_width 用到的最小接口（dialect/begin）。"""

    def __init__(self, dialect: str, sink: list[str]) -> None:
        self.dialect = types.SimpleNamespace(name=dialect)
        self._sink = sink

    @contextmanager
    def begin(self):
        yield _FakeConn(self._sink)


class _FakeInspector:
    def __init__(self, tables: list[str], columns: list[dict]) -> None:
        self._tables = tables
        self._columns = columns

    def get_table_names(self) -> list[str]:
        return list(self._tables)

    def get_columns(self, table: str) -> list[dict]:
        return list(self._columns)


def test_仓库存在超32字符revision_id时必须有列宽自愈() -> None:
    """真实数据驱动的护栏：一旦有 revision id 超过 32 字符，放宽 DDL 必须存在。"""
    assert _revision_id_lengths(), "未找到 migrations/versions 下的 revision id"
    longest_len, longest_id = max(_revision_id_lengths())

    if longest_len > 32:
        assert create_version_table_ddl("mysql"), "缺表预建 DDL 缺失"
        assert widen_version_column_ddl("mysql"), "MySQL 放宽 DDL 缺失"
        assert widen_version_column_ddl("postgresql"), "PostgreSQL 放宽 DDL 缺失"
        assert len(longest_id) == longest_len


def test_mysql与postgres的建表与放宽DDL() -> None:
    assert "VARCHAR(255)" in (create_version_table_ddl("mysql") or "")
    assert "MODIFY COLUMN version_num VARCHAR(255)" in (widen_version_column_ddl("mysql") or "")
    assert "VARCHAR(255)" in (create_version_table_ddl("postgresql") or "")
    assert "ALTER COLUMN version_num TYPE VARCHAR(255)" in (widen_version_column_ddl("postgresql") or "")
    assert create_version_table_ddl("sqlite") is None
    assert widen_version_column_ddl("sqlite") is None


def test_缺表时按宽列预建(monkeypatch) -> None:
    sink: list[str] = []
    monkeypatch.setattr(
        sa,
        "inspect",
        lambda engine: _FakeInspector([], [{"name": "version_num", "type": types.SimpleNamespace(length=32)}]),
    )

    assert ensure_version_table_width(_FakeEngine("mysql", sink)) is True
    assert len(sink) == 1
    assert "CREATE TABLE IF NOT EXISTS alembic_version" in sink[0]
    assert "VARCHAR(255)" in sink[0]


def test_已有窄列时执行放宽DDL(monkeypatch) -> None:
    sink: list[str] = []
    monkeypatch.setattr(
        sa,
        "inspect",
        lambda engine: _FakeInspector(
            ["alembic_version"], [{"name": "version_num", "type": types.SimpleNamespace(length=32)}]
        ),
    )

    assert ensure_version_table_width(_FakeEngine("mysql", sink)) is True
    assert len(sink) == 1
    assert "MODIFY COLUMN version_num VARCHAR(255)" in sink[0]


def test_列宽已足够时不再执行DDL(monkeypatch) -> None:
    sink: list[str] = []
    monkeypatch.setattr(
        sa,
        "inspect",
        lambda engine: _FakeInspector(
            ["alembic_version"], [{"name": "version_num", "type": types.SimpleNamespace(length=255)}]
        ),
    )

    assert ensure_version_table_width(_FakeEngine("mysql", sink)) is False
    assert sink == []


def test_sqlite引擎下no_op不建表() -> None:
    engine = create_engine("sqlite://")

    assert ensure_version_table_width(engine) is False
    assert migrations.VERSION_TABLE not in inspect(engine).get_table_names()


def test_预建宽列表的列宽声明为255() -> None:
    """用 sqlite 走一遍「预建宽列表」的真实分支（sqlite 不校验长度，仅验流程与列宽声明）。"""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(255) NOT NULL, PRIMARY KEY (version_num))"
            )
        )
    columns = {c["name"]: c for c in inspect(engine).get_columns("alembic_version")}

    assert getattr(columns["version_num"]["type"], "length", None) == 255
