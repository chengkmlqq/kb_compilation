#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全库规则-实体关联缝合（2026-09-02，坑 106 配套）

背景：原两次独立抽取（规则/业务）靠事后 canonical 文本匹配缝合，名称漂移+时序错位
导致关联断裂（0829 实测：rule 约束对象 61% 纯文本未链、biz 关键规则表 94% 空）。
B2 联合抽取（JOINT_PROMPT）从源头修复新构建；本脚本用**最终全库注册表**治理存量：
  方向① rule 页 ← biz：约束对象/关联对象纯文本 → canonicalize 精确/核心包含匹配
           → 补 [[entity-b-xxx|名]]（唯一命中才链，歧义/无对应不链，宁缺毋滥）
  方向② biz 页「关键规则」表 ← 全库 rule：约束对象 canonical 匹配 biz 名
           → 追加 | [[entity-r-xxx|title]] | 描述 | 来源 |（按规则 slug 去重，幂等）

用法：
    python3 backfill_rule_biz_links.py --kb <kb_id> [--dry-run] [--limit N] [--slugs s1,s2]
执行后需 wiki_rebuild_links（新增了大量 wikilink）。
"""
import sys, os, json, re, argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import weknora_rpc as wr
from extract_entities_single import canonicalize

ENT_LINK_RE = re.compile(r'\[\[(entity-[br]-[a-z0-9-]+)')


def load_pages_by_type(kb, page_type):
    pages, page = {}, 1
    while True:
        r = wr.list_wiki_pages(kb_id=kb, page=page, page_size=200)
        data = r.get('pages') or []
        for p in data:
            if p.get('page_type') == page_type:
                pages[p['slug']] = p
        if len(data) < 200:
            break
        page += 1
        if page > 300:
            break
    return pages


def basic_field(content, field):
    """取 ## 基本信息 节中某字段行的值。"""
    m = re.search(r'## 基本信息(.*?)(\n## |\Z)', content, re.S)
    if not m:
        return ''
    for ln in m.group(1).splitlines():
        lm = re.match(r'\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|$', ln)
        if lm and lm.group(1).strip() == field:
            return lm.group(2).strip()
    return ''


def rule_desc(content):
    """规则页一句话定位（**类别**　描述 首段）。"""
    m = re.search(r'\*\*[^*]+?\*\*\s*　?(.*)', content or '')
    return (m.group(1).strip() if m else '')[:80]


def match_biz_slug(name, biz_canons, biz_by_canon):
    """名称 → biz slug：canonical 精确 → 核心词唯一包含 → 无（歧义不链）。"""
    cc = canonicalize(name)
    if cc in biz_by_canon:
        return biz_by_canon[cc][0]
    cands = [slugs for bcn, slugs in biz_by_canon.items()
             if bcn and (cc in bcn or bcn in cc)]
    if len(cands) == 1 and len(cands[0]) == 1:
        return cands[0][0]
    return ''


def link_plain_in_basic(content, field, biz_by_canon, biz_canons_names):
    """把基本信息表某字段行中的纯文本词链接到 biz（已链/'-'/非候选不动）。
    整行值若为单个纯文本名 → 尝试链接；多个（顿号/、连接）逐个尝试。"""
    if '## 基本信息' not in content:
        return content, 0
    seg_m = re.search(r'## 基本信息(.*?)(\n## |\Z)', content, re.S)
    seg = seg_m.group(1)
    line_m = re.search(rf'^\| {field} \|(.*)\|$', seg, re.M)
    if not line_m:
        return content, 0
    raw = line_m.group(1).strip()
    if raw.startswith('[[') or raw in ('-', ''):
        return content, 0
    parts = re.split(r'[；、,，]', raw)
    out_parts = []
    nlink = 0
    for pt in parts:
        pt = pt.strip()
        if not pt or pt.startswith('[['):
            out_parts.append(pt)
            continue
        slug = match_biz_slug(pt, set(), biz_by_canon)
        if slug:
            out_parts.append(f'[[{slug}|{pt}]]')
            nlink += 1
        else:
            out_parts.append(pt)
    if nlink == 0:
        return content, 0
    new_line = f'| {field} | ' + '；'.join(out_parts) + ' |'
    new_seg = seg[:line_m.start()] + new_line + seg[line_m.end():]
    return content[:seg_m.start(1)] + new_seg + content[seg_m.end(1):], nlink


