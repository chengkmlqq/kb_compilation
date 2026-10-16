"""doc_process 摘要驱动自动领域构建（题材分析链路）测试。

场景（2026-10-16 用户需求）：知识库创建时未选构建引擎、上传文件也未选时，
构建任务等摘要 run 结束 → 题材分析编排（LLM 基于摘要 + 平台已有领域构建
编排划分）判定领域 → 用判定的领域编排启动构建；任一环节缺失/失败/超时
即停止、不构建（2026-10-16 起废除技能直跑兜底）。

测试通过 monkeypatch 屏蔽编排 run 的真实调度（等待/读取/建 run），聚焦
_enqueue_domain_guided_build 的分支语义。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models.framework import Dim
import worker.tasks.doc_process as dp


@pytest.fixture()
def dgb_env(monkeypatch) -> sessionmaker:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    import api.db as db_mod

    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda: Session)
    return Session


def _patch(monkeypatch, **kwargs) -> None:
    for name, val in kwargs.items():
        monkeypatch.setattr(dp, name, val)


def test_domain_resolve_tape_id_reads_dim(dgb_env) -> None:
    Session = dgb_env
    db = Session()
    db.add(Dim(
        id="dim-resolve-1", dim_group="SYSTEM_CONFIG", dim_code="DOMAIN_RESOLVE_TAPE",
        dim_value="tape-abc", dim_desc="", parent_dim_code=None, seq=41, state="1",
    ))
    db.commit()
    db.close()
    assert dp._domain_resolve_tape_id() == "tape-abc"


def test_domain_resolve_tape_id_missing_dim_returns_empty(dgb_env) -> None:
    assert dp._domain_resolve_tape_id() == ""


def test_dgb_param_not_configured_skips_build(dgb_env, monkeypatch) -> None:
    """DOMAIN_RESOLVE_TAPE 未配置 → 停止、不构建（不再回退技能直跑）。"""
    _patch(monkeypatch,
           _domain_resolve_tape_id=lambda: "",
           _enqueue_wiki_skill_task=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应再直跑")))
    out = dp._enqueue_domain_guided_build("kb-1", "doc-1", summary_run_id=None)
    assert out["domain_guided"] is False
    assert out["build_skipped"] is True
    assert out["build_skipped_reason"] == "DOMAIN_RESOLVE_TAPE 未配置"
    assert out["wiki_skill_triggered"] is False


def test_dgb_summary_not_triggered_skips_build(dgb_env, monkeypatch) -> None:
    """摘要未触发（策略 off 等）→ 无依据，停止、不构建。"""
    _patch(monkeypatch,
           _domain_resolve_tape_id=lambda: "tape-resolve",
           _enqueue_wiki_skill_task=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应再直跑")))
    out = dp._enqueue_domain_guided_build("kb-1", "doc-1", summary_run_id=None)
    assert out["domain_guided"] is False
    assert out["build_skipped"] is True
    assert out["build_skipped_reason"] == "摘要未触发"
    assert out["wiki_skill_triggered"] is False


def test_dgb_summary_failed_skips_build(dgb_env, monkeypatch) -> None:
    """摘要 run 失败/超时 → 停止、不构建（且不启动题材分析 run、不直跑）。"""
    _patch(monkeypatch,
           _domain_resolve_tape_id=lambda: "tape-resolve",
           _wait_run_terminal=lambda run_id, timeout_s, poll_s=2.0: "failed",
           _enqueue_resolve_run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应触发")),
           _enqueue_wiki_skill_task=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应再直跑")))
    out = dp._enqueue_domain_guided_build("kb-1", "doc-1", summary_run_id="run-sum")
    assert out["domain_guided"] is False
    assert out["summary_run_status"] == "failed"
    assert "摘要未成功" in out["build_skipped_reason"]
    assert out["wiki_skill_triggered"] is False


def test_dgb_success_selects_domain_tape(dgb_env, monkeypatch) -> None:
    """摘要成功 → 题材分析 success 命中领域 → 用选定领域编排启动构建。"""
    built: dict = {}

    def fake_build(kb_id, document_id, tape_id, parent_job_id=None, cookie="", domain=""):
        built.update(kb_id=kb_id, document_id=document_id, tape_id=tape_id, domain=domain)
        return True

    _patch(monkeypatch,
           _domain_resolve_tape_id=lambda: "tape-resolve",
           _wait_run_terminal=lambda run_id, timeout_s, poll_s=2.0: "success",
           _enqueue_resolve_run=lambda *a, **k: "run-resolve",
           _read_run_binding=lambda run_id, key: {"domain": "法务与合同管理",
                                                   "resolved_tape_id": "tape-law",
                                                   "resolved_tape_name": "w"}.get(key),
           _enqueue_build_engine_task=fake_build,
           _enqueue_wiki_skill_task=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应回退")))
    out = dp._enqueue_domain_guided_build("kb-1", "doc-1", parent_job_id="DOC_1",
                                          cookie="c1", summary_run_id="run-sum")
    assert out["domain_guided"] is True
    assert out["domain"] == "法务与合同管理"
    assert out["resolved_tape_id"] == "tape-law"
    assert out["build_engine_triggered"] is True
    assert built == {"kb_id": "kb-1", "document_id": "doc-1", "tape_id": "tape-law", "domain": "法务与合同管理"}


def test_dgb_resolve_no_match_skips_build(dgb_env, monkeypatch) -> None:
    """题材分析成功但未匹配到领域（resolved_tape_id 空）→ 停止、不构建。"""
    _patch(monkeypatch,
           _domain_resolve_tape_id=lambda: "tape-resolve",
           _wait_run_terminal=lambda run_id, timeout_s, poll_s=2.0: "success",
           _enqueue_resolve_run=lambda *a, **k: "run-resolve",
           _read_run_binding=lambda run_id, key: {"domain": "", "resolved_tape_id": ""}.get(key),
           _enqueue_wiki_skill_task=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应再直跑")))
    out = dp._enqueue_domain_guided_build("kb-1", "doc-1", summary_run_id="run-sum")
    assert out["domain_guided"] is True
    assert "未匹配到领域" in out["build_skipped_reason"]
    assert out["wiki_skill_triggered"] is False


def test_dgb_build_engine_run_injects_kid_and_doc_name(dgb_env, monkeypatch) -> None:
    """2026-10-15 修复：构建编排 run 的 inputs 必须注入 kid/doc_name/source_file
    （wiki构建-* 领域编排 n01 输入契约，缺失则 n03 raise「缺少 kid」整个编排失败）。"""
    import api.services.orchestration as orch_mod
    from api.models.knowledge import KbDocument

    Session = dgb_env
    db = Session()
    db.add(KbDocument(id="doc-k1", kb_id="kb-1", file_name="测试文件.docx", parse_state="READY"))
    db.commit()
    db.close()

    captured: dict = {}

    def fake_create(db_, tape_id, inputs, env):
        captured["inputs"] = inputs
        return {"run_id": "run-1", "run_env": "prod"}

    import worker.celery_app as celery_mod

    class FakeCelery:
        def send_task(self, *a, **k):
            pass

    monkeypatch.setattr(celery_mod, "celery_app", FakeCelery())
    monkeypatch.setattr(orch_mod, "_create_tape_run", fake_create)
    ok = dp._enqueue_build_engine_task("kb-1", "doc-k1", "tape-x", domain="采购与供应链")
    assert ok is True
    ins = captured["inputs"]
    assert ins["kid"] == "doc-k1"
    assert ins["doc_name"] == "测试文件.docx"
    assert ins["source_file"] == "测试文件.docx"
    assert ins["domain"] == "采购与供应链"


def test_dgb_resolve_failed_skips_build(dgb_env, monkeypatch) -> None:
    """题材分析 run 失败 → 停止、不构建（不直跑）。"""
    _patch(monkeypatch,
           _domain_resolve_tape_id=lambda: "tape-resolve",
           _enqueue_resolve_run=lambda *a, **k: "run-resolve",
           _read_run_binding=lambda *a, **k: None,
           _enqueue_wiki_skill_task=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应再直跑")))
    # _wait_run_terminal 第一次返回 success（摘要），第二次返回 failed（题材分析）
    states = iter(["success", "failed"])
    _patch(monkeypatch,
           _wait_run_terminal=lambda run_id, timeout_s, poll_s=2.0: next(states))
    out = dp._enqueue_domain_guided_build("kb-1", "doc-1", summary_run_id="run-sum")
    assert out["domain_guided"] is True
    assert "题材分析未成功" in out["build_skipped_reason"]
    assert out["wiki_skill_triggered"] is False