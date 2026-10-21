#!/usr/bin/env python
"""生成单维度质量评估编排种子（2026-10-21 质量评估拆分）。

参考「质量评估-QA」（三维度合一），拆出三个独立单维度评估编排：

- 文档转换评估（dim=doc）：检查 kb_document 解析/转换质量（parse_state 分布、
  失败原因、空转换产物）
- 切片评估（dim=chunk）：复用「质量评估-QA」的切片质量检查（QC_CODE）
- 图数据评估（dim=graph）：复用「质量评估-QA」的图数据质量检查（QG_CODE）

每个编排 4 节点：输入定义 def → 检查 script（产出 bindings[<dim>_r]）→
汇总落库 script（按 dim 写 kb_quality_run 对应 metrics 列 + kb_quality_report
单条）→ 完成汇总 print。落库语义对齐「质量评估-QA」n08（quality_id 传空则自建行）。

用法（仓库根目录）:
  uv run python scripts/gen_quality_dim_tapes.py
产物: scripts/seed_tape_data/文档转换评估.json / 切片评估.json / 图数据评估.json
（由 seed_framework_db._seed_tapes_from_exports 按 tape_name 幂等播种）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.seed_framework_db import QG_CODE, QC_CODE  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "seed_tape_data"

# ── 文档转换质量检查（dim=doc）──────────────────────────────
QD_CODE = """\

import json
from api.db import get_sessionmaker
from sqlalchemy import text

_kb = bindings.get("kb_id") or ""
_docs = [d.strip() for d in str(bindings.get("doc_ids") or "").split(",") if d.strip()] or None
_db = get_sessionmaker()()
try:
    if _docs:
        _cond = "kb_id=:kb AND id IN :ids"
        _params = {"kb": _kb, "ids": tuple(_docs)}
    else:
        _cond = "kb_id=:kb"
        _params = {"kb": _kb}
    _rows = _db.execute(
        text(f"SELECT id, file_name, file_ext, parse_state, parse_error, file_size FROM kb_document WHERE {_cond}"),
        _params,
    ).all()
finally:
    _db.close()

if not _rows:
    _r = {"metrics": {"docs": 0, "ready": 0, "failed": 0, "pending": 0, "health_score": 0},
          "issues": [{"dim": "doc", "issue_type": "no_docs", "detail": "无文档数据(未上传或知识库为空)"}],
          "attribution": [{"dim": "doc", "cause": "知识库内无文档记录", "suggestion": "先上传/导入文档再评估"}]}
else:
    _states = {}
    _failed = []
    _ready_ids = []
    for _id, _fn, _ext, _ps, _perr, _fsize in _rows:
        _states[str(_ps or "")] = _states.get(str(_ps or ""), 0) + 1
        if str(_ps) == "FAILED":
            _failed.append({"doc_id": _id, "file_name": _fn, "error": str(_perr or "")[:200]})
        elif str(_ps) == "READY":
            _ready_ids.append(_id)
    # 已就绪但无切片的文档 = 空转换产物（解析成功但没产出文本切片）
    _empty_conv = 0
    if _ready_ids:
        try:
            _chunks = dict(
                _db.execute(
                    text("SELECT document_id, count(*) FROM doc_chunk WHERE kb_id=:kb AND document_id IN :ids AND enabled=1 GROUP BY document_id"),
                    {"kb": _kb, "ids": tuple(_ready_ids)},
                ).all()
            )
            _empty_conv = sum(1 for _rid in _ready_ids if _chunks.get(_rid, 0) == 0)
        except Exception:
            pass
    _n = len(_rows)
    _ready = _states.get("READY", 0)
    _failed_n = _states.get("FAILED", 0)
    _pending = _states.get("PENDING", 0) + _states.get("PARSING", 0) + _states.get("EMBEDDING", 0)
    _issues = []
    _issues.append({"dim": "doc", "issue_type": "parse_failed",
                    "detail": f"{_failed_n} 个文档转换失败" + (f"，示例: {json.dumps(_failed[:3], ensure_ascii=False)}" if _failed else "")}
                   ) if _failed_n else None
    if _empty_conv:
        _issues.append({"dim": "doc", "issue_type": "empty_conversion",
                        "detail": f"{_empty_conv} 个文档解析成功但无切片(空转换产物)"})
    if _pending:
        _issues.append({"dim": "doc", "issue_type": "conversion_pending",
                        "detail": f"{_pending} 个文档仍在解析/向量化处理中"})
    _issues = [i for i in _issues if i]
    _fail_r = _failed_n / max(_n, 1)
    _empty_r = _empty_conv / max(_n, 1)
    _score = max(0, int(100.0 - _fail_r * 90 - _empty_r * 50))
    _metrics = {"docs": _n, "ready": _ready, "failed": _failed_n, "pending": _pending,
                "empty_conversion": _empty_conv, "by_state": _states,
                "fail_ratio": round(_fail_r, 4), "health_score": _score}
    _attr = []
    if _fail_r > 0:
        _attr.append({"dim": "doc", "cause": f"{_failed_n} 个文档转换失败 → 格式不支持/文件损坏/解析器异常",
                      "suggestion": "查看失败详情(parse_error)，换支持格式或修复文件后重传"})
    if _empty_r > 0:
        _attr.append({"dim": "doc", "cause": "解析成功但无切片 → 空文档/纯图片/扫描件未走 OCR",
                      "suggestion": "确认文档含可提取文本；图片型 PDF 需 OCR 支持"})
    _r = {"metrics": _metrics, "issues": _issues, "attribution": _attr}

