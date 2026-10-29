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
    db: Session, kb_id: str, slug: str, title: str, content: str = "# t\n",
    page_type: str = "entity", folder_id: str = "",
) -> WikiPage:
    p = WikiPage(
        id=slug,  # 用 slug 当 id 简化链接关联（真实是 uuid，这里便于断言）
        kb_id=kb_id,
        slug=slug,
        title=title,
        page_type=page_type,
        content=content,
        folder_id=folder_id,
        status="active",
    )
    db.add(p)
    db.commit()
    return p


def test_wiki_branch_page_total_subtree(db: Session) -> None:
    """2026-10-15：左侧树节点数量统计 = 子树 wiki 页总数（直属 + 全部子孙目录，不含目录本身）。

    目录树：f1 → f11 → f111；另 f12（f1 的另一个子目录）。
    页面分布：f1(2) / f11(1) / f111(3) / f12(1)，根级散页 1。
      - f111.page_total = 3
      - f11.page_total  = 1 + 3 = 4
      - f12.page_total  = 1
      - f1.page_total   = 2 + 4 + 1 = 7
    """
    kb_id = "kb-subtree"
    folders = ["f1", "f11", "f111", "f12"]
    parents = {"f1": "", "f11": "f1", "f111": "f11", "f12": "f1"}
    for fid in folders:
        db.add(WikiFolder(id=fid, kb_id=kb_id, name=f"目录{fid}", parent_id=parents[fid]))
    pages = {  # folder_id -> [slug]
        "": ["root-page"],
        "f1": ["p-f1-a", "p-f1-b"],
        "f11": ["p-f11-a"],
        "f111": ["p-f111-a", "p-f111-b", "p-f111-c"],
        "f12": ["p-f12-a"],
    }
    for fid, slugs in pages.items():
        for s in slugs:
            _make_page(db, kb_id, s, s, folder_id=fid)
    db.commit()

    # 懒加载分支：根级只返回直属目录（仅 f1；f12 是 f1 的子目录），深层需逐级展开
    branch = kb_admin.wiki_branch(db, kb_id)
    by_id = {f["id"]: f for f in branch["folders"]}
    assert set(by_id.keys()) == {"f1"}
    assert by_id["f1"]["page_total"] == 7, f"f1 子树页数应 7，实际 {by_id['f1']['page_total']}"
    sub = kb_admin.wiki_branch(db, kb_id, folder_id="f1")
    sub_by_id = {f["id"]: f for f in sub["folders"]}
    assert set(sub_by_id.keys()) == {"f11", "f12"}
    assert sub_by_id["f11"]["page_total"] == 4
    assert sub_by_id["f12"]["page_total"] == 1
    sub2 = kb_admin.wiki_branch(db, kb_id, folder_id="f11")
    assert {f["id"] for f in sub2["folders"]} == {"f111"}
    assert next(f for f in sub2["folders"] if f["id"] == "f111")["page_total"] == 3
    # 语义不变：child_count 仍是「直属子项数」（展开箭头用，= 子目录 2 + 直属页 2），
    # page_count 仍是直属页数
    assert by_id["f1"]["child_count"] == 4
    assert by_id["f1"]["page_count"] == 2

    # 全量接口同样带 page_total（管理面板/深链定位复用）
    tree = kb_admin.wiki_tree(db, kb_id)
    tree_by_id = {f["id"]: f for f in tree["folders"]}
    assert tree_by_id["f1"]["page_total"] == 7
    folders_full = kb_admin.wiki_folders(db, kb_id)
    full_by_id = {f["id"]: f for f in folders_full}
    assert full_by_id["f11"]["page_total"] == 4


