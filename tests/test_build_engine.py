"""构建引擎（KB 绑定服务编排中已发布的编排程序）测试。

覆盖：
- GET /api/v1/kbs/build-engines 只返回已发布（status=effective）的编排
- create_kb / update_kb 绑定 build_tape_id（服务层保存/回显）
- 绑定校验：编排不存在 / 未发布 → ValueError
- doc_process 上传链：KB 绑定构建引擎 → 建 kb_tape_run + Job 行（KbOrchestrationRunTask）
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base, get_db
from api.models.framework import Job
from api.models.orchestration import Tape, TapeStep
from api.services.kb_admin import create_kb, get_kb, update_kb
from api.main import app
import api.routers.kbs as kbs_router
import worker.tasks.doc_process as dp


@pytest.fixture()
def be_db():
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # 共享底层连接：TestClient 线程与主线程同一库
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


def _seed_tape(db, tape_id="tape-1", name="构建-基础原子版", status="effective") -> Tape:
    t = Tape(
        id=tape_id, tape_name=name, tape_label=name,
        tape_type="general", status=status, nodes=[], edges=[],
    )
    db.add(t)
    db.commit()
    return t


# ───────────────────────── 服务层：create/update kb 绑定 ─────────────────────────


def test_create_kb_with_build_engine(be_db) -> None:
    _seed_tape(be_db, "tape-a", "文档构建引擎A")
    kb = create_kb(be_db, "制度库", build_tape_id="tape-a")
    assert kb is not None
    assert kb.build_tape_id == "tape-a"
    got = get_kb(be_db, kb.id)
    assert got is not None and got.build_tape_id == "tape-a"


def test_create_kb_rejects_unpublished_engine(be_db) -> None:
    _seed_tape(be_db, "tape-draft", status="draft")
    with pytest.raises(ValueError, match="未发布"):
        create_kb(be_db, "库", build_tape_id="tape-draft")


def test_create_kb_rejects_missing_engine(be_db) -> None:
    with pytest.raises(ValueError, match="编排不存在"):
        create_kb(be_db, "库", build_tape_id="tape-nope")


def test_update_kb_build_engine(be_db) -> None:
    _seed_tape(be_db, "tape-a", "引擎A")
    _seed_tape(be_db, "tape-b", "引擎B")
    kb = create_kb(be_db, "库")
    assert kb is not None and kb.build_tape_id is None
    updated = update_kb(be_db, kb.id, {"build_tape_id": "tape-b"})
    assert updated is not None and updated.build_tape_id == "tape-b"
    cleared = update_kb(be_db, kb.id, {"build_tape_id": ""})
    assert cleared is not None and cleared.build_tape_id is None


# ───────────────────────── API：GET /kbs/build-engines ─────────────────────────


@pytest.fixture()
def be_client(be_db, monkeypatch):
    import types as _types

    from api.services.identity import Identity, encode_identity_cookie
    import api.middleware as mw

    app.dependency_overrides[get_db] = lambda: be_db
    monkeypatch.setattr(
        kbs_router, "decode_identity_cookie",
        lambda cookie: types.SimpleNamespace(user_id="admin", user_name="admin", team_name="ROOT"),
    )
    # middleware 守卫：受控路径需身份——mock 身份解析并带上登录 cookie
    monkeypatch.setattr(mw, "get_sessionmaker", lambda: type("SM", (), {"__call__": lambda s: be_db})())
    monkeypatch.setattr(mw, "get_settings", lambda: types.SimpleNamespace(AUTH_ADMIN_USERS="admin"))
    monkeypatch.setattr(mw, "decode_identity_cookie", lambda cookie: Identity(user_id="admin"))
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="admin", user_name="admin", team_name="ROOT")),
    )
    yield client
    app.dependency_overrides.clear()


def test_build_engines_only_published(be_client, be_db) -> None:
    _seed_tape(be_db, "tape-ok", "已发布引擎", status="effective")
    _seed_tape(be_db, "tape-draft", "草稿引擎", status="draft")
    _seed_tape(be_db, "tape-off", "下线引擎", status="offline")
    r = be_client.get("/api/v1/kbs/build-engines")
    assert r.status_code == 200
    data = r.json()["data"]
    ids = [it["id"] for it in data["items"]]
    assert ids == ["tape-ok"]
    assert data["items"][0]["tape_label"] == "已发布引擎"


# ───────────────────────── worker：上传链触发编排 ─────────────────────────


@pytest.fixture()
def chain_env(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    import api.db as db_mod

    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda: Session)

    sent: dict = {}

    class FakeCelery:
        def send_task(self, name, args, task_id, queue):
            sent["name"] = name
            sent["task_id"] = task_id
            sent["queue"] = queue

    import worker.celery_app as celery_mod

    monkeypatch.setattr(celery_mod, "celery_app", FakeCelery())
    yield Session, sent


def test_enqueue_build_engine_task_writes_job(chain_env) -> None:
    Session, sent = chain_env
    db = Session()
    _seed_tape(db, "tape-1", "文档构建引擎", status="effective")
    db.add(
        TapeStep(
            id="s1", tape_id="tape-1", step_inst="print", step_label="输出",
            step_config={"text": "hello"}, step_seq=0,
            pre_step_ids=[], next_step_ids=[],
        )
    )
    db.commit()
    db.close()

    ok = dp._enqueue_build_engine_task("kb-1", "doc-1", "tape-1")
    assert ok is True
    # 投递走 execute_modo_job（orch 队列），调度中枢按 Job 分派 KbOrchestrationRunTask
    assert sent["name"] == "worker.tasks.scheduler.execute_modo_job"
    assert sent["queue"] == "orch"
    assert sent["task_id"].startswith("ORCH_RUN_")

    db = Session()
    jobs = db.execute(select(Job)).scalars().all()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.task_class == "KbOrchestrationRunTask"
    assert job.queue_name == "orch"
    assert job.state == "PENDING"
    params = json.loads(job.task_params)
    assert params["env"] == "prod"  # 上传链数据在 prod，编排节点同环境读写
    assert params["runId"]
    from api.models.orchestration import TapeRun

    runs = db.execute(select(TapeRun)).scalars().all()
    assert len(runs) == 1
    assert runs[0].tape_id == "tape-1"
    assert runs[0].inputs == {
        "kb_id": "kb-1",
        "document_id": "doc-1",
        "doc_id": "doc-1",  # 兼容存量编排的 doc_id 变量名
        # 2026-10-15 编排输入契约（wiki构建-* 领域编排读 kid/doc_name/source_file）
        "kid": "doc-1",
        "doc_name": "",  # fixture 无 KbDocument 行 → file_name 空
        "source_file": "",
        "x_next_identity": "",  # 未传身份时为空串（上传链会带操作者 cookie）
        "limit": "",  # context 组装兜底：编排 def/print 可能引用的变量
        "only_codes": "",
        "build_summary": "",
        "language": "中文",
    }
    db.close()


def test_enqueue_build_engine_task_rejects_draft(chain_env) -> None:
    Session, _sent = chain_env
    db = Session()
    _seed_tape(db, "tape-draft", status="draft")
    db.commit()
    db.close()
    assert dp._enqueue_build_engine_task("kb-1", "doc-1", "tape-draft") is False


def test_kb_pipeline_flags_reads_tape(chain_env) -> None:
    Session, _sent = chain_env
    db = Session()
    from api.models.knowledge import KbDatasource

    _seed_tape(db, "tape-1", status="effective")
    db.add(KbDatasource(id="kb-1", name="制度库", build_tape_id="tape-1"))
    db.commit()

    flags = dp._kb_pipeline_flags("kb-1")
    assert flags["tape"] == "tape-1"
    db.close()


def test_kb_pipeline_flags_doc_overrides_kb(chain_env) -> None:
    """文档级构建引擎优先于 KB 默认（上传/选表重选覆盖）。"""
    Session, _sent = chain_env
    db = Session()
    from api.models.knowledge import KbDatasource, KbDocument

    _seed_tape(db, "tape-kb", status="effective")
    _seed_tape(db, "tape-doc", status="effective")
    db.add(KbDatasource(id="kb-1", name="制度库", build_tape_id="tape-kb"))
    db.add(KbDocument(id="doc-1", kb_id="kb-1", file_name="a.pdf", build_tape_id="tape-doc"))
    db.add(KbDocument(id="doc-2", kb_id="kb-1", file_name="b.pdf"))
    db.commit()

    # 文档覆盖：tape-doc 生效
    assert dp._kb_pipeline_flags("kb-1", "doc-1")["tape"] == "tape-doc"
    # 未覆盖：回落 KB 默认
    assert dp._kb_pipeline_flags("kb-1", "doc-2")["tape"] == "tape-kb"
    db.close()


def test_upload_accepts_doc_build_engine(be_client, be_db, monkeypatch) -> None:
    """上传端点带 build_tape_id → kb_document.build_tape_id 落库。"""
    _seed_tape(be_db, "tape-up", "上传引擎", status="effective")

    from api.services.kb_admin import create_kb

    # personal 归属 + admin 属主，绕开 system 库的管理员角色门槛（测试库无角色数据）
    kb = create_kb(be_db, "构建库", scope="personal", owner_user_id="admin", created_by="admin", build_tape_id=None)
    import io

    r = be_client.post(
        f"/api/v1/kbs/{kb.id}/documents/upload",
        data={"build_tape_id": "tape-up"},
        files={"file": ("d.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")},
    )
    assert r.status_code == 200, r.text
    from api.models.knowledge import KbDocument

    doc = be_db.execute(select(KbDocument).where(KbDocument.kb_id == kb.id)).scalars().first()
    assert doc is not None and doc.build_tape_id == "tape-up"


def test_metric_wiki_lib_build_page() -> None:
    """任务库 metric_wiki_lib.build_page 产出与 wr.create_wiki_pages_batch 契约一致。"""
    import sys

    lib_dir = str(Path(__file__).resolve().parents[1] / "worker" / "builtin_engine" / "scripts")
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
    import metric_wiki_lib as mwl  # noqa: PLC0415

    page = mwl.build_page(
        {
            "指标编码": "A001",
            "指标名称": "月营收",
            "指标分类-I": "财务",
            "指标分类-II": "利润",
            "业务口径": "主营业务收入合计",
            "技术口径": "日汇聚总",
        },
        "SELECT SUM(amt) FROM t",
        "folder-2",
        {"依赖表": ["dim_a", "fact_b"], "说明": "按日聚合并去重"},
        document_id="doc-x",  # 2026-10-12：source_refs 必须带文档引用（删除文档时识别）
    )
    assert page["title"] == "月营收"
    assert page["slug"] == "A001_月营收"
    assert page["page_type"] == "entity"
    assert page["folder_id"] == "folder-2"
    assert page["source_refs"] == ["doc-x|doc-x"]
    assert "## 业务口径" in page["content"] and "主营业务收入合计" in page["content"]
    assert "```sql" in page["content"] and "SELECT SUM(amt) FROM t" in page["content"]
    assert "依赖表" in page["content"]
    assert page["summary"]
    # 无 document_id 时 source_refs 为空（兼容历史调用）
    assert mwl.build_page({"指标编码": "A001"}, "", "", {})["source_refs"] == []


# ───────────────────────── 编排分类（2026-10-13 左右结构）─────────────────────────


def test_tape_category_roundtrip(be_db) -> None:
    """编排分类：create/list 过滤/serialize 输出/list_tape_categories 聚合。"""
    from api.services.orchestration import (
        create_tape,
        list_tape_categories,
        list_tapes,
        serialize_tape,
    )

    # create 带分类（默认兜底「通用」）
    t1 = create_tape(be_db, {"tape_name": "清洗流水线", "category": "数据治理"}, "u1")
    t2 = create_tape(be_db, {"tape_name": "摘要生成"}, "u1")  # 不带 category → 通用
    be_db.commit()

    assert serialize_tape(t1)["category"] == "数据治理"
    assert serialize_tape(t2)["category"] == "通用"

    # list_tapes 按分类过滤
    r = list_tapes(be_db, category="数据治理")
    assert r["total"] == 1 and r["items"][0]["id"] == t1.id
    r = list_tapes(be_db, category="通用")
    assert r["total"] == 1 and r["items"][0]["id"] == t2.id
    r = list_tapes(be_db, category="不存在")
    assert r["total"] == 0

    # 分类聚合（每类计数 + effective 计数）
    cats = {c["category"]: c for c in list_tape_categories(be_db)}
    assert cats["数据治理"]["total"] == 1 and cats["数据治理"]["effective"] == 0
    assert cats["通用"]["total"] == 1

    # update 改分类
    from api.services.orchestration import update_tape

    update_tape(be_db, t2.id, {"category": "智能问答"})
    assert serialize_tape(t2)["category"] == "智能问答"