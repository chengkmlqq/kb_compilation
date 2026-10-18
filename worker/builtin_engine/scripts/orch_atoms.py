"""编排业务原子：llm_extract / wiki_publish（2026-10-09）

设计目标：把 wiki 构建里"LLM 结构化抽取"和"幂等建页"两类动作从
build_full.py / extract_*.py 的硬编码流程中抽成编排可配置的通用组件。

llm_extract
    输入 JSON(记录数组) + prompt 模板 + 分批参数
    → LLM 按模板抽取 JSON → 汇总写输出 JSON
    覆盖原先长句拆分/关键词过滤/实体抽取/规则补抽 4 个脚本的共同模式。

wiki_publish
    输入 JSON + slug 策略 + 页面类型 + 目标目录 + 内容模板
    → 幂等建页/更新(read-first) → 返回 created/updated/skipped 统计
    覆盖原先长句建页/关键词建页/实体建页/目录创建的固定动作。

两者都以子进程方式被 orchestration_engine 调用(沿用 run_script 的
进程组+超时语义)，故本脚本只负责业务逻辑与参数校验。

CLI:
    python3 orch_atoms.py llm_extract --config <cfg.json>
    python3 orch_atoms.py wiki_publish --config <cfg.json>
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NoReturn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr  # noqa: E402


# ──────────────────────────── 工具 ────────────────────────────

def _die(msg: str) -> NoReturn:
    print(f"[orch_atoms] ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _render(template: str, ctx: dict) -> str:
    """{{key}} 模板渲染（与编排引擎一致的双花括号语法）。"""
    def sub(m):
        key = m.group(1).strip()
        val = ctx.get(key, "")
        if isinstance(val, (list, dict)):
            val = json.dumps(val, ensure_ascii=False)
        return str(val)
    return re.sub(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}", sub, template)


def _read_json(path: str) -> Any:
    """读 JSON。支持 MinIO 对象路径（minio:// 或 reg-cache/ 前缀）。

    2026-10-16: 编排中间产物改存 MinIO 后，wiki_publish 等原子的
    source（如 {{reg_dir}}/ls_all.json）可能是 minio://... 或相对前缀，
    本地 open 读不到。统一在此适配：远程路径走 storage.get_bytes。
    """
    if path.startswith("reg-cache/"):
        from api.config import get_settings
        path = f"minio://{get_settings().MINIO_BUCKET or 'kb-compilation'}/{path}"
    if path.startswith("minio://"):
        from api.services.storage import get_bytes
        try:
            raw = get_bytes(path)
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            _die(f"输入 JSON 解析失败 {path}: {e}")
        except Exception as e:  # noqa: BLE001 — 对象不存在等统一视为文件缺失
            _die(f"输入文件不存在: {path} ({e})")
    if not os.path.exists(path):
        _die(f"输入文件不存在: {path}")
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            _die(f"输入 JSON 解析失败 {path}: {e}")


def _as_records(data: Any) -> list:
    """把输入规整为记录数组：list 原样；dict 取 values 或按 key 重组。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # {name: {...}} 形态 → 记录数组，每条注入 _key
        vals = list(data.values())
        if vals and all(isinstance(v, dict) for v in vals):
            out = []
            for k, v in zip(data.keys(), vals):
                rec = dict(v)
                rec.setdefault("_key", k)
                out.append(rec)
            return out
        return [data]
    _die(f"输入结构不支持: {type(data).__name__}")


# ──────────────────────────── llm_extract ────────────────────────────

