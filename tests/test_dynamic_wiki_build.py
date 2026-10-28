"""动态 wiki 构建（方案 B）测试——生成器结构、知识点判定、触发链路、目录模板渲染。

覆盖：
- gen_dynamic_wiki_tape.build_dynamic_tape_payload：节点/边拓扑、loop 配置、
  内联代码语法、模板取值语法（{{kp['name']}}）；
- dynamic_wiki_build.detect_knowledge_points：LLM 输出解析（dict/list/坏 JSON）；
- dynamic_wiki_build.enqueue_dynamic_wiki_build：开关门控、tape 创建、run 创建、
  Job 写入、投递（monkeypatch 隔离 LLM/DB）；
- wiki_ensure_folder 的 paths 模板整体渲染（{{kp_paths}}）。
"""

from __future__ import annotations

import ast
import json

import pytest

from api.services.orchestration_engine import TapeRuntimeContext, WikiEnsureFolderStep


# ─────────────────────────────────────────────────────────────────────
# 生成器结构
# ─────────────────────────────────────────────────────────────────────
def test_payload_topology() -> None:
    from scripts.gen_dynamic_wiki_tape import build_dynamic_tape_payload

    p = build_dynamic_tape_payload("wiki构建-动态-测试")
    assert p["tape_type"] == "kb_build"
    assert p["category"] == "知识构建"
    insts = {n["id"]: (n.get("data") or {}).get("stepInst") for n in p["nodes"]}
    # 三类核心节点齐备：建目录（wiki_ensure_folder）/ 建wiki（wiki_upsert_page）/ 循环（loop）
    assert insts["n05"] == "wiki_ensure_folder"
    assert insts["n07"] == "loop"
    assert insts["n07d"] == "wiki_upsert_page"
    # 循环体连线
    src_of = {e["source"]: e["target"] for e in p["edges"]}
    assert src_of["n07"] == "n07a"  # loop → 循环体起点
    assert src_of["n07d"] == "n07e"  # body 终点
    # body 终点无出边（不被 collect_body_ids 卷进每轮执行）
    assert "n07e" not in src_of


def test_loop_and_node_configs() -> None:
    from scripts.gen_dynamic_wiki_tape import build_dynamic_tape_payload

    p = build_dynamic_tape_payload("wiki构建-动态-测试")
    by_id = {n["id"]: n for n in p["nodes"]}

    loop_cfg = (by_id["n07"]["data"]["config"])
    assert loop_cfg["item_var"] == "kp"
    assert loop_cfg["collection"] == "{{kp_items}}"

    folder_cfg = by_id["n05"]["data"]["config"]
    assert folder_cfg["paths"] == "{{kp_paths}}"  # 模板批量路径

    upsert_cfg = by_id["n07d"]["data"]["config"]
    assert upsert_cfg["title"] == "{{kp['name']}}"  # dict 下标模板
    assert upsert_cfg["slug"] == "{{kp['slug']}}"
    assert upsert_cfg["folder_id"] == "{{kp['folder_id']}}"
    assert upsert_cfg["skip_empty"] is True


def test_all_inline_code_syntax_ok() -> None:
    """所有内联 script 代码必须通过 Python 语法解析（防拼装时残留转义）。"""
    from scripts.gen_dynamic_wiki_tape import build_dynamic_tape_payload

    p = build_dynamic_tape_payload("wiki构建-动态-测试")
    for n in p["nodes"]:
        code = ((n.get("data") or {}).get("config") or {}).get("code") or ""
        if not code:
            continue
        try:
            ast.parse(code)
        except SyntaxError as e:  # pragma: no cover
            pytest.fail(f"node {n['id']} 内联代码语法错误: {e}")


def test_input_def_bindings() -> None:
    from scripts.gen_dynamic_wiki_tape import build_dynamic_tape_payload

    p = build_dynamic_tape_payload("wiki构建-动态-测试")
    by_id = {n["id"]: n for n in p["nodes"]}
    assignments = json.loads((by_id["n01"]["data"]["config"])["assignments"])
    vars_ = {a["variable"]: a["expression"] for a in assignments}
    assert vars_["kid"] == "bindings.get('kid', '')"
    assert vars_["kp_tree"] == "bindings.get('kp_tree', {})"


