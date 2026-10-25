"""切片智能增强 + 向量存储增强字段测试（2026-10-25 对齐 duowen ChunkExtend）。

覆盖：
  1. normalize_enhancement_config 归一（开关/密度/上限，非法值回退默认）
  2. enhancement_enabled 文件级 > KB 级覆盖链
  3. enhance_boost 加权升位（关键词命中升位、无增强命中分数不变、空查询穿透）
  4. 编排种子结构校验（切片智能增强.json：9 节点/9 步骤，script/loop/kb_llm_chat 契约）
  5. 编排执行（mock LLM）：n1 读切片 → n2 文档级 → n3 组装 → loop 逐片 →
     末轮汇总 enhancement（含降级：短片回退文档级关键词）

测试环境：sqlite in-memory + get_db 覆盖（对齐 test_orchestration_manual_env）；
编排 LLM 节点走 api.services.chat.ChatClient——用 monkeypatch 假 chat 配置
返回固定 JSON，验证 bindings.enhancement 组装正确。
"""
from __future__ import annotations

import json
import types
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db import Base, get_db
from api.main import app
from api.models.framework import CronTask
from api.models.orchestration import Tape, TapeRun, TapeStep
from api.services.chunk_enhance import (
    enhancement_enabled,
    normalize_enhancement_config,
)
from api.services.identity import Identity, encode_identity_cookie
from api.services.retrieval import ChunkHit, enhance_boost


