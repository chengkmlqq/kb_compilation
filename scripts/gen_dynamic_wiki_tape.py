#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动态 wiki 构建编排（Tape）生成器 —— 方案 B。

依据文档内容（全文或摘要）动态判定知识点，生成一份「文档专属」的
服务编排：目录按知识点多级构建，循环组件逐知识点抽取并挂页。

节点 DAG（单文档构建，输入：kid / doc_name / source_file / kb_id / kp_tree）：
  n01 def 输入定义
  n02 def 派生变量（kid8 / kp_paths / kp_count）
  n03 script 初始化 + 切片拉取清洗（全文或摘要，阈值可控）
  n04 script 门禁（chunks>0，否则阻断后继）
  n05 wiki_ensure_folder 按知识点建多级目录（幂等）→ folder_ids
  n06 script 组装 loop 集合（知识点 × folder_id × slug）→ kp_items
  n07 loop 循环组件（collection=kp_items, item_var=kp）
    n07a script 按知识点抽取相关原文切片（extract_hint 过滤）
    n07b kb_llm_chat 生成该知识点 wiki 内容（json_mode）
    n07c script 内容组装（LLM json → markdown 字符串）
    n07d wiki_upsert_page 挂到对应目录（skip_empty 兜底）
    n07e script 末轮收尾（idx=末轮才重建互链）

循环体 = loop 节点下游可达子图（edges 表达）：n07a~n07e 每轮全跑；
n07d 靠 skip_empty 让空抽取轮跳过；n07e 靠 item.idx 判定末轮才真正
重建互链（Map-Reduce 同款「每轮跑+条件收尾」模式）；n07e 是 body
终点（其后不再连线，否则被 collect_body_ids 卷进 body 每轮执行）。

知识点判定（kp_tree）发生在编排之外（上传触发链路 worker 进程内 LLM
判定），经 run inputs 注入；本生成器只产出「按知识点构建」的编排骨架。
生成器也可独立运行（--out 输出 payload），此时 kp_tree 仍为运行时输入。

用法（在 api-server / worker 容器内，PYTHONPATH=/srv/kb）：
  python scripts/gen_dynamic_wiki_tape.py --kb <kb_id> --doc <document_id> \
      --out /tmp/tape.json [--max-fulltext 14000] [--dry-run]
输出：可直接 POST /api/v1/orchestrations 创建的 payload（含 nodes/edges）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# ─────────────────────────────────────────────────────────────────────
# 内联代码模板 —— script 节点头部（注入 weknora_rpc，基础工具通道）
# ─────────────────────────────────────────────────────────────────────
WR_HEAD = '''import sys, os, json, re, hashlib, time, math
G = globals()
G.update({'sys': sys, 'os': os, 'json': json, 're': re,
          'hashlib': hashlib, 'time': time, 'math': math})
SD = '/srv/kb/worker/builtin_engine/scripts'
if SD not in sys.path:
    sys.path.insert(0, SD)
os.environ.setdefault('KB_API_BASE_URL', 'http://api-server:8000')
import weknora_rpc as wr
import reg_cache as rc
G['wr'] = wr
G['bindings'] = bindings
G['context'] = context
G['config'] = config
wr.load_config()
_kb = bindings.get('kb_id') or os.environ.get('WEKNORA_KB_ID', '')
if _kb:
    wr.KB = _kb
    os.environ['WEKNORA_KB_ID'] = _kb
wr.mcp_init()
_kid = bindings.get('kid', '')
_doc = bindings.get('doc_name', '')
_src = bindings.get('source_file', '')
G['_kid'] = _kid
G['_doc'] = _doc
G['_src'] = _src
'''