def llm_extract(cfg: dict[str, Any]) -> dict[str, Any]:
    """LLM 结构化抽取。

    cfg 字段:
      source: 输入 JSON 路径
      source_key: 从记录取文本的字段名（默认 text，缺失回退 content/chunk）
      prompt_file / prompt: prompt 模板文件或内联字符串
      prompt_var: 注入模板的批次变量名（默认 items）
      output: 输出 JSON 路径
      output_shape: list（默认）| map（{key: value} 汇总）
      output_key_field: output_shape=map 时，每条取哪个字段作为 key
      output_value_field: output_shape=map 时，每条取哪个字段作为 value
      batch_size: 每批记录数（默认 8）
      max_workers: 并发线程（默认 2）
      max_tokens: 单次 max_tokens（默认 8000）
      temperature: 默认 0.1
      retries: 单条重试次数（默认 3）
      dedupe_field: 去重字段（可选，如 text）
      limit: 只取前 N 条（可选，调试用）
    """
    src = cfg.get("source")
    if not src:
        _die("llm_extract 缺 source")
    out_path = cfg.get("output")
    if not out_path:
        _die("llm_extract 缺 output")

    records = _as_records(_read_json(src))
    src_key = cfg.get("source_key") or "text"

    def pick_text(rec) -> str:
        for k in (src_key, "text", "content", "chunk", ""):
            if k and isinstance(rec, dict) and rec.get(k):
                return str(rec[k])
        return ""

    # 组批
    batch_size = int(cfg.get("batch_size") or 8)
    limit = int(cfg.get("limit") or 0)
    if limit:
        records = records[:limit]
    batches = [records[i:i + batch_size] for i in range(0, len(records), batch_size)]
    if not batches:
        _die(f"source 无可用记录: {src}")

    # prompt 模板
    prompt_tpl = cfg.get("prompt")
    prompt_file = cfg.get("prompt_file")
    if prompt_file:
        if not os.path.exists(prompt_file):
            _die(f"prompt_file 不存在: {prompt_file}")
        with open(prompt_file, encoding="utf-8") as f:
            prompt_tpl = f.read()
    if not prompt_tpl:
        _die("llm_extract 缺 prompt 或 prompt_file")
    prompt_tpl = str(prompt_tpl)

    prompt_var = cfg.get("prompt_var") or "items"
    shape = cfg.get("output_shape") or "list"
    warn: list[tuple[int, str]] = []
    max_tokens = int(cfg.get("max_tokens") or 8000)
    temperature = float(cfg.get("temperature") or 0.1)
    retries = int(cfg.get("retries") or 3)
    max_workers = int(cfg.get("max_workers") or 2)

    want_lines = shape == "lines"

    def run_batch(idx_batch):
        idx, batch = idx_batch
        items = "\n".join(f"- {pick_text(r)}" for r in batch)
        user = _render(prompt_tpl, {prompt_var: items, "count": len(batch),
                                   "index": idx + 1, "total": len(batches)})
        parse_fail = 0
        last_err = ""  # 2026-10-09 fix: except-as e 的绑定在 except 块结束即删除(PEP 3110)，
        # 重试耗尽后引用 e 会 UnboundLocalError——本意是「显式记账不静默产出 0 条」，
        # 却在记账这行崩掉。用外层变量捕获最后一次错误。
        for attempt in range(retries):
            try:
                raw = wr.llm_call(
                    [{"role": "user", "content": user}],
                    max_tokens=max_tokens, temperature=temperature)
                if want_lines:
                    # 非 JSON 输出：按行切分，去空/去编号/去 markdown 噪声
                    lines = []
                    for ln in (raw or "").splitlines():
                        ln = ln.strip().lstrip("-*·•0123456789. ").strip()
                        if ln and not ln.startswith("```"):
                            lines.append({"text": ln})
                    print(f"[llm_extract] 批次 {idx + 1}/{len(batches)} → {len(lines)} 行")
                    return lines
                parsed = json.loads(wr.ensure_full_json(raw))
                if not isinstance(parsed, list):
                    raise ValueError(f"期望数组, 得到 {type(parsed).__name__}")
                print(f"[llm_extract] 批次 {idx + 1}/{len(batches)} → {len(parsed)} 条")
                return parsed
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                parse_fail += 1
                print(f"[llm_extract] 批次 {idx + 1} 第 {attempt + 1} 次失败: {e}")
                if attempt < retries - 1:
                    time.sleep(min(2 ** attempt, 8))
        # 重试耗尽：显式记账，不静默产出 0 条（对齐"静默零产出"坑教训）
        warn.append((idx + 1, f"重试 {retries} 次仍失败: {last_err}"))
        print(f"[llm_extract] ⚠️ 批次 {idx + 1} 重试耗尽, 记空（详见汇总）")
        return []

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(run_batch, enumerate(batches)))
    rows = [r for batch_res in results for r in batch_res]

    # 去重
    dd = cfg.get("dedupe_field")
    if dd:
        seen, uniq = set(), []
        for r in rows:
            key = str(r.get(dd, ""))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            uniq.append(r)
        print(f"[llm_extract] 去重 {len(rows)} → {len(uniq)}")
        rows = uniq

    # 输出形状
    if shape == "map":
        kf = cfg.get("output_key_field")
        vf = cfg.get("output_value_field")
        if not kf or not vf:
            _die("output_shape=map 需 output_key_field / output_value_field")
        shaped = {}
        for r in rows:
            k, v = r.get(kf), r.get(vf)
            if k is None or v is None:
                continue
            shaped[str(k)] = v
        payload = shaped
    else:
        payload = rows

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[llm_extract] 完成: {len(rows)} 条 → {out_path} "
          f"({time.time() - t0:.0f}s)")
    if warn:
        print(f"[llm_extract] ⚠️ {len(warn)}/{len(batches)} 批失败: "
              f"{[f'#{b}' for b, _ in warn]}")
        for b, msg in warn:
            print(f"    批 {b}: {msg}")
    return {"success": not warn, "count": len(rows), "output": out_path,
            "shape": shape, "failed_batches": [b for b, _ in warn]}