bindings["doc_r"] = _r
print(f"[doc] 文档转换质量: 健康分 {_r['metrics'].get('health_score')} 问题 {len(_r['issues'])}")

"""

# ── 单维度汇总落库（dim=doc|chunk|graph）────────────────────
# 对齐「质量评估-QA」n08 落库语义：quality_id 传空则自建行；写对应 metrics 列 +
# kb_quality_report 单条（dim）；异常置 failed 不回滚编排。
QSUM_DIM_CODE = """\

import json
import uuid
from api.db import get_sessionmaker
from sqlalchemy import text

_kb = bindings.get("kb_id") or ""
_docs = [d.strip() for d in str(bindings.get("doc_ids") or "").split(",") if d.strip()] or None
_scope = str(bindings.get("scope") or "all")
_qid = str(bindings.get("quality_id") or "").strip()
_dim = str(bindings.get("dim") or "chunk")
_r = bindings.get(_dim + "_r") or {}
_col = {"doc": "doc_metrics", "chunk": "chunk_metrics", "graph": "graph_metrics"}.get(_dim, "chunk_metrics")
_label = {"doc": "文档转换", "chunk": "切片", "graph": "图数据"}.get(_dim, _dim)

_m = _r.get("metrics") or {}
_issues = _r.get("issues") or []
_attr = _r.get("attribution") or []
_score = _m.get("health_score")
_summary = f"评估完成: {_label}质量 {_score} 分 | 问题 {len(_issues)} 条, 归因建议 {len(_attr)} 条"

_db = get_sessionmaker()()
try:
    if _qid:
        # quality.py 已建 queued 占位行 → 更新它（状态流转 queued→success）
        _db.execute(
            text(f"UPDATE kb_quality_run SET status='success', {_col}=:m, issues=:iss, attribution=:attr, summary=:sum, updated_at=CURRENT_TIMESTAMP WHERE id=:rid"),
            {"rid": _qid, "m": json.dumps(_m, ensure_ascii=False),
             "iss": json.dumps(_issues, ensure_ascii=False),
             "attr": json.dumps(_attr, ensure_ascii=False), "sum": _summary},
        )
    else:
        # 兼容独立运行：自建行
        _rid = uuid.uuid4().hex[:32]
        _db.execute(
            text(f"INSERT INTO kb_quality_run (id, kb_id, scope, doc_ids, status, dim, {_col}, issues, attribution, summary, updated_at) VALUES (:id, :kb, :scope, :docs, 'success', :dim, :m, :iss, :attr, :sum, CURRENT_TIMESTAMP)"),
            {"id": _rid, "kb": _kb, "scope": _scope, "docs": json.dumps(_docs or [], ensure_ascii=False),
             "dim": _dim, "m": json.dumps(_m, ensure_ascii=False),
             "iss": json.dumps(_issues, ensure_ascii=False),
             "attr": json.dumps(_attr, ensure_ascii=False), "sum": _summary},
        )
        _qid = _rid
    _db.execute(text("DELETE FROM kb_quality_report WHERE run_id=:rid AND dim=:dim"), {"rid": _qid, "dim": _dim})
    _db.execute(
        text("INSERT INTO kb_quality_report (id, run_id, kb_id, doc_id, dim, metrics, issues) VALUES (:id, :rid, :kb, :doc, :dim, :m, :i)"),
        {"id": uuid.uuid4().hex[:32], "rid": _qid, "kb": _kb,
         "doc": _docs[0] if _docs and _scope == "single" else None,
         "dim": _dim, "m": json.dumps(_m, ensure_ascii=False),
         "i": json.dumps(_issues, ensure_ascii=False)},
    )
    _db.commit()
except Exception as _e:
    _db.rollback()
    try:
        _db.execute(text("UPDATE kb_quality_run SET status='failed', error=:e, updated_at=CURRENT_TIMESTAMP WHERE id=:rid"),
                    {"e": str(_e)[:500], "rid": _qid or ""})
        _db.commit()
    except Exception:
        pass
    raise
finally:
    _db.close()

bindings["quality_summary"] = _summary
print("[summary] " + _summary)

