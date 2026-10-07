#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfill summary-page relation tables so that each "关系说明" cites
original long sentences from the related document via wikilinks.

Run:
  python3 backfill_summary_relation_ls.py --kb <kb_id> [--limit N] [--dry-run]
"""

import os, sys, json, re, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr


KB = wr.load_kb()


def tool(name, **kwargs):
    kwargs["kb_id"] = KB
    return wr.tool_call(name, kwargs)


def load_pages():
    pages = []
    page = 1
    while True:
        r = wr.list_wiki_pages(kb_id=KB, page=page, page_size=200)
        ps = r.get("pages") or []
        pages.extend(ps)
        if len(ps) < 200:
            break
        page += 1
    return pages


def load_all_long_sentences():
    pages = load_pages()
    ls_by_kid = {}
    for p in pages:
        slug = p.get("slug", "")
        if not slug.startswith("longsentence/"):
            continue
        content = p.get("content") or ""
        title = p.get("title") or ""
        for sr in p.get("source_refs") or []:
            kid = sr.split("|")[0]
            if not kid:
                continue
            ls_by_kid.setdefault(kid, []).append({
                "slug": slug,
                "title": title,
                "content": content,
            })
    return ls_by_kid


def load_doc_names(kb_id: str):
    """全库文档 {kid: file_name}。"""
    doc_by_kid = {}
    try:
        _pg = 1
        while True:
            _r = wr.list_docs(kb_id=kb_id, page=_pg, page_size=20)
            _docs = _r.get("data") or []
            if not _docs:
                break
            for _d in _docs:
                doc_by_kid[_d["id"]] = _d["file_name"]
            if len(_docs) < 20:
                break
            _pg += 1
    except Exception as e:
        print(f"doc_by_kid load warning: {e}")
    return doc_by_kid


def is_revision_doc(fname: str) -> bool:
    """判断是否为修订/修改类文档（市场监督域：全国人大《关于修改〈XX〉的决定》、
    修正案类——filename 含 关于修改/修改决定/修正）。这类文档的关系声明最权威
    （supersedes/based_on 明确），关系表原文依据优先从这类文档的长句取。"""
    fname = fname or ""
    if any((k in fname for k in ["关于修改", "修改决定", "修正"])):
        return True
    if "决定" in fname and "全国人民代表大会" in fname:
        return True
    return False


def family_core(fname: str) -> str:
    """家族核心名（与 build_relations.family_core 同口径：去公司前缀/扩展名/版次/修饰词）。
    用于：① 找目标文档同家族的修订类文档长句；② 判定关系行中的制度名是否库内同族。"""
    n = fname or ""
    n = re.sub(r"\.(docx|doc|pdf|xlsx|xls)$", "", n)
    n = n.replace("中华人民共和国", "")
    n = re.sub(r"^[^《]*?〔[^〕]*〕\s*", "", n)
    n = re.sub(r"^[^《]*?号\s*", "", n)
    m = re.search(r"《([^》]+)》", n)
    if m:
        n = m.group(1)
    n = re.sub(r"^[A-Z]\d+\s*[\d.]*\s*年?[，,]?\s*", "", n)
    n = re.sub(r"^(附件\d*[：.:]?)\s*", "", n)
    n = re.sub(r"[（(]V[\d.]+[)）]", "", n)
    n = re.sub(r"[\d.]+年?$", "", n)
    n = re.sub(r"（[^）]*）", "", n)
    n = n.replace("修正文本", "")
    n = n.replace("暂行", "")
    n = n.strip(" ：《》")
    if n.endswith("的") and len(n) > 1:
        n = n[:-1]
    n = n.replace("实施细则", "细则")
    return n


def collect_revision_ls(ls_by_kid, doc_by_kid, target_kid):
    """收集目标文档相关的修订类文档长句（2026-08-30 新增，2026-08-31 扩跨族）。
    关系表原文依据第一优先级来自修订类文档（用户规则：修订文档 > 原文）。
    返回 dict：{'same_family': {kid: [ls...]}, 'cross_family': {kid: [ls...]}}
    same_family = 目标文档同族修订类（第一优先）；cross_family = 全库其他修订类（跨族兜底）。
    2026-08-31 修复：跨族引用场景（如 采购管理办法 引用《采购实施管理办法》修订说明里的句子）
    原逻辑只收同族 → 跨族修订类长句收集不到 → 该行应改未改。"""
    target_fname = doc_by_kid.get(target_kid, "")
    target_core = family_core(target_fname)
    same = {}
    cross = {}
    for kid, fname in doc_by_kid.items():
        if kid == target_kid:
            continue
        if not is_revision_doc(fname):
            continue
        if not ls_by_kid.get(kid):
            continue
        if target_core and family_core(fname) == target_core:
            same[kid] = ls_by_kid[kid]
        else:
            cross[kid] = ls_by_kid[kid]
    return {'same_family': same, 'cross_family': cross}


def doc_core_name(fname: str) -> str:
    """提取文件名核心名（与 build_relations.ref_core 一致）用于库内匹配。"""
    n = fname or ""
    n = n.replace('中国移动通信集团上海有限公司', '')
    n = n.replace('中国移动通信集团有限公司', '')
    n = n.replace('中国移动上海公司', '')
    n = n.replace('《', '').replace('》', '')
    n = re.sub(r'\.(docx|doc|pdf|xlsx|xls)$', '', n)
    return n.strip()


def is_doc_in_kb(related: str, doc_by_kid: dict) -> bool:
    """判断关系行中的制度名是否在库内有对应文档（核心词双向包含，排除目标自身）。"""
    core = doc_core_name(related)
    if not core or len(core) < 4:
        return False
    for fname in (doc_by_kid or {}).values():
        fn = doc_core_name(fname)
        if core and (core in fn or fn in core):
            return True
    return False


def rel_keywords(rel_type: str) -> list:
    rel_type = (rel_type or "").lower()
    if rel_type == "supersedes":
        return ["替代", "废止", "取代", "同时废止", "被"]
    if rel_type == "based_on":
        return ["依据", "制定", "根据", "遵循", "按照"]
    if rel_type == "specializes":
        return ["参照执行", "遵照执行", "执行", "衔接", "配套"]
    return ["", "依据", "制定", "执行", "配套", "衔接", "参照", "替代", "废止"]


def pick_sentence(long_sentences, target_name: str, rel_type: str):
    clean_name = re.sub(r"^[^《]*?〔[^〕]*〕\s*", "", target_name)
    clean_name = re.sub(r"^[^《]*?号\s*", "", clean_name)
    clean_name = clean_name.replace("《", "").replace("》", "")
    if not clean_name:
        return []
    keywords = rel_keywords(rel_type)
    hits = []
    for ls in long_sentences:
        text = ls["content"]
        title = ls["title"]
        if clean_name not in text and clean_name not in title:
            continue
        rank = 0
        for kw in keywords:
            if kw and kw in text:
                rank += 1
        # 2026-08-31 放宽：修订说明/一览表表格行（D: 内容调整,E: 新条款…）常无关系关键词，
        # 只要长句含制度名即视为候选（rank=0 也允许），避免漏掉修订类依据
        hits.append((rank, ls))
    hits.sort(key=lambda x: (-x[0], len(x[1]["content"])))
    return [x[1] for x in hits[:2]]


def parse_old_relation_desc(original_desc: str):
    """从旧版 关系说明 中提取纯描述和原文依据链接（兼容已回填和未回填的行）。"""
    if "（原文依据：" in original_desc:
        m = re.search(r'^(.*?)（原文依据：(.*)）$', original_desc)
        if m:
            pure_desc = m.group(1)
            links_str = m.group(2)
            ls_links = re.findall(r'\[\[([^\]|]+)\|([^\]]+)\]\]', links_str)
            return pure_desc, ls_links
    return original_desc, []


def render_relation_desc(long_sentences, target_name: str, rel_type: str, original_desc: str):
    """返回 (pure_desc, ls_links) 三元组中的 ls_links 列值。"""
    if "（原文依据：" in original_desc:
        pure_desc, ls_links = parse_old_relation_desc(original_desc)
        if ls_links:
            return pure_desc, ls_links
    pure_desc = original_desc
    matched = pick_sentence(long_sentences, target_name, rel_type)
    if matched:
        # 2026-08-31：ls_links 元素带长句页内容（第 3 位），供 ls_links_to_markdown 提取句子显示名
        ls_links = [(ls['slug'], ls['title'], ls) for ls in matched]
        return pure_desc, ls_links
    # 检查目标文档是否在库内
    return pure_desc, []


def find_evidence_sentence(ls_page, target_name):
    """从长句页表格行中找包含制度名的原文句子（2026-08-31，与 fix_rel_evidence 同逻辑）。
    返回 (句子, 是否找到)；找不到返回 None, False。"""
    content = (ls_page or {}).get("content") or ""
    rows = []
    for l in content.split("\n"):
        s = l.strip()
        if s.startswith("|") and "|" in s[1:] and not s.startswith("| 序号") and "---" not in s:
            cells = [x.strip() for x in s.strip("|").split("|")]
            if len(cells) >= 2:
                rows.append(cells[-1])
    ref_n = target_name.strip()
    ref_core = ref_n.strip("《》")
    ref_short = re.sub(r"^(中国移动通信集团上海有限公司|中国移动)", "", ref_core)
    for r_ in rows:
        if ref_n and ref_n in r_:
            return r_, True
    for r_ in rows:
        if ref_core and ref_core in r_:
            return r_, True
    for r_ in rows:
        if ref_short and len(ref_short) >= 4 and ref_short in r_:
            return r_, True
    return None, False

def ls_links_to_markdown(ls_links, target_name=""):
    """将 [(slug, title), ...] 转为 markdown 链接字符串。
    2026-08-31 用户要求：显示名用长句中反映依赖关系的原文句子（截断 60 字），
    非长句标题——与 fix_rel_evidence_0828.py 的句子显示名口径一致。
    ls_links 元素可为 (slug, title) 或 (slug, title, ls_page)（带长句页内容则提取句子）。"""
    parts = []
    for item in ls_links:
        if len(item) >= 3:
            slug, title, ls_page = item[0], item[1], item[2]
            sent, found = find_evidence_sentence(ls_page, target_name)
            disp = sent if found else title
        else:
            slug, title = item[0], item[1]
            disp = title
        disp = re.sub(r"\s+", " ", disp).strip()
        disp = disp.lstrip("|").strip()  # 2026-08-31：表格行残留行首竖线清洗
        if len(disp) > 60:
            disp = disp[:60] + "…"
        parts.append(f"[[{slug}|{disp}]]")
    return "、".join(parts)


def _split_table_row(line: str):
    """安全解析表格行：保护 [[slug|text]] wikilink 内竖线后按 '|' 切（坑 55/82）。
    返回 [cell0, cell1, cell2]（原文依据列可缺省）。"""
    row = line.strip()
    protected = []
    buf = ''
    i = 0
    while i < len(row):
        if row.startswith('[[', i):
            j = row.find(']]', i)
            if j == -1:
                j = len(row)
            protected.append(row[i:j + 2])
            buf += f'\x01{len(protected) - 1}\x01'
            i = j + 2
        else:
            buf += row[i]
            i += 1
    cells = [c.strip() for c in buf.strip('|').split('|')]
    out = []
    for c in cells:
        for m in re.finditer(r'\x01(\d+)\x01', c):
            c = c.replace(m.group(0), protected[int(m.group(1))])
        out.append(c)
    return out


def rewrite_summary_relations(summary: dict, ls_by_kid: dict, dry_run: bool,
                              doc_by_kid: dict = None, rev_ls_by_kid: dict = None):
    content = summary.get("content") or ""
    if "与其他法律法规的关系" not in content:
        return None

    target_kid = (summary.get("source_refs") or [""])[0].split("|")[0]
    target_ls = ls_by_kid.get(target_kid, [])
    # 2026-08-30：同家族修订类文档长句（第一优先级，用户规则：修订文档 > 原文）
    # 2026-08-31：扩跨族——同族修订类（①）→ 本文件（②）→ 跨族修订类（③）三级通道
    rev_ls_same = []
    rev_ls_cross = []
    for _kid, _ls in (rev_ls_by_kid or {}).get('same_family', {}).items():
        rev_ls_same.extend(_ls)
    for _kid, _ls in (rev_ls_by_kid or {}).get('cross_family', {}).items():
        rev_ls_cross.extend(_ls)

    lines = content.splitlines()

    # Find section start
    sec_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "## 与其他法律法规的关系":
            sec_idx = i
            break
    if sec_idx is None:
        return None

    # Find header row (may be after a descriptive paragraph)
    header_idx = None
    for i in range(sec_idx + 1, len(lines)):
        if lines[i].strip().startswith("| 相关制度"):
            header_idx = i
            break
    if header_idx is None:
        return None

    # Collect table rows (skip header/separator lines; stop at first non-"|" line)
    row_buffer = []
    post_idx = None
    for i in range(header_idx + 1, len(lines)):
        line = lines[i]
        if line.strip() in {"|---|---|---|", "|---|---|",
                            "| 相关制度 | 关系说明 | 原文依据 |",
                            "| 相关制度 | 关系说明 |"}:
            continue
        if not line.startswith("|"):
            post_idx = i
            break
        row_buffer.append(line)
    else:
        post_idx = len(lines)

    prelude = lines[:sec_idx + 1] + [""]
    postlude = lines[post_idx:]

    new_table = []
    changed = False
    deleted_rows = 0
    for line in row_buffer:
        if not line.startswith("|"):
            new_table.append(line)
            continue
        cells = _split_table_row(line)
        if len(cells) < 2:
            new_table.append(line)
            continue
        related = cells[0]
        desc = cells[1]
        ls_col = cells[2].strip().rstrip("|").strip() if len(cells) >= 3 else ""
        rel_type = "specializes"
        if any(k in desc for k in ["替代", "废止", "取代"]):
            rel_type = "supersedes"
        elif any(k in desc for k in ["依据", "制定", "根据"]):
            rel_type = "based_on"
        elif any(k in desc for k in ["参照执行", "遵照执行", "衔接", "配套"]):
            rel_type = "specializes"
        # 2026-08-30 用户规则：原文依据必须双向绑定到长句双链——
        # ① 同家族修订类文档长句（第一优先级）→ ② 本文件长句（第二优先级）
        # 2026-08-31：③ 跨族修订类长句（兜底，覆盖"采购管理办法引用《采购实施管理办法》修订说明"场景）
        # 两条通道都匹配不到 → 该关系记录无效，整行删除（不填占位词）。
        pure_desc, ls_links = render_relation_desc(rev_ls_same, related, rel_type, desc)
        if not ls_links:
            pure_desc, ls_links = render_relation_desc(target_ls, related, rel_type, desc)
        if not ls_links:
            pure_desc, ls_links = render_relation_desc(rev_ls_cross, related, rel_type, desc)
        if not ls_links:
            # 2026-08-31 边界修正：原文依据列已有纯文本描述（库内无对应文档时的合法形态，
            # 技能约定：库内无对应文档用纯文本《引用名》）→ 保留行不动，不强行删行。
            # 仅当整行无内容（相关制度/关系说明/原文依据全空）或原文依据列完全为空才删。
            if ls_col.strip():
                new_table.append(line)  # 保留原行（含已有纯文本依据）
                continue
            deleted_rows += 1
            changed = True
            continue
        ls_col = ls_links_to_markdown(ls_links, related)
        new_row = f"| {related} | {pure_desc} | {ls_col} |"
        new_table.append(new_row)
        if new_row != line:
            changed = True

    if not changed:
        return None

    # 全部行被删 → 整个章节删除（含 prelude 的 ## 标题行）
    if not new_table:
        print(f"  [删节] {summary.get('slug', '')}: 关系表全部 {deleted_rows} 行无长句依据，删除整个章节")
        head = lines[:sec_idx]
        # 去掉章节前的空行残留
        while head and not head[-1].strip():
            head.pop()
        new_content = "\n".join(head + [""] + postlude).rstrip() + "\n"
        return new_content

    print(f"  [回填] {summary.get('slug', '')}: 保留 {len(new_table)} 行, 删除 {deleted_rows} 行")
    new_content = "\n".join(
        prelude + ["", "| 相关制度 | 关系说明 | 原文依据 |", "|---|---|---|"] + new_table + [""] + postlude
    ).rstrip() + "\n"
    return new_content


def maybe_status(summary: dict) -> dict:
    current = summary.get("status") or ""
    return {"status": current} if current else {}


def run(kb_id: str, limit: int = 0, dry_run: bool = False):
    wr.load_config(config_path=os.path.expanduser("~/.hermes/config.yaml"))
    wr.KB = kb_id
    wr.mcp_init()

    pages = load_pages()
    ls_by_kid = load_all_long_sentences()
    summaries = [p for p in pages if p.get("slug", "").startswith("summary/")]
    print(f"summaries={len(summaries)} long_sentence_kids={len(ls_by_kid)}")

    doc_by_kid = load_doc_names(kb_id)

    # 2026-08-30：每个 summary 的修订类同族文档长句（第一优先级匹配通道）
    rev_ls_cache = {}
    for summary in summaries:
        _kid = (summary.get("source_refs") or [""])[0].split("|")[0]
        if _kid and _kid not in rev_ls_cache:
            rev_ls_cache[_kid] = collect_revision_ls(ls_by_kid, doc_by_kid, _kid)

    count = 0
    updated = 0
    failed = []
    for summary in summaries:
        if limit and count >= limit:
            break
        count += 1
        _kid = (summary.get("source_refs") or [""])[0].split("|")[0]
        new_content = rewrite_summary_relations(
            summary, ls_by_kid, dry_run, doc_by_kid,
            rev_ls_by_kid=rev_ls_cache.get(_kid, {}))
        if not new_content:
            continue
        slug = summary["slug"]
        title = summary.get("title") or slug
        folder_id = summary.get("folder_id") or ""
        source_refs = summary.get("source_refs") or []
        page_type = summary.get("page_type") or "summary"
        if dry_run:
            print(f"[dry-run] {slug}\n{new_content[:500]}\n---")
            updated += 1
            continue
        payload = {
            "slug": slug,
            "kb_id": kb_id,
            "title": title,
            "content": new_content,
            "folder_id": folder_id,
            "source_refs": source_refs,
            "page_type": page_type,
        }
        payload.update(maybe_status(summary))
        r = tool("update_wiki_page", **payload)
        if "error" in r:
            failed.append((slug, str(r["error"])))
            print(f"UPDATE FAILED {slug}: {r['error']}")
        else:
            updated += 1

    print(f"processed={count} updated={updated} failed={len(failed)}")
    if failed:
        with open("/tmp/summary_relation_backfill_failed.json", "w", encoding="utf-8") as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=KB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.kb, limit=args.limit, dry_run=args.dry_run)
