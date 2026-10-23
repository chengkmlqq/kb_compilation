# 切片评估 v3（2026-10-23）：单文件粒度，LLM 对照完整原文评估该文档构建出的
# Wiki 页面——① 内容错误 ② 内容缺失 ③ wiki 是否有意义；同时提取 wiki 构建
# 起始时间与耗时（优先编排 run 记录，回落 wiki 页创建时间）用于展示。
# 2026-10-23 wiki 评估迁移：本脚本为「质量评估-QA」编排 n06「Wiki-原文对比评估」
# 节点代码（原挂靠「切片评估」单维度编排，现迁至 wiki 维度执行与展示）。
import json
import time as _t
from datetime import datetime
from api.db import get_sessionmaker
from sqlalchemy import text

MAX_MD_CHUNKS = 20    # 每文件送入 LLM 的原文切片数（超出均匀抽样）
MAX_MD_CHARS = 1000   # 每个原文切片送入 LLM 的前缀截断长度
MAX_WIKI_PAGES = 12   # 每文件最多送入 LLM 的 wiki 页数（摘要页优先，其余均匀抽样）
MAX_WIKI_CHARS = 2000 # 每个 wiki 页送入 LLM 的截断长度


def _llm(prompt, system="", temperature=0.2, max_tokens=2048, json_mode=True):
    from api.services.chat import ChatClient, load_chat_config, ChatMessage
    _db2 = get_sessionmaker()()
    try:
        cfg = load_chat_config(_db2, model_id=None)
        client = ChatClient(cfg)
        msgs = []
        if system:
            msgs.append(ChatMessage(role="system", content=system))
        msgs.append(ChatMessage(role="user", content=prompt))
        last = None
        for attempt in range(4):
            try:
                raw = client.chat(msgs, temperature=temperature, max_tokens=max_tokens, thinking=False)
                if json_mode:
                    text_ = (raw or "").strip()
                    if text_.startswith("```"):
                        text_ = text_.strip("`")
                        if text_.startswith("json"):
                            text_ = text_[4:].strip()
                    return json.loads(text_)
                return raw or ""
            except Exception as e:
                last = e
                resp = getattr(e, "response", None)
                code = getattr(resp, "status_code", None) if resp is not None else None
                if code == 429:
                    _t.sleep(min(15 * (attempt + 1), 45))
                else:
                    _t.sleep(3)
        raise last
    finally:
        _db2.close()