def test_n02_derives_kp_paths() -> None:
    """n02 派生变量必须产出 kp_paths（n05 建目录节点消费），防拓扑回归。

    2026-10-28 线上事故：n02 漏派生 kp_paths，n05 wiki_ensure_folder 的
    paths="{{kp_paths}}" 未渲染 → ast.literal_eval 炸 → 整条 run failed
    （附件1：行业短彩端口收入效益评估报表需求.xlsx）。此处断言 n02 派生 +
    n02 执行后 n05 模板能渲染成合法 JSON 数组。
    2026-10-28 文档根目录：kp_paths 统一挂「文档名/」根下（doc_root 前缀）。
    """
    from scripts.gen_dynamic_wiki_tape import build_dynamic_tape_payload
    from api.services.orchestration_engine import DefStep

    p = build_dynamic_tape_payload("wiki构建-动态-测试")
    by_id = {n["id"]: n for n in p["nodes"]}
    assignments = json.loads((by_id["n02"]["data"]["config"])["assignments"])
    vars_ = {a["variable"]: a["expression"] for a in assignments}
    assert "kp_paths" in vars_, "n02 必须派生 kp_paths，否则 n05 建目录节点拿未渲染模板失败"
    assert "doc_root" in vars_, "n02 必须派生 doc_root（文档名根目录）"

    ctx = TapeRuntimeContext({"kp_tree": {"knowledge_points": [
        {"name": "出帐金额", "path": "收入指标/出帐金额", "extract_hint": "含税"},
        {"name": "结算对象", "path": "维度/结算对象", "extract_hint": "类型"},
    ]}, "doc_root": "行业短彩端口收入效益评估报表需求"})
    step = DefStep("n02", "派生变量", "def", {"assignments": assignments})
    resp = step.handle(ctx)
    assert resp.success is True
    # 知识点目录统一挂在「文档名/」根目录下
    assert ctx.bindings["kp_paths"] == [
        "行业短彩端口收入效益评估报表需求/收入指标/出帐金额",
        "行业短彩端口收入效益评估报表需求/维度/结算对象",
    ]

    # 模拟 n05 建目录节点对 paths 模板的整体渲染（与线上失败路径一致）：
    # _template_replace 用 str(list) 产 Python 字面量（单引号）→ json 失败后 ast 兜底
    cfg = by_id["n05"]["data"]["config"]
    assert cfg["paths"] == "{{kp_paths}}"
    rendered = ctx.render(cfg["paths"])
    assert rendered.startswith("['行业短彩端口收入效益评估报表需求/收入指标/出帐金额'")  # str(list) 字面量
    import ast

    assert ast.literal_eval(rendered) == [
        "行业短彩端口收入效益评估报表需求/收入指标/出帐金额",
        "行业短彩端口收入效益评估报表需求/维度/结算对象",
    ]


def test_n07d_upsert_has_doc_ownership() -> None:
    """n07d 挂页节点必须携带 source_refs（文档归属），删文档级联清理可命中。"""
    from scripts.gen_dynamic_wiki_tape import build_dynamic_tape_payload
    p = build_dynamic_tape_payload("wiki构建-动态-测试")
    by_id = {n["id"]: n for n in p["nodes"]}
    upsert_cfg = by_id["n07d"]["data"]["config"]
    assert upsert_cfg["source_refs"] == "[\"{{doc_ref}}\"]",         "挂页必须带 source_refs（{document_id}|{file_name}），否则删文档时 wiki 页成孤儿"

    # 渲染后必须是合法 JSON 数组（引号内置，{{doc_ref}} 只替换值）
    from api.services.orchestration_engine import TapeRuntimeContext

    ctx = TapeRuntimeContext({"doc_ref": "doc-abc|行业短彩端口收入效益评估报表需求.xlsx"})
    rendered = ctx.render(upsert_cfg["source_refs"])
    assert json.loads(rendered) == ["doc-abc|行业短彩端口收入效益评估报表需求.xlsx"]

    # n02 必须派生 doc_ref
    assignments = json.loads((by_id["n02"]["data"]["config"])["assignments"])
    vars_ = {a["variable"]: a["expression"] for a in assignments}
    assert "doc_ref" in vars_


