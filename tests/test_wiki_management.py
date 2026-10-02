"""Tests for wiki management (page/folder CRUD, stats, lint, rebuild-links)
in api/services/kb_admin.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.models.framework import Base, Job
from api.models.knowledge import KbDocument, WikiFolder, WikiLink, WikiPage
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
    s.query(WikiPage).delete()
    s.query(WikiFolder).delete()
    s.query(WikiLink).delete()
    s.query(KbDocument).delete()
    s.commit()
    yield s
    s.close()


class FakeKb:
    """kb_admin.wiki_* 只查 wiki 表，不依赖 kb 表；mock 掉 get_kb 用静态块。"""


def _make_page(
    db: Session, kb_id: str, slug: str, title: str, content: str = "# t\n", page_type: str = "entity"
) -> WikiPage:
    p = WikiPage(
        id=slug,  # 用 slug 当 id 简化链接关联（真实是 uuid，这里便于断言）
        kb_id=kb_id,
        slug=slug,
        title=title,
        page_type=page_type,
        content=content,
        folder_id="",
        status="active",
    )
    db.add(p)
    db.commit()
    return p


def test_wiki_stats(db: Session) -> None:
    kb = "kb-stats"
    _make_page(db, kb, "a", "甲", content="内容a")
    _make_page(db, kb, "b", "乙", content="内容b", page_type="concept")
    db.add(WikiLink(id="l1", kb_id=kb, from_page_id="a", to_page_id="b", link_type="related"))
    db.commit()

    stats = kb_admin.wiki_stats(db, kb)
    assert stats["total_pages"] == 2
    assert stats["total_links"] == 1
    assert stats["pages_by_type"] == {"entity": 1, "concept": 1}
    assert stats["orphan_count"] == 0


def test_wiki_page_crud(db: Session) -> None:
    kb = "kb-crud"
    # create
    r = kb_admin.wiki_create_page(db, kb, {"title": "新建页", "content": "内容"}, "u1")
    assert r["slug"] == "新建页"

    # duplicate slug -> error
    with pytest.raises(ValueError):
        kb_admin.wiki_create_page(db, kb, {"title": "另一个", "slug": "新建页"})

    # update
    r2 = kb_admin.wiki_update_page(db, kb, "新建页", {"title": "改名", "content": "新内容", "page_type": "concept"})
    page = db.get(WikiPage, r["id"])
    assert page.title == "改名"
    assert page.page_type == "concept"

    # delete (soft)
    r3 = kb_admin.wiki_delete_page(db, kb, "新建页")
    assert r3["deleted"] is True
    deleted = db.get(WikiPage, r["id"])
    assert deleted.status == "archived"


def test_wiki_delete_page_cleans_links(db: Session) -> None:
    kb = "kb-del-links"
    _make_page(db, kb, "a", "甲", content="引用[[b]]")
    _make_page(db, kb, "b", "乙", content="被[[a]]引用")
    kb_admin.wiki_rebuild_links(db, kb)
    assert db.query(WikiLink).filter(WikiLink.kb_id == kb).count() == 2

    kb_admin.wiki_delete_page(db, kb, "a")
    # 删除 a 后其出入链应全部清理
    assert db.query(WikiLink).filter(WikiLink.kb_id == kb).count() == 0


def test_wiki_search(db: Session) -> None:
    kb = "kb-search"
    _make_page(db, kb, "a", "政府采购", content="# 甲\n政府采购法相关内容")
    _make_page(db, kb, "b", "招标投标", content="# 乙\n招标投标规则")
    _make_page(db, kb, "c", "无关页", content="# 丙\n完全无关内容")

    r = kb_admin.wiki_search(db, kb, "采购")
    assert r["total"] == 1
    assert r["items"][0]["slug"] == "a"

    r2 = kb_admin.wiki_search(db, kb, "招标")
    assert r2["total"] == 1
    assert r2["items"][0]["slug"] == "b"

    r3 = kb_admin.wiki_search(db, kb, "不存在")
    assert r3["total"] == 0

    r4 = kb_admin.wiki_search(db, kb, "")
    assert r4["total"] == 0


def test_wiki_folder_crud(db: Session) -> None:
    kb = "kb-folder"
    f = kb_admin.wiki_create_folder(db, kb, {"name": "根目录"}, "u1")
    f2 = kb_admin.wiki_create_folder(db, kb, {"name": "子目录", "parent_id": f["id"]}, "u1")

    kb_admin.wiki_update_folder(db, kb, f["id"], {"name": "改名目录"})
    assert db.get(WikiFolder, f["id"]).name == "改名目录"

    # delete folder with pages -> error
    _make_page(db, kb, "p1", "页一")
    page = db.query(WikiPage).filter(WikiPage.slug == "p1").first()
    page.folder_id = f["id"]
    db.commit()
    with pytest.raises(ValueError):
        kb_admin.wiki_delete_folder(db, kb, f["id"])

    # delete empty folder
    page.folder_id = ""
    db.commit()
    r = kb_admin.wiki_delete_folder(db, kb, f2["id"])
    assert r["deleted"] is True


def test_wiki_lint(db: Session) -> None:
    kb = "kb-lint"
    _make_page(db, kb, "empty", "空页", content="")
    _make_page(db, kb, "orphan", "孤立页", content="内容但无链")
    _make_page(db, kb, "broken", "断链页", content="引用[[不存在的目标]]")

    lint = kb_admin.wiki_lint(db, kb)
    types = {i["issue_type"] for i in lint["issues"]}
    assert "empty_content" in types
    assert "orphan" in types
    assert lint["broken_link_count"] >= 1


def test_wiki_rebuild_links(db: Session) -> None:
    kb = "kb-rebuild"
    _make_page(db, kb, "a", "甲", content="# 甲\n引用[[b]]")
    _make_page(db, kb, "b", "乙", content="# 乙\n被[[a]]引用")
    _make_page(db, kb, "c", "丙", content="# 丙\n无链接")

    # 先手工加一条错链（指向不存在的目标）
    db.add(WikiLink(id="bad", kb_id=kb, from_page_id="a", to_page_id="ghost", link_type="related"))
    db.commit()

    r = kb_admin.wiki_rebuild_links(db, kb)
    # a↔b 双向 = 2 条；c 无链接。旧错链被清空。
    assert r["added_links"] == 2

    links = db.query(WikiLink).filter(WikiLink.kb_id == kb).all()
    pairs = {(l.from_page_id, l.to_page_id) for l in links}
    assert len(links) == 2
    assert ("a", "b") in pairs
    assert ("b", "a") in pairs