# ─────────────────────────────────────────────────────────────────────
# n03: 初始化 + 切片拉取清洗
# ─────────────────────────────────────────────────────────────────────
CODE_INIT_CHUNKS = WR_HEAD + '''
DIR = f"reg-cache/reg_{_kid[:8]}"
if not _kid:
    raise ValueError("缺少 kid")
chunks = []
page, psize = 1, 100
while True:
    r = wr.list_chunks(_kid, kb_id=wr.KB, page=page, page_size=psize)
    data = (r or {}).get('data') or []
    if not data:
        break
    chunks.extend(data)
    if len(data) < psize:
        break
    page += 1
chunks.sort(key=lambda c: c.get('chunk_index', 0))
cleaned = []
for c in chunks:
    t = (c.get('content') or '')
    t = wr.clean_chunk_text(t)
    lines = [ln for ln in t.split('\\n') if ln.strip()]
    cleaned.append('\\n'.join(lines))
full_text = '\\n\\n'.join(cleaned).strip()
chunk_count = len(chunks)
rc.write_json(DIR, 'chunks.json', [{'content': c} for c in cleaned])
rc.write_text(DIR, 'fulltext.txt', full_text)
bindings['chunk_count'] = chunk_count
bindings['full_text_len'] = len(full_text)
bindings['reg_dir'] = DIR
print(f"[n03] chunks={chunk_count} full_text={len(full_text)}字 → {DIR}")
'''

# ─────────────────────────────────────────────────────────────────────
# n04: 门禁
# ─────────────────────────────────────────────────────────────────────
CODE_GATE = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"reg-cache/reg_{_kid[:8]}")
cc = int(bindings.get('chunk_count', 0) or 0)
if cc <= 0:
    raise RuntimeError(f"切片缺失（chunks={cc}），禁止自行恢复，阻断构建")
print(f"[n04] 门禁通过：chunks={cc}")
'''

# ─────────────────────────────────────────────────────────────────────
# n05: 知识点判定（LLM json_mode）
# ─────────────────────────────────────────────────────────────────────
KP_PROMPT = """你是知识库构建专家。请阅读下面给定的文档内容（可能来自全文或摘要），
识别其中包含的知识点，输出一个知识点清单。

【判定要求】
1. 知识点 = 文档中相对独立、值得单独建 wiki 页的知识单元（指标、概念、对象、流程、规则、口径等）。
2. 每个知识点给出 name（简短中文名，用作 wiki 页标题）与 path（多级目录路径，用 / 分隔，
   如「收入指标/出帐金额」；层级由你根据知识点从属关系判定，允许 1~3 级）。
3. extract_hint 给后续抽取提供聚焦线索（该知识点对应原文的典型关键词/句式，30 字内）。
4. 数量控制在 3~15 个；宁少勿滥，只收录文档确有内容的。
5. 若文档过短/无实质内容，返回空数组。

【文档内容】
{document_text}

【输出要求——严格 JSON，禁止任何其他文字】
{{"knowledge_points": [{{"name": "知识点名", "path": "一级/二级", "extract_hint": "聚焦线索"}}]}}
"""

# ─────────────────────────────────────────────────────────────────────
# n07: 组装 loop 集合（知识点 × folder_id 绑定）
# ─────────────────────────────────────────────────────────────────────
CODE_ASSEMBLE_ITEMS = WR_HEAD + '''
import hashlib as _hl
kp_tree = bindings.get('kp_tree') or {}
kps = kp_tree.get('knowledge_points') or []
folder_ids = bindings.get('folder_ids') or []
# 兜底：LLM 判定失败/空 → 建单一「文档要点」页，保证构建不空转
if not kps:
    kps = [{"name": _doc or "文档要点", "path": "文档要点", "extract_hint": "全文要点"}]
# path 列表（wiki_ensure_folder 的 paths 入参）——按知识点顺序，
# 统一挂「文档名/」根目录下（doc_root 由输入侧清洗预置）
_doc_root = str(bindings.get('doc_root') or (bindings.get('doc_name') or '未命名文档')).strip() or '未命名文档'
paths = [(_doc_root + '/' + str(k.get('path') or k.get('name') or '未分类')).strip('/') for k in kps]
bindings['kp_paths'] = paths
bindings['kp_count'] = len(kps)
# folder_ids 与 paths 同序（wiki_ensure_folder 批量输出），绑定到每个知识点
items = []
seen_slugs = set()
for i, k in enumerate(kps):
    fid = folder_ids[i] if i < len(folder_ids) else ''
    name = str(k.get('name') or '').strip()
    if not name:
        name = f"知识点{i + 1}"
    base = re.sub(r'[^\\w\\u4e00-\\u9fff-]', '-', name).strip('-') or f"kp{i}"
    slug = base
    n = 2
    while slug in seen_slugs:
        slug = f"{base}-{n}"
        n += 1
    seen_slugs.add(slug)
    items.append({
            "name": name,
            "path": str(k.get('path') or name),
            "full_path": f"{_doc_root}/{str(k.get('path') or name).strip('/')}",
            "hint": str(k.get('extract_hint') or ''),
            "folder_id": fid,
            "slug": slug,
            "idx": i,
        })