def _to_dt(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _md5(s):
    import hashlib
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _ref_doc(r):
    """source_refs 条目 → 文档 id（形如 '{document_id}|{file_name}' 或裸 id）。"""
    if not isinstance(r, str):
        return ""
    return str(r).split("|", 1)[0].strip()


_kb = bindings.get("kb_id") or ""
_docs = [d.strip() for d in str(bindings.get("doc_ids") or "").split(",") if d.strip()] or None
_doc_cond = "kb_id=:kb" if not _docs else "kb_id=:kb AND id IN :ids"
_chunk_cond = "kb_id=:kb" if not _docs else "kb_id=:kb AND document_id IN :ids"
_params = {"kb": _kb}
if _docs:
    _params["ids"] = tuple(_docs)

_db = get_sessionmaker()()
try:
    _doc_rows = _db.execute(
        text(f"SELECT id, file_name, created_at, updated_at FROM kb_document WHERE {_doc_cond}"),
        _params,
    ).all()
    _chunk_rows = _db.execute(
        text(f"SELECT document_id, seq, content, created_at FROM doc_chunk WHERE {_chunk_cond} AND enabled=1 ORDER BY document_id, seq"),
        _params,
    ).all()
    # wiki 页窄列 + source_refs（content 按命中页二次回表，避免整库拉正文）
    _wiki_rows = _db.execute(
        text("SELECT id, slug, title, page_type, source_refs, created_at, updated_at FROM wiki_page WHERE kb_id=:kb AND status='active'"),
        {"kb": _kb},
    ).all()
    # wiki 构建编排运行记录（inputs JSON 含文档 id；只拉窄列，避开大 JSON 列）
    _run_rows = _db.execute(
        text("SELECT id, tape_name, status, created_at, updated_at, inputs FROM kb_tape_run "
             "WHERE (tape_name LIKE 'wiki构建-%' OR tape_name LIKE '表构建Wiki%') AND inputs LIKE :kbp"),
        {"kbp": f"%{_kb}%"},
    ).all()
finally:
    _db.close()

_doc_info = {str(d): {"file_name": str(fn or ""), "created_at": ct, "updated_at": ut}
             for d, fn, ct, ut in _doc_rows}
# 文档 → 切片（seq 升序）
_by_doc = {}
for _rid, _seq, _content, _ct in _chunk_rows:
    _by_doc.setdefault(str(_rid), []).append((int(_seq or 0), str(_content or ""), _ct))
# 文档 → wiki 页
_by_doc_wiki = {}
for _pid, _slug, _title, _ptype, _srefs, _pct, _put in _wiki_rows:
    if isinstance(_srefs, str):
        try:
            _srefs = json.loads(_srefs)
        except Exception:
            _srefs = []
    _hit = ""
    for _r in (_srefs or []):
        _rd = _ref_doc(_r)
        if _rd and _rd in _by_doc:
            _hit = _rd
            break
    if not _hit:
        # 摘要页 slug 兜底：summary-<md5(docid)> / doc-<md5前10>
        _flat = str(_slug or "").replace("/", "-")
        for _did in _by_doc:
            _m = _md5(_did)
            if _flat in (f"summary-{_m}", f"doc-{_m[:10]}"):
                _hit = _did
                break
    if _hit:
        _by_doc_wiki.setdefault(_hit, []).append(str(_pid))
# 命中页补 content
_hit_ids = [pid for pids in _by_doc_wiki.values() for pid in pids]
_wiki_content = {}
if _hit_ids:
    _db2 = get_sessionmaker()()
    try:
        _rows2 = _db2.execute(
            text("SELECT id, content FROM wiki_page WHERE id IN :ids"), {"ids": tuple(_hit_ids)}
        ).all()
        _wiki_content = {str(r[0]): str(r[1] or "") for r in _rows2}
    finally:
        _db2.close()
_wiki_meta = {str(r[0]): r for r in _wiki_rows}
# 文档 → wiki 构建 run（最近 success 优先，否则最近一条）
_run_by_doc = {}
for _rid, _tname, _rstatus, _rct, _rut, _inputs in _run_rows:
    try:
        _in = json.loads(_inputs) if isinstance(_inputs, str) else (_inputs or {})
    except Exception:
        _in = {}
    _hit = ""
    for _k in ("document_id", "doc_id", "kid"):
        _v = str(_in.get(_k) or "").strip()
        if _v and _v in _by_doc:
            _hit = _v
            break
    if _hit:
        _run_by_doc.setdefault(_hit, []).append({
            "tape_name": str(_tname or ""), "status": str(_rstatus or ""),
            "created_at": _rct, "updated_at": _rut,
        })

if not _doc_rows:
    _r = {"metrics": {"docs": 0, "files_eval": 0, "chunks": 0, "wiki_pages": 0,
                      "avg_len": 0, "eval_errors": 0, "health_score": 0, "per_doc": []},
          "issues": [{"dim": "wiki", "issue_type": "no_chunks", "detail": "无切片数据(文档未解析或无切片)"}],
          "attribution": [{"dim": "wiki", "cause": "知识库无文档或文档未进入切片阶段",
                           "suggestion": "检查文档解析状态 / 重跑解析"}]}
    _metrics = _r["metrics"]
    _issues = _r["issues"]
else:
    _issues = []
    _per_doc = []
    _scores = []
    _docs_n = len(_by_doc)
    _chunks_n = sum(len(v) for v in _by_doc.values())
    _all_lens = [len(c) for v in _by_doc.values() for _, c, _ in v]
    _avg_len = round(sum(_all_lens) / len(_all_lens), 1) if _all_lens else 0
    _wiki_n = sum(len(v) for v in _by_doc_wiki.values())
    _idx = 0
    for _doc_id in sorted(_by_doc.keys()):
        _idx += 1
        _chunks = sorted(_by_doc[_doc_id], key=lambda x: x[0])
        _lens = [len(c) for _, c, _ in _chunks]
        _n = len(_chunks)
        _d_avg = round(sum(_lens) / _n, 1) if _n else 0
        _dinfo = _doc_info.get(_doc_id, {})
        _pages_meta = [_wiki_meta[p] for p in _by_doc_wiki.get(_doc_id, [])]
        # wiki 构建时间窗：优先编排 run，回落 wiki 页创建时间
        _runs = sorted(_run_by_doc.get(_doc_id, []),
                       key=lambda r: _to_dt(r["created_at"]) or datetime.min)
        _run = None
        for _r in reversed(_runs):
            if _r["status"] == "success":
                _run = _r
                break
        if _run is None and _runs:
            _run = _runs[-1]
        _wstart = _to_dt(_run["created_at"]) if _run else None
        _wend = _to_dt(_run["updated_at"]) if _run else None
        if _wstart is None and _pages_meta:
            _pts = [_to_dt(p[5]) for p in _pages_meta if _to_dt(p[5])]
            if _pts:
                _wstart = min(_pts)
                _wend = max(_pts)
        _wspan = round((_wend - _wstart).total_seconds(), 1) if _wstart and _wend else None
        _wtape = _run["tape_name"] if _run else ""
        _proc_sec = None
        _dc, _du = _to_dt(_dinfo.get("created_at")), _to_dt(_dinfo.get("updated_at"))
        if _dc and _du:
            # kb_document 时间戳混用时区（created_at=服务器本地 / updated_at=UTC），
            # 可能算出负数 → 负值视为无效置空
            _psec = round((_du - _dc).total_seconds(), 1)
            if _psec >= 0:
                _proc_sec = _psec
        _doc_issues = []
        if _n == 0:
            _doc_issues.append({"dim": "wiki", "doc_id": _doc_id, "issue_type": "no_chunks",
                                "detail": "文档无切片（未解析或未进入切片阶段），无法对照评估 wiki"})
            _per_doc.append({"doc_id": _doc_id, "file_name": _dinfo.get("file_name", ""),
                             "chunks": 0, "avg_len": 0, "wiki_pages": 0,
                             "wiki_start_time": _wstart.isoformat() if _wstart else None,
                             "wiki_build_sec": _wspan, "doc_process_sec": _proc_sec,
                             "wiki_tape": _wtape, "eval_duration_sec": 0,
                             "score": 0, "conclusion": "文档无切片，无法评估",
                             "issues": [i["issue_type"] for i in _doc_issues]})
            _scores.append(0)
            _issues.extend(_doc_issues)
            print(f"[wiki评估] {_idx}/{_docs_n} 文档 {_doc_id[:8]} 无切片")
            continue
        if not _pages_meta:
            _doc_issues.append({"dim": "wiki", "doc_id": _doc_id, "issue_type": "wiki_no_pages",
                                "detail": "该文档未构建出 wiki 页面（或页面非 active 状态）"})
            _per_doc.append({"doc_id": _doc_id, "file_name": _dinfo.get("file_name", ""),
                             "chunks": _n, "avg_len": _d_avg, "wiki_pages": 0,
                             "wiki_start_time": _wstart.isoformat() if _wstart else None,
                             "wiki_build_sec": _wspan, "doc_process_sec": _proc_sec,
                             "wiki_tape": _wtape, "eval_duration_sec": 0,
                             "score": 0, "conclusion": "未构建 wiki 页面",
                             "issues": [i["issue_type"] for i in _doc_issues]})
            _scores.append(0)
            _issues.extend(_doc_issues)
            print(f"[wiki评估] {_idx}/{_docs_n} 文档 {_doc_id[:8]} 无 wiki 页（chunks={_n}）")
            continue
        # 原文切片抽样
        _md_parts = []
        if _n > MAX_MD_CHUNKS:
            _step = (_n - 1) / (MAX_MD_CHUNKS - 1)
            _pick = sorted({int(round(i * _step)) for i in range(MAX_MD_CHUNKS)})
        else:
            _pick = range(_n)
        for _i in _pick:
            _seq, _c, _ = _chunks[_i]
            _md_parts.append(f"[原文块{_seq}] {_c[:MAX_MD_CHARS]}" + ("…[截断]" if len(_c) > MAX_MD_CHARS else ""))
        _md_body = "\n\n".join(_md_parts)
        # wiki 页抽样：摘要页优先，其余均匀
        _summ = [p for p in _pages_meta if str(p[3] or "") == "summary"]
        _restp = [p for p in _pages_meta if str(p[3] or "") != "summary"]
        _ordered = _summ + _restp
        if len(_ordered) > MAX_WIKI_PAGES:
            _keep = list(_summ)
            _budget = MAX_WIKI_PAGES - len(_keep)
            if _budget <= 0:
                _ordered = _keep[:MAX_WIKI_PAGES]
            elif len(_restp) <= _budget:
                _ordered = _keep + _restp
            else:
                _st = (len(_restp) - 1) / (_budget - 1) if _budget > 1 else 0
                _pick2 = sorted({int(round(i * _st)) for i in range(_budget)})
                _ordered = _keep + [_restp[i] for i in _pick2]
        _page_parts = []
        for _p in _ordered[:MAX_WIKI_PAGES]:
            _pc = _wiki_content.get(str(_p[0]), "").strip()
            if len(_pc) > MAX_WIKI_CHARS:
                _pc = _pc[:MAX_WIKI_CHARS] + "…[截断]"
            _page_parts.append(f"[Wiki页 {str(_p[3] or 'entity')}｜{str(_p[2] or '')}](slug:{str(_p[1] or '')})\n{_pc}")
        _wiki_body = "\n\n".join(_page_parts)
        _prompt = (
            "你是知识库 wiki 构建质量评估专家。下面是某文档的【原文内容】"
            + (f"（共 {_n} 个切片，均匀抽样展示 {len(_pick)} 个代表）" if _n > MAX_MD_CHUNKS else f"（共 {_n} 个切片）")
            + "，[原文块N] 为序号，按原文顺序排列；以及该文档构建出的【Wiki 页面内容】（摘要页+实体/概念页）。\n"
            "请对照原文评估该文档的 wiki 页面质量：\n"
            "1. 内容错误：wiki 中是否有明显事实性错误或与原文不符的内容（如数字/日期/主体/条款含义/定义错误、凭空编造）\n"
            "2. 内容缺失：原文中的重要内容（关键定义/条款/流程/数据/结论）在 wiki 中是否明显缺失\n"
            "3. wiki 是否有意义：wiki 整体是否如实反映文档、有业务价值（而非空泛、占位、大量无关或噪音内容）\n"
            "只输出 JSON：\n"
            "{\n"
            '  "score": 0-100 整数（综合分，越低问题越严重）,\n'
            '  "has_content_error": true/false,\n'
            '  "content_errors": ["具体错误说明（指明哪个 wiki 页与原文哪处不符）"],\n'
            '  "has_missing": true/false,\n'
            '  "missing_items": ["具体缺失说明（指明原文哪些重要内容未被 wiki 覆盖）"],\n'
            '  "meaningful": true/false,\n'
            '  "meaningful_note": "wiki 是否有意义的简要说明",\n'
            '  "conclusion": "一句话总体结论"\n'
            "}\n"
            "【原文内容】\n" + _md_body + "\n\n【Wiki 页面内容】\n" + _wiki_body
        )
        _t0 = _t.time()
        try:
            _out = _llm(_prompt, system="你是严谨的知识库 wiki 质量评估器。只依据给定原文与 wiki 内容判断，只输出 JSON。")
        except Exception as _e:
            _per_doc.append({"doc_id": _doc_id, "file_name": _dinfo.get("file_name", ""),
                             "chunks": _n, "avg_len": _d_avg, "wiki_pages": len(_pages_meta),
                             "wiki_start_time": _wstart.isoformat() if _wstart else None,
                             "wiki_build_sec": _wspan, "doc_process_sec": _proc_sec,
                             "wiki_tape": _wtape, "eval_duration_sec": round(_t.time() - _t0, 1),
                             "score": None, "conclusion": "LLM 评估失败",
                             "error": str(_e)[:200], "issues": []})
            _issues.append({"dim": "wiki", "doc_id": _doc_id, "issue_type": "sem_eval_error",
                            "detail": f"LLM wiki 评估失败: {str(_e)[:200]}"})
            print(f"[wiki评估] {_idx}/{_docs_n} 文档 {_doc_id[:8]} LLM 失败: {str(_e)[:100]}")
            continue
        _dur = round(_t.time() - _t0, 1)
        if not isinstance(_out, dict):
            _out = {}
        _sc = _out.get("score")
        _score = (round(float(_sc), 1)
                  if isinstance(_sc, (int, float)) and 0 <= float(_sc) <= 100 else None)
        if _score is not None:
            _scores.append(_score)
        if _out.get("has_content_error") is True or str(_out.get("has_content_error")).lower() == "true":
            _errs = _out.get("content_errors") or []
            if isinstance(_errs, str):
                _errs = [_errs]
            _doc_issues.append({"dim": "wiki", "doc_id": _doc_id, "issue_type": "wiki_content_error",
                                "detail": "Wiki 内容错误: " + "; ".join(str(x) for x in _errs[:3])[:200]})
        if _out.get("has_missing") is True or str(_out.get("has_missing")).lower() == "true":
            _miss = _out.get("missing_items") or []
            if isinstance(_miss, str):
                _miss = [_miss]
            _doc_issues.append({"dim": "wiki", "doc_id": _doc_id, "issue_type": "wiki_missing_content",
                                "detail": "Wiki 内容缺失: " + "; ".join(str(x) for x in _miss[:3])[:200]})
        if _out.get("meaningful") is False or str(_out.get("meaningful")).lower() == "false":
            _doc_issues.append({"dim": "wiki", "doc_id": _doc_id, "issue_type": "wiki_not_meaningful",
                                "detail": "Wiki 无意义: " + str(_out.get("meaningful_note") or "")[:200]})
        _issues.extend(_doc_issues)
        _per_doc.append({"doc_id": _doc_id, "file_name": _dinfo.get("file_name", ""),
                         "chunks": _n, "avg_len": _d_avg, "wiki_pages": len(_pages_meta),
                         "wiki_start_time": _wstart.isoformat() if _wstart else None,
                         "wiki_build_sec": _wspan, "doc_process_sec": _proc_sec,
                         "wiki_tape": _wtape, "eval_duration_sec": _dur,
                         "score": _score,
                         "conclusion": str(_out.get("conclusion") or "")[:200],
                         "issues": [i["issue_type"] for i in _doc_issues]})
        print(f"[wiki评估] {_idx}/{_docs_n} 文档 {_doc_id[:8]} wiki页 {len(_pages_meta)} 分 {_score} 耗时 {_dur}s 问题 {len(_doc_issues)}")
    _valid = [s for s in _scores if s is not None]
    _health = round(sum(_valid) / len(_valid), 1) if _valid else None
    _metrics = {"docs": _docs_n, "files_eval": _docs_n, "chunks": _chunks_n,
                "avg_len": _avg_len, "wiki_pages": _wiki_n,
                "eval_errors": _docs_n - len(_valid),
                "avg_score": _health, "health_score": _health, "per_doc": _per_doc}
    _attr = []
    _cnt = {}
    for _i in _issues:
        _cnt[_i["issue_type"]] = _cnt.get(_i["issue_type"], 0) + 1
    if _cnt.get("wiki_content_error", 0) > 0:
        _attr.append({"dim": "wiki", "cause": f"{_cnt['wiki_content_error']} 个文件 wiki 存在内容错误（与原文不符/编造）",
                      "suggestion": "检查构建编排 LLM 抽取的原文切片输入与提示词，核对数字/日期/条款/定义准确性，对错误页重新构建"})
    if _cnt.get("wiki_missing_content", 0) > 0:
        _attr.append({"dim": "wiki", "cause": f"{_cnt['wiki_missing_content']} 个文件 wiki 缺失原文重要内容",
                      "suggestion": "增大实体/规则抽取范围或按章节/长句拆分抽取，检查切片是否完整进入构建流程"})
    if _cnt.get("wiki_not_meaningful", 0) > 0:
        _attr.append({"dim": "wiki", "cause": f"{_cnt['wiki_not_meaningful']} 个文件 wiki 空泛/占位/价值低",
                      "suggestion": "检查构建编排的抽取粒度与摘要提示词，考虑重新构建该文档 wiki"})
    if _cnt.get("wiki_no_pages", 0) > 0:
        _attr.append({"dim": "wiki", "cause": f"{_cnt['wiki_no_pages']} 个文件未构建出 wiki 页面",
                      "suggestion": "检查该文档 wiki 构建编排是否触发/成功（任务监控查看 wiki 构建 run 状态），失败则重跑构建"})
    if _cnt.get("no_chunks", 0) > 0:
        _attr.append({"dim": "wiki", "cause": f"{_cnt['no_chunks']} 个文件无切片",
                      "suggestion": "检查文档解析状态，重跑解析后再评估"})
    if _cnt.get("sem_eval_error", 0) > 0:
        _attr.append({"dim": "wiki", "cause": f"{_cnt['sem_eval_error']} 个文件 LLM 评估失败（模型服务异常/超时）",
                      "suggestion": "稍后重跑评估，或检查模型服务可用性"})
    _r = {"metrics": _metrics, "issues": _issues, "attribution": _attr}

bindings["wiki_cmp_r"] = _r
print(f"[wiki评估] LLM 评估完成: 文档 {_metrics.get('docs')} 切片 {_metrics.get('chunks')} "
      f"wiki页 {_metrics.get('wiki_pages')} 健康分 {_metrics.get('health_score')} 问题 {len(_issues)}")