"""Tests for high-frequency keyword overview + independent mark channel
(migrated from WeKnora wiki/keywords/overview + WikiFeedbackPresetKeys) in
api/services/kb_admin.py.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.models.framework import Base, Job
from api.models.knowledge import WikiFeedback, WikiPage
from api.services import kb_admin

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db() -> Iterator[Session]:
    Base.metadata.create_all(engine)
    s = SessionLocal()
    s.query(Job).delete()
    s.query(WikiFeedback).delete()
    s.query(WikiPage).delete()
    s.commit()
    yield s
    s.close()


def _make_kw_page(
    db: Session, slug: str, title: str, head: str, page_type: str = "frequent_keyword"
) -> WikiPage:
    p = WikiPage(
        id=slug,
        kb_id="kb1",
        slug=slug,
        title=title,
        page_type=page_type,
        content=head,
        folder_id="",
        status="active",
    )
    db.add(p)
    db.commit()
    return p


def test_keyword_overview_frequency_parse_and_sort(db: Session) -> None:
    """标准开头句解析（N 篇文档 / 总频次），按频次降序 + 文档数降序。"""
    _make_kw_page(db, "keyword/a", "高频词A", "本关键词在 2 篇文档中高频出现（总频次：8）。")
    _make_kw_page(db, "keyword/b", "高频词B", "本关键词在 3 篇文档中高频出现（总频次：8）。")
    _make_kw_page(db, "keyword/c", "高频词C", "本关键词在 1 篇文档中高频出现（总频次：15）。")
    _make_kw_page(db, "entity/x", "实体X", "普通实体页", page_type="entity")

    res = kb_admin.wiki_keyword_overview(db, "kb1")
    assert res["total"] == 3
    # 15 > 8；同 8 时文档数多的靠前
    assert [i["keyword"] for i in res["items"]] == ["高频词C", "高频词B", "高频词A"]
    assert res["items"][0]["total_freq"] == 15
    assert res["items"][0]["doc_count"] == 1


def test_keyword_overview_search_filter(db: Session) -> None:
    """search 按关键词子串过滤（排序前）。"""
    _make_kw_page(db, "keyword/a", "进口药材", "本关键词在 1 篇文档中高频出现（总频次：8）。")
    _make_kw_page(db, "keyword/b", "药品管理", "本关键词在 1 篇文档中高频出现（总频次：5）。")

    res = kb_admin.wiki_keyword_overview(db, "kb1", search="进口")
    assert res["total"] == 1
    assert res["items"][0]["keyword"] == "进口药材"


def test_keyword_overview_legacy_freq_fallback(db: Session) -> None:
    """旧规范页面无标准开头句：按分组频次累加兜底，docs=1。"""
    _make_kw_page(
        db,
        "keyword/old",
        "旧词",
        "一些正文\n**《文档A》**（频次：4）\n**《文档B》**（频次：6）",
    )
    res = kb_admin.wiki_keyword_overview(db, "kb1")
    assert res["total"] == 1
    assert res["items"][0]["total_freq"] == 10
    assert res["items"][0]["doc_count"] == 1


def test_keyword_overview_feedback_counts(db: Session) -> None:
    """标注计数按 preset 聚合到对应词页（软删标注不计入）。"""
    _make_kw_page(db, "keyword/a", "高频词A", "本关键词在 1 篇文档中高频出现（总频次：8）。")
    for _ in range(2):
        db.add(
            WikiFeedback(
                id=f"fb{_}",
                kb_id="kb1",
                slug="keyword/a",
                user_id="u1",
                feedback_type="question",
                preset="kw_meaningful",
                content="",
                status="pending",
            )
        )
    db.add(
        WikiFeedback(
            id="fb-noise",
            kb_id="kb1",
            slug="keyword/a",
            user_id="u1",
            feedback_type="question",
            preset="kw_noise",
            content="",
            status="pending",
        )
    )
    # 软删的标注不计入（deleted_at 置位）
    db.add(
        WikiFeedback(
            id="fb-deleted",
            kb_id="kb1",
            slug="keyword/a",
            user_id="u1",
            feedback_type="question",
            preset="kw_meaningful",
            content="",
            status="pending",
            deleted_at=datetime.now(),
        )
    )
    db.commit()

    res = kb_admin.wiki_keyword_overview(db, "kb1")
    counts = res["items"][0]["feedback_counts"]
    assert counts["kw_meaningful"] == 2
    assert counts["kw_noise"] == 1
    assert "kw_mis_extracted" not in counts


def test_keyword_overview_pagination(db: Session) -> None:
    """分页：page_size 生效，total 为过滤后总数。"""
    for i in range(5):
        _make_kw_page(db, f"keyword/{i}", f"词{i}", "本关键词在 1 篇文档中高频出现（总频次：1）。")

    res = kb_admin.wiki_keyword_overview(db, "kb1", page=1, page_size=2)
    assert res["total"] == 5
    assert len(res["items"]) == 2
    assert res["page"] == 1

    res2 = kb_admin.wiki_keyword_overview(db, "kb1", page=3, page_size=2)
    assert len(res2["items"]) == 1


def test_wiki_keyword_mark(db: Session) -> None:
    """独立标注通道：preset 白名单校验 + 落库 + 普通反馈不受影响。"""
    r = kb_admin.wiki_keyword_mark(db, "kb1", "keyword/a", "u1", "kw_noise")
    assert r["preset"] == "kw_noise"

    r2 = kb_admin.wiki_keyword_mark(db, "kb1", "keyword/a", "u1", "custom", content="行业术语")
    assert r2["preset"] == "custom"

    row = db.query(WikiFeedback).filter(WikiFeedback.slug == "keyword/a").first()
    assert row is not None and row.preset == "kw_noise"


def test_wiki_keyword_mark_rejects_bad_preset(db: Session) -> None:
    """自定义预设缺内容 / 未知 preset 被拒。"""
    with pytest.raises(ValueError):
        kb_admin.wiki_keyword_mark(db, "kb1", "keyword/a", "u1", "custom")
    with pytest.raises(ValueError):
        kb_admin.wiki_keyword_mark(db, "kb1", "keyword/a", "u1", "unknown_preset")