# ──────────────────────────── wiki_publish ────────────────────────────

def wiki_publish(cfg: dict[str, Any]) -> dict[str, Any]:
    """幂等建页/更新。

    cfg 字段:
      source: 输入 JSON 路径（list 或 {key: {...}}）
      folder: 目标目录名（如 长句原文）；或 folder_id 直接给 ID
      page_type: original_sentence / frequent_keyword / entity-b / entity-r …
      slug_prefix: slug 前缀（如 longsentence）
      slug_hash_field: slug 里做 hash 的字段（默认 text；实体用 title）
      slug_hash_len: hash 长度（默认 8）
      slug_with_kid_prefix: 是否把 kid 前 10 位拼进 slug（默认 true）
      title_field: 标题字段（默认 title；无则用 hash 字段内容）
      content_template: 内容模板（支持 {{title}}/{{text}}/{{_key}} 等）
      content_field: 无模板时，直接用该字段作为 content
      source_ref: source_refs 值（"<kid>|<file>"）
      status: 页面状态（默认 published）
      summary_slug: 存在时传给脚本做摘要回填（可选）
      dry_run: 只统计不写库
    """
    src = cfg.get("source")
    if not src:
        _die("wiki_publish 缺 source")
    prefix = cfg.get("slug_prefix")
    if not prefix:
        _die("wiki_publish 缺 slug_prefix")
    dry_run = bool(cfg.get("dry_run"))
    # 2026-10-09 fix: dry_run 只统计不写库(只 print slug)，不需要 folder；
    # 原实现在 dry_run 也 _die 缺 folder + 跑 _resolve_folder(一次往返)，
    # 导致「仅统计」这个最安全的调试入口反而跑不通。dry_run 放行无 folder。
    folder_name = cfg.get("folder")
    folder_id = cfg.get("folder_id")
    if not dry_run and not folder_name and not folder_id:
        _die("wiki_publish 缺 folder 或 folder_id（dry_run 时可省略）")
    page_type = cfg.get("page_type") or "custom"

    records = _as_records(_read_json(src))
    hash_field = cfg.get("slug_hash_field") or "text"
    hash_len = int(cfg.get("slug_hash_len") or 8)
    with_kid = cfg.get("slug_with_kid_prefix", True)
    title_field = cfg.get("title_field") or "title"
    sref = cfg.get("source_ref")
    # 2026-10-18：聚类知识页来自多篇文档，支持从记录里取列表字段作为 source_refs
    srefs_field = cfg.get("source_refs_field")
    status = cfg.get("status") or "published"
    content_tpl = cfg.get("content_template")
    content_field = cfg.get("content_field")
    kid = (sref or "").split("|")[0]

    wr.load_config()
    wr.mcp_init()

    # 目录解析
    if dry_run:
        print(f"[wiki_publish] dry_run: 跳过目录解析（folder={folder_name or folder_id or '未指定'}）")
    else:
        if not folder_id:
            folder_id = _resolve_folder(
                folder_name or "",
                kb_id=wr.KB,
                parent_id=cfg.get("folder_parent_id") or "",
            )
        print(f"[wiki_publish] 目录: {folder_name or folder_id} → {folder_id}")

    kid_prefix = hashlib.md5(kid.encode()).hexdigest()[:10] if (kid and with_kid) else ""
    created = updated = skipped = errors = 0

    for rec in records:
        title = str(rec.get(title_field) or "").strip()
        body_src = rec.get(hash_field) or rec.get(content_field) or rec.get("text") or ""
        body_src = str(body_src).strip()
        if not title:
            title = (body_src[:30] + "…") if body_src else ""
        if not body_src and not title:
            errors += 1
            continue
        # slug
        h = hashlib.md5(body_src.encode()).hexdigest()[:hash_len]
        slug = f"{prefix}/{kid_prefix}-{h}" if kid_prefix else f"{prefix}/{h}"
        # content
        if content_tpl:
            content = _render(content_tpl, {"title": title, "text": body_src,
                                             "summary": rec.get("summary") or "",
                                             "_key": rec.get("_key") or "",
                                             "record": rec})
        elif content_field:
            content = str(rec.get(content_field) or "")
        else:
            content = f"# {title}\n\n{body_src}"
        if dry_run:
            print(f"  [dry] {slug}")
            continue
        # 幂等：read-first
        r = wr.tool_call("wiki_read_page", {"slug": slug})
        exists = not (isinstance(r, dict) and r.get("error"))
        kw = {"slug": slug, "title": title, "content": content,
              "folder_id": folder_id, "page_type": page_type}
        if srefs_field:
            _refs = rec.get(srefs_field)
            if isinstance(_refs, list) and _refs:
                kw["source_refs"] = [str(x) for x in _refs]
        elif sref:
            kw["source_refs"] = [sref]
        try:
            if exists:
                up = wr.tool_call("update_wiki_page", kw)
                if isinstance(up, dict) and up.get("error"):
                    errors += 1
                else:
                    updated += 1
            else:
                cr = wr.tool_call("create_wiki_page", kw)
                if isinstance(cr, dict) and cr.get("error"):
                    # slug 竞态：create 500 但页可能已存在 → 读回补 update
                    rr = wr.tool_call("wiki_read_page", {"slug": slug})
                    if not (isinstance(rr, dict) and rr.get("error")):
                        wr.tool_call("update_wiki_page", kw)
                        updated += 1
                    else:
                        errors += 1
                        print(f"  ⚠️ create 失败: {slug}: {cr.get('error')}")
                else:
                    created += 1
                    # 坑 2：create 不带 source_refs 生效时用 update 补
                    if sref:
                        wr.tool_call("update_wiki_page", kw)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  ⚠️ {slug}: {e}")

    print(f"[wiki_publish] 完成: created={created} updated={updated} "
          f"skipped={skipped} errors={errors} / 共 {len(records)} 条")
    return {"success": errors == 0, "created": created, "updated": updated,
            "errors": errors, "total": len(records), "folder_id": folder_id}


