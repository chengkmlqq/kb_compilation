"""实体注册表（跨文档实体归一化 P1，2026-08-15）。

从 WeKnora 全量拉取实体页，构建 canonical_name → 页面信息 索引，
供 rebuild 流程做"复用优先"判定（同名实体跨文档合并）。

数据源：wiki_pages 中**新规范实体 slug**（`entity-b-<md5[:10]>` /
`entity-r-ro<编号>-<md5[:10]>`）——canonical 确定性编码在 slug 的 md5 前缀里，
不依赖 page_metadata（2026-08-15 实测 MCP 网关不转发 page_metadata）。

Usage:
    import entity_registry as er
    er.wr = wr                      # weknora_rpc 模块
    reg = er.build_registry(kb_id)  # 全量拉取构建索引
    hit = er.lookup(reg, '采购方式', '组织角色')  # 命中 → 页面信息 or None
    er.save_registry(reg, path)     # 缓存到 JSON
    reg = er.load_registry(path)    # 从缓存加载
"""
import json
import os
import re
import time
from slugger import canonicalize, entity_slug
SLUG_RE = re.compile('^entity-(b|r-ro\\d+)-([0-9a-f]{10})$')

def _slug_info(slug: str) -> tuple | None:
    """从新规范 slug 提取 (类型, md5前缀)。非新规范 slug 返回 None。"""
    m = SLUG_RE.match(slug or '')
    if not m:
        return None
    return (m.group(1), m.group(2))

def build_registry(wr, kb_id, page_size=100, max_pages=None, verbose=False) -> dict:
    """全量拉实体页，构建 {md5_prefix: page_info}。

    page_info: {page_id, slug, title, folder_id, folder_ids, source_refs, ontology}
    只收录新规范实体 slug（canonical 编码在 md5 前缀）。
    旧规范页面（带文档名的 entity-b-xxx）不在注册表内 —— 由 P4 迁移时转换。
    """
    reg = {}
    page_no = 1
    total = 0
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb_id, 'page': page_no, 'page_size': page_size})
        pages = r.get('pages') or []
        if not pages:
            break
        for p in pages:
            total += 1
            info = _slug_info(p.get('slug'))
            if not info:
                continue
            kind, md5pre = info
            src_refs = p.get('source_refs') or []
            fids = p.get('folder_ids') or []
            reg[md5pre] = {'page_id': p.get('id'), 'slug': p.get('slug'), 'title': p.get('title'), 'kind': kind, 'folder_id': p.get('folder_id') or (fids[0] if fids else ''), 'folder_ids': fids, 'source_refs': src_refs if isinstance(src_refs, list) else [], 'content': p.get('content') or ''}
        if len(pages) < page_size or (max_pages and page_no >= max_pages):
            break
        page_no += 1
        if verbose and page_no % 10 == 0:
            print(f'  ... scanned {total} pages, registry {len(reg)} entities')
    if verbose:
        print(f'registry: scanned {total} pages, {len(reg)} canonical entities')
    return reg

def _canonical_index(reg: dict) -> dict:
    """注册表 → {canonical: [slugs]}（按 title canonical 索引，跨本体合并用）"""
    idx = {}
    for md5pre, info in reg.items():
        cname = canonicalize(info.get('title') or '')
        idx.setdefault(cname, []).append(info.get('slug') or md5pre)
    return idx

def lookup(reg: dict, entity_name: str, ontology: str) -> dict | None:
    """按实体名（原始中文）+ 本体类别查注册表。计算 entity_slug 的 md5 前缀命中。
    命中顺序（2026-08-16 升级：canonical 优先于本体）：
    1. canonical 精确匹配（不管本体），候选选共享度最高（folder_ids 最多）——同名不同本体合并
    2. 同义词表（SYNONYMS，别名→主名）归一后再查
    ontology 仅用于无候选时计算 slug 兜底。
    """
    cname = canonicalize(entity_name)
    idx = _canonical_index(reg)
    cands = idx.get(cname, [])
    if cands:
        best = max(cands, key=lambda s: len((reg.get(_slug_info(s)[1]) or {}).get('folder_ids') or []))
        return reg.get(_slug_info(best)[1])
    main = SYNONYMS.get(cname)
    if main:
        mc = canonicalize(main)
        mcands = idx.get(mc, [])
        if mcands:
            best = max(mcands, key=lambda s: len((reg.get(_slug_info(s)[1]) or {}).get('folder_ids') or []))
            return reg.get(_slug_info(best)[1])
    return None

def load_synonyms(path=None) -> dict:
    """加载同义词表 {别名canonical: 主名}。默认读技能目录 synonyms.json。"""
    global SYNONYMS
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'synonyms.json')
    try:
        data = json.load(open(path, encoding='utf-8'))
        SYNONYMS = data.get('synonyms', {})
    except Exception as e:
        print(f'[warn] synonyms.json 加载失败: {e}')
    return SYNONYMS
# 市场监督域同义词表（初始为空，跨文档合并发现真同义对后在此累积，格式 别名canonical: 主名）
# 注意只合并纯表述差异/同物异名，禁止合并对立概念/不同法规（见 SKILL 坑 15 同义词判定原则）
SYNONYMS = {}

def lookup_slug(reg: dict, slug: str) -> dict | None:
    """按目标 slug（已用 entity_slug 生成）查注册表。"""
    info = _slug_info(slug)
    if not info:
        return None
    return reg.get(info[1])

def save_registry(reg: dict, path: str):
    json.dump(reg, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

def load_registry(path: str) -> dict:
    return json.load(open(path, encoding='utf-8'))

def registry_stats(reg: dict) -> dict:
    """统计：实体总数 / 多文档共享实体数。"""
    multi = [v for v in reg.values() if len(v.get('folder_ids') or []) > 1]
    multi_src = [v for v in reg.values() if len(v.get('source_refs') or []) > 1]
    return {'total': len(reg), 'multi_folder': len(multi), 'multi_source': len(multi_src)}
if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import weknora_rpc as wr
    wr.load_config()
    kb = sys.argv[1] if len(sys.argv) > 1 else None
    if not kb:
        print('Usage: python entity_registry.py <kb_id> [save_path]')
        sys.exit(1)
    save = sys.argv[2] if len(sys.argv) > 2 else 'entity_registry.json'
    wr.KB = kb
    wr.mcp_init()
    reg = build_registry(wr, kb, verbose=True)
    save_registry(reg, save)
    print(json.dumps(registry_stats(reg), ensure_ascii=False))
    print(f'saved -> {save}')