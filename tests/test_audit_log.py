"""Audit log service tests — oper_log write + field clamping + classification."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import OperLog
from api.services.audit_log import classify_action, format_oper_time, write_oper_log


@pytest.fixture()
def log_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


def test_write_oper_log_basic(log_db) -> None:
    row = write_oper_log(
        log_db,
        oper_type="LOGIN",
        oper_content="admin在2026-09-30 10:00:00登录的系统",
        user_id="admin",
        user_name="系统管理员",
        team_name="DEFAULT_TEAM",
        oper_url="/login",
    )
    assert row.id
    assert row.oper_type == "LOGIN"
    assert row.user_id == "admin"
    # reload from db to confirm persistence
    reloaded = log_db.get(OperLog, row.id)
    assert reloaded is not None
    assert reloaded.user_name == "系统管理员"
    assert reloaded.team_name == "DEFAULT_TEAM"


def test_write_oper_log_clamps_long_fields(log_db) -> None:
    long_user = "x" * 500
    long_url = "u" * 2000
    row = write_oper_log(log_db, oper_type="SAVE", oper_content="c", user_id=long_user, oper_url=long_url)
    assert len(row.user_id) == 64  # clamped
    assert len(row.oper_url) == 512  # clamped


def test_write_oper_log_blank_fields_become_null(log_db) -> None:
    row = write_oper_log(log_db, oper_type="LOGIN", oper_content="x", user_id="   ", user_name=None)
    assert row.user_id is None
    assert row.user_name is None


def test_classify_action() -> None:
    assert classify_action("saveUserAction") == "mutating"
    assert classify_action("deleteAgent") == "mutating"
    assert classify_action("getUsersAction") == "readonly"
    assert classify_action("listDatasources") == "readonly"
    assert classify_action("randomThing") == "other"


def test_format_oper_time() -> None:
    from datetime import datetime

    ts = datetime(2026, 9, 30, 10, 30, 45)
    assert format_oper_time(ts) == "2026-09-30 10:30:45"
    assert format_oper_time(None).startswith("20")
    assert format_oper_time("2026-09-30T10:30:45") == "2026-09-30T10:30:45"