def parse_basic_table_plain_links(content):
    """基本信息表中所有纯文本（非 [[ ）字段值，供方向②关键规则匹配的候选对象。"""
    m = re.search(r'## 基本信息(.*?)(\n## |\Z)', content, re.S)
    if not m:
        return []
    names = []
    for ln in m.group(1).splitlines():
        lm = re.match(r'\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|$', ln)
        if lm and lm.group(2).strip() and not lm.group(2).strip().startswith('[['):
            fname = lm.group(1).strip()
            if fname in ('约束对象', '关联对象'):
                for pt in re.split(r'[；、,，]', lm.group(2).strip()):
                    pt = pt.strip()
                    if pt and not pt.startswith('[[') and '（' not in pt[:2]:
                        names.append(pt)
    return names


def append_key_rule_rows(content, rows):
    """在 ## 关键规则 表分隔行后追加行（先清占位行；幂等由调用方保证 slug 去重）。"""
    if not rows or '## 关键规则' not in content:
        return content
    seg_m = re.search(r'## 关键规则(.*?)(\n## |\Z)', content, re.S)
    seg = seg_m.group(1)
    lines = seg.split('\n')
    # 清占位行（首格占位/（待补充）/（待关联规则实体））
    lines = [ln for ln in lines
             if not (ln.strip().startswith('|')
                     and (re.match(r'^\|\s*（待[^）]*）', ln.strip())
                          or '（待补充）' in ln or '（待关联规则实体）' in ln))]
    insert_at = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith('|') and set(ln.strip().replace('|', '').replace('-', '').replace(':', '').strip()) == set():
            insert_at = i + 1
            break
    if insert_at is None:
        return content
    lines[insert_at:insert_at] = rows
    new_seg = '\n'.join(lines)
    return content[:seg_m.start(1)] + new_seg + content[seg_m.end(1):]