bindings['kp_items'] = items
# 2026-10-29 需求2：全量知识点清单（name+path+slug），供 n07b 生成内容时
# 知道文档全貌（相关知识点上下文）、n07c 程序化互链（不依赖 LLM 编 slug）。
bindings['kp_all_desc'] = [
    {"name": it["name"], "path": it["path"], "slug": it["slug"]} for it in items
]
bindings['kp_all_names'] = '、'.join(it["name"] for it in items)
print(f"[n07] 知识点 {len(items)} 个，目录 {len(set(paths))} 条 → {json.dumps(paths, ensure_ascii=False)}")
'''

# ─────────────────────────────────────────────────────────────────────
# n07a: 按知识点抽取相关原文切片
# ─────────────────────────────────────────────────────────────────────
CODE_EXTRACT_SRC = WR_HEAD + '''
kp = bindings.get('kp') or {}
hint = (kp.get('hint') or '').strip()
full_text = rc.read_text(bindings.get('reg_dir', f"reg-cache/reg_{_kid[:8]}"), 'fulltext.txt')
src = full_text or ''
# 聚焦抽取：有 hint 时按关键词切窗口；无 hint 用全文截断
if hint and len(hint) >= 2:
    segs = [ln for ln in re.split(r'[\\n。；;]', src) if hint in ln]
    if segs:
        src = '\\n'.join(segs)[:3000]
