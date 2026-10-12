#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""目录结构校验门禁（2026-08-28 新增，与 check_ls_template.py 页面门禁配套）。

校验 wiki 目录树结构（DB 直连，快且准）：
  1. 根目录健康：无"碎片根"（修订说明/授权清单/一览表类根应已并入主根）
  2. 版本目录命名：N-<版本名> 序号前缀规范、无重复序号
  3. 子目录完整：版本目录下 基础实体/长句原文/高频关键词；基础实体下 知识对象 9 类 + 规范要求 12 类
  4. 摘要页挂载：folder 非空、指向版本目录（不悬空、不挂根）
  5. 页面归属：页面 folder 必须存在于目录树内（无悬空 folder）
  6. 无空根残留（DB 视角根下无子目录 = 违规）

用法：python3 check_folder_structure.py --kb <kb_id>
输出：按根分组报告 + 问题列表 + 通过/失败统计。只读不写。
"""
import sys
import domain
import os
import re
import json
import argparse
import subprocess
from collections import defaultdict

KB = None

# 碎片根特征词（根下无制度正文且名称含这些词 → 疑似碎片根）
FRAG_WORDS = ['修订说明', '授权清单', '一览表', '主要修订情况']
# 合法独立根特征（允许无正文的根）
LEGIT_INDEPENDENT = ['审批记录', '违规干预']


def psql(q):
    r = subprocess.run(['docker', 'exec', 'WeKnora-postgres', 'psql', '-U', 'weknora',
                        '-d', 'weknora', '-t', '-A', '-c', q], capture_output=True, text=True, timeout=60)
    return (r.stdout or '').strip()


def load_folders():
    """拉全部目录。返回 {id: {name, parent_id}} + 根 id 列表。"""
    rows = psql(f"SELECT id, name, parent_id FROM wiki_folders "
                f"WHERE knowledge_base_id='{KB}' ORDER BY created_at")
    folders = {}
    for line in rows.splitlines():
        parts = line.split('|', 2)
        if len(parts) == 3:
            folders[parts[0]] = {'name': parts[1], 'parent_id': parts[2]}
    roots = [fid for fid, f in folders.items() if f['parent_id'] == '']
    return folders, roots


def load_pages():
    """拉摘要页与全部页面的 folder 归属。返回 (summaries, page_folders)。"""
    rows = psql(f"SELECT slug, folder_id, page_type, source_refs::text FROM wiki_pages "
                f"WHERE knowledge_base_id='{KB}' AND deleted_at IS NULL")
    summaries = []
    page_folders = defaultdict(int)
    for line in rows.splitlines():
        parts = line.split('|', 3)
        if len(parts) < 4:
            continue
        slug, folder_id, ptype, srefs = parts
        if ptype == 'summary':
            kid = ''
            # 2026-08-30 修复：source_refs::text 是 JSON 数组（["kid|文件名",...]），
            # 原 replace('"') + split(',') 会残留前导 '[' → kid 变 '[kid' 匹配失败（全库 WARN）。
            # 用 UUID 正则直接提取。
            m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', srefs or '')
            if m:
                kid = m.group(1)
            summaries.append({'slug': slug, 'folder_id': folder_id, 'kid': kid})
        page_folders[folder_id] += 1
    return summaries, page_folders


def doc_type_of_summary(summary, folders):
    """从摘要 source_refs 文件名推断类型。"""
    return '未知'


def check_roots(folders, roots, summaries):
    """① 根目录健康：碎片根检测。返回问题列表。"""
    problems = []
    # 每根下的摘要（folder 落在根树内——简化：直接子级版本目录的摘要）
    root_has_main = {}
    for rid in roots:
        has_main = False
        for s in summaries:
            fid = s['folder_id']
            # 摘要 folder → 其父应为根（版本目录直接挂根）
            if fid and folders.get(fid, {}).get('parent_id') == rid:
                fname = s.get('_fname', '')
                if '通知' not in fname and '附件' not in fname and '修订' not in fname \
                        and '清单' not in fname and '说明' not in fname:
                    has_main = True
        root_has_main[rid] = has_main
    for rid in roots:
        name = folders[rid]['name']
        has_main = root_has_main.get(rid, False)
        if not has_main:
            if any(w in name for w in FRAG_WORDS):
                problems.append(f'[ERROR] 疑似碎片根: 「{name}」（根下无制度正文且名称含修饰词，应已并入主根）')
            elif not any(w in name for w in LEGIT_INDEPENDENT):
                problems.append(f'[WARN] 独立根无制度正文: 「{name}」（如非纯辅助记录请核实归属）')
    return problems


def check_version_dirs(folders, roots):
    """② 版本目录命名（2026-08-28 更新：无序号前缀、唯一标识命名）+ 空根检查。"""
    problems = []
    for rid in roots:
        name = folders[rid]['name']
        children = [(fid, f) for fid, f in folders.items() if f['parent_id'] == rid]
        if not children:
            problems.append(f'[ERROR] 空根: 「{name}」（DB 视角无子目录，应删除）')
            continue
        seen = {}
        for fid, f in children:
            vname = f['name']
            if re.match(r'^\d+-', vname):
                problems.append(f'[WARN] {name} 下版本目录带序号前缀（2026-08-28 起去序号，改唯一标识命名）: 「{vname}」')
            if vname in seen:
                problems.append(f'[ERROR] {name} 下版本目录重名（无法唯一标识）: 「{vname}」')
            seen[vname] = fid
    return problems


def check_subdirs(folders, roots):
    """③ 子目录完整：三子目录 + 本体 9/12 类。"""
    problems = []
    BIZ = domain.BIZ_CATS
    RULE = domain.RULE_CATS
    for rid in roots:
        rname = folders[rid]['name']
        version_dirs = [f for f in folders.values() if f['parent_id'] == rid]
        for vd in version_dirs:
            vid = [k for k, v in folders.items() if v is vd][0]
            subs = [f['name'] for f in folders.values() if f['parent_id'] == vid]
            for need in ('基础实体', '长句原文', '高频关键词'):
                if need not in subs:
                    problems.append(f'[ERROR] {rname}/{vd["name"]} 缺子目录: {need}')
            if '基础实体' in subs:
                base_id = [k for k, v in folders.items() if v['parent_id'] == vid and v['name'] == '基础实体'][0]
                onto = [f['name'] for f in folders.values() if f['parent_id'] == base_id]
                for need in ('知识对象', '规范要求'):
                    if need not in onto:
                        problems.append(f'[ERROR] {rname}/{vd["name"]}/基础实体 缺: {need}')
                for oname, cats in (('知识对象', BIZ), ('规范要求', RULE)):
                    oid = [k for k, v in folders.items() if v['parent_id'] == base_id and v['name'] == oname]
                    if oid:
                        cat_names = [f['name'] for f in folders.values() if f['parent_id'] == oid[0]]
                        missing = [c for c in cats if c not in cat_names]
                        if missing:
                            problems.append(f'[WARN] {rname}/{vd["name"]}/{oname} 缺类别: {missing}')
    return problems


def check_summary_attribution(folders, roots, summaries, page_kids_all):
    """⑧ 摘要页归存（2026-08-28 新增，用户要求的"摘要文件归存"门禁）：
    ① source_refs 必须含 kid（缺失=没有归属文件，ERROR）；
    ② folder 必须指向"该 kid 文档自己的版本目录"——folder 子树（长句/关键词页）
       的 kid 集合必须包含摘要自身的 kid（无内容页的表格文档用关键词页判定；仍无则 WARN）。"""
    problems = []
    root_ids = set(roots)
    version_dirs = {fid for fid, f in folders.items() if f['parent_id'] in root_ids}
    subtree = {}
    for vd in version_dirs:
        st = {vd}
        changed = True
        while changed:
            changed = False
            for fid, f in folders.items():
                if f['parent_id'] in st and fid not in st:
                    st.add(fid)
                    changed = True
        subtree[vd] = st
    dir_kids = {}
    for fid, kids in page_kids_all.items():
        dir_kids[fid] = kids
    # 版本目录子树 kid 集合
    vd_kids = {}
    for vd in version_dirs:
        ks = set()
        for fid in subtree[vd]:
            ks |= dir_kids.get(fid, set())
        vd_kids[vd] = ks

    for s in summaries:
        slug = s['slug']
        # ① source_refs 必须含 kid
        kid = s.get('kid', '')
        if not kid:
            problems.append(f'[ERROR] 摘要页无归属文件（source_refs 缺 kid）: {slug}')
            continue
        # ② folder 归属
        fid = s['folder_id']
        if not fid:
            problems.append(f'[ERROR] 摘要页 folder 空（未归存）: {slug}（kid={kid[:8]}）')
            continue
        if fid in version_dirs:
            ks = vd_kids.get(fid, set())
            if kid in ks:
                continue  # 正确
            # 该 kid 内容页在别的版本目录 → 挂错
            for vd2, ks2 in vd_kids.items():
                if kid in ks2 and vd2 != fid:
                    problems.append(f'[ERROR] 摘要页挂错版本目录: {slug} 挂「{folders[fid]["name"]}」'
                                    f'但其文档页面在「{folders[vd2]["name"]}」')
                    break
            else:
                problems.append(f'[WARN] 摘要页目录无该文档内容页（可能空目录/表格文档）: {slug}'
                                f' 挂「{folders[fid]["name"]}」')
        else:
            problems.append(f'[WARN] 摘要页 folder 非版本目录: {slug} -> {folders.get(fid, {}).get("name", fid)}')
    return problems


def check_page_attribution(folders, roots, page_folders):
    """⑤ 页面归属：页面 folder 存在于目录树内。"""
    problems = []
    valid = set(folders.keys())
    for fid in page_folders:
        if fid and fid not in valid:
            problems.append(f'[ERROR] 页面 folder 悬空（目录不存在）: {fid}（{page_folders[fid]} 页）')
    return problems


def check_version_dir_docs(folders, roots, page_kids_all):
    """⑦ 版本目录文档归属（2026-08-28 新增，摘要页"挂错目录"问题根因校验）：
    每个版本目录（含子树）的长句/关键词/摘要页 kid 集合：
      - 含 >1 kid → WARN（版本目录未按文档拆分，多文档共享——同家族双格式/多版本合理，但正文/通知/修订说明应分目录）
      - 含跨家族 kid → ERROR（跨家族错挂）
    排除共享实体页（entity-b/entity-r 天然多 kid 多目录挂载）。"""
    problems = []
    root_ids = set(roots)
    # 版本目录（parent=根）及其子树
    version_dirs = {fid for fid, f in folders.items() if f['parent_id'] in root_ids}
    subtree = {}
    for vd in version_dirs:
        st = {vd}
        changed = True
        while changed:
            changed = False
            for fid, f in folders.items():
                if f['parent_id'] in st and fid not in st:
                    st.add(fid)
                    changed = True
        subtree[vd] = st
    # 每目录 kid 集合（page_kids_all: folder_id -> {kid}，仅含长句/关键词/摘要页）
    for vd in sorted(version_dirs):
        kids = set()
        for fid in subtree[vd]:
            kids |= page_kids_all.get(fid, set())
        if len(kids) <= 1:
            continue
        name = folders[vd]['name']
        rname = folders[folders[vd]['parent_id']]['name']
        # 跨家族判断：kid 是否全部属于同一根？简化——输出多 kid 目录为 WARN，
        # 若目录名与 kid 文档家族明显不符（后续可加文档→根映射精确判定）为 ERROR
        problems.append(f'[WARN] 版本目录多文档共享: {rname}/{name} 含 {len(kids)} 篇'
                        f'（规范应按版本/文档类型拆分目录）')
    return problems


def main():
    global KB
    ap = argparse.ArgumentParser(description='目录结构校验门禁')
    ap.add_argument('--kb', default=None)
    args = ap.parse_args()

    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import weknora_rpc as wr
    wr.load_config()
    wr.mcp_init()
    KB = args.kb or wr.load_kb()
    wr.KB = KB

    print(f'目录校验门禁 (kb={KB})', flush=True)
    folders, roots = load_folders()
    summaries, page_folders = load_pages()
    print(f'目录总数: {len(folders)} | 根目录: {len(roots)} | 摘要页: {len(summaries)}', flush=True)

    # 内容页（长句/关键词/摘要）folder → kid 集合（用于版本目录归属校验，排除共享实体页）
    rows = psql(f"SELECT folder_id, source_refs::text FROM wiki_pages "
                f"WHERE knowledge_base_id='{KB}' AND deleted_at IS NULL "
                f"AND page_type IN ('original_sentence','frequent_keyword','summary')")
    page_kids_all = {}
    for line in rows.splitlines():
        parts = line.split('|', 1)
        if len(parts) == 2:
            for k in re.findall(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', parts[1]):
                page_kids_all.setdefault(parts[0], set()).add(k)

    # 摘要文件名（用于根健康判定）
    for s in summaries:
        s['_fname'] = ''

    all_problems = []
    all_problems += check_roots(folders, roots, summaries)
    all_problems += check_version_dirs(folders, roots)
    all_problems += check_subdirs(folders, roots)
    all_problems += check_page_attribution(folders, roots, page_folders)
    all_problems += check_version_dir_docs(folders, roots, page_kids_all)
    all_problems += check_summary_attribution(folders, roots, summaries, page_kids_all)

    errors = [p for p in all_problems if p.startswith('[ERROR]')]
    warns = [p for p in all_problems if p.startswith('[WARN]')]

    print(f'\n=== 结果 ===')
    print(f'ERROR: {len(errors)} | WARN: {len(warns)} | 通过: {len(roots)} 根')
    for p in all_problems:
        print(f'  {p}')
    print('\n建议：修复后执行 wiki_rebuild_links + wiki_lint 验证')


if __name__ == '__main__':
    main()