def _resolve_folder(name: str, kb_id: str | None = None, parent_id: str | None = None) -> str:
    """按名称解析目录 ID。

    优先复用脚本层缓存（list_all_folders_cached，一次拉全量 + 本地查，
    避免 direct 模式每层 5.66s 的往返）；缓存不可用时回退逐层列举。

    2026-10-17 修复：给出 parent_id 时**必须**在父目录下匹配（不存在则在该父目录
    下创建）。此前只按名字全库取首个匹配 —— 50 个文档都有「长句原文」目录时，
    所有文档的长句页全挂到第一个同名目录，其余目录全空。
    """
    kb = kb_id or wr.KB
    want_parent = str(parent_id).strip() if parent_id else ""
    # 1) 全量缓存命中（有 parent 限定则严格要求 parent 匹配）
    try:
        for f in wr.list_all_folders_cached(kb_id=kb):
            if f.get("name") != name:
                continue
            if want_parent and str(f.get("parent_id") or "") != want_parent:
                continue
            return f.get("id")
    except Exception:  # noqa: BLE001 — 缓存不可用走回退
        pass
    # 1b) 有 parent 限定：直接在父目录下列举
    if want_parent:
        r = wr.list_folders(kb_id=kb, parent_id=want_parent)
        for f in (r.get("data") or r.get("folders") or []):
            if f.get("name") == name:
                return f.get("id")
        r = wr.create_folder(name, parent_id=want_parent, kb_id=kb)
        if isinstance(r, dict) and "error" in r:
            raise RuntimeError(f"create_folder 失败: {r['error']}")
        return r.get("folder", {}).get("id", r.get("id", ""))
    # 2) 逐层列举（两层足够：根→法规根→子目录）
    frontier = [""]
    seen = set()
    for _ in range(3):
        nxt = []
        for pid in frontier:
            if pid in seen:
                continue
            seen.add(pid)
            r = wr.list_folders(kb_id=kb, parent_id=pid)
            items = r.get("data") or r.get("folders") or []
            for f in items:
                if f.get("name") == name:
                    return f.get("id")
                nxt.append(f.get("id"))
        frontier = [x for x in nxt if x]
        if not frontier:
            break
    _die(f"未找到目录: {name}")