src = (src or '')[:6000]
bindings['kp_src'] = src
print(f"[n07a] 知识点「{kp.get('name')}」抽取源 {len(src)} 字")
'''

# ─────────────────────────────────────────────────────────────────────
# n07b: 生成该知识点 wiki 内容（LLM json_mode）
# ─────────────────────────────────────────────────────────────────────
KP_CONTENT_PROMPT = """你是知识库 wiki 编写专家。基于给定文档片段，为一个知识点生成 wiki 页面内容。

【文档名】{{doc_name}}
【知识点】{{kp['name']}}
【该知识点在文档中的定位】{{kp['path']}}
【本知识点聚焦线索】{{kp['hint']}}

【文档全貌——本知识库该文档共判定出以下知识点（你写的内容需体现与它们的关联，而非孤立叙述）】
{{kp_all_names}}

【文档片段】
{{kp_src}}

【输出要求——严格 JSON，禁止任何其他文字】
{
  "title": "页面标题（= 知识点名）",
  "content": "markdown 格式的 wiki 正文，遵循以下结构：先一句话定位本知识点是什么；再用 ## 小节展开（定义/口径/与其他知识点的关系/在文档中的出处）。必须做到：1) 开篇点明该知识点隶属哪个文档、属于哪一类（如收入指标/维度）；2) 在『与其他知识点的关系』小节中，说明它和文档中其他哪些知识点有前后依赖、父子、并列或计算关联（只写文档片段事实能支撑的，不要编造；确实无关联就写『本知识点相对独立』）；3) 基于片段事实，不编造；无相关片段时 content 输出空字符串"
}
"""


# ─────────────────────────────────────────────────────────────────────
# n07e: 循环末轮收尾（重建互链 + 汇总）——loop body 每轮全跑，
# 靠 item.idx 判定最后一轮才真正执行（Map-Reduce 同款模式）
# ─────────────────────────────────────────────────────────────────────
CODE_LOOP_FINALE = WR_HEAD + '''
kp = bindings.get('kp') or {}
items = bindings.get('kp_items') or []
is_last = int(kp.get('idx') or 0) >= len(items) - 1
bindings['kp_is_last'] = is_last
if is_last:
    try:
        from api.db import get_sessionmaker as _gsm
        from api.services.kb_admin import wiki_rebuild_links as _wrl
        from api.services.kb_admin import wiki_create_page, wiki_update_page
        # 2026-10-29 需求3：写文档索引页（挂在 doc_root 文档根目录下）。
        # 聚合本文档全部知识点 → 双向链接页，供 Agent 查阅文档知识范围/能解决的问题。
        _doc_root = str(bindings.get('doc_root') or (bindings.get('doc_name') or '未命名文档')).strip() or '未命名文档'
        _index_slug = str(bindings.get('doc_index_slug') or f"{_doc_root}-索引")
        _doc_ref = str(bindings.get('doc_ref') or '')
        _kps = bindings.get('kp_all_desc') or [{"name": it.get('name'), "slug": it.get('slug'), "path": it.get('path')} for it in items]
        _db = _gsm()()
        try:
            # 2026-10-29 容错：n07b 内容级失败跳过（skip_on_invalid）的知识点没有
            # 写入页，索引页只列实际存在（status=active）的知识点，避免死链。
            from api.models.knowledge import WikiPage
            from sqlalchemy import select as _sq_sel
            try:
                _alive = set(_db.execute(
                    _sq_sel(WikiPage.slug).where(
                        WikiPage.kb_id == wr.KB, WikiPage.status == "active",
                    )
                ).scalars().all())
            except Exception as _ae:
                print(f"[n07e] 查已写页失败（索引页列全量）: {_ae}")
                _alive = {str(x.get('slug') or '') for x in _kps}
            _kps = [x for x in _kps if str(x.get('slug') or '') in _alive]
            # 2026-10-29 需求1：索引页挂在「文档根目录」(doc_root) 下，与知识点平级。
            # n05 已建好 doc_root 根目录（kp_paths 均带该前缀）；此处查根目录 id，
            # 查不到再建（wiki_create_folder 非幂等，必须查了才建）。
            from api.models.knowledge import WikiFolder
            _root_id = ''
            try:
                _row = _db.execute(_sq_sel(WikiFolder.id).where(
                    WikiFolder.kb_id == wr.KB, WikiFolder.name == _doc_root,
                    WikiFolder.parent_id == '',
                )).scalars().first()
                _root_id = str(_row or '')
            except Exception as _fe:
                print(f"[n07e] 查文档根目录失败（索引页挂 KB 根）: {_fe}")
            if not _root_id:
                try:
                    from api.services.kb_admin import wiki_create_folder as _wcf
                    _r = _wcf(_db, wr.KB, {"name": _doc_root, "parent_id": ""}, user_id="system")
                    _root_id = str(_r.get("id") or '')
                except Exception as _fe:
                    print(f"[n07e] 建文档根目录失败（索引页挂 KB 根）: {_fe}")
            # 按一级目录分组，便于 Agent 快速判断文档结构
            _groups = {}
            for _it in _kps:
                _top = str(_it.get('path') or _it.get('name') or '').split('/')[0].strip() or '其他'
                _groups.setdefault(_top, []).append(_it)
            _lines = [f"# {_doc_root} · 文档索引", "",
                      f"> 本文档由系统动态构建知识图谱，共 {len(_kps)} 个知识点。下方为分类索引与双向链接，点击可跳转各知识点详情页。", ""]
            for _g, _members in _groups.items():
                _lines.append(f"## {_g}")
                for _m in _members:
                    _lines.append(f"- [[{_m.get('slug')}|{_m.get('name')}]]")
                _lines.append("")
            # 文档可解决问题（给 Agent 的检索引导）：列出知识点即文档覆盖主题
            _lines.append("## 本文档可解答的问题")
            for _m in _kps:
                _lines.append(f"- 关于「{_m.get('name')}」的定义、口径与关联（见 [[{_m.get('slug')}|{_m.get('name')}]]）")
            _index_content = "\\n".join(_lines)
            try:
                wiki_create_page(_db, wr.KB, {
                    "title": f"{_doc_root} · 文档索引", "content": _index_content,
                    "slug": _index_slug, "page_type": "summary",
                    "folder_id": _root_id, "source_refs": [_doc_ref] if _doc_ref else [],
                }, user_id="system")
            except ValueError as _ve:
                if "已存在" in str(_ve) or "exists" in str(_ve).lower():
                    _upd: dict = {"title": f"{_doc_root} · 文档索引", "content": _index_content, "status": "active",
                                  "source_refs": [_doc_ref] if _doc_ref else []}
                    if _root_id:
                        _upd["folder_id"] = _root_id
                    wiki_update_page(_db, wr.KB, _index_slug, _upd)
                else:
                    raise
            _db.commit()
            print(f"[n07e] 文档索引页已写（{_index_slug}，{len(_kps)} 个知识点分组，folder={_root_id}）")
        finally:
            _db.close()
        # 全库互链重建（含索引页↔知识点↔来源文档的多向链接）
        _db = _gsm()()
        try:
            _wrl(_db, wr.KB)
        finally:
            _db.close()
        print(f"[n07e] 末轮收尾：全库互链重建完成（{len(items)} 个知识点）")
    except Exception as _e:
        print(f"[n07e] 索引页/互链重建失败（不阻断）: {_e}")
