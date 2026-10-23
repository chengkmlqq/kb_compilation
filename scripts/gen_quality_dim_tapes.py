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
import re
from collections import Counter
from api.db import get_sessionmaker
from sqlalchemy import bindparam, text

# ── 乱码检测（纯程序，无 LLM）────────────────────────────────────
_GARBLED_WORDS = ("锟斤拷", "烫烫烫", "屯屯屯", "锘挎", "鈥", "Ã©", "Ã¤", "Ã¥", "Ã¼")


def _meta_engine(_m):
    # doc_chunk.meta 取 parse_engine（兼容 dict / JSON 字符串两种形态）
    if _m is None:
        return ""
    if isinstance(_m, dict):
        return str(_m.get("parse_engine") or "")
    if isinstance(_m, str):
        try:
            _d = json.loads(_m)
            return str(_d.get("parse_engine") or "") if isinstance(_d, dict) else ""
        except Exception:
            return ""
    return ""


def _garbled_check(_text):
    # 返回 (hits, garbled, sample)。garbled=命中替换符/代理区/乱码特征词；控制字符仅备注
    _hits = []
    _n = _text.count("\ufffd")
    if _n:
        _hits.append(f"替换字符 U+FFFD ×{_n}")
    _n = sum(1 for _c in _text if 0xD800 <= ord(_c) <= 0xDFFF)
    if _n:
        _hits.append(f"非法代理区字符 ×{_n}")
    for _w in _GARBLED_WORDS:
        _n = _text.count(_w)
        if _n:
            _hits.append(f"乱码特征「{_w}」×{_n}")
    _n = sum(1 for _c in _text if (ord(_c) < 9) or (14 <= ord(_c) < 32))
    if _n:
        _hits.append(f"非法控制字符 ×{_n}（不计乱码）")
    _garbled = bool(re.search(r"替换字符|非法代理区|乱码特征", " ".join(_hits)))
    _sample = ""
    for _i, _c in enumerate(_text):
        if _c == "\ufffd" or 0xD800 <= ord(_c) <= 0xDFFF:
            _sample = _text[max(0, _i - 30):_i + 30]
            break
    if not _sample:
        for _w in _GARBLED_WORDS:
            _j = _text.find(_w)
            if _j >= 0:
                _sample = _text[max(0, _j - 30):_j + 40]
                break
    return _hits, _garbled, _sample


def _fn_garbled_check(_fn):
    # 返回 (hits, garbled)。检测文件名本身乱码（编码错位/解码错误产物）：
    # ① GBK→CP437/拉丁错位：框线/数学/希腊/西里尔符号堆砌（╓╞╢╚┴≈│╠╣▄└φ╞¿）
    # ② UTF-8→GBK 错位：高频乱码字（鎴戜滑鐨勬柊浜у搧 等）+ 拉丁扩展混排
    _hits = []
    _n = _fn.count("\ufffd")
    if _n:
        _hits.append(f"替换字符 U+FFFD ×{_n}")
    # UTF-8 字节流被按 GBK/GB2312 解码出的高频错位字（正常文件名几乎不出现）
    _FN_BAD_WORDS = ("鎴", "戜", "鐨", "勬", "柊", "浜", "搧", "鏄", "鍒", "娑",
                     "鎵", "鏈", "寰", "寮", "涓", "杩", "涔", "绠", "楄", "瀵",
                     "缂", "璁", "储", "锻", "忓", "鍙", "鍥", "鎺", "鐢", "鏂")
    _nw = sum(_fn.count(_w) for _w in _FN_BAD_WORDS)
    if _nw >= 2:
        _hits.append(f"编码错位特征字 ×{_nw}")
    # 错位特征符号区间：拉丁补充-1/扩展、希腊、西里尔、数学运算符、盒绘线、块状
    _bad = 0
    _bad_chars: list[str] = []
    for _c in _fn:
        _o = ord(_c)
        if (
            (0x00C0 <= _o <= 0x024F)  # 拉丁补充-1/扩展A/B Ã¤Ã©ŽÅÈ…
            or (0x0370 <= _o <= 0x03FF)  # 希腊字母 φψα…
            or (0x0400 <= _o <= 0x04FF)  # 西里尔字母 ущф…
            or (0x2200 <= _o <= 0x22FF)  # 数学运算符 ≈≤≥≠…
            or (0x2500 <= _o <= 0x257F)  # 盒绘线 ╓╞╢╚┴│╠╣└┼…
            or (0x2580 <= _o <= 0x259F)  # 块状 ▄▀█…
        ):
            _bad += 1
            if len(_bad_chars) < 8:
                _bad_chars.append(_c)
    _nz = sum(1 for _c in _fn if ord(_c) > 127)
    # 阈值：错位符号 ≥3 个，或占非 ASCII 字符 >50%（排除如「3×4」单符号情形）
    if _bad >= 3 or (_nz and _bad / _nz > 0.5):
        _hits.append(f"编码错位符号 ×{_bad}（{' '.join(_bad_chars)}）")
    _garbled = bool(re.search(r"替换字符|编码错位特征字|编码错位符号", " ".join(_hits)))
    return _hits, _garbled