# ──────────────────────────── 长句拆分 / 关键词抽取（2026-10-12）────────────
# 业务组件：包装既有 WeKnora 脚本（extract_long_sentences_v2 / discover_terms+
# filter_terms_llm），以 run_script 子进程语义被编排调用。输入输出都是 JSON 文件。

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def long_sentence_extract(cfg: dict[str, Any]) -> dict[str, Any]:
    """长句原文拆分（业务组件）。

    cfg 字段:
      chunks_file: 输入 chunks JSON 路径（[{seq, content}]）
      output: 输出 candidates JSON 路径（[{text, title, md5_text, len, keep}]）
      summary_file: 可选摘要文件路径（--summary-file）
      summary_slug: 可选摘要页 slug
      max_tokens: LLM 单次 max_tokens（默认 8000）
      model: LLM 模型（默认 wr.DEFAULT_MODEL）
      timeout_sec: 子进程超时（默认 1800）
    """
    src = cfg.get("chunks_file") or cfg.get("source")
    if not src:
        _die("long_sentence_extract 缺 chunks_file")
    out = cfg.get("output") or "candidates.json"
    cmd = [sys.executable, f"{_SCRIPTS_DIR}/extract_long_sentences_v2.py", str(src), "-o", str(out)]
    if cfg.get("summary_file"):
        cmd += ["--summary-file", str(cfg["summary_file"])]
    if cfg.get("summary_slug"):
        cmd += ["--summary-slug", str(cfg["summary_slug"])]
    if cfg.get("max_tokens"):
        cmd += ["--max-tokens", str(cfg["max_tokens"])]
    if cfg.get("model"):
        cmd += ["--model", str(cfg["model"])]
    timeout = float(cfg.get("timeout_sec") or 1800)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        _die(f"长句拆分失败 rc={r.returncode}: {r.stderr[-500:]}")
    if not os.path.exists(out):
        _die(f"长句拆分无输出: {out}")
    cands = _as_records(_read_json(out))
    print(f"[long_sentence_extract] 完成: {len(cands)} 条候选 → {out}")
    return {"success": True, "count": len(cands), "output": out}