def run(kb=None, dry_run=False, limit=None, slugs=None):
    KB = kb or wr.load_kb()
    wr.load_config()
    wr.KB = KB
    wr.mcp_init()
    print(f'[stitch] kb={KB} 拉取页面...')
    biz_pages = load_pages_by_type(KB, 'business_ontology')
    rule_pages = load_pages_by_type(KB, 'rule_ontology')
    # biz 注册表（canonical -> [slug...]）
    biz_by_canon = {}
    for slug, p in biz_pages.items():
        biz_by_canon.setdefault(canonicalize(p.get('title') or ''), []).append(slug)
    print(f'[stitch] biz {len(biz_pages)} | rule {len(rule_pages)}')
    targets = sorted(rule_pages) if not slugs else [s for s in slugs if s in rule_pages]
    stats = {'rule_linked': 0, 'biz_keyrules': 0, 'updated_pages': 0, 'errors': 0}

    # 方向①：rule 页约束对象/关联对象纯文本 → 补链
    for slug in targets:
        p = rule_pages[slug]
        content = p.get('content') or ''
        new_content, n1 = link_plain_in_basic(content, '约束对象', biz_by_canon, set())
        # 关联对象行内是多值（名（角色）混排）——仅当整行无 [[ 时按单值试链（防误伤）
        obj_row = basic_field(content, '关联对象')
        if obj_row and '[[' not in obj_row and obj_row != '-' and '（' in obj_row:
            new_content, n2 = link_plain_in_basic(new_content, '关联对象', biz_by_canon, set())
        else:
            n2 = 0
        if new_content != content:
            stats['rule_linked'] += n1 + n2
            stats['updated_pages'] += 1
            if dry_run:
                print(f"  [DRY] rule {slug}: 补链 约束对象+{n1} 关联对象+{n2}")
                continue
            r = wr.update_page(slug=slug, kb_id=KB, content=new_content, title=p.get('title'),
                               folder_id=p.get('folder_id') or '',
                               folder_ids=p.get('folder_ids') or ([p.get('folder_id')] if p.get('folder_id') else []),
                               source_refs=p.get('source_refs') or [], page_type='rule_ontology',
                               status=p.get('status') or 'published')
            if 'error' in r and r.get('isError'):
                stats['errors'] += 1
                print(f"  [ERR] {slug}: {str(r.get('error'))[:100]}")

    # 方向②：biz 页关键规则表 ← 全库 rule 约束对象匹配
    # 反查：每 rule 的约束对象（含纯文本）canonical → 命中的 biz 页（不精确则跳过）
    rule_by_biz = {}
    for slug, p in rule_pages.items():
        content = p.get('content') or ''
        cons = basic_field(content, '约束对象')
        if not cons or cons.startswith('[['):
            m = re.match(r'\[\[entity-b-[a-z0-9-]+\|([^\]]+)\]\]', cons)
            cons_name = m.group(1) if m else cons
        else:
            cons_name = cons.split('（')[0].strip()
        if not cons_name:
            continue
        cc = canonicalize(cons_name)
        if cc not in biz_by_canon:
            continue
        desc = rule_desc(content)
        src = (p.get('source_refs') or [''])[0]
        src_name = src.split('|', 1)[1] if '|' in src else src
        for bslug in biz_by_canon[cc]:
            rule_by_biz.setdefault(bslug, []).append((slug, p.get('title') or '', desc, src_name))
    for bslug, rows in rule_by_biz.items():
        p = biz_pages.get(bslug)
        if not p:
            continue
        content = p.get('content') or ''
        existing = set(ENT_LINK_RE.findall(content))
        add = [r for r in rows if r[0] not in existing]
        if not add:
            continue
        new_rows = [f"| [[{s}|{t}]] | {d} | {wr.bookname(src)} |" for s, t, d, src in add]
        new_content = append_key_rule_rows(content, new_rows)
        if new_content == content:
            continue
        stats['biz_keyrules'] += len(add)
        stats['updated_pages'] += 1
        if dry_run:
            print(f"  [DRY] biz {bslug}: 追加关键规则 {len(add)} 行")
            continue
        r = wr.update_page(slug=bslug, kb_id=KB, content=new_content, title=p.get('title'),
                           folder_id=p.get('folder_id') or '',
                           folder_ids=p.get('folder_ids') or ([p.get('folder_id')] if p.get('folder_id') else []),
                           source_refs=p.get('source_refs') or [], page_type='business_ontology',
                           status=p.get('status') or 'published')
        if 'error' in r and r.get('isError'):
            stats['errors'] += 1
            print(f"  [ERR] {bslug}: {str(r.get('error'))[:100]}")

    print(f"[stitch] 完成: rule 补链 {stats['rule_linked']} | biz 关键规则补 {stats['biz_keyrules']} 行"
          f" | 更新页 {stats['updated_pages']} | 错误 {stats['errors']}")
    if stats['updated_pages'] and not dry_run:
        print('[stitch] 提示：新增大量 wikilink，请执行 wiki_rebuild_links')
    return stats


def main():
    ap = argparse.ArgumentParser(description='全库规则-实体关联缝合（坑 106 配套，治理存量断链）')
    ap.add_argument('--kb', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--slugs', default=None, help='逗号分隔的 rule 页 slug（默认全库）')
    args = ap.parse_args()
    slugs = [s.strip() for s in args.slugs.split(',')] if args.slugs else None
    run(kb=args.kb, dry_run=args.dry_run, limit=args.limit, slugs=slugs)


if __name__ == '__main__':
    main()
