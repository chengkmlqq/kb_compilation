"""Doc folder service tests — multi-level document directory tree.

决策（2026-10-10）：单归属 / 递归子树浏览 / 非空禁删 / 仅浏览不参与检索。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.services.kb_admin import (
    create_document,
    create_kb,
    doc_branch,
    doc_create_folder,
    doc_delete_folder,
    doc_folders,
    doc_move_documents,
    doc_update_folder,
    list_documents,
)


@pytest.fixture()
def kb_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


def _seed(db, n_docs: int = 3):
    """一个 KB + N 个文档（folder_id 为空 = 根层级）。"""
    kb = create_kb(db, "文档目录库")
    ids = []
    for i in range(n_docs):
        d = create_document(db, kb.id, file_name=f"doc-{i}.pdf", file_size=100 + i)
        ids.append(d.id)
    return kb.id, ids


def test_doc_folder_crud(kb_db):
    kb_id, _ = _seed(kb_db)

    # 建根目录 + 子目录
    root = doc_create_folder(kb_db, kb_id, {"name": "制度"})
    child = doc_create_folder(kb_db, kb_id, {"name": "办法", "parent_id": root["id"]})
    assert child["parent_id"] == root["id"]

    # 同级同名唯一
    with pytest.raises(ValueError, match="同名"):
        doc_create_folder(kb_db, kb_id, {"name": "制度"})

    # 全量目录元数据
    folders = doc_folders(kb_db, kb_id)
    assert len(folders) == 2
    by_id = {f["id"]: f for f in folders}
    assert by_id[root["id"]]["child_count"] == 1  # 子目录算一个直接子项

    # 重命名
    renamed = doc_update_folder(kb_db, kb_id, child["id"], {"name": "规定"})
    assert renamed["name"] == "规定"

    # 移动目录到自身 = 拒绝
    with pytest.raises(ValueError, match="自身"):
        doc_update_folder(kb_db, kb_id, root["id"], {"parent_id": root["id"]})

    # 父目录不存在 = 拒绝
    with pytest.raises(ValueError, match="不存在"):
        doc_create_folder(kb_db, kb_id, {"name": "孤儿", "parent_id": "nope"})

    # 防环：child 是 root 的子孙，root 不能移到 child 下
    with pytest.raises(ValueError, match="子目录"):
        doc_update_folder(kb_db, kb_id, root["id"], {"parent_id": child["id"]})

    # 删除非空（有子目录）拒绝
    with pytest.raises(ValueError, match="子目录"):
        doc_delete_folder(kb_db, kb_id, root["id"])

    # 删除空目录成功
    ok = doc_delete_folder(kb_db, kb_id, child["id"])
    assert ok["deleted"] is True


def test_doc_folder_delete_blocks_docs(kb_db):
    kb_id, doc_ids = _seed(kb_db, n_docs=2)
    f = doc_create_folder(kb_db, kb_id, {"name": "有文档"})
    doc_move_documents(kb_db, kb_id, doc_ids, f["id"])

    with pytest.raises(ValueError, match="文档"):
        doc_delete_folder(kb_db, kb_id, f["id"])

    # 移走后可删
    doc_move_documents(kb_db, kb_id, doc_ids, "")
    assert doc_delete_folder(kb_db, kb_id, f["id"])["deleted"] is True


def test_doc_move_and_recursive_list(kb_db):
    kb_id, doc_ids = _seed(kb_db, n_docs=4)
    root = doc_create_folder(kb_db, kb_id, {"name": "财务"})
    child = doc_create_folder(kb_db, kb_id, {"name": "报销", "parent_id": root["id"]})

    # 移 2 个到 child、1 个到 root、1 个留在根
    doc_move_documents(kb_db, kb_id, doc_ids[:2], child["id"])
    doc_move_documents(kb_db, kb_id, [doc_ids[2]], root["id"])

    # 递归子树：root 可见 child 下的文档
    data_root = list_documents(kb_db, kb_id, folder_id=root["id"])
    assert data_root["total"] == 3
    data_child = list_documents(kb_db, kb_id, folder_id=child["id"])
    assert data_child["total"] == 2

    # 无 folder 过滤 = 全部
    data_all = list_documents(kb_db, kb_id)
    assert data_all["total"] == 4

    # 移动的文档 folder_id 已落库
    items = {it["id"]: it for it in list_documents(kb_db, kb_id)["items"]}
    assert items[doc_ids[0]]["folder_id"] == child["id"]

    # 跨 KB 文档拒绝
    other_kb, other_docs = _seed(kb_db, n_docs=1)
    with pytest.raises(ValueError, match="不属于"):
        doc_move_documents(kb_db, kb_id, other_docs, child["id"])
    del other_kb


def test_doc_branch_lazy(kb_db):
    kb_id, doc_ids = _seed(kb_db, n_docs=5)
    root = doc_create_folder(kb_db, kb_id, {"name": "根目录"})
    child = doc_create_folder(kb_db, kb_id, {"name": "子目录", "parent_id": root["id"]})
    doc_move_documents(kb_db, kb_id, doc_ids[:3], child["id"])

    # 根分支：1 个子目录（「根目录」节点）+ 2 个根级文档
    branch = doc_branch(kb_db, kb_id, "")
    assert branch["total_folders"] == 1
    assert branch["total_docs"] == 2
    # 「根目录」节点的直接子项 = 1（它的直接子目录「子目录」）；
    # 3 个文档在子目录下，不算根目录节点的直接子项。
    assert branch["folders"][0]["name"] == "根目录"
    assert branch["folders"][0]["child_count"] == 1

    # 子目录分支：0 目录 + 3 文档
    sub = doc_branch(kb_db, kb_id, child["id"])
    assert sub["total_docs"] == 3
    assert sub["folders"] == []