def test_doc_branch_doc_total_subtree(db: Session) -> None:
    """2026-10-15：文档目录树节点 = 子树文档总数（含子孙目录）。"""
    from api.models.knowledge import DocFolder

    kb_id = "kb-doc-subtree"
    db.add(DocFolder(id="d1", kb_id=kb_id, name="根目录", parent_id=""))
    db.add(DocFolder(id="d11", kb_id=kb_id, name="子目录", parent_id="d1"))
    db.add(DocFolder(id="d12", kb_id=kb_id, name="子目录2", parent_id="d1"))
    db.commit()
    for fid, slugs in {"": ["doc-root"], "d1": ["doc-1", "doc-2"], "d11": ["doc-11"],
                       "d12": ["doc-12a", "doc-12b"]}.items():
        for s in slugs:
            db.add(KbDocument(id=s, kb_id=kb_id, file_name=f"{s}.docx", folder_id=fid))
    db.commit()

    folders = kb_admin.doc_folders(db, kb_id)
    by_id = {f["id"]: f for f in folders}
    assert by_id["d1"]["doc_total"] == 5, f"d1 子树文档数应 5（2 直属 + 1 + 2），实际 {by_id['d1']['doc_total']}"
    assert by_id["d11"]["doc_total"] == 1
    assert by_id["d12"]["doc_total"] == 2


def test_subtree_totals_cycle_safe(db: Session) -> None:
    """parent_id 成环（脏数据）不挂死：memo 占位防递归死循环。"""
    from api.services.kb_admin import _subtree_file_totals

    kb_id = "kb-cycle"
    db.add(WikiFolder(id="c1", kb_id=kb_id, name="环1", parent_id="c2"))
    db.add(WikiFolder(id="c2", kb_id=kb_id, name="环2", parent_id="c1"))
    db.commit()
    totals = _subtree_file_totals(db, kb_id, WikiFolder, {"c1": 2, "c2": 1})
    # 不抛异常；c1 = 直属2 + (c2 直属1 + c1[已占位0]) = 3
    assert totals["c1"] == 3
    assert totals["c2"] == 1


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


def test_wiki_logs_and_feedback(db: Session) -> None:
    kb = "kb-logfb"
    # 建页触发日志
    kb_admin.wiki_create_page(db, kb, {"title": "日志页", "content": "内容"}, "u1")
    logs = kb_admin.wiki_list_logs(db, kb)
    assert logs["total"] == 1
    assert logs["items"][0]["action"] == "page_create"
    assert logs["items"][0]["slug"] == "日志页"

    # 更新触发日志
    kb_admin.wiki_update_page(db, kb, "日志页", {"title": "改名"})
    logs2 = kb_admin.wiki_list_logs(db, kb)
    assert logs2["total"] == 2
    assert any(it["action"] == "page_update" for it in logs2["items"])

    # 反馈（默认状态 pending，对齐 WeKnora 三态）
    fb = kb_admin.wiki_submit_feedback(db, kb, "日志页", "u1", "issue", "内容有误")
    assert fb["status"] == "pending"
    fblist = kb_admin.wiki_list_feedback(db, kb, slug="日志页")
    assert fblist["total"] == 1
    assert fblist["items"][0]["feedback_type"] == "issue"

    # 更新反馈状态
    kb_admin.wiki_update_feedback_status(db, kb, fb["id"], "resolved")
    fblist2 = kb_admin.wiki_list_feedback(db, kb, slug="日志页")
    assert fblist2["items"][0]["status"] == "resolved"

    # 统计卡聚合（按状态/类型）
    stats = kb_admin.wiki_feedback_stats(db, kb)
    assert stats["total"] == 1
    assert stats["resolved"] == 1
    assert stats["pending"] == 0
    assert stats["question"] == 1  # issue 归入问题类
    assert stats["comment"] == 0


def test_wiki_index(db: Session) -> None:
    kb = "kb-index"
    f = kb_admin.wiki_create_folder(db, kb, {"name": "目录A"}, "u1")
    kb_admin.wiki_create_page(db, kb, {"title": "页1", "content": "内容", "folder_id": f["id"]}, "u1")
    kb_admin.wiki_create_page(db, kb, {"title": "页2", "content": "内容"}, "u1")

    idx = kb_admin.wiki_index(db, kb)
    assert idx["total_pages"] == 2
    assert idx["total_folders"] == 1
    assert len(idx["folder_tree"]) == 1
    assert idx["folder_tree"][0]["name"] == "目录A"
    assert idx["pages_by_type"].get("entity") == 2
    assert len(idx["recent_pages"]) == 2
    assert any(r["slug"] == "页1" and r["folder_id"] == f["id"] for r in idx["recent_pages"])


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