"""


def _assignments_json() -> str:
    """def 节点 assignments：kb_id/doc_ids/scope/quality_id/dim 五变量（对齐质量评估-QA n01）。"""
    return "[{\"variable\": \"kb_id\", \"expression\": \"bindings.get('kb_id', '')\"}, {\"variable\": \"doc_ids\", \"expression\": \"bindings.get('doc_ids', '')\"}, {\"variable\": \"scope\", \"expression\": \"bindings.get('scope', 'all')\"}, {\"variable\": \"quality_id\", \"expression\": \"bindings.get('quality_id', '')\"}, {\"variable\": \"dim\", \"expression\": \"bindings.get('dim', 'chunk')\"}]"


def _def_node(nid: str) -> dict:
    return {
        "id": nid, "type": "stepNode",
        "data": {"label": "输入定义", "stepInst": "def",
                 "config": {"queue": "build", "assignments": _assignments_json()}},
        "position": {"x": 0, "y": 0},
    }


def _script_node(nid: str, label: str, code: str) -> dict:
    return {
        "id": nid, "type": "stepNode",
        "data": {"label": label, "stepInst": "script", "config": {"queue": "build", "code": code}},
        "position": {"x": 0, "y": 0},
    }


def _print_node(nid: str) -> dict:
    return {
        "id": nid, "type": "stepNode",
        "data": {"label": "完成汇总", "stepInst": "print",
                 "config": {"queue": "build", "text": "{{quality_summary}}"}},
        "position": {"x": 0, "y": 0},
    }


def _step(seq: int, nid: str, inst: str, label: str, pre: list[str], nxt: list[str], config: dict) -> dict:
    return {
        "step_seq": seq, "step_id": nid, "step_inst": inst, "step_label": label,
        "pre_step_ids": pre, "next_step_ids": nxt, "queue_name": "build", "step_config": config,
    }


def build_tape(tape_name: str, tape_label: str, descr: str, check_code: str, dim: str) -> dict:
    nodes = [
        _def_node("n1"),
        _script_node("n2", "质量检查", check_code),
        _script_node("n3", "汇总落库", QSUM_DIM_CODE),
        _print_node("n4"),
    ]
    edges = [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
    ]
    steps = [
        _step(0, "n1", "def", "输入定义", [], ["n2"], {
            "queue": "build", "assignments": _assignments_json(),
        }),
        _step(1, "n2", "script", "质量检查", ["n1"], ["n3"], {"queue": "build", "code": check_code}),
        _step(2, "n3", "script", "汇总落库", ["n2"], ["n4"], {"queue": "build", "code": QSUM_DIM_CODE}),
        _step(3, "n4", "print", "完成汇总", ["n3"], [], {"queue": "build", "text": "{{quality_summary}}"}),
    ]
    return {
        "exportedAt": "",
        "schemaVersion": 1,
        "type": "kb.orchestration.draft",
        "draft": {
            "tapeName": tape_name,
            "tapeLabel": tape_label,
            "tapeDescr": descr,
            "tapeType": "general",
            "category": "通用",
            "status": "draft",
            "nodes": nodes,
            "edges": edges,
            "execParams": {"kb_id": "", "doc_ids": "", "scope": "all", "quality_id": "", "dim": dim},
            "steps": steps,
        },
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tapes = [
        build_tape(
            "文档转换评估",
            "文档转换质量评估（解析/转换成功率、失败原因、空转换产物）",
            "单维度评估：检查知识库文档的解析/转换质量——parse_state 分布（READY/FAILED/"
            "处理中）、失败原因、解析成功但无切片的空转换产物。输入 bindings：kb_id / "
            "doc_ids(逗号分隔,空=整库) / scope / quality_id / dim=doc。结果落 kb_quality_run.doc_metrics"
            " + kb_quality_report(dim=doc)，供文档转换评估页面查询。",
            QD_CODE,
            "doc",
        ),
        build_tape(
            "切片评估",
            "切片质量评估（长度分布/截断/重复）",
            "单维度评估：检查文档切片质量——切片长度分布（过短<50字/过长>2000字）、"
            "截断标记、近似重复。输入 bindings：kb_id / doc_ids(逗号分隔,空=整库) / "
            "scope / quality_id / dim=chunk。结果落 kb_quality_run.chunk_metrics + "
            "kb_quality_report(dim=chunk)，供切片评估页面查询。",
            QC_CODE,
            "chunk",
        ),
        build_tape(
            "图数据评估",
            "图数据质量评估（哑节点/边类型/可回溯性）",
            "单维度评估：检查 Neo4j 图数据质量——哑节点（无 attributes）、非法边类型、"
            "不可回溯边（缺 rule_slug/note）、节点对应 wiki 页缺失。输入 bindings：kb_id / "
            "doc_ids(逗号分隔,空=整库) / scope / quality_id / dim=graph。结果落 "
            "kb_quality_run.graph_metrics + kb_quality_report(dim=graph)，供图数据评估页面查询。",
            QG_CODE,
            "graph",
        ),
    ]
    for t in tapes:
        name = t["draft"]["tapeName"]
        fp = OUT_DIR / f"{name}.json"
        fp.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"OK {fp.name}（{len(t['draft']['nodes'])} 节点 / {len(t['draft']['steps'])} 步骤, dim={t['draft']['execParams']['dim']}）")


if __name__ == "__main__":
    main()