else:
    print(f"[n07e] 第 {int(kp.get('idx') or 0) + 1}/{len(items)} 轮，跳过收尾")
'''

# ─────────────────────────────────────────────────────────────────────
# n07c: 知识点内容组装（LLM json → markdown 字符串）
# ─────────────────────────────────────────────────────────────────────
CODE_WRAP_CONTENT = WR_HEAD + '''
kp = bindings.get('kp') or {}
raw = bindings.get('kp_content_raw') or {}
if isinstance(raw, dict):
    title = str(raw.get('title') or kp.get('name') or '')
    content = str(raw.get('content') or '').strip()
elif isinstance(raw, str):
    title = str(kp.get('name') or '')
    content = raw.strip()
else:
    title, content = str(kp.get('name') or ''), ''
if not content:
    print(f"[n07c] 知识点「{kp.get('name')}」无内容，跳过（skip_empty）")
# 2026-10-29 需求2：程序化追加「来源文档 / 相关知识点」段——
# 与文档索引页双向链（来源）+ 与该文档其他知识点 [[slug]] 互链。
_me = str(kp.get('slug') or '')
_items = bindings.get('kp_items') or []
_ctx_items = bindings.get('kp_all_desc') or []
_link = (_ctx_items or [{"name": it.get('name'), "slug": it.get('slug')} for it in _items])
_rel = [f"- [[{x.get('slug')}|{x.get('name')}]]" for x in _link
        if str(x.get('slug') or '') and str(x.get('slug')) != _me]
_doc_root = str(bindings.get('doc_root') or (bindings.get('doc_name') or '未命名文档')).strip() or '未命名文档'
_index_slug = str(bindings.get('doc_index_slug') or f"{_doc_root}-索引")
if content:
    _sec = []
    _sec.append(f"## 来源文档\\n本文档：[[{_index_slug}|{_doc_root}]]")
    if _rel:
        _sec.append("## 相关知识点\\n" + "\\n".join(_rel))
    content = content + "\\n\\n" + "\\n\\n".join(_sec)
