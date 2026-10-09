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
                parse_fail += 1
                print(f"[llm_extract] 批次 {idx + 1} 第 {attempt + 1} 次失败: {e}")
                if attempt < retries - 1:
                    time.sleep(min(2 ** attempt, 8))
        # 重试耗尽：显式记账，不静默产出 0 条（对齐"静默零产出"坑教训）
        warn.append((idx + 1, f"重试 {retries} 次仍失败: {e}"))
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
    folder_name = cfg.get("folder")
    folder_id = cfg.get("folder_id")
    if not folder_name and not folder_id:
        _die("wiki_publish 缺 folder 或 folder_id")
    prefix = cfg.get("slug_prefix")
    if not prefix:
        _die("wiki_publish 缺 slug_prefix")
    page_type = cfg.get("page_type") or "custom"

    records = _as_records(_read_json(src))
    hash_field = cfg.get("slug_hash_field") or "text"
    hash_len = int(cfg.get("slug_hash_len") or 8)
    with_kid = cfg.get("slug_with_kid_prefix", True)
    title_field = cfg.get("title_field") or "title"
    dry_run = bool(cfg.get("dry_run"))
    sref = cfg.get("source_ref")
    status = cfg.get("status") or "published"
    content_tpl = cfg.get("content_template")
    content_field = cfg.get("content_field")
    kid = (sref or "").split("|")[0]

    wr.load_config()
    wr.mcp_init()

    # 目录解析
    if not folder_id:
        folder_id = _resolve_folder(folder_name, kb_id=wr.KB)
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
        if sref:
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


def _resolve_folder(name: str, kb_id: str | None = None) -> str:
    """按名称解析目录 ID。

    优先复用脚本层缓存（list_all_folders_cached，一次拉全量 + 本地查，
    避免 direct 模式每层 5.66s 的往返）；缓存不可用时回退逐层列举。
    同名目录取首个匹配（与 build_full.ensure_folder 语义一致）。
    """
    kb = kb_id or wr.KB
    # 1) 全量缓存命中
    try:
        for f in wr.list_all_folders_cached(kb_id=kb):
            if f.get("name") == name:
                return f.get("id")
    except Exception:  # noqa: BLE001 — 缓存不可用走回退
        pass
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
    else:
        _die(f"未知原子: {atom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())