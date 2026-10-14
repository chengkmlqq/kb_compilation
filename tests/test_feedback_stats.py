"""问答反馈（点赞/点踩）统计服务测试 — stats / list / 筛选 / 用户隔离。

对齐 test_chat_sessions 的 SQLite 内存库模式。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models import chat_session  # noqa: F401  (register tables)
from api.services import chat_sessions as chat_svc
from api.services import feedback_stats as fb_svc


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_feedback(db, user_id="user1", title="我的会话"):
    """建一个会话并写入 2 条反馈（1 like + 1 dislike）与 1 条未反馈回答。"""
    s = chat_svc.create_session(db, user_id, "kb1", title=title)
    chat_svc.append_message(db, user_id, s["id"], "user", "问题一")
    chat_svc.append_message(db, user_id, s["id"], "assistant", "回答一")
    chat_svc.append_message(db, user_id, s["id"], "user", "问题二")
    chat_svc.append_message(db, user_id, s["id"], "assistant", "回答二")
    msgs = chat_svc.load_messages(db, user_id, s["id"])
    chat_svc.set_message_feedback(db, user_id, s["id"], msgs[1]["id"], "like")
    chat_svc.set_message_feedback(db, user_id, s["id"], msgs[3]["id"], "dislike")
    return s, msgs


def test_stats_counts(db) -> None:
    _seed_feedback(db)
    r = fb_svc.feedback_stats(db, "admin")
    assert r["success"] is True
    d = r["data"]
    assert d["total_feedback"] == 2
    assert d["like_count"] == 1
    assert d["dislike_count"] == 1
    assert d["like_rate"] == 0.5
    assert d["today_count"] == 2  # 刚写入，feedback_at=now
    assert d["week_count"] == 2


def test_stats_empty(db) -> None:
    r = fb_svc.feedback_stats(db, "admin")
    d = r["data"]
    assert d["total_feedback"] == 0
    assert d["like_count"] == 0
    assert d["dislike_count"] == 0
    assert d["like_rate"] == 0


def test_stats_user_scoped(db) -> None:
    """非 admin 只统计自己的反馈（对齐数据查询团队授权口径）。"""
    _seed_feedback(db, user_id="user1")
    _seed_feedback(db, user_id="user2")
    admin = fb_svc.feedback_stats(db, "admin")["data"]
    assert admin["total_feedback"] == 4
    scoped = fb_svc.feedback_stats(db, "user1")["data"]
    assert scoped["total_feedback"] == 2
    assert scoped["scoped"] is True


def test_list_pagination_and_fields(db) -> None:
    s, msgs = _seed_feedback(db)
    r = fb_svc.list_feedback(db, 1, 20, user_id="admin")
    assert r["success"] is True
    d = r["data"]
    assert d["total"] == 2
    assert len(d["items"]) == 2
    item = d["items"][0]
    # 问题 = assistant 之前的 user 消息
    assert item["question"] in ("问题一", "问题二")
    assert item["answer"].startswith("回答")
    assert item["user_id"] == "user1"
    assert item["session_title"] == "我的会话"
    assert item["feedback"] in ("like", "dislike")


def test_list_feedback_type_filter(db) -> None:
    _seed_feedback(db)
    likes = fb_svc.list_feedback(db, 1, 20, feedback_type="like", user_id="admin")
    assert likes["data"]["total"] == 1
    assert likes["data"]["items"][0]["feedback"] == "like"
    dislikes = fb_svc.list_feedback(db, 1, 20, feedback_type="dislike", user_id="admin")
    assert dislikes["data"]["total"] == 1
    assert dislikes["data"]["items"][0]["feedback"] == "dislike"


def test_list_keyword_filter(db) -> None:
    _seed_feedback(db)
    r = fb_svc.list_feedback(db, 1, 20, keyword="问题二", user_id="admin")
    assert r["data"]["total"] == 1
    assert r["data"]["items"][0]["question"] == "问题二"


def test_list_user_scoped(db) -> None:
    _seed_feedback(db, user_id="user1")
    _seed_feedback(db, user_id="user2")
    admin = fb_svc.list_feedback(db, 1, 20, user_id="admin")["data"]["total"]
    assert admin == 4
    scoped = fb_svc.list_feedback(db, 1, 20, user_id="user1")["data"]["total"]
    assert scoped == 2