bindings['kp_content'] = content
bindings['kp_title'] = title
print(f"[n07c] 知识点「{kp.get('name')}」内容 {len(content)} 字（互链 {len(_rel)} 条）")
'''


def _content_wrap_node(nid: str, label: str) -> dict:
    return _script_node(nid, label, CODE_WRAP_CONTENT)


# ─────────────────────────────────────────────────────────────────────
# 节点定义（拓扑模板）
# ─────────────────────────────────────────────────────────────────────
def _def_node(nid: str, label: str, assignments: list) -> dict:
    return {
        "id": nid,
        "data": {
            "icon": None, "group": None, "label": label,
            "config": {"queue": "build", "assignments": json.dumps(assignments, ensure_ascii=False)},
            "stepInst": "def",
        },
        "type": "stepNode",
        "position": {"x": 60, "y": 120},
    }


def _script_node(nid: str, label: str, code: str, queue: str = "build") -> dict:
    return {
        "id": nid,
        "data": {
            "icon": None, "group": None, "label": label,
            "config": {"code": code, "queue": queue},
            "stepInst": "script",
        },
        "type": "stepNode",
        "position": {"x": 220, "y": 120},
    }


def _llm_node(nid: str, label: str, prompt: str, output_var: str,
              json_mode: bool = False, model: str = "{{summary_model_id}}",
              system: str = "你只输出符合要求的 JSON，禁止任何思考过程或多余文字。",
              invalid_retries: int = 0, skip_on_invalid: bool = False) -> dict:
    cfg = {
        "prompt": prompt, "output_var": output_var, "model": model,
        "temperature": 0.2, "max_tokens": 3000,
        "system": system, "queue": "build", "max_attempts": 5,
    }
    # 2026-10-29 内容级容错：非 JSON（deepseek-v4-pro 偶发回显 prompt/推理草稿）
    # 按 invalid_retries 重试，耗尽后 skip_on_invalid=True 时跳过该节点（success+skipped）
    # 而非失败——动态 wiki 构建单知识点坏输出不再拖垮整条 run。仅 n07b 开启。
    if invalid_retries:
        cfg["invalid_retries"] = invalid_retries
    if skip_on_invalid:
        cfg["skip_on_invalid"] = True
    if json_mode:
        cfg["json_mode"] = True
        # 2026-10-28：json_mode 必须禁 thinking——deepseek-v4-pro 默认
        # thinking 开启会把推理过程写进 content（实测 n07b 输出
        # 「首先，我需要分析…」而非 JSON）；与 weknora_rpc.llm_call
        # 的 enable_thinking=False + thinking.type=disabled 同款语义。
        cfg["thinking"] = "disabled"
    return {
        "id": nid,
        "data": {"icon": None, "group": None, "label": label, "config": cfg, "stepInst": "kb_llm_chat"},
        "type": "stepNode",
        "position": {"x": 380, "y": 120},
    }


def _folder_node(nid: str, label: str, kb_id_tpl: str = "{{kb_id}}") -> dict:
    """wiki_ensure_folder：批量建多级目录，输出 folder_ids（与 paths 同序）。"""
    cfg = {
        "kb_id": kb_id_tpl,
        "paths": "{{kp_paths}}",
        "sep": "/",
        "output_var": "folder_ids",
        "queue": "build",
    }
    return {
        "id": nid,
        "data": {"icon": None, "group": None, "label": label, "config": cfg, "stepInst": "wiki_ensure_folder"},
        "type": "stepNode",
        "position": {"x": 540, "y": 120},
    }


def _loop_node(nid: str, label: str, collection_tpl: str, item_var: str) -> dict:
    cfg = {"collection": collection_tpl, "item_var": item_var, "queue": "build"}
    return {
        "id": nid,
        "data": {"icon": None, "group": None, "label": label, "config": cfg, "stepInst": "loop"},
        "type": "stepNode",
        "position": {"x": 700, "y": 120},
    }


def _upsert_node(nid: str, label: str) -> dict:
    """wiki_upsert_page：挂到当前知识点对应目录（skip_empty 兜底）。

    注意：loop 的 item_var 是 dict，节点 config 模板必须经 eval 取值 →
    用 {{kp['name']}} 等 dict 下标语法（不能用属性访问，dict 无该属性）。
    2026-10-28 文档归属：source_refs 写 {document_id}|{file_name}，
    删文档时 wiki 页级联清理按文档前缀命中（见 kb_admin 删文档清理）。
    """
    cfg = {
        "kb_id": "{{kb_id}}",
        "title": "{{kp['name']}}",
        "content": "{{kp_content}}",
        "slug": "{{kp['slug']}}",
        "folder_id": "{{kp['folder_id']}}",
        "page_type": "entity",
        "skip_empty": True,
                # 2026-10-28 文档归属：渲染后为合法 JSON 数组 ["doc123|文件名.docx"]；
                # 引号内置在模板里（{{doc_ref}} 只替换值），json.loads 可直接解析。
                "source_refs": "[\"{{doc_ref}}\"]",
                "output_var": "page_slug",
                "queue": "build",
    }
    return {
        "id": nid,
        "data": {"icon": None, "group": None, "label": label, "config": cfg, "stepInst": "wiki_upsert_page"},
        "type": "stepNode",
        "position": {"x": 860, "y": 120},
    }


def _finale_node(nid: str, label: str) -> dict:
    """循环末轮收尾（script）：重建互链 + 汇总（仅末轮执行）。

    注意：本节点必须是 loop body 的终点——loop 的循环体 = 其下游可达
    子图（collect_body_ids），body 之后再连节点会把该节点也卷进每轮
    执行。重建互链/汇总全部在末轮判定内完成。
    """
    return _script_node(nid, label, CODE_LOOP_FINALE)


def _print_node(nid: str, label: str, text_tpl: str) -> dict:
    return {
        "id": nid,
        "data": {"icon": None, "group": None, "label": label,
                 "config": {"text": text_tpl, "queue": "build"}, "stepInst": "print"},
        "type": "stepNode",
        "position": {"x": 980, "y": 120},
    }


def _edge(eid: str, src: str, tgt: str) -> dict:
    return {"id": eid, "type": "default", "source": src, "target": tgt}


# ─────────────────────────────────────────────────────────────────────
# 生成入口
# ─────────────────────────────────────────────────────────────────────
def build_dynamic_tape_payload(
    tape_name: str,
    tape_label: str | None = None,
    tape_descr: str | None = None,
) -> dict:
    """组装动态 wiki 构建编排 payload（nodes/edges/exec_params）。

    拓扑固定（建目录→loop 挂页），知识点数据运行时经 run inputs 注入
    （kp_tree → kp_paths → kp_items → loop collection）。
    """
    n01 = _def_node("n01", "输入定义", [
        {"variable": "kid", "expression": "bindings.get('kid', '')"},
        {"variable": "doc_name", "expression": "bindings.get('doc_name', '')"},
        {"variable": "source_file", "expression": "bindings.get('source_file', '')"},
        {"variable": "kb_id", "expression": "bindings.get('kb_id', '')"},
        {"variable": "kp_tree", "expression": "bindings.get('kp_tree', {})"},
    ])
    n02 = _def_node("n02", "派生变量", [
            {"variable": "kid8", "expression": "bindings.get('kid', '')[:8]"},
            # 2026-10-28 文档根目录：doc_root = 清洗后的文档名（去扩展名/非法路径
            # 字符），知识点的多级目录统一挂在「文档名/」根下——多文档构建不再
            # 在 KB 根下相互混叠。清洗在输入侧（enqueue_dynamic_wiki_build）已
            # 预置 doc_root 字段；这里兜底（无预置时用文件名）。
            {"variable": "doc_root",
             "expression": "str(bindings.get('doc_root') or (bindings.get('doc_name') or '未命名文档')).strip() or '未命名文档'"},
            {"variable": "kp_paths",
             "expression": "[(str(bindings.get('doc_root') or (bindings.get('doc_name') or '未命名文档')).strip() or '未命名文档') + '/' + (k.get('path') or k.get('name') or '未分类').strip('/') for k in ((bindings.get('kp_tree') or {}).get('knowledge_points') or [])]"},
            {"variable": "kp_count", "expression": "len((bindings.get('kp_tree') or {}).get('knowledge_points') or [])"},
                    # 2026-10-28 文档归属引用：{document_id}|{file_name}（对齐平台
                    # source_refs 契约，删文档级联清理按此前缀命中 wiki 页）。
                    {"variable": "doc_ref",
                     "expression": "(str(bindings.get('document_id') or bindings.get('doc_id') or bindings.get('kid') or '') + '|' + str(bindings.get('doc_name') or bindings.get('source_file') or ''))"},
                    # 2026-10-29 文档索引页 slug：固定派生（doc_root 清洗名 + 后缀），
                    # 与知识点 slug 天然隔离；n07e 末轮写索引页 + 知识点页反向链时复用。
                    {"variable": "doc_index_slug",
                     "expression": "(str(bindings.get('doc_root') or '未命名文档').strip() or '未命名文档') + '-索引'"},
                ])
    n03 = _script_node("n03", "初始化+切片拉取清洗", CODE_INIT_CHUNKS)
    n04 = _script_node("n04", "切片门禁", CODE_GATE)
    n05 = _folder_node("n05", "按知识点建目录树")
    n06 = _script_node("n06", "组装循环集合", CODE_ASSEMBLE_ITEMS)
    n07 = _loop_node("n07", "逐知识点循环", "{{kp_items}}", "kp")
    n07a = _script_node("n07a", "抽取知识点原文", CODE_EXTRACT_SRC)
    n07b = _llm_node("n07b", "生成知识点wiki",
                     KP_CONTENT_PROMPT, "kp_content_raw", json_mode=True,
                     invalid_retries=2, skip_on_invalid=True)
    n07c = _content_wrap_node("n07c", "内容组装")
    n07d = _upsert_node("n07d", "写入知识点页")
    n07e = _finale_node("n07e", "末轮收尾-互链重建")

    # loop body = n07 下游可达子图（n07a~n07e），每轮全跑；n07e 靠
    # item.idx 判定末轮才重建互链；body 终点 = n07e（其后不再连线，
    # 否则被 collect_body_ids 卷进 body 每轮执行）。
    nodes = [n01, n02, n03, n04, n05, n06, n07, n07a, n07b, n07c, n07d, n07e]
    # 依赖顺序（2026-10-28 修复 kp_paths 缺失导致的建目录节点失败）：
    #   n02 派生 kp_paths → n05 建目录消费 kp_paths/产出 folder_ids
    #   → n06 组装消费 folder_ids/重写 kp_paths → n07 loop
    # 旧版 n02 漏派生 kp_paths，n05 拿到未渲染 {{kp_paths}} 解析炸
    # （2026-10-28「附件1：行业短彩端口收入效益评估报表需求.xlsx」构建失败）。
    edges = [
        _edge("e01", "n01", "n02"),
        _edge("e02", "n02", "n03"),
        _edge("e03", "n03", "n04"),
        _edge("e04", "n04", "n05"),
        _edge("e05", "n05", "n06"),
        _edge("e06", "n06", "n07"),
        # loop → 循环体
        _edge("e07", "n07", "n07a"),
        _edge("e08", "n07a", "n07b"),
        _edge("e09", "n07b", "n07c"),
        _edge("e10", "n07c", "n07d"),
        _edge("e11", "n07d", "n07e"),
        # n07e = body 终点（无后继，loop 全部轮次完成后 run 终态）
    ]

    exec_params = [
        {"paramName": "kid", "paramType": "input", "paramLabel": "文档 knowledge_id"},
        {"paramName": "doc_name", "paramType": "input", "paramLabel": "文档名"},
        {"paramName": "source_file", "paramType": "input", "paramLabel": "源文件名"},
        {"paramName": "kb_id", "paramType": "input", "paramLabel": "知识库ID", "defaultValue": ""},
        {"paramName": "kp_tree", "paramType": "input", "paramLabel": "知识点树(JSON)", "defaultValue": ""},
    ]

    payload = {
        "tape_name": tape_name,
        "tape_label": tape_label or tape_name,
        "tape_descr": tape_descr or (
            "动态 wiki 构建（方案 B）：依据文档内容判定知识点，按知识点多级建目录，"
            "循环组件逐知识点抽取挂页。知识库无需配置 build_tape_id。"
        ),
        "tape_type": "kb_build",
        "category": "知识构建",
        "status": "effective",
        "nodes": nodes,
        "edges": edges,
        "exec_params": exec_params,
    }
    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="动态 wiki 构建编排生成器")
    ap.add_argument("--out", default="/tmp/dynamic_wiki_tape.json", help="输出 JSON 路径")
    ap.add_argument("--name", default="wiki构建-动态知识点", help="tape_name")
    args = ap.parse_args()

    payload = build_dynamic_tape_payload(
        tape_name=args.name,
    )
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"动态 wiki 构建编排已生成 → {args.out}")
    print(f"节点 {len(payload['nodes'])} 个，边 {len(payload['edges'])} 条")