def test_doc_root_name_cleanup() -> None:
    """文档名 → 根目录名清洗：去扩展名、替换非法字符、兜底。"""
    from worker.tasks.dynamic_wiki_build import doc_root_name

    assert doc_root_name("多模态数据管理平台方案V111452-d.docx") == "多模态数据管理平台方案V111452-d"
    assert doc_root_name("报表/预算:2024?.xlsx") == "报表-预算-2024"
    assert doc_root_name("") == "未命名文档"
    assert doc_root_name("a.tar.gz") == "a.tar"  # 只去最后一个扩展名


# ─────────────────────────────────────────────────────────────────────
# 知识点判定解析
# ─────────────────────────────────────────────────────────────────────
def test_detect_knowledge_points_parses(monkeypatch) -> None:
    from worker.tasks import dynamic_wiki_build as dwb

    class _FakeWR:
        KB = ""

        @staticmethod
        def load_config():
            return None

        @staticmethod
        def llm_call(messages, **kw):
            return '{"knowledge_points": [{"name": "收入指标", "path": "收入指标", "extract_hint": "出帐"}, {"name": "出帐金额", "path": "收入指标/出帐金额", "extract_hint": "含税"}]}'

        @staticmethod
        def ensure_full_json(t):
            return t

    monkeypatch.setattr(dwb.sys, "path", list(dwb.sys.path))
    monkeypatch.setitem(dwb.sys.modules, "__fake_weknora_rpc__", object())
    import importlib

    mod = importlib.import_module("worker.tasks.dynamic_wiki_build")

    # 直接调真实函数，monkeypatch weknora_rpc import 目标
    import types

    fake = types.ModuleType("weknora_rpc")
    fake.load_config = _FakeWR.load_config
    fake.llm_call = _FakeWR.llm_call
    fake.ensure_full_json = _FakeWR.ensure_full_json
    monkeypatch.setitem(mod.sys.modules, "weknora_rpc", fake)

    kps = mod.detect_knowledge_points("kb1", "文档内容……", timeout_s=10)
    assert len(kps) == 2
    assert kps[0]["name"] == "收入指标"
    assert kps[1]["path"] == "收入指标/出帐金额"


def test_detect_knowledge_points_list_form(monkeypatch) -> None:
    from worker.tasks import dynamic_wiki_build as dwb

    import types

    fake = types.ModuleType("weknora_rpc")
    fake.load_config = lambda: None
    fake.KB = ""
    fake.llm_call = lambda messages, **kw: '[{"name": "A", "path": "A", "extract_hint": "a"}]'
    fake.ensure_full_json = lambda t: t
    monkeypatch.setitem(dwb.sys.modules, "weknora_rpc", fake)

    kps = dwb.detect_knowledge_points("kb1", "文本")
    assert len(kps) == 1 and kps[0]["name"] == "A"


