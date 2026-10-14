"""编排分类独立表（kb_tape_category）测试 — upsert / 排序 / 计数 / 回退。

迁移 0019 起，编排管理与编排模板左侧目录树分类定义改存 kb_tape_category：
- ensure_tape_category 幂等 upsert（新分类自动补录）
- list_tape_categories / list_template_categories 读表按 sort_num 排序，
  计数仍从业务表动态聚合；分类表为空时回退为按业务表现有分类聚合
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models import orchestration as om  # noqa: F401  (register tables)
from api.services import orchestration as svc


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_ensure_tape_category_creates(db) -> None:
    name = svc.ensure_tape_category(db, "数据处理", cat_type="tape", user_id="u1")
    assert name == "数据处理"
    row = db.query(om.TapeCategory).filter(om.TapeCategory.category == "数据处理").first()
    assert row is not None
    assert row.cat_type == "tape"
    assert row.state == "1"


def test_ensure_tape_category_idempotent(db) -> None:
    svc.ensure_tape_category(db, "数据处理", cat_type="tape")
    svc.ensure_tape_category(db, "数据处理", cat_type="tape")
    assert db.query(om.TapeCategory).filter(om.TapeCategory.category == "数据处理").count() == 1


def test_ensure_tape_category_merges_type_to_both(db) -> None:
    svc.ensure_tape_category(db, "共享", cat_type="tape")
    svc.ensure_tape_category(db, "共享", cat_type="template")
    row = db.query(om.TapeCategory).filter(om.TapeCategory.category == "共享").first()
    assert row.cat_type == "both"


def test_ensure_tape_category_blank_defaults(db) -> None:
    assert svc.ensure_tape_category(db, "", cat_type="tape") == "通用"
    assert svc.ensure_tape_category(db, None, cat_type="template") == "通用"


def test_create_tape_registers_new_category(db) -> None:
    t = svc.create_tape(db, {"tape_name": "t1", "category": "新分类"}, user_id="u1")
    assert t.category == "新分类"
    assert db.query(om.TapeCategory).filter(om.TapeCategory.category == "新分类").count() == 1


def test_create_tape_template_registers_new_category(db) -> None:
    t = svc.create_tape_template(db, {"name": "tpl1", "category": "模板类"}, user_id="u1")
    assert t.category == "模板类"
    assert db.query(om.TapeCategory).filter(om.TapeCategory.category == "模板类").count() == 1


def test_list_categories_sorted_by_sort_num(db) -> None:
    db.add(om.TapeCategory(id="c1", category="Z类", label="Z类", cat_type="tape", sort_num=2, state="1"))
    db.add(om.TapeCategory(id="c2", category="A类", label="A类", cat_type="tape", sort_num=1, state="1"))
    db.commit()
    names = [c["category"] for c in svc.list_tape_categories(db)]
    assert names == ["A类", "Z类"]  # 按 sort_num 排，非字典序


def test_list_categories_counts_from_tape(db) -> None:
    db.add(om.TapeCategory(id="c1", category="通用", label="通用", cat_type="tape", sort_num=0, state="1"))
    db.add(om.Tape(id="t1", tape_name="a", category="通用", status="effective"))
    db.add(om.Tape(id="t2", tape_name="b", category="通用", status="draft"))
    db.add(om.Tape(id="t3", tape_name="c", category="通用", status="draft"))
    db.commit()
    cats = {c["category"]: c for c in svc.list_tape_categories(db)}
    assert cats["通用"]["total"] == 3
    assert cats["通用"]["effective"] == 1


def test_list_categories_falls_back_when_table_empty(db) -> None:
    """迁移未跑（分类表为空）时回退为按业务表现有分类聚合。"""
    db.add(om.Tape(id="t1", tape_name="a", category="旧分类", status="effective"))
    db.commit()
    cats = {c["category"]: c for c in svc.list_tape_categories(db)}
    assert "旧分类" in cats
    assert cats["旧分类"]["total"] == 1


def test_list_categories_includes_data_only_category(db) -> None:
    """分类表没有但数据里有的分类（历史数据）兜底出现在结果中。"""
    db.add(om.TapeCategory(id="c1", category="通用", label="通用", cat_type="tape", sort_num=0, state="1"))
    db.add(om.Tape(id="t1", tape_name="a", category="历史遗留", status="draft"))
    db.commit()
    names = [c["category"] for c in svc.list_tape_categories(db)]
    assert "通用" in names and "历史遗留" in names


def test_list_categories_filters_by_cat_type(db) -> None:
    db.add(om.TapeCategory(id="c1", category="仅编排", cat_type="tape", sort_num=0, state="1"))
    db.add(om.TapeCategory(id="c2", category="仅模板", cat_type="template", sort_num=1, state="1"))
    db.add(om.TapeCategory(id="c3", category="公用", cat_type="both", sort_num=2, state="1"))
    db.commit()
    tape_names = [c["category"] for c in svc.list_tape_categories(db)]
    tpl_names = [c["category"] for c in svc.list_template_categories(db)]
    assert tape_names == ["仅编排", "公用"]
    assert tpl_names == ["仅模板", "公用"]


def test_list_categories_excludes_disabled(db) -> None:
    db.add(om.TapeCategory(id="c1", category="启用", cat_type="tape", sort_num=0, state="1"))
    db.add(om.TapeCategory(id="c2", category="停用", cat_type="tape", sort_num=1, state="0"))
    db.commit()
    names = [c["category"] for c in svc.list_tape_categories(db)]
    assert "启用" in names and "停用" not in names


def test_apply_tape_template_registers_category(db) -> None:
    tpl = svc.create_tape_template(db, {"name": "tpl", "category": "应用类"}, user_id="u1")
    tape = svc.apply_tape_template(db, tpl.id, {"tape_name": "from_tpl"}, user_id="u1")
    assert tape.category == "应用类"