_kb = bindings.get("kb_id") or ""
_docs = [d.strip() for d in str(bindings.get("doc_ids") or "").split(",") if d.strip()] or None
_db = get_sessionmaker()()
try:
    if _docs:
        _stmt = text("SELECT id, file_name, file_ext, parse_state, parse_error, file_size, chunk_count FROM kb_document WHERE kb_id=:kb AND id IN :ids").bindparams(bindparam("ids", expanding=True))
        _params = {"kb": _kb, "ids": tuple(_docs)}
    else:
        _stmt = text("SELECT id, file_name, file_ext, parse_state, parse_error, file_size, chunk_count FROM kb_document WHERE kb_id=:kb")
        _params = {"kb": _kb}
    _rows = _db.execute(_stmt, _params).all()

    # ── 批量取切片（转换引擎 + 文本聚合，供乱码检测）──
    _texts: dict[str, list[str]] = {}
    _engines: dict[str, str] = {}
    if _rows:
        try:
            _chunk_rows = _db.execute(
                text("SELECT document_id, content, meta FROM doc_chunk WHERE kb_id=:kb AND document_id IN :ids AND enabled=1 AND chunk_type='text' ORDER BY document_id, seq").bindparams(bindparam("ids", expanding=True)),
                {"kb": _kb, "ids": tuple(r[0] for r in _rows)},
            ).all()
            for _cid, _content, _meta in _chunk_rows:
                _texts.setdefault(_cid, []).append(_content)
                if _cid not in _engines:
                    _engines[_cid] = _meta_engine(_meta)
        except Exception:
            _texts, _engines = {}, {}

    # ── 批量取转换任务耗时（modo_job 最近一次解析任务）──
    _durations: dict[str, int] = {}
    if _rows:
        try:
            _job_rows = _db.execute(
                text("SELECT doc_id, duration_ms, create_time FROM modo_job WHERE doc_id IN :ids AND task_class LIKE '%Document%' ORDER BY create_time DESC").bindparams(bindparam("ids", expanding=True)),
                {"ids": tuple(r[0] for r in _rows)},
            ).all()
            for _jid, _dur, _ct in _job_rows:
                if _jid not in _durations and _dur:
                    _durations[_jid] = int(_dur)
        except Exception:
            _durations = {}
finally:
    _db.close()

if not _rows:
    _r = {"metrics": {"docs": 0, "ready": 0, "failed": 0, "pending": 0, "garbled": 0,
                      "empty_conversion": 0, "health_score": 0, "per_doc": []},
          "issues": [{"dim": "doc", "issue_type": "no_docs", "detail": "无文档数据(未上传或知识库为空)"}],
          "attribution": [{"dim": "doc", "cause": "知识库内无文档记录", "suggestion": "先上传/导入文档再评估"}]}