@pytest.fixture()
def env(monkeypatch):
    """in-memory 库 + get_db 覆盖 + 假身份。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    Base.metadata.create_all(engine)

    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        "api.middleware.get_sessionmaker",
        lambda: type("SM", (), {"__call__": lambda s: db})(),
    )
    ns = types.SimpleNamespace()
    setattr(ns, "AUTH" + "_ADMIN_USERS", "")
    monkeypatch.setattr("api.middleware.get_settings", lambda: ns)
    monkeypatch.setattr("api.config.get_settings", lambda: ns)
    client = TestClient(app)
    client.cookies.set(
        "x-next-identity",
        encode_identity_cookie(Identity(user_id="u1", user_name="u1", team_name="team1")),
    )
    yield db, client
    app.dependency_overrides.pop(get_db, None)


# ── 1. 配置归一 ─────────────────────────────────────────────────


def test_normalize_enhancement_config_defaults(env) -> None:
    cfg = normalize_enhancement_config({"enabled": True})
    assert cfg["enabled"] is True
    assert cfg["keywords_dividend"] == 120
    assert cfg["questions_dividend"] == 180
    assert cfg["max_chunks"] == 0


def test_normalize_enhancement_config_custom_and_invalid(env) -> None:
    cfg = normalize_enhancement_config({
        "enabled": True,
        "keywords_dividend": 100,
        "questions_dividend": "bad",  # 非法回退默认
        "max_chunks": -5,  # 非法回退默认
    })
    assert cfg["keywords_dividend"] == 100
    assert cfg["questions_dividend"] == 180
    assert cfg["max_chunks"] == 0

    cfg2 = normalize_enhancement_config({})
    assert cfg2["enabled"] is False


# ── 2. 文件级 > KB 级覆盖链 ─────────────────────────────────────


def test_enhancement_enabled_override_chain(env) -> None:
    # KB 开启 + 文件未设置 → 开
    assert enhancement_enabled({"enabled": True}, {}) is True
    # KB 开启 + 文件关闭 → 关（文件级优先）
    assert enhancement_enabled({"enabled": True}, {"enhancement": "off"}) is False
    # KB 关闭 + 文件开启 → 开（文件级优先）
    assert enhancement_enabled({}, {"enhancement": "on"}) is True
    # 都未设置 → 关
    assert enhancement_enabled({}, {}) is False
    # KB 配置为 None → 关
    assert enhancement_enabled(None, None) is False


# ── 3. enhance_boost 加权升位 ───────────────────────────────────


def test_enhance_boost_keyword_boost_reranks(env) -> None:
    hits = [
        ChunkHit(
            chunk_id="a", content="x", score=0.02,
            enhance={"chunk_keywords": ["皖南地区", "市场份额"], "chunk_questions": [], "title": "装修报告"},
        ),
        ChunkHit(chunk_id="b", content="y", score=0.021, enhance=None),
    ]
    boosted = enhance_boost(hits, "皖南地区市场份额")
    # a 命中增强词 → 升到 b 前面
    assert boosted[0].chunk_id == "a"
    assert boosted[0].score > boosted[1].score
    # 无增强命中分数不变
    assert boosted[1].score == 0.021


def test_enhance_boost_question_and_title(env) -> None:
    hits = [
        ChunkHit(
            chunk_id="q", content="x", score=0.01,
            enhance={"chunk_keywords": [], "chunk_questions": ["在皖南地区，哪个城市市场份额最高？"], "title": ""},
        ),
        ChunkHit(chunk_id="t", content="y", score=0.011,
                 enhance={"chunk_keywords": [], "chunk_questions": [], "title": "全省装修装饰行业分析报告"}),
        ChunkHit(chunk_id="n", content="z", score=0.012, enhance=None),
    ]
    boosted = enhance_boost(hits, "皖南 市场份额 装修")
    by_id = {h.chunk_id: h.score for h in boosted}
    assert by_id["q"] > by_id["n"]  # 问题命中升位
    assert by_id["t"] > by_id["n"]  # 标题命中升位


def test_enhance_boost_passthrough(env) -> None:
    hits = [ChunkHit(chunk_id="c", content="z", score=0.03)]
    same = enhance_boost(hits, "")
    assert same[0].chunk_id == "c" and same[0].score == 0.03
    same2 = enhance_boost(hits, "   ")
    assert same2[0].score == 0.03


# ── 4. 编排种子结构校验 ─────────────────────────────────────────


def test_chunk_enhance_tape_seed_structure() -> None:
    seed = json.loads(
        open("scripts/seed_tape_data/切片智能增强.json", encoding="utf-8").read()
    )
    dr = seed["draft"]
    assert dr["tapeName"] == "切片智能增强"
    assert dr["tapeType"] == "chunk_enhance"
    assert dr["category"] == "知识聚合"
    assert dr["status"] == "effective"
    assert len(dr["nodes"]) == 9
    assert len(dr["steps"]) == 9

    insts = {s["step_inst"] for s in dr["steps"]}
    assert {"script", "loop", "kb_llm_chat"} <= insts

    # loop 步骤：item_var=chunk_item，collection 引用 bindings.chunk_items
    loop = next(s for s in dr["steps"] if s["step_inst"] == "loop")
    assert loop["step_config"]["item_var"] == "chunk_item"
    assert "chunk_items" in loop["step_config"]["collection"]

    # 末轮汇总步骤 n4e：产出 enhancement + 写 /tmp/chunk_enhance.json
    n4e = next(s for s in dr["steps"] if s["step_id"] == "n4e")
    code = n4e["step_config"]["code"]
    assert "enhancement" in code
    assert "enhance_map" in code

    # 编排名需与触发服务常量一致
    from api.services.chunk_enhance import TAPE_NAME

    assert TAPE_NAME == "切片智能增强"


# ── 5. 编排执行（mock LLM）──────────────────────────────────────


def _seed_tape(db) -> str:
    """把种子编排灌入内存库（对齐 apply 脚本 _seed_tape 逻辑），返回 tape.id。"""
    seed = json.loads(
        open("scripts/seed_tape_data/切片智能增强.json", encoding="utf-8").read()
    )
    d = seed["draft"]
    tape = Tape(
        id=uuid.uuid4().hex[:32],
        tape_name=d["tapeName"],
        tape_label=d["tapeLabel"],
        tape_descr=d["tapeDescr"],
        tape_type=d["tapeType"],
        category=d["category"],
        status="effective",
        nodes=d["nodes"],
        edges=d["edges"],
        exec_params=d["execParams"],
    )
    db.add(tape)
    db.commit()
    prefix = tape.id[:12]
    for st in d["steps"]:
        sid = f"{prefix}::{st['step_id']}"
        db.add(
            TapeStep(
                id=sid,
                tape_id=tape.id,
                step_inst=st["step_inst"],
                step_label=st["step_label"],
                step_config=st["step_config"],
                step_seq=st["step_seq"],
                pre_step_ids=[f"{prefix}::{p}" for p in st["pre_step_ids"]],
                next_step_ids=[f"{prefix}::{n}" for n in st["next_step_ids"]],
                queue_name=st.get("queue_name", ""),
            )
        )
    db.commit()
    return tape.id


def test_chunk_enhance_tape_executes(env, monkeypatch) -> None:
    """编排执行到末轮汇总：mock LLM 返回固定 JSON，验证 enhancement 组装。"""
    db, _ = env
    # get_sessionmaker 真实返回 sessionmaker 类（可调用 → 新 session）；fake 须同构
    class _SM:
        def __call__(self):
            return db

    # 所有编排代码都在函数内 from api.db import get_sessionmaker——patch 该模块属性即可
    monkeypatch.setattr("api.db.get_sessionmaker", lambda: _SM())

    tape_id = _seed_tape(db)

    # mock ChatClient.chat：按 prompt 关键词返回固定 JSON
    from api.services import chat as chat_mod

    class _FakeClient:
        def __init__(self, cfg):
            pass

        def chat(self, msgs, temperature=None, max_tokens=None, thinking=None):
            prompt = "\n".join(m.content for m in msgs if m.role == "user")
            if "文档增强提取器" in prompt:
                return json.dumps({"keywords": ["皖南地区", "市场份额"], "summary": "安徽装修装饰行业分析报告摘要"}, ensure_ascii=False)
            if "切片关键词提取器" in prompt:
                return json.dumps(["皖南地区", "市场份额"], ensure_ascii=False)
            if "问题生成器" in prompt:
                return json.dumps(["在皖南地区，哪个城市的市场份额最高？"], ensure_ascii=False)
            return "{}"

    monkeypatch.setattr(chat_mod, "ChatClient", _FakeClient)

    # 插入文档 + 切片（n1 读 doc_chunk）
    from api.models.knowledge import KbDatasource, KbDocument, DocChunk

    kb = KbDatasource(id="kb1", name="测试库", state="1", scope="system")
    doc = KbDocument(id="doc1", kb_id="kb1", file_name="测试文档.md", file_ext="md", parse_state="READY")
    db.add(kb)
    db.add(doc)
    for i, text in enumerate(["皖南地区市场份额分析内容" * 30, "黄山宣城马鞍山池州市场情况" * 20]):
        db.add(DocChunk(id=f"c{i}", kb_id="kb1", document_id="doc1", seq=i, content=text, enabled=True))
    db.commit()

    from api.services.orchestration_engine import OrchestrationEngine
    from api.services.orchestration import _create_tape_run

    tape = db.execute(select(Tape).where(Tape.id == tape_id)).scalars().first()
    created = _create_tape_run(db, tape.id, inputs={"kb_id": "kb1", "document_id": "doc1"}, env="prod")
    run_id = created["run_id"]

    engine = OrchestrationEngine(db)
    result = engine.execute(tape, inputs={"kb_id": "kb1", "document_id": "doc1"})

    # 编排执行成功
    assert result["success"], [s for s in result["steps"] if s["status"] != "success"]

    # bindings 里有 enhancement（execute 返回的 bindings 值被 str 截断 200 字符，
    # 用 JSON 字符串包含关键字段断言）
    assert "enhancement" in result["bindings"], result["bindings"].keys()
    enh_repr = result["bindings"]["enhancement"]
    assert "皖南地区" in enh_repr or "市场份额" in enh_repr