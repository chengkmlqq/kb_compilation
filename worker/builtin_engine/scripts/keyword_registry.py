#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高频关键词注册表 + 创建即合并（2026-08-31 新增，整库关键词合并）。

背景：旧规范关键词页按文档独立建（slug=`keyword/<kid前缀>-<词>`），同词跨文档
分裂成多页。本模块仿业务实体（entity_registry）的"创建即合并"模式：

- slug 新规范：`keyword/<md5(canonicalize(词))[:10]>`——canonical 确定性编码在
  md5 前缀里（与 entity-b 同思路），同词跨文档必然命中同一 slug
- 注册表：全量拉取新规范 frequent_keyword 页 → {md5前缀: 页面信息}
- 合并模式（canonical 命中）：update 现有页——folder_ids += 本文档关键词目录、
  source_refs += kid|文件名、原文出现位置按来源文档分组追加去重、总频次累加
- 新建模式（未命中）：create 新页（slug=新规范），一页多文档挂载

页面结构（多文档分组）：
    # <词>
    本关键词在 N 篇文档中高频出现（总频次：M）。
    ## 原文出现位置
    **《文档A》**（频次：a）
    | 序号 | 关联原文 |
    |---|---|
    | 1 | [[longsentence/xxx|…片段…]] |
    **《文档B》**（频次：b）
    | 序号 | 关联原文 |
    ...

Usage:
    import keyword_registry as kr
    kr.wr = wr
    reg = kr.build_registry(wr, kb_id)   # 全量拉新规范关键词页
    slug = kr.kw_slug('行政处罚')        # keyword/<md5[:10]>
    content = kr.render_page(title, groups, total_freq, total_docs)
    kr.merge_or_create(wr, kb_id, reg, word, freq, ls_links, folder_id, kid, source_file)
"""
import hashlib
import json
import os
import re

sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in os.sys.path:
    os.sys.path.insert(0, sys_path)
from slugger import canonicalize

# 新规范 slug：keyword/<10位hex>（旧规范 keyword/<kid前缀>-<词> 不在注册表——存量不迁移）
KW_SLUG_RE = re.compile(r'^keyword/([0-9a-f]{10})$')
# 分组标题行：**《文档名》**（频次：N）或 **关于修订《…》的通知.doc**（频次：N）
# （坑 97：文件名含《时 wr.bookname 原样返回、不带外层《，故不强制 **《 开头）
GROUP_RE = re.compile(r'^\*\*(.+?)\*\*（频次：(\d+)）\s*$')
GROUP_RE_OLD = re.compile(r'^\*\*(.+?)\*\*\s*$')  # 旧格式 **《文档名》**（无频次，兼容解析）


def kw_slug(word: str) -> str:
    """词 → 新规范 slug：keyword/<md5(canonicalize(词))[:10]>。"""
    c = canonicalize(word or '')
    return f'keyword/{hashlib.md5(c.encode("utf-8")).hexdigest()[:10]}' if c else ''


def build_registry(wr, kb_id, page_size=200, max_pages=None, verbose=False) -> dict:
    """全量拉新规范 frequent_keyword 页 → {md5前缀: page_info}。

    page_info: {page_id, slug, title, folder_id, folder_ids, source_refs, content}
    只收录新规范 slug（keyword/<10hex>）；旧规范页面（带文档名的）不在注册表内。
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
            if p.get('deleted_at'):
                continue
            if p.get('page_type') != 'frequent_keyword':
                continue
            total += 1
            slug = p.get('slug') or ''
            m = KW_SLUG_RE.match(slug)
            if not m:
                continue
            md5pre = m.group(1)
            fids = p.get('folder_ids') or []
            reg[md5pre] = {
                'page_id': p.get('id'),
                'slug': slug,
                'title': p.get('title') or '',
                'folder_id': p.get('folder_id') or (fids[0] if fids else ''),
                'folder_ids': fids,
                'source_refs': p.get('source_refs') or [],
                'content': p.get('content') or '',
            }
        if len(pages) < page_size or (max_pages and page_no >= max_pages):
            break
        page_no += 1
    if verbose:
        print(f'keyword registry: scanned {total} kw pages, {len(reg)} canonical (new-spec)')
    return reg