else:
    _issues: list[dict] = []
    _per_doc: list[dict] = []
    for _id, _fn, _ext, _ps, _perr, _fsize, _cc in _rows:
        _ps = str(_ps or "")
        _txt = "".join(_texts.get(_id) or [])
        _eng = _engines.get(_id) or ""
        _dur = _durations.get(_id)
        _score = 100
        _garbled, _garbled_hits, _item_issues = False, [], []
        _fn_garbled, _txt_garbled = False, False
        # 文件名乱码检测（独立于转换状态：编码错位文件名在任何状态下都应标出）
        _fn_hits, _fn_garbled = _fn_garbled_check(str(_fn or ""))
        if _fn_garbled:
            _score -= 20
            _garbled = True
            _garbled_hits.extend(f"[文件名] {h}" for h in _fn_hits)
            _item_issues.append({"dim": "doc", "doc_id": _id, "file_name": _fn,
                                 "issue_type": "garbled_filename",
                                 "detail": f"文件名乱码: {str(_fn or '')[:120]}（{'；'.join(_fn_hits[:3])}）"})
        if _ps == "FAILED":
            _score -= 100
            _item_issues.append({"dim": "doc", "doc_id": _id, "file_name": _fn,
                                 "issue_type": "parse_failed",
                                 "detail": f"转换失败: {str(_perr or '')[:200]}"})
        elif _ps in ("PENDING", "PARSING", "EMBEDDING"):
            _score -= 50
            _item_issues.append({"dim": "doc", "doc_id": _id, "file_name": _fn,
                                 "issue_type": "conversion_pending",
                                 "detail": f"文档仍在处理中（{_ps}）"})
        elif _ps == "READY":
            if not _txt:
                _score -= 70
                _item_issues.append({"dim": "doc", "doc_id": _id, "file_name": _fn,
                                     "issue_type": "empty_conversion",
                                     "detail": "解析成功但无文本切片（空文档/纯图片/扫描件未走 OCR）"})
            else:
                _txt_hits, _txt_garbled, _sample = _garbled_check(_txt)
                if _txt_garbled:
                    _score -= 40
                    _garbled = True
                    _garbled_hits.extend(h for h in _txt_hits if "非法控制字符" not in h)
                    _detail = "转换文本乱码: " + "; ".join(_txt_hits[:4])
                    if _sample:
                        _detail += f"（样例: {_sample[:120]!r}）"
                    _item_issues.append({"dim": "doc", "doc_id": _id, "file_name": _fn,
                                         "issue_type": "garbled_text", "detail": _detail})
        _issues.extend(_item_issues)
        _per_doc.append({
            "doc_id": _id, "file_name": _fn, "file_ext": _ext or "",
            "file_size": _fsize, "parse_state": _ps,
            "parse_error": str(_perr or "")[:500],
            "parse_engine": _eng, "duration_ms": _dur,
            "chunk_count": int(_cc or 0), "md_length": len(_txt),
            "garbled": _garbled, "garbled_hits": _garbled_hits[:6],
            "file_name_garbled": _fn_garbled, "content_garbled": _txt_garbled,
            "score": max(0, _score),
        })
    _garbled_n = sum(1 for p in _per_doc if p["garbled"])
    _scores = [p["score"] for p in _per_doc]
    _durs = [p["duration_ms"] for p in _per_doc if p["duration_ms"]]
    _metrics = {
        "docs": len(_per_doc),
        "ready": sum(1 for p in _per_doc if p["parse_state"] == "READY"),
        "failed": sum(1 for p in _per_doc if p["parse_state"] == "FAILED"),
        "pending": sum(1 for p in _per_doc if p["parse_state"] in ("PENDING", "PARSING", "EMBEDDING")),
        "garbled": _garbled_n,
        "empty_conversion": sum(1 for i in _issues if i["issue_type"] == "empty_conversion"),
        "avg_score": round(sum(_scores) / len(_scores)) if _scores else 0,
        "health_score": round(sum(_scores) / len(_scores)) if _scores else 0,  # 汇总分=平均单文件分（页面兼容）
        "avg_duration_ms": round(sum(_durs) / len(_durs)) if _durs else None,
        "engines": dict(Counter(p["parse_engine"] or "未知" for p in _per_doc)),
        "by_state": dict(Counter(p["parse_state"] for p in _per_doc)),
        "fail_ratio": round(sum(1 for p in _per_doc if p["parse_state"] == "FAILED") / max(len(_per_doc), 1), 4),
        "per_doc": _per_doc,
    }
    _attr = []
    if _metrics["failed"]:
        _attr.append({"dim": "doc", "cause": f"{_metrics['failed']} 个文档转换失败 → 格式不支持/文件损坏/解析器异常",
                      "suggestion": "查看失败详情(parse_error)，换支持格式或修复文件后重传"})
    if _metrics["empty_conversion"]:
        _attr.append({"dim": "doc", "cause": f"{_metrics['empty_conversion']} 个文档解析成功但无文本 → 空文档/纯图片/扫描件未走 OCR",
                      "suggestion": "确认文档含可提取文本；图片型 PDF 需 OCR 支持"})
    if _garbled_n:
        _attr.append({"dim": "doc", "cause": f"{_garbled_n} 个文档转换文本乱码（替换符/编码错位）",
                      "suggestion": "检查源文件编码与转换引擎输出；必要时更换解析引擎后重传"})
    if _metrics["pending"]:
        _attr.append({"dim": "doc", "cause": f"{_metrics['pending']} 个文档仍在解析/向量化处理中",
                      "suggestion": "稍后重新评估"})
    _r = {"metrics": _metrics, "issues": _issues, "attribution": _attr}