def keyword_extract(cfg: dict[str, Any]) -> dict[str, Any]:
    """高频关键词抽取（业务组件，两步：程序统计 discover_terms → LLM 过滤 filter_terms_llm）。

    cfg 字段:
      ls_file: 输入长句 JSON 路径（candidates 格式 [{text, ...}]）
      output: 输出过滤后关键词 JSON 路径（[{term, freq, keep}]）
      min_freq: 最低频次（默认 3）
      top: 候选 Top N（默认 50）
      max_len: 最长短语字数（默认 6）
      batch_size: LLM 过滤每批条数（默认 15）
      kb: 知识库 ID（LLM 配置来源）
      model: LLM 模型
      dry_run: 只统计不写最终过滤文件
      timeout_sec: 子进程超时（默认 1800）
    """
    ls = cfg.get("ls_file") or cfg.get("source")
    if not ls:
        _die("keyword_extract 缺 ls_file")
    terms_tmp = cfg.get("terms_file") or "/tmp/kb_terms.json"
    out = cfg.get("output") or "/tmp/kb_terms_filtered.json"

    # 步 1：程序统计
    cmd1 = [sys.executable, f"{_SCRIPTS_DIR}/discover_terms.py", str(ls), "-o", terms_tmp]
    if cfg.get("min_freq"):
        cmd1 += ["--min-freq", str(cfg["min_freq"])]
    if cfg.get("top"):
        cmd1 += ["--top", str(cfg["top"])]
    if cfg.get("max_len"):
        cmd1 += ["--max-len", str(cfg["max_len"])]
    timeout = float(cfg.get("timeout_sec") or 1800)
    r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=timeout)
    if r1.returncode != 0:
        _die(f"关键词统计失败 rc={r1.returncode}: {r1.stderr[-500:]}")
    if not os.path.exists(terms_tmp):
        _die(f"关键词统计无输出: {terms_tmp}")
    stats = _as_records(_read_json(terms_tmp))
    print(f"[keyword_extract] 统计候选 {len(stats)} 条")

    # 步 2：LLM 过滤
    if cfg.get("dry_run"):
        print(f"[keyword_extract] dry_run: 跳过 LLM 过滤，候选 {len(stats)} 条")
        return {"success": True, "candidates": len(stats), "dry_run": True}
    cmd2 = [sys.executable, f"{_SCRIPTS_DIR}/filter_terms_llm.py",
            "--terms-file", terms_tmp, "--ls-file", str(ls), "-o", out]
    if cfg.get("batch_size"):
        cmd2 += ["--batch-size", str(cfg["batch_size"])]
    if cfg.get("kb"):
        cmd2 += ["--kb", str(cfg["kb"])]
    if cfg.get("model"):
        cmd2 += ["--model", str(cfg["model"])]
    r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=timeout)
    if r2.returncode != 0:
        _die(f"关键词过滤失败 rc={r2.returncode}: {r2.stderr[-500:]}")
    if not os.path.exists(out):
        _die(f"关键词过滤无输出: {out}")
    terms = _as_records(_read_json(out))
    print(f"[keyword_extract] 完成: {len(terms)} 条 → {out}")
    return {"success": True, "count": len(terms), "output": out}


# ──────────────────────────── CLI ────────────────────────────

def main() -> int:
    if len(sys.argv) < 3:
        print("用法: orch_atoms.py <llm_extract|wiki_publish> --config <cfg.json>")
        return 1
    atom = sys.argv[1]
    cfg_path = None
    for i, a in enumerate(sys.argv):
        if a == "--config" and i + 1 < len(sys.argv):
            cfg_path = sys.argv[i + 1]
    if not cfg_path:
        _die("缺 --config")
    cfg = _read_json(cfg_path)
    if atom == "llm_extract":
        llm_extract(cfg)
    elif atom == "wiki_publish":
        wiki_publish(cfg)
    elif atom == "long_sentence_extract":
        long_sentence_extract(cfg)
    elif atom == "keyword_extract":
        keyword_extract(cfg)
    else:
        _die(f"未知原子: {atom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())