def parse_groups(content: str) -> dict:
    """解析已有页面 content → {文档名: [(ls_slug, 片段), ...]}（按分组标题切）。

    兼容新格式 `**《名》**（频次：N）` 与旧格式 `**《名》**`。
    返回 None 表示页面无有效分组（空壳页）。
    """
    if not content:
        return None
    groups = {}
    cur = None
    for ln in content.split('\n'):
        s = ln.strip()
        m = GROUP_RE.match(s)
        if m:
            cur = m.group(1)
            groups.setdefault(cur, [])
            continue
        m2 = GROUP_RE_OLD.match(s)
        if m2 and '原文出现位置' not in s and '高频出现' not in s and not s.startswith('#'):
            doc = m2.group(1).strip().strip('《》').strip()
            if doc and not doc.startswith('序号'):
                cur = doc
                groups.setdefault(cur, [])
                continue
        m3 = re.match(r'^\|\s*\d+\s*\|\s*\[\[([^\]|]+)\|([^\]]*)\]\]\s*\|', s)
        if m3 and cur is not None:
            groups[cur].append((m3.group(1), m3.group(2)))
    # 有分组但全空 → 视为无
    if groups and all(not v for v in groups.values()):
        return None
    return groups or None


def render_page_v2(title, groups_with_freq, total_docs, bookname_fn=None):
    """组装多文档分组页面（分组带频次）。

    groups_with_freq: {文档名: (freq, [(ls_slug, 片段), ...])}（有序）
    """
    bn = bookname_fn or (lambda x: x)
    freq_total = sum(f for f, _ in groups_with_freq.values())
    lines = [f'# {title}', '',
             f'本关键词在 {total_docs} 篇文档中高频出现（总频次：{freq_total}）。',
             '', '## 原文出现位置', '']
    for doc, (freq, links) in groups_with_freq.items():
        lines.append(f'**{bn(doc)}**（频次：{freq}）')
        lines.append('')
        lines.append('| 序号 | 关联原文 |')
        lines.append('|---|---|')
        if not links:
            lines.append('| - | 暂无关联长句 |')
        for i, (slug, snippet) in enumerate(links, 1):
            lines.append(f'| {i} | [[{slug}|{snippet}]] |')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def parse_groups_v2(content: str) -> dict:
    """解析已有页面 → {文档名: (freq, [(ls_slug, 片段), ...])}。"""
    if not content:
        return {}
    groups = {}
    cur = None
    cur_freq = 0
    for ln in content.split('\n'):
        s = ln.strip()
        m = GROUP_RE.match(s)
        if m:
            cur = m.group(1)
            cur_freq = int(m.group(2))
            groups.setdefault(cur, (cur_freq, []))
            continue
        m3 = re.match(r'^\|\s*\d+\s*\|\s*\[\[([^\]|]+)\|([^\]]*)\]\]\s*\|', s)
        if m3 and cur is not None and cur in groups:
            groups[cur][1].append((m3.group(1), m3.group(2)))
    return groups


def lookup(reg, word):
    """注册表查询：词 canonical → md5 → 命中返回 page_info or None。"""
    slug = kw_slug(word)
    if not slug:
        return None
    return reg.get(slug.split('/')[-1])


if __name__ == '__main__':
    # 自测
    assert kw_slug('行政处罚') == f'keyword/{hashlib.md5(canonicalize("行政处罚").encode()).hexdigest()[:10]}'
    c1 = render_page_v2('行政处罚', {'A.doc': (14, [('longsentence/x', '片段1')]), 'B.pdf': (6, [])}, 2)
    print(c1)
    g = parse_groups_v2(c1)
    print('parse:', {k: (v[0], len(v[1])) for k, v in g.items()})
    print('self-test OK')