bindings["doc_r"] = _r
print(f"[doc] 文档转换质量: 健康分 {_r['metrics'].get('health_score')} 文档 {_r['metrics'].get('docs')} 问题 {len(_r['issues'])}")

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
_summary = f"评估完成: {_label}质量 {_score if isinstance(_score, (int, float)) else '-'} 分 | 问题 {len(_issues)} 条, 归因建议 {len(_attr)} 条"

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
    # ── 文档转换评估（dim=doc）：单文件粒度明细落库，每文件一行，按 kb 整批覆盖 ──
    if _dim == "doc":
        _db.execute(text("DELETE FROM kb_quality_doc_eval WHERE kb_id=:kb"), {"kb": _kb})
        _per = _m.get("per_doc") or []
        for _p in _per:
            _db.execute(
                text("INSERT INTO kb_quality_doc_eval (id, kb_id, doc_id, run_id, file_name, file_ext, file_size, parse_state, parse_error, parse_engine, duration_ms, chunk_count, md_length, garbled, garbled_hits, file_name_garbled, content_garbled, score) VALUES (:id, :kb, :did, :rid, :fn, :ext, :fs, :ps, :perr, :eng, :dur, :cc, :mdl, :gb, :gh, :fngb, :ctgb, :sc)"),
                {"id": uuid.uuid4().hex[:32], "kb": _kb, "did": str(_p.get("doc_id") or ""),
                 "rid": _qid, "fn": str(_p.get("file_name") or ""),
                 "ext": str(_p.get("file_ext") or "")[:32],
                 "fs": _p.get("file_size"), "ps": str(_p.get("parse_state") or ""),
                 "perr": str(_p.get("parse_error") or "")[:500],
                 "eng": str(_p.get("parse_engine") or ""),
                 "dur": _p.get("duration_ms"), "cc": _p.get("chunk_count"),
                 "mdl": _p.get("md_length"), "gb": 1 if _p.get("garbled") else 0,
                 "gh": json.dumps(_p.get("garbled_hits") or [], ensure_ascii=False),
                 "fngb": 1 if _p.get("file_name_garbled") else 0,
                 "ctgb": 1 if _p.get("content_garbled") else 0,
                 "sc": _p.get("score")},
            )
    # ── 切片评估（dim=chunk）：单文件粒度明细落库，每文件一行，按 kb 整批覆盖 ──
    # 兼容两种 per_doc 形态：v3 LLM 版为 list[dict]；程序版（QC_CODE）为 doc_id → 明细 映射
    if _dim == "chunk":
        _db.execute(text("DELETE FROM kb_quality_chunk_eval WHERE kb_id=:kb"), {"kb": _kb})
        _per = _m.get("per_doc") or []
        if isinstance(_per, dict):
            _db2 = get_sessionmaker()()
            try:
                _fnames = dict(_db2.execute(
                    text("SELECT id, file_name FROM kb_document WHERE kb_id=:kb"), {"kb": _kb}
                ).all())
            finally:
                _db2.close()
            _per = [dict(v, file_name=_fnames.get(str(v.get("doc_id") or ""), ""))
                    for v in _per.values()]
        for _p in _per:
            _db.execute(
                text("INSERT INTO kb_quality_chunk_eval (id, kb_id, doc_id, run_id, file_name, chunks, avg_len, short, `long`, truncated, dup, wiki_pages, wiki_start_time, wiki_build_sec, doc_process_sec, wiki_tape, eval_duration_sec, score, conclusion, issues, error) VALUES (:id, :kb, :did, :rid, :fn, :ch, :alen, :sh, :lg, :tr, :dp, :wp, :wst, :wbs, :dps, :wt, :eds, :sc, :conc, :iss, :err)"),
                {"id": uuid.uuid4().hex[:32], "kb": _kb, "did": str(_p.get("doc_id") or ""),
                 "rid": _qid, "fn": str(_p.get("file_name") or "")[:255],
                 "ch": _p.get("chunks"), "alen": _p.get("avg_len"),
                 "sh": _p.get("short"), "lg": _p.get("long"),
                 "tr": _p.get("truncated"), "dp": _p.get("dup"),
                 "wp": _p.get("wiki_pages"),
                 "wst": _p.get("wiki_start_time"), "wbs": _p.get("wiki_build_sec"),
                 "dps": _p.get("doc_process_sec"), "wt": str(_p.get("wiki_tape") or "")[:128],
                 "eds": _p.get("eval_duration_sec"), "sc": _p.get("score"),
                 "conc": str(_p.get("conclusion") or "")[:500],
                 "iss": json.dumps(_p.get("issues") or [], ensure_ascii=False),
                 "err": str(_p.get("error") or "")[:500]},
            )
    # ── 图数据评估（dim=graph）：单文件粒度明细落库，每文件一行，按 kb 整批覆盖 ──
    if _dim == "graph":
        _db.execute(text("DELETE FROM kb_quality_graph_eval WHERE kb_id=:kb"), {"kb": _kb})
        for _p in (_m.get("per_doc") or []):
            _db.execute(
                text("INSERT INTO kb_quality_graph_eval (id, kb_id, doc_id, run_id, file_name, nodes, rels, dumb_nodes, untraceable_rels, wiki_missing, score, issues) VALUES (:id, :kb, :did, :rid, :fn, :nd, :rl, :dumb, :utr, :wkm, :sc, :iss)"),
                {"id": uuid.uuid4().hex[:32], "kb": _kb, "did": str(_p.get("doc_id") or ""),
                 "rid": _qid, "fn": str(_p.get("file_name") or "")[:255],
                 "nd": _p.get("nodes"), "rl": _p.get("rels"),
                 "dumb": _p.get("dumb_nodes"), "utr": _p.get("untraceable_rels"),
                 "wkm": _p.get("wiki_missing"), "sc": _p.get("score"),
                 "iss": json.dumps(_p.get("issues") or [], ensure_ascii=False)},
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
            "文档转换质量评估（单文件：状态/引擎/耗时/乱码）",
            "单维度评估：按单文件粒度检查知识库文档的转换质量——文件名/转换状态\n"
            "（parse_state READY/FAILED/处理中）、转换后 MD 文本乱码检测（U+FFFD 替换符/"
            "代理区/编码错位特征词/控制字符）、转换引擎（doc_chunk.meta.parse_engine）、\n"
            "转换耗时（modo_job 最近一次解析任务 duration_ms）。输入 bindings：kb_id /\n"
            "doc_ids(逗号分隔,空=整库) / scope / quality_id / dim=doc。结果落 kb_quality_run."
            "doc_metrics（含 per_doc 单文件明细）+ kb_quality_report(dim=doc)，供文档转换\n"
            "评估页面展示每文件状态/引擎/耗时/乱码/得分。",
            QD_CODE,
            "doc",
        ),
        build_tape(
            "切片评估",
            "切片质量评估（过碎/过长/截断/重复）",
            "单维度评估：检查文档切片自身质量——过短切片占比（<50 字）、过长切片占比（>2000 "
            "字）、截断标记（…/（节选）/[截断]）、近似重复切片（前 200 字 MD5 去重计数）；"
            "按文件粒度输出 per_doc 明细与健康分。输入 bindings：kb_id / doc_ids(逗号分隔,空=整库) / "
            "scope / quality_id / dim=chunk。结果落 kb_quality_run.chunk_metrics + "
            "kb_quality_report(dim=chunk) + kb_quality_chunk_eval（每文件一行），供切片评估页面查询。",
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