# ─────────────────────────────────────────────────────────────────────
# 触发链路（monkeypatch 隔离）
# ─────────────────────────────────────────────────────────────────────
def test_enqueue_steps(monkeypatch, tmp_path) -> None:
    """全链路：开关→读文档→判知识点→建tape→建run→Job→投递（全部打桩）。"""
    from worker.tasks import dynamic_wiki_build as dwb

    calls: dict[str, list] = {}

    monkeypatch.setattr(dwb, "dynamic_wiki_build_enabled", lambda: True)

    def _fake_read(kb_id, document_id, max_fulltext=14000):
        return {"text": "一些文档内容……", "source": "fulltext", "chunk_count": 32,
                "doc_name": "测试.xlsx"}

    monkeypatch.setattr(dwb, "read_doc_source", _fake_read)

    def _fake_detect(kb_id, text, doc_name="", timeout_s=600):
        return [{"name": "指标", "path": "指标", "extract_hint": "出帐"}]

    monkeypatch.setattr(dwb, "detect_knowledge_points", _fake_detect)

    # 建 tape 桩：真实 create_tape 走 sqlite 内存库太重，这里直接模拟返回 id
    def _fake_create_tape(db, payload, user_id="system"):
        calls["create_tape"] = payload
        return "dyn-tape-id"

    # _create_dynamic_tape 内部 import create_tape/save_tape_design —— 打桩整个函数
    def _fake_create_dynamic_tape(db, payload, user_id="system"):
        calls["create_dynamic_tape"] = payload
        return "dyn-tape-id"

    monkeypatch.setattr(dwb, "_create_dynamic_tape", _fake_create_dynamic_tape)

    # run 创建桩：enqueue 内部 `from api.services.orchestration import _create_tape_run`
    # 是局部导入 → patch 源模块 api.services.orchestration._create_tape_run
    import api.services.orchestration as orch_svc

    def _fake_create_run(db, tape_id, inputs, env=None):
        calls["create_run"] = {"tape_id": tape_id, "inputs": inputs, "env": env}
        return {"run_id": "dyn-run-id", "run_env": "prod"}

    monkeypatch.setattr(orch_svc, "_create_tape_run", _fake_create_run)

    # Job 写入桩：enqueue 内部 `from api.models.framework import Job` 局部导入
    import api.models.framework as framework_mod

    class _FakeJob:
        def __init__(self, **kw):
            calls["job"] = kw

    monkeypatch.setattr(framework_mod, "Job", _FakeJob)

    # celery 投递桩：`from worker.celery_app import celery_app`
    class _FakeCelery:
        @staticmethod
        def send_task(*args, **kw):
            calls["send_task"] = (args, kw)

    import worker.celery_app as celery_mod

    monkeypatch.setattr(celery_mod, "celery_app", _FakeCelery)

    # db 桩：doc_name 读取走 `from api.db import get_sessionmaker` 局部导入
    # → patch api.db.get_sessionmaker（enqueue 内部首次 `_gsm()()` 读文档名）
    import api.db as api_db_mod

    class _FakeResult:
        def __init__(self, doc):
            self._doc = doc

        def scalars(self):
            return self

        def first(self):
            return self._doc

    class _FakeExecuteDb:
        def execute(self, stmt):
            return _FakeResult(type("_D", (), {"file_name": "测试.xlsx"})())

        def add(self, obj):
            calls["db_add"] = obj

        def commit(self):
            return None

        def close(self):
            return None

        def rollback(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(api_db_mod, "get_sessionmaker", lambda: _FakeExecuteDb)

    r = dwb.enqueue_dynamic_wiki_build("kb1", "doc1", cookie="c")
    assert r["dynamic_generated"] is True
    assert r["tape_id"] == "dyn-tape-id"
    assert r["run_id"] == "dyn-run-id"
    assert r["kp_count"] == 1
    payload = calls["create_dynamic_tape"]
    assert "wiki构建-动态" in payload["tape_name"]
    run_inputs = calls["create_run"]["inputs"]
    assert run_inputs["kp_tree"]["knowledge_points"][0]["name"] == "指标"
    assert run_inputs["kid"] == "doc1"
    assert calls["send_task"][0][0] == "worker.tasks.scheduler.execute_modo_job"


def test_enqueue_skipped_when_disabled(monkeypatch) -> None:
    from worker.tasks import dynamic_wiki_build as dwb

    monkeypatch.setattr(dwb, "dynamic_wiki_build_enabled", lambda: False)
    r = dwb.enqueue_dynamic_wiki_build("kb1", "doc1")
    assert r["dynamic_generated"] is False
    assert "DYNAMIC_WIKI_BUILD" in r.get("dynamic_skipped", "")


def test_enqueue_empty_kp_skips(monkeypatch) -> None:
    from worker.tasks import dynamic_wiki_build as dwb

    monkeypatch.setattr(dwb, "dynamic_wiki_build_enabled", lambda: True)
    monkeypatch.setattr(
        dwb, "read_doc_source",
        lambda kb, doc, max_fulltext=14000: {"text": "内容", "source": "fulltext",
                                              "chunk_count": 1, "doc_name": "a.txt"},
    )
    monkeypatch.setattr(dwb, "detect_knowledge_points", lambda *a, **kw: [])

    import api.db as api_db_mod

    class _FakeResult:
        def scalars(self):
            return self

        def first(self):
            return type("_D", (), {"file_name": "a.txt"})()

    class _FakeExecuteDb:
        def execute(self, stmt):
            return _FakeResult()

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(api_db_mod, "get_sessionmaker", lambda: _FakeExecuteDb)

    r = dwb.enqueue_dynamic_wiki_build("kb1", "doc1")
    assert r["dynamic_generated"] is False
    assert r.get("dynamic_skipped") == "知识点判定为空"


def test_tape_name_length() -> None:
    from worker.tasks.dynamic_wiki_build import build_tape_name

    name = build_tape_name("a" * 64, "非常非常非常非常非常非常非常非常长的文档文件名" * 3)
    assert len(name) <= 64


# ─────────────────────────────────────────────────────────────────────
# wiki_ensure_folder paths 模板渲染（2026-10-28 动态建目录）
# ─────────────────────────────────────────────────────────────────────
def test_ensure_folder_paths_template(monkeypatch) -> None:
    """paths="{{kp_paths}}" 整体渲染后按 JSON 数组解析。"""
    step = WikiEnsureFolderStep("n05", "建目录", "wiki_ensure_folder", {
        "kb_id": "kb1",
        "paths": "{{kp_paths}}",
        "sep": "/",
        "output_var": "folder_ids",
    })

    created: list[dict] = []

    class _FakeRow:  # 模拟 DB 行（folder 不存在 → 走创建）
        i = 0

        def __init__(self):
            self.id = f"f-{_FakeRow.i}"
            _FakeRow.i += 1

    class _FakeSession:
        def __init__(self):
            self.rows: list[_FakeRow] = []

        def execute(self, stmt):
            return self

        def scalars(self):
            return self

        def first(self):
            return None  # 不存在 → 走创建分支

        def add(self, obj):
            self.rows.append(obj)
            obj.id = f"f-sess-{len(self.rows)}"

        def commit(self):
            return None

        def close(self):
            return None

    import api.db as api_db_mod

    monkeypatch.setattr(api_db_mod, "get_sessionmaker", lambda: _FakeSession)

    # 目录创建内部走 api.services.kb_admin.wiki_create_folder —— 打桩
    import api.services.kb_admin as kb_admin

    def _fake_wiki_create_folder(db, kb_id, payload, user_id="system"):
        f = _FakeRow()
        db.add(f)
        return {"id": f.id}

    monkeypatch.setattr(kb_admin, "wiki_create_folder", _fake_wiki_create_folder)

    ctx = TapeRuntimeContext({"kp_paths": ["指标/出帐金额", "维度/结算对象"]})
    resp = step.handle(ctx)
    assert resp.success is True
    assert ctx.bindings["folder_ids"][0].startswith("f-sess-")  # 列表输出，与 paths 同序
    assert len(ctx.bindings["folder_ids"]) == 2
    assert resp.body["paths"] == ["指标/出帐金额", "维度/结算对象"]


def test_ensure_folder_paths_json_literal() -> None:
    """paths 为 JSON 数组字面量（无模板）时保持解析（存量行为不回归）。"""
    step = WikiEnsureFolderStep("n05", "建目录", "wiki_ensure_folder", {
        "kb_id": "kb1",
        "paths": '["A/B", "C"]',
        "output_var": "folder_ids",
    })
    # 只测 config 阶段：拿到 raw_paths 前不会碰 DB —— 直接构造 context 验证解析逻辑
    ctx = TapeRuntimeContext({"kb_id": "kb1"})
    raw = step.config
    batch = raw.get("paths")
    assert isinstance(batch, str)
    assert json.loads(batch) == ["A/B", "C"]