"""「wiki反馈修正」编排 + 定时任务测试（2026-10-25）。

覆盖：
- 种子 JSON 结构合法性（steps/nodes 对齐、inst 全注册、边闭合、脚本可编译）
- n1 脚本：收集待处理反馈、kw_ 标注自动确认、页面缺失反馈置 ignored、max_pages 截断
- n5 脚本：LLM 修正应用（页面更新 + 反馈 resolved / ignored）、报告落盘、汇总
- 定时任务 handler：扫描全库按知识库逐库串行发起编排（env=prod）+ 轮询终态
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.knowledge import WikiFeedback, WikiPage
from api.services.orchestration_engine import ATOM_MAP, ScriptStep, TapeRuntimeContext

SEED_FILE = Path(__file__).resolve().parents[1] / "scripts" / "seed_tape_data" / "wiki反馈修正.json"
OUT = "/tmp/fbfix"


@pytest.fixture()
def orch_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _clean_tmp():
    shutil.rmtree(OUT, ignore_errors=True)
    yield
    shutil.rmtree(OUT, ignore_errors=True)


def _use_db(monkeypatch, db):
    import api.db

    monkeypatch.setattr(
        api.db, "get_sessionmaker",
        lambda: sessionmaker(bind=db.bind, expire_on_commit=False),
    )


def _seed_json() -> dict:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


def _seed_step_config(step_id: str) -> dict:
    for s in _seed_json()["draft"]["steps"]:
        if s["step_id"] == step_id:
            return s["step_config"]
    raise KeyError(step_id)


def _add_feedback(db, fid: str, kb: str, slug: str, ftype: str = "comment",
                  preset: str = "", content: str = "", status: str = "pending") -> None:
    db.add(WikiFeedback(id=fid, kb_id=kb, slug=slug, feedback_type=ftype,
                        preset=preset, content=content, status=status,
                        page_title="", reported_by_name=""))
    db.commit()


def _add_page(db, kb: str, slug: str, title: str, content: str) -> None:
    db.add(WikiPage(id=f"p_{kb}_{slug}", kb_id=kb, slug=slug, title=title,
                    content=content, status="active"))
    db.commit()


# ───────────────────────── 种子结构 ─────────────────────────

def test_seed_structure() -> None:
    d = _seed_json()["draft"]
    assert d["tapeName"] == "wiki反馈修正"
    assert d["tapeType"] == "feedback_fix"
    assert d["status"] == "effective"

    node_ids = {n["id"] for n in d["nodes"]}
    steps = {s["step_id"]: s for s in d["steps"]}
    assert set(steps) == node_ids
    # 所有 step_inst 已注册
    for s in d["steps"]:
        assert s["step_inst"] in ATOM_MAP, f"未知组件指令 {s['step_inst']}"
    # script code 可编译
    for s in d["steps"]:
        if s["step_inst"] == "script":
            compile(s["step_config"]["code"], f"<{s['step_id']}>", "exec")
    # 边闭合：边两端都在节点集合内
    for e in d["edges"]:
        assert e["source"] in node_ids and e["target"] in node_ids
    # loop 语义：n2 loop 后继（n3→n4→n5）即循环体
    assert steps["n2"]["step_inst"] == "loop"
    assert steps["n2"]["step_config"]["item_var"] == "page_idx"
    assert steps["n5"]["next_step_ids"] == []


def test_script_steps_reachable_from_loop() -> None:
    """n2(loop) 的下游闭包 = {n3,n4,n5}；循环后无节点，收尾在 n5 is_last 分支。"""
    d = _seed_json()["draft"]
    nxt = {s["step_id"]: s["next_step_ids"] for s in d["steps"]}
    body, stack = [], list(nxt["n2"])
    while stack:
        n = stack.pop(0)
        if n in body:
            continue
        body.append(n)
        stack.extend(nxt.get(n, []))
    assert set(body) == {"n3", "n4", "n5"}


# ───────────────────────── n1 收集 ─────────────────────────

def test_n1_collects_and_auto_resolves_kw(orch_db, monkeypatch, tmp_path) -> None:
    _use_db(monkeypatch, orch_db)
    _add_page(orch_db, "kb1", "s1", "制度一", "# 制度一\n\n原文内容")
    _add_feedback(orch_db, "f1", "kb1", "s1", preset="content_error", content="第二段数字错误")
    _add_feedback(orch_db, "f2", "kb1", "s1", preset="kw_meaningful", content="")
    _add_feedback(orch_db, "f3", "kb1", "s2", preset="broken_link", content="链接失效")

    ctx = TapeRuntimeContext({"kb_id": "kb1"})
    resp = ScriptStep("n1", "收集待处理反馈", "script", _seed_step_config("n1")).handle(ctx)
    assert resp.success

    # kw 标注自动 resolved
    kw = orch_db.execute(select(WikiFeedback).where(WikiFeedback.id == "f2")).scalar_one()
    assert kw.status == "resolved"

    # 内容类：s1（2 条反馈聚合）+ s2 进 pages.json（s2 页面不存在 → 忽略）
    pages = json.loads(Path(OUT, "pages.json").read_text(encoding="utf-8"))
    assert [p["slug"] for p in pages] == ["s1"]
    p1 = pages[0]
    assert p1["kb_id"] == "kb1" and p1["content"] == "# 制度一\n\n原文内容"
    assert [f["id"] for f in p1["feedbacks"]] == ["f1"]

    # s2 反馈（页面缺失）→ ignored
    assert orch_db.execute(select(WikiFeedback).where(WikiFeedback.id == "f3")).scalar_one().status == "ignored"

    assert ctx.bindings["pages_idx"] == [0]
    assert ctx.bindings["kw_resolved"] == 1


def test_n1_respects_max_pages(orch_db, monkeypatch) -> None:
    _use_db(monkeypatch, orch_db)
    for i in range(3):
        _add_page(orch_db, "kb1", f"s{i}", f"标题{i}", f"正文{i}")
        _add_feedback(orch_db, f"f{i}", "kb1", f"s{i}", preset="outdated", content="过时")
    ctx = TapeRuntimeContext({"kb_id": "kb1", "max_pages": "2"})
    resp = ScriptStep("n1", "收集待处理反馈", "script", _seed_step_config("n1")).handle(ctx)
    assert resp.success
    assert len(ctx.bindings["pages_idx"]) == 2


# ───────────────────────── n5 应用修正 ─────────────────────────

def _n5_run(monkeypatch, orch_db, fix_raw: str, page: dict | None = None):
    _use_db(monkeypatch, orch_db)
    page = page or {"kb_id": "kb1", "slug": "s1", "title": "制度一",
                    "content": "# 制度一\n\n旧的错误数字", "feedbacks": [{"id": "f1"}]}
    Path(OUT).mkdir(parents=True, exist_ok=True)
    Path(OUT, "pages.json").write_text(json.dumps([page], ensure_ascii=False), encoding="utf-8")
    ctx = TapeRuntimeContext({"page_idx": 0, "_total": 1, "min_confidence": "0.7"})
    ctx.bindings = {"page_idx": 0, "_total": 1, "min_confidence": "0.7"}
    ctx.bindings["fix_raw"] = fix_raw
    ScriptStep("n5", "应用修正并回写", "script", _seed_step_config("n5")).handle(ctx)
    return ctx


def test_n5_applies_fix_and_resolves(orch_db, monkeypatch) -> None:
    _add_page(orch_db, "kb1", "s1", "制度一", "# 制度一\n\n旧的错误数字")
    _add_feedback(orch_db, "f1", "kb1", "s1", preset="content_error", content="数字错误")
    fix_raw = json.dumps({"slug": "s1", "action": "fix", "confidence": 0.9,
                          "reason": "更新错误数字", "revised_content": "# 制度一\n\n正确的数字 100"}, ensure_ascii=False)
    ctx = _n5_run(monkeypatch, orch_db, fix_raw)

    page = orch_db.execute(select(WikiPage).where(WikiPage.slug == "s1")).scalar_one()
    assert "正确的数字 100" in page.content
    assert orch_db.execute(select(WikiFeedback).where(WikiFeedback.id == "f1")).scalar_one().status == "resolved"
    assert ctx.bindings["fix_summary_applied"] == 1

    report = [json.loads(l) for l in Path(OUT, "report.jsonl").read_text(encoding="utf-8").splitlines() if l]
    assert report[0]["applied"] is True and report[0]["status"] == "resolved"


def test_n5_skip_marks_ignored(orch_db, monkeypatch) -> None:
    _add_page(orch_db, "kb1", "s1", "制度一", "# 制度一\n\n内容")
    _add_feedback(orch_db, "f1", "kb1", "s1", content="没什么问题")
    fix_raw = json.dumps({"slug": "s1", "action": "skip", "confidence": 0.9,
                          "reason": "反馈无明确问题", "revised_content": ""}, ensure_ascii=False)
    ctx = _n5_run(monkeypatch, orch_db, fix_raw)

    assert orch_db.execute(select(WikiFeedback).where(WikiFeedback.id == "f1")).scalar_one().status == "ignored"
    assert ctx.bindings.get("fix_summary_applied") == 0
    page = orch_db.execute(select(WikiPage).where(WikiPage.slug == "s1")).scalar_one()
    assert page.content == "# 制度一\n\n内容"  # 页面未被改动


def test_n5_low_confidence_skips(orch_db, monkeypatch) -> None:
    _add_page(orch_db, "kb1", "s1", "制度一", "# 制度一\n\n内容")
    _add_feedback(orch_db, "f1", "kb1", "s1", content="可能有点问题")
    fix_raw = json.dumps({"slug": "s1", "action": "fix", "confidence": 0.3,
                          "reason": "不确定", "revised_content": "# 制度一\n\n改动"}, ensure_ascii=False)
    _n5_run(monkeypatch, orch_db, fix_raw)
    assert orch_db.execute(select(WikiFeedback).where(WikiFeedback.id == "f1")).scalar_one().status == "ignored"


def test_n5_unparseable_output_records_skipped(orch_db, monkeypatch) -> None:
    _add_page(orch_db, "kb1", "s1", "制度一", "# 制度一\n\n内容")
    _add_feedback(orch_db, "f1", "kb1", "s1", content="有问题")
    ctx = _n5_run(monkeypatch, orch_db, "模型输出的非 JSON 文本")
    assert orch_db.execute(select(WikiFeedback).where(WikiFeedback.id == "f1")).scalar_one().status == "ignored"
    report = [json.loads(l) for l in Path(OUT, "report.jsonl").read_text(encoding="utf-8").splitlines() if l]
    assert report[0]["action"] == "skip" and "解析" in report[0]["reason"]


# ───────────────────────── 定时任务 handler ─────────────────────────

def test_handler_drives_tape_per_kb_prod(monkeypatch, orch_db) -> None:
    import worker.tasks.feedback_fix as ff

    _add_feedback(orch_db, "f1", "kb1", "s1", content="问题A")
    _add_feedback(orch_db, "f2", "kb2", "s2", preset="outdated", content="问题B")

    from api.models.orchestration import Tape

    tape = Tape(id="t" * 32, tape_name="wiki反馈修正", tape_type="feedback_fix", status="effective")
    orch_db.add(tape)
    orch_db.commit()

    calls: list[dict] = []

    def fake_execute_tape(db, tape_id, inputs, env=None):
        calls.append({"tape_id": tape_id, "inputs": inputs, "env": env})
        return {"success": True, "run_id": f"run_{inputs['kb_id']}"}

    monkeypatch.setattr("api.services.orchestration.execute_tape", fake_execute_tape)
    monkeypatch.setattr(ff, "_wait_run_terminal", lambda run_id, timeout_s: "success")
    _use_db(monkeypatch, orch_db)

    result = ff.run_feedback_fix({})
    assert result["success"] is True
    out = result["output"]
    assert out["kbs_with_feedback"] == 2
    assert [c["env"] for c in calls] == ["prod", "prod"]
    assert [c["inputs"]["kb_id"] for c in calls] == ["kb1", "kb2"]
    assert [r["status"] for r in out["runs"]] == ["success", "success"]


def test_handler_dry_run_does_not_execute(monkeypatch, orch_db) -> None:
    import worker.tasks.feedback_fix as ff

    _add_feedback(orch_db, "f1", "kb1", "s1", content="问题A")
    calls: list[dict] = []
    monkeypatch.setattr(
        "api.services.orchestration.execute_tape",
        lambda db, tape_id, inputs, env=None: calls.append({"env": env}) or {"run_id": "x"},
    )
    _use_db(monkeypatch, orch_db)
    result = ff.run_feedback_fix({"dry_run": True})
    assert result["success"] is True
    assert calls == []
    assert result["output"]["runs"][0]["action"] == "dry_run"


def test_handler_no_feedback_returns_ok(monkeypatch, orch_db) -> None:
    import worker.tasks.feedback_fix as ff

    _use_db(monkeypatch, orch_db)
    result = ff.run_feedback_fix({})
    assert result["success"] is True
    assert result["output"]["kbs_with_feedback"] == 0


def test_handler_missing_tape_fails(monkeypatch, orch_db) -> None:
    import worker.tasks.feedback_fix as ff

    _add_feedback(orch_db, "f1", "kb1", "s1", content="问题A")
    _use_db(monkeypatch, orch_db)
    result = ff.run_feedback_fix({})
    assert result["success"] is False
    assert "未找到编排" in result["error"]