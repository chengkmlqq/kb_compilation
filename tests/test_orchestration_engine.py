"""编排引擎细粒度基础原子测试（2026-10-12）。

覆盖：
- ATOM_MAP 注册 4 个新原子（kb_read_doc / kb_llm_chat / wiki_upsert_page / wiki_rebuild_links）
- KbReadDocStep：读文档切片写 bindings
- WikiUpsertPageStep：幂等建页（create/update/skip_empty）
- WikiRebuildLinksStep：缺 kb_id 失败
- wiki 构建模板：节点/边结构合法、含全部基础原子
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.services.orchestration_engine import (
    ATOM_MAP,
    KbReadDocStep,
    TapeRuntimeContext,
    WikiEnsureFolderStep,
    WikiUpsertPageStep,
)
from api.models.knowledge import DocChunk


@pytest.fixture()
def orch_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add_all(
        [
            DocChunk(id="c1", kb_id="kb1", document_id="doc1", seq=0, content="第一块内容"),
            DocChunk(id="c2", kb_id="kb1", document_id="doc1", seq=1, content="第二块内容"),
            DocChunk(id="c3", kb_id="kb1", document_id="doc2", seq=0, content="另一文档"),
        ]
    )
    db.commit()
    yield db
    db.close()


def test_atom_map_registered() -> None:
    for inst in ("kb_read_doc", "kb_read_table", "kb_llm_chat", "wiki_upsert_page",
                 "kb_write_doc_summary", "wiki_rebuild_links",
                 "wiki_ensure_folder", "long_sentence_extract", "keyword_extract",
                 "agent_run"):
        assert inst in ATOM_MAP, f"{inst} 未注册到 ATOM_MAP"


def test_kb_write_doc_summary_writes_document(orch_db, monkeypatch) -> None:
    """kb_write_doc_summary：把 final_summary 写入 kb_document.summary/summary_status。"""
    import api.db
    from api.models.knowledge import KbDocument
    from api.services.orchestration_engine import KbWriteDocSummaryStep

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    orch_db.add(KbDocument(id="doc1", kb_id="kb1", file_name="测试.docx", parse_state="READY"))
    orch_db.commit()

    ctx = TapeRuntimeContext({})
    ctx.bindings["final_summary"] = "这是一段干净的中文摘要。"
    step = KbWriteDocSummaryStep(
        "n5", "写文档摘要", "kb_write_doc_summary",
        {"document_id": "{{document_id}}", "summary_var": "final_summary"},
    )
    ctx.bindings["document_id"] = "doc1"
    resp = step.handle(ctx)
    assert resp.success
    doc = orch_db.query(KbDocument).filter(KbDocument.id == "doc1").first()
    assert doc.summary == "这是一段干净的中文摘要。"
    assert doc.summary_status == "READY"
    assert doc.summary_error is None


def test_kb_write_doc_summary_skip_empty(orch_db, monkeypatch) -> None:
    """skip_empty：final_summary 为空时跳过（success + skipped）不写库。"""
    import api.db
    from api.services.orchestration_engine import KbWriteDocSummaryStep

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    ctx = TapeRuntimeContext({})
    ctx.bindings["final_summary"] = ""
    step = KbWriteDocSummaryStep(
        "n5", "写文档摘要", "kb_write_doc_summary",
        {"document_id": "doc1", "summary_var": "final_summary", "skip_empty": True},
    )
    resp = step.handle(ctx)
    assert resp.success
    assert resp.body.get("skipped") is True


def test_kb_write_doc_summary_missing_doc_fails(orch_db, monkeypatch) -> None:
    """文档不存在时失败。"""
    import api.db
    from api.services.orchestration_engine import KbWriteDocSummaryStep

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    ctx = TapeRuntimeContext({})
    ctx.bindings["final_summary"] = "摘要"
    step = KbWriteDocSummaryStep(
        "n5", "写文档摘要", "kb_write_doc_summary",
        {"document_id": "doc-none", "summary_var": "final_summary"},
    )
    resp = step.handle(ctx)
    assert not resp.success


def test_kb_read_doc_writes_bindings(orch_db, monkeypatch) -> None:
    import api.db

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    step = KbReadDocStep("n1", "读切片", "kb_read_doc", {"document_id": "doc1"})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success
    chunks = ctx.bindings.get("chunks")
    assert chunks is not None and len(chunks) == 2
    assert chunks[0]["content"] == "第一块内容"
    assert "set:chunks" in resp.body
    # 2026-10-14：文档链路注入 KB 摘要模型（fixture 无 KB 行 → 空串兜底，不失败）
    assert ctx.bindings.get("summary_model_id") == ""


def test_kb_read_doc_injects_kb_summary_model(orch_db, monkeypatch) -> None:
    """kb_read_doc 读 KB 的 summary_model_id 注入 bindings（文档链路编排用 KB 模型）。"""
    import api.db
    from api.models.knowledge import KbDatasource, KbDocument

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    orch_db.add(KbDatasource(id="kb1", name="测试库", summary_model_id="mdl_summary_x"))
    orch_db.add(KbDocument(id="doc1", kb_id="kb1", file_name="测试.docx", parse_state="READY"))
    orch_db.commit()

    step = KbReadDocStep("n1", "读切片", "kb_read_doc", {"document_id": "doc1"})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success
    assert ctx.bindings.get("summary_model_id") == "mdl_summary_x"


def test_kb_read_doc_missing_doc_fails(orch_db, monkeypatch) -> None:
    import api.db

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    step = KbReadDocStep("n1", "读切片", "kb_read_doc", {"document_id": "doc-none"})
    resp = step.handle(TapeRuntimeContext({}))
    assert not resp.success


def test_kb_read_doc_max_chunks(orch_db, monkeypatch) -> None:
    import api.db

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    step = KbReadDocStep("n1", "读切片", "kb_read_doc", {"document_id": "doc1", "max_chunks": 1})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success and len(ctx.bindings["chunks"]) == 1


def test_kb_llm_chat_skip_empty(monkeypatch) -> None:
    """kg_llm_chat skip_empty：空 prompt 跳过（success + skipped）不调 LLM；非空正常调。"""
    from api.services.orchestration_engine import KbLlmChatStep

    called = []

    def fake_chat(cfg, messages, temperature=None, max_tokens=None, thinking=None):
        called.append(messages[-1].content)
        return "摘要结果"

    import api.services.chat as chat_mod
    chat_mod.ChatClient = lambda cfg: type("FC", (), {"chat": lambda self, m, **k: fake_chat(cfg, m, **k)})()
    chat_mod.load_chat_config = lambda db=None, model_id=None: {"base_url": "http://f", "api_key": "k", "model": "m"}

    # skip_empty=True + 空 prompt → 跳过不调 LLM
    step = KbLlmChatStep("n7", "合并", "kb_llm_chat",
                         {"prompt": "{{reduce_prompt}}", "output_var": "out",
                          "skip_empty": True, "model": "test-model"})
    resp = step.handle(TapeRuntimeContext({"reduce_prompt": ""}))
    assert resp.success and resp.body.get("skipped") is True
    assert len(called) == 0, "空 prompt + skip_empty 不应调 LLM"

    # 非空 prompt → 正常调用
    ctx = TapeRuntimeContext({"reduce_prompt": "请合并"})
    resp2 = step.handle(ctx)
    assert resp2.success and ctx.bindings["out"] == "摘要结果"
    assert len(called) == 1

    # 无 skip_empty + 空 prompt → fail
    step3 = KbLlmChatStep("n7", "合并", "kb_llm_chat", {"prompt": "{{x}}", "output_var": "out"})
    resp3 = step3.handle(TapeRuntimeContext({"x": ""}))
    assert not resp3.success


def test_kb_llm_chat_invalid_json_retry_then_skip(monkeypatch) -> None:
    """内容级容错：json_mode 非 JSON 按 invalid_retries 重试，耗尽后 skip_on_invalid
    跳过（success+skipped）且清空 output_var，不拖垮整条 run（2026-10-29）。"""
    from api.services.orchestration_engine import KbLlmChatStep

    import api.services.chat as chat_mod

    responses = [
        "我们被要求以知识库wiki编写专家……（prompt 回显）",
        "还是非 JSON 的废话",
        "{\"title\": \"结算指标\", \"content\": \"结算指标是……\"}",
    ]
    called = []

    def fake_chat(cfg, messages, temperature=None, max_tokens=None, thinking=None):
        called.append(messages[-1].content)
        return responses.pop(0)

    chat_mod.ChatClient = lambda cfg: type("FC", (), {"chat": lambda self, m, **k: fake_chat(cfg, m, **k)})()
    chat_mod.load_chat_config = lambda db=None, model_id=None: {"base_url": "http://f", "api_key": "k", "model": "m"}

    # 第 3 次才成功：invalid_retries=2 → 前两次非 JSON 重试，第 3 次成功
    step = KbLlmChatStep("n7", "生成wiki", "kb_llm_chat",
                         {"prompt": "{{kp_prompt}}", "output_var": "out",
                          "json_mode": True, "invalid_retries": 2, "model": "test-model"})
    ctx = TapeRuntimeContext({"kp_prompt": "写页面", "summary_model_id": "m"})
    resp = step.handle(ctx)
    assert resp.success is True
    assert ctx.bindings["out"]["title"] == "结算指标"
    assert len(called) == 3, "非 JSON 前两次应重试"

    # 重试耗尽仍非 JSON + skip_on_invalid → 跳过且 output_var 清空
    responses2 = ["垃圾1", "垃圾2", "垃圾3"]
    called2 = []

    def fake_chat2(cfg, messages, temperature=None, max_tokens=None, thinking=None):
        called2.append(1)
        return responses2.pop(0)

    chat_mod.ChatClient = lambda cfg: type("FC", (), {"chat": lambda self, m, **k: fake_chat2(cfg, m, **k)})()
    chat_mod.load_chat_config = lambda db=None, model_id=None: {"base_url": "http://f", "api_key": "k", "model": "m"}

    ctx2 = TapeRuntimeContext({"kp_prompt": "写页面", "summary_model_id": "m", "out": "旧的上轮残留"})
    step2 = KbLlmChatStep("n7", "生成wiki", "kb_llm_chat",
                          {"prompt": "{{kp_prompt}}", "output_var": "out",
                           "json_mode": True, "invalid_retries": 2, "skip_on_invalid": True,
                           "model": "test-model"})
    resp2 = step2.handle(ctx2)
    assert resp2.success is True and resp2.body.get("skipped") is True
    assert ctx2.bindings["out"] is None, "跳过必须清空 output_var，防 loop 复用上轮内容"
    assert len(called2) == 3, "invalid_retries=2 + 首试 = 3 次调用后才跳过"

    # 无 skip_on_invalid → 非 JSON 仍失败（历史行为不回归）
    responses3 = ["垃圾A", "垃圾B", "垃圾C"]
    chat_mod.ChatClient = lambda cfg: type("FC", (), {"chat": lambda self, m, **k: "垃圾C"})()
    chat_mod.load_chat_config = lambda db=None, model_id=None: {"base_url": "http://f", "api_key": "k", "model": "m"}

    step3 = KbLlmChatStep("n7", "生成wiki", "kb_llm_chat",
                          {"prompt": "{{kp_prompt}}", "output_var": "out",
                           "json_mode": True, "invalid_retries": 2, "model": "test-model"})
    resp3 = step3.handle(TapeRuntimeContext({"kp_prompt": "写页面", "summary_model_id": "m"}))
    assert resp3.success is False and "非 JSON" in resp3.error


def test_wiki_upsert_page_skip_empty() -> None:
    # page_title 为空字符串（script 节点准备了空变量）→ skip_empty 跳过成功
    step = WikiUpsertPageStep("n8", "写实体页", "wiki_upsert_page",
                              {"kb_id": "kb1", "title": "{{page_title}}",
                               "slug": "{{page_slug}}", "skip_empty": True})
    ctx = TapeRuntimeContext({"page_title": "", "page_slug": ""})
    resp = step.handle(ctx)
    assert resp.success and resp.body.get("skipped") is True


def test_wiki_upsert_page_requires_kb() -> None:
    step = WikiUpsertPageStep("n5", "写摘要页", "wiki_upsert_page",
                              {"title": "t", "slug": "s", "content": "c"})
    resp = step.handle(TapeRuntimeContext({}))
    assert not resp.success and "kb_id" in resp.error


def test_wiki_ensure_folder_idempotent(orch_db) -> None:
    import api.db
    from api.models.knowledge import WikiFolder

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    step = WikiEnsureFolderStep("n0", "初始化目录", "wiki_ensure_folder",
                                {"kb_id": "kb1", "path": "长句原文/2024"})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success and resp.body["created"] == 2 and resp.body["reused"] == 0
    assert ctx.bindings["folder_id"]  # 单条路径 → 输出末级目录 id

    # 幂等：再跑一次 → 全部复用
    ctx2 = TapeRuntimeContext({})
    resp2 = step.handle(ctx2)
    assert resp2.success and resp2.body["created"] == 0 and resp2.body["reused"] == 2
    assert ctx2.bindings["folder_id"] == ctx.bindings["folder_id"]

    # 校验目录树确实存在（2 层）
    folders = orch_db.query(WikiFolder).filter(WikiFolder.kb_id == "kb1").all()
    assert len(folders) == 2


def test_wiki_ensure_folder_batch_and_dry_run(orch_db) -> None:
    import api.db

    api.db.get_sessionmaker = lambda: __import__("sqlalchemy").orm.sessionmaker(bind=orch_db.bind, expire_on_commit=False)
    # dry_run：只统计不写库
    step = WikiEnsureFolderStep("n0", "初始化目录", "wiki_ensure_folder",
                                {"kb_id": "kb1", "paths": ["长句原文", "高频关键词"], "dry_run": True})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success and resp.body["created"] == 2 and resp.body["reused"] == 0
    # 批量：不 dry_run，输出 id 列表
    step2 = WikiEnsureFolderStep("n0", "初始化目录", "wiki_ensure_folder",
                                 {"kb_id": "kb1", "paths": ["长句原文", "高频关键词"], "output_var": "folder_ids"})
    ctx2 = TapeRuntimeContext({})
    resp2 = step2.handle(ctx2)
    assert resp2.success and resp2.body["created"] == 2
    assert isinstance(ctx2.bindings["folder_ids"], list) and len(ctx2.bindings["folder_ids"]) == 2


def test_wiki_ensure_folder_requires_kb() -> None:
    step = WikiEnsureFolderStep("n0", "初始化目录", "wiki_ensure_folder",
                                {"path": "长句原文"})
    resp = step.handle(TapeRuntimeContext({}))
    assert not resp.success and "kb_id" in resp.error


def test_step_result_upsert_semantics(monkeypatch) -> None:
    """step_results 同 (step_id, round) 应替换而非追加（2026-10-12 动态效果）。

    run_node 先写 running 占位、执行完写终态——若追加则每个节点两条记录，
    前端会看到重复步骤；必须替换。
    """
    import json

    from api.models.orchestration import TapeRun
    from sqlalchemy import select

    from worker.tasks.orch_runner import _append_step_result

    # 用内存 SQLite 模拟（sessionmaker 打桩）
    from api.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSM = sessionmaker(bind=engine)
    monkeypatch.setattr("worker.tasks.orch_runner._run_db", lambda env=None: TestSM())
    db = TestSM()
    run = TapeRun(
        id="r1", tape_id="t1", tape_name="t", status="running",
        inputs={}, step_results=[], bindings={},
    )
    db.add(run)
    db.commit()

    _append_step_result("r1", {
        "step_id": "t1::n1", "step_inst": "kb_read_table", "step_label": "读",
        "queue": "orch", "status": "running", "body": None, "error": None,
        "duration_ms": 0, "round": 0,
    })
    _append_step_result("r1", {
        "step_id": "t1::n1", "step_inst": "kb_read_table", "step_label": "读",
        "queue": "orch", "status": "success", "body": {"rows": 8}, "error": None,
        "duration_ms": 134, "round": 0,
    })
    db2 = TestSM()
    run2 = db2.execute(select(TapeRun).where(TapeRun.id == "r1")).scalars().first()
    results = run2.step_results or []
    assert len(results) == 1, f"同 step_id 应替换为 1 条，实际 {len(results)}"
    assert results[0]["status"] == "success", f"应为终态 success，实际 {results[0]['status']}"
    assert results[0]["duration_ms"] == 134
    db2.close()


def test_wiki_build_template_structure() -> None:
    import sys
    sys.path.insert(0, ".")
    from scripts.seed_framework_db import WIKI_BUILD_TAPE

    insts = [n["data"]["stepInst"] for n in WIKI_BUILD_TAPE["nodes"]]
    assert insts == ["kb_read_doc", "script", "kb_llm_chat", "script",
                     "wiki_upsert_page", "loop", "script", "wiki_upsert_page", "wiki_rebuild_links"]
    assert len(WIKI_BUILD_TAPE["edges"]) == 8


def test_table_wiki_template_structure() -> None:
    """表→wiki 构建编排模板结构：节点指令全部注册、边闭合、输入参数齐备（2026-10-12）。"""
    from scripts.seed_framework_db import TABLE_WIKI_TAPE

    nodes = TABLE_WIKI_TAPE["nodes"]
    edges = TABLE_WIKI_TAPE["edges"]
    by_id = {n["id"]: n for n in nodes}
    assert len(nodes) == 7, f"期望 7 节点实际 {len(nodes)}"
    for n in nodes:
        inst = n["data"]["stepInst"]
        assert inst in ATOM_MAP, f"指令 {inst} 未注册到 ATOM_MAP"
    for e in edges:
        assert e["source"] in by_id and e["target"] in by_id, f"边引用了不存在的节点: {e}"
    insts = [n["data"]["stepInst"] for n in nodes]
    assert insts[0] == "kb_read_table", "首节点应为 kb_read_table"
    assert "kb_llm_chat" in insts, "应有 LLM 生成表用途说明"
    assert insts[-2] == "wiki_upsert_page" and insts[-1] == "wiki_rebuild_links", "末两节点应为写页+重建互链"
    upsert = next(n for n in nodes if n["data"]["stepInst"] == "wiki_upsert_page")
    assert upsert["data"]["config"].get("page_type") == "table"


# ── 文档摘要三模式模板结构（2026-10-12）──────────────────────────

def test_summary_tapes_structure() -> None:
    """三个摘要模板：节点指令全部注册、边闭合、含 loop/skip_empty 关键语义、script 可编译。"""
    from scripts.seed_framework_db import (
        SUMMARY_STUFF_TAPE,
        SUMMARY_MAPREDUCE_TAPE,
        SUMMARY_REFINE_TAPE,
    )

    tapes = {
        "stuff": SUMMARY_STUFF_TAPE,
        "mapreduce": SUMMARY_MAPREDUCE_TAPE,
        "refine": SUMMARY_REFINE_TAPE,
    }
    for name, tape in tapes.items():
        nodes, edges = tape["nodes"], tape["edges"]
        by_id = {n["id"]: n for n in nodes}
        assert len(by_id) == len(nodes), f"{name}: 节点 id 重复"
        for n in nodes:
            inst = n["data"]["stepInst"]
            assert inst in ATOM_MAP, f"{name}: 指令 {inst} 未注册"
            if inst == "script":
                compile(n["data"]["config"].get("code") or "", f"<{name}:{n['id']}>", "exec")
        for e in edges:
            assert e["source"] in by_id and e["target"] in by_id, f"{name}: 边引用不存在节点 {e}"
        assert all(n["data"]["stepInst"] == "kb_read_doc" for n in nodes[:1]), f"{name}: 首节点应为 kb_read_doc"
        # 2026-10-14：摘要不再写 wiki 摘要页，改为写 kb_document.summary 字段
        # （直连 LLM 时代摘要的存储位置）——最后一个节点应为 kb_write_doc_summary。
        assert any(n["data"]["stepInst"] == "kb_write_doc_summary" for n in nodes), f"{name}: 缺写文档摘要节点"
        writer = next(n for n in nodes if n["data"]["stepInst"] == "kb_write_doc_summary")
        assert writer["data"]["config"].get("skip_empty") is True, f"{name}: 写摘要需 skip_empty"
        assert writer["data"]["config"].get("summary_var") == "final_summary", f"{name}: 摘要来源变量应为 final_summary"
        assert not any(n["data"]["stepInst"] == "wiki_upsert_page" for n in nodes), f"{name}: 不应再写 wiki 摘要页"
        # 2026-10-14：LLM 节点绑定知识库配置的摘要模型（文档链路 KbReadDocStep
        # 注入 bindings['summary_model_id']）。
        for n in nodes:
            if n["data"]["stepInst"] == "kb_llm_chat":
                assert n["data"]["config"].get("model") == "{{summary_model_id}}", \
                    f"{name}: LLM 节点应用 KB 摘要模型 model={{summary_model_id}}"
        assert "kb_id" in tape["exec_params"] and "document_id" in tape["exec_params"]


def test_summary_tapes_mode_semantics() -> None:
    """三种模式的语义差异断言：stuff 无 loop；mapreduce/refine 有 loop 且循环体内含 LLM。"""
    from scripts.seed_framework_db import (
        SUMMARY_STUFF_TAPE,
        SUMMARY_MAPREDUCE_TAPE,
        SUMMARY_REFINE_TAPE,
    )

    insts_of = lambda t: [n["data"]["stepInst"] for n in t["nodes"]]
    assert "loop" not in insts_of(SUMMARY_STUFF_TAPE), "Stuff 模式不应有 loop"
    assert "loop" in insts_of(SUMMARY_MAPREDUCE_TAPE) and "loop" in insts_of(SUMMARY_REFINE_TAPE)

    # Map-Reduce：循环体内应有两个 kb_llm_chat（子摘要 + 合并），合并那个带 skip_empty
    mr_llms = [n for n in SUMMARY_MAPREDUCE_TAPE["nodes"] if n["data"]["stepInst"] == "kb_llm_chat"]
    assert len(mr_llms) == 2, f"Map-Reduce 应有 2 个 LLM 节点（子摘要+合并），实际 {len(mr_llms)}"
    assert any(n["data"]["config"].get("skip_empty") for n in mr_llms), "合并 LLM 应带 skip_empty"

    # Refine：单 LLM 节点（滚动更新），无 skip_empty 需要
    rf_llms = [n for n in SUMMARY_REFINE_TAPE["nodes"] if n["data"]["stepInst"] == "kb_llm_chat"]
    assert len(rf_llms) == 1, f"Refine 应有 1 个 LLM 节点，实际 {len(rf_llms)}"


# ───────────────────────── Agent 执行组件（agent_run，2026-10-15）─────────────────────────

def _stub_agent_runtime(monkeypatch, fn) -> None:
    """把 worker.agent.runtime 替换为 stub 模块（openai-agents SDK 不在测试依赖）。"""
    import sys
    import types

    fake = types.ModuleType("worker.agent.runtime")
    setattr(fake, "run_agent_sync", fn)
    monkeypatch.setitem(sys.modules, "worker.agent.runtime", fake)


def _fake_async_result(result: dict):
    """模拟 celery AsyncResult（只暴露编排原子用到的 .get）。"""
    class _FakeRes:
        def get(self, timeout=None):
            return result
    return _FakeRes()


def test_agent_run_registered() -> None:
    assert "agent_run" in ATOM_MAP
    from api.services.orchestration_engine import KbAgentRunStep

    assert ATOM_MAP["agent_run"] is KbAgentRunStep


def test_agent_run_skip_empty() -> None:
    from api.services.orchestration_engine import KbAgentRunStep

    step = KbAgentRunStep("n1", "Agent执行", "agent_run", {"skip_empty": True})
    ctx = TapeRuntimeContext({})
    resp = step.handle(ctx)
    assert resp.success
    assert resp.body.get("skipped") is True


def test_agent_run_requires_prompt() -> None:
    from api.services.orchestration_engine import KbAgentRunStep

    step = KbAgentRunStep("n1", "Agent执行", "agent_run", {})
    resp = step.handle(TapeRuntimeContext({}))
    assert not resp.success
    assert "缺 prompt" in resp.error


def test_agent_run_inline_success(monkeypatch) -> None:
    """节点 worker 即 agent worker（SDK 可 import）→ 进程内内联执行，无投递。"""
    from api.services.orchestration_engine import KbAgentRunStep

    captured: dict = {}

    def fake_run_agent_sync(payload, task_id=None):
        captured["payload"] = payload
        captured["task_id"] = task_id
        return {"success": True, "output": "agent 输出结果", "runs_ms": 123, "sdk_trace_id": "tr-1"}

    _stub_agent_runtime(monkeypatch, fake_run_agent_sync)
    step = KbAgentRunStep(
        "n1", "Agent执行", "agent_run",
        {"prompt": "请总结：{{doc_summary}}", "instructions": "只输出结论",
         "agent_name": "我的Agent", "skill": "市场监管法规", "kb_id": "{{kb_id}}",
         "model": "deepseek-v4-pro"},
    )
    ctx = TapeRuntimeContext({"doc_summary": "文档内容", "kb_id": "kb-1"})
    resp = step.handle(ctx)
    assert resp.success
    assert ctx.bindings["agent_out"] == "agent 输出结果"
    assert resp.body == {"set:agent_out": "agent 输出结果", "runs_ms": 123}
    # payload 组装：模板已渲染、脚本/权限字段透传
    p = captured["payload"]
    assert p["input"] == "请总结：文档内容"
    assert p["instructions"] == "只输出结论"
    assert p["agent_name"] == "我的Agent"
    assert p["config"]["kb_id"] == "kb-1"
    assert p["config"]["skill"] == "市场监管法规"
    assert p["config"]["model"] == "deepseek-v4-pro"


def test_agent_run_model_literal_guard(monkeypatch) -> None:
    """model 配置残留 {{...}} 字面量（bindings 缺键）→ 视为未配置，不传模型。"""
    from api.services.orchestration_engine import KbAgentRunStep

    captured: dict = {}

    def fake_run_agent_sync(payload, task_id=None):
        captured["payload"] = payload
        return {"success": True, "output": "ok"}

    _stub_agent_runtime(monkeypatch, fake_run_agent_sync)
    step = KbAgentRunStep(
        "n1", "Agent执行", "agent_run",
        {"prompt": "hi", "model": "{{summary_model_id}}"},  # bindings 无此键
    )
    resp = step.handle(TapeRuntimeContext({}))
    assert resp.success
    assert "model" not in captured["payload"]["config"]


def test_agent_run_enqueue_and_wait(monkeypatch) -> None:
    """非 agent worker（SDK import 失败）→ 投递 agent 队列 + 同步等待结果写 bindings。"""
    import sys

    from api.services.orchestration_engine import KbAgentRunStep

    # 确保 runtime 不可 import（不 stub → 真实环境 SDK 缺失 → ImportError）
    monkeypatch.setitem(sys.modules, "worker.agent.runtime", None)
    monkeypatch.delitem(sys.modules, "worker.agent.runtime", raising=False)

    sent: dict = {}

    def fake_send_task(name, args=None, task_id=None, queue=None):
        sent["name"] = name
        sent["args"] = args
        sent["task_id"] = task_id
        sent["queue"] = queue
        assert queue == "agent", "agent_run 必须投递 agent 队列（agent-worker 执行）"
        return _fake_async_result({"success": True, "output": "编排里的agent结果", "runs_ms": 456})

    import types

    fake_celery = types.ModuleType("worker.celery_app")
    setattr(fake_celery, "celery_app", types.SimpleNamespace(send_task=fake_send_task))
    monkeypatch.setitem(sys.modules, "worker.celery_app", fake_celery)

    step = KbAgentRunStep(
        "n1", "Agent执行", "agent_run",
        {"prompt": "请回答问题 A", "output_var": "agent_res", "kb_id": "{{kb_id}}"},
    )
    ctx = TapeRuntimeContext({"kb_id": "kb-9"})
    resp = step.handle(ctx)
    assert resp.success
    assert ctx.bindings["agent_res"] == "编排里的agent结果"
    # 投递契约：任务名 / agent 队列 / payload
    assert sent["name"] == "worker.tasks.agent_worker.run_agent_inline"
    assert sent["args"][0]["input"] == "请回答问题 A"
    assert sent["args"][0]["config"]["kb_id"] == "kb-9"
    assert sent["task_id"].startswith("agent_run_")
    assert resp.body["runs_ms"] == 456


def test_agent_run_failure_propagates(monkeypatch) -> None:
    """agent 结果失败/异常 → 节点 failed，错误透传。"""
    from api.services.orchestration_engine import KbAgentRunStep

    def fake_run_agent_sync(payload, task_id=None):
        return {"success": False, "error": "MaxTurnsExceeded: 60 轮工具调用上限"}

    _stub_agent_runtime(monkeypatch, fake_run_agent_sync)
    step = KbAgentRunStep("n1", "Agent执行", "agent_run", {"prompt": "do it"})
    resp = step.handle(TapeRuntimeContext({}))
    assert not resp.success
    assert "MaxTurnsExceeded" in resp.error


def test_agent_run_enqueue_timeout_fails(monkeypatch) -> None:
    """投递后等待超时（AsyncResult.get 抛超时）→ 节点 failed。"""
    import sys
    import types

    from api.services.orchestration_engine import KbAgentRunStep

    monkeypatch.setitem(sys.modules, "worker.agent.runtime", None)
    monkeypatch.delitem(sys.modules, "worker.agent.runtime", raising=False)

    class _TimeoutRes:
        def get(self, timeout=None):
            raise TimeoutError(f"任务等待超过 {timeout}s")

    fake_celery = types.ModuleType("worker.celery_app")
    setattr(
        fake_celery, "celery_app",
        types.SimpleNamespace(
            send_task=lambda name, args=None, task_id=None, queue=None: _TimeoutRes()
        ),
    )
    monkeypatch.setitem(sys.modules, "worker.celery_app", fake_celery)

    step = KbAgentRunStep(
        "n1", "Agent执行", "agent_run",
        {"prompt": "do it", "timeout_sec": 10},
    )
    resp = step.handle(TapeRuntimeContext({}))
    assert not resp.success
    assert "超" in resp.error or "Agent 执行失败" in resp.error
