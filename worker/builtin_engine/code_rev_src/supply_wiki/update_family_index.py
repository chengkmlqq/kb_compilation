#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根目录索引页维护（2026-08-28 新增）。

每篇文档构建完成（摘要生成后）调用：拉根目录下全部文档摘要 → 重建根索引页。
内容：文档清单表（文档双链/类型/一句话定位/族内关系）+ 关系概述。
幂等：每次重建覆盖，永远反映根目录下当前全部文档。

用法：
  python3 update_family_index.py --kb <kb_id> --family <家族根目录名>
  python3 update_family_index.py --kb <kb_id> --family-all     # 全库所有根目录
"""
import sys
import os
import re
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_wiki_rpc as wr


def walk_folders(kb, parent_id, acc):
    """递归拉目录树。"""
    r = wr.list_folders(kb_id=kb, parent_id=parent_id)
    for f in r.get('folders', []):
        acc[f['id']] = f['name']
        walk_folders(kb, f['id'], acc)
    return acc


def doc_type(fname):
    low = fname.lower()
    if low.endswith(('.xls', '.xlsx')):
        if '审批' in fname:
            return '审批记录'
        if '授权' in fname:
            return '授权清单'
        if '修订说明' in fname or '一览表' in fname:
            return '修订说明'
        return '表格文档'
    if '通知' in fname:
        return '通知'
    if '附件' in fname:
        return '附件'
    return '制度正文'


def extract_summary_info(content):
    """从摘要页 content 提取：一句话定位 + 关系表行。"""
    one_liner = ''
    rel_rows = []
    lines = content.split('\n')
    # 一句话定位：# 标题后第一个非空段落
    after_title = False
    for ln in lines:
        s = ln.strip()
        if s.startswith('# '):
            after_title = True
            continue
        if after_title and s and not s.startswith('#'):
            one_liner = s[:120]
            break
    # 关系表行
    in_rel = False
    for ln in lines:
        s = ln.strip()
        if s.startswith('## 与其他制度的关系'):
            in_rel = True
            continue
        if in_rel and s.startswith('## '):
            break
        if in_rel and s.startswith('|') and '|' in s[1:]:
            # 坑 25/55（2026-08-29 索引页实测）：不能直接 split('|')——wikilink [[slug|标题]] 内部竖线会把行拆碎，
            # 生成残缺链接（如 [[summary/88eab...46f：标题）。先保护 wikilink 再拆列。
            protected = ''
            row = s
            i = 0
            while i < len(row):
                if row.startswith('[[', i):
                    j = row.find(']]', i + 2)
                    if j > 0:
                        seg = row[i:j + 2]
                        protected += seg.replace('|', '\x01')
                        i = j + 2
                        continue
                protected += row[i]
                i += 1
            cells = [c.strip().replace('\x01', '|') for c in protected.strip('|').split('|')]
            if len(cells) >= 2 and '相关制度' not in cells[0] and '---' not in s:
                # 2026-08-29 不再截断（原 [:40]/[:60] 会把《…采购实施细则》（知识库中暂无对应文档）砍成
                # 《…采购施细则》，族内关键关系展示残缺）。完整保留供索引页展示。
                rel_rows.append((cells[0], cells[1] if len(cells) > 1 else ''))
    return one_liner, rel_rows


def build_index_content(family, docs_info):
    """docs_info: [{fname, slug, dtype, one_liner, rels}]"""
    lines = [f'# {family} 制度族索引', '',
             f'> 本页汇总「{family}」制度族下全部文档及其关系，随各文档构建自动维护更新。', '',
             '## 文档清单', '',
             '| 文档 | 类型 | 一句话定位 | 族内关系 |', '|---|---|---|---|']
    for d in docs_info:
        link = f"[[{d['slug']}|{d['fname']}]]"
        rel_txt = '；'.join(f"{r[0]}：{r[1]}" for r in d['rels'][:3]) or '-'
        lines.append(f"| {link} | {d['dtype']} | {d['one_liner'] or '-'} | {rel_txt} |")
    # 关系概述
    lines += ['', '## 关系概述']
    versions = [d for d in docs_info if d['dtype'] == '制度正文']
    notices = [d for d in docs_info if d['dtype'] in ('通知', '附件', '修订说明', '授权清单', '审批记录', '表格文档')]
    if versions:
        lines.append(f"- 制度正文 {len(versions)} 篇：{'、'.join(d['fname'] for d in versions)}")
    if notices:
        lines.append(f"- 配套文档 {len(notices)} 篇：{'、'.join(d['fname'] for d in notices)}")
    # 族内关系（版本链/配套）——2026-08-29 去掉 [:15]/[:15]/[:30] 截断（会劈开 wikilink 且丢失信息）
    supersedes = []
    for d in docs_info:
        for r in d['rels']:
            if any(k in r[0] for k in ('办法', '细则', '制度')) and any(k in r[1] for k in ('废止', '替代', '修订', '依据', '配套')):
                supersedes.append(f"- {d['fname']} {r[0]}：{r[1]}")
    if supersedes:
        lines.append('')
        lines.append('**族内关键关系**：')
        for s in supersedes[:8]:
            lines.append(s)
    return '\n'.join(lines)


# ---------- 索引页内容生成（2026-08-31 方案 B：族索引页直接用演进技能模板，见 weknora-version-evolution）----------

EVOLUTION_SYSTEM = """你是企业采购管理制度知识库的版本演进分析专家。基于给定制度家族各版本文档的摘要与长句原文，生成该家族的「制度族索引页」。

必须严格使用以下四段式模板（与 weknora-version-evolution 技能一致）：

> ⚠️ **版本状态说明**：本文以现行生效版为准。历史版本仅作沿革参考，现行业务一律以现行版本生效力为准。

## 一、版本演进链

| 版本 | 文号 | 状态 | 废止/替代关系 | 触发原因 | 原文依据 |
|---|---|---|---|---|---|
| **V1 版** | 文号 | **已被废止（被V2版替代）** | 原始版本，后被V2版替代 | 依据XX制定，规范XX | [[summary/<md5>|标题]] |
| **现行版** | 文号 | **生效（现行）** | 替代V1版，废止原文号 | 规范XX，承接最新制度 | [[summary/<md5>|标题]] |

## 二、逐版本内容迭代

> 按版本顺序，逐条写出**前一版到后一版具体新增、删除、改写的内容**。每条变化均标注原文依据。

### 2.1 V1版 内容要点
- **核心点1**：...（[[longsentence/<slug>|长句标题]]）

### 2.2 V1版 → V2版 具体迭代内容
| # | 迭代方向 | 具体变化 | 原文依据 |
|---|---|---|---|
| 1 | 新增/删除/改写 | 从V1版"旧表述"改为V2版"新表述" | V1版 [[longsentence/...|标题]]；V2版 [[summary/<md5>|标题]] |

（版本数 > 3 时按"版本要点 ↔ 相邻版本迭代"交替编排；无独立文档的版本写"该版在库内无独立文档，无法提取内容要点"）

## 三、版本演进洞察与实操提醒

> 只做趋势归纳和实操风险点提炼，每条从前文迭代条目归纳，不得引入正文未出现的新事实。

### 3.1 制度演进主线
| 维度 | 趋势判断 | 实操影响 |
|---|---|---|

### 3.2 跨版本高频变化模式（供实操参考）
| 变化模式 | 典型表现 | 实操注意点 |
|---|---|---|

### 3.3 实操风险提示
1. ...

## 四、知识推导过程

1. 检索 → 2. 溯源 → 3. 条文比对 → ...（简述如何从各版本摘要/长句得出上述结论）

硬性要求：
1. 所有内容必须基于提供的原文/摘要内容，禁止编造；无原文依据的点写「该版在库内无独立文档/无原文依据，无法提取本维度内容」，禁止写「（依据摘要页）」「（推断）」「（依据）」等占位
2. 差异对比必须具体到条款层面，每条写「前版怎么说 → 后版怎么说」
3. 原文依据列只写真实 slug 双链 [[summary/<md5>|标题]] 或 [[longsentence/<slug>|标题]]；**版本演进链表每一行的原文依据列、迭代表每一行的原文依据列都必须含 [[...]] 双链**（禁止留空或纯文本描述；无对应原文的版本写「该版在库内无独立文档」）
4. 版本状态硬要求：明确标注 生效/已被废止（被X版替代）；单版本家族版本链只有一行、迭代章节写"本族仅一个版本，无版本演进"
5. **版本演进链只列制度正文的版本**：通知（印发/修订/废止通知）、附件、授权清单、修订说明等**配套文档不得作为独立版本行**——它们是制度的附属品（印发/废止载体），只能在对应版本行的「废止/替代关系」或「触发原因」列中以纯文本或链接方式说明（如"由《关于修订...的通知》印发/废止"）；配套文档的信息放到迭代表或洞察章节引用
6. 输出 markdown，以「# <家族名> 制度族索引」开头，第二行是 ⚠️ 版本状态说明"""


def gen_index_content(kb, family, docs_info):
    """整个族索引页内容（LLM，演进技能四段式模板）。返回 markdown；失败返回空（调用方回退程序骨架）。"""
    # 组装输入：各摘要页全文
    summaries = []
    for d in sorted(docs_info, key=lambda x: x['fname']):
        p = wr.read_page(d['slug'], kb_id=kb)
        content = p.get('content', '') if 'error' not in p else ''
        if content:
            if len(content) > 3000:
                content = content[:3000] + '\n…（截断）'
            summaries.append(f"### 文档：{d['fname']}\n{content}")
    if not summaries:
        return ''
    prompt = f"制度家族「{family}」共 {len(docs_info)} 篇文档。各文档摘要如下：\n\n" + '\n\n'.join(summaries)
    try:
        out = wr.llm_call([{'role': 'system', 'content': EVOLUTION_SYSTEM},
                           {'role': 'user', 'content': prompt}],
                          max_tokens=20000)
        out = (out or '').strip()
        return wr.clean_llm_tail(out)  # 2026-08-31：LLM 长输出末尾偶发「😊 有什么我可以帮你的吗？」聊天尾巴，落库前必须清理
    except Exception as e:
        print(f'  [索引] LLM 失败: {e}')
        return ''


def fix_fake_slugs(kb, content, docs_info, family):
    """校验 LLM 输出中的 wikilink slug 是否真实存在（2026-08-31，坑 39 扩展：LLM 会编造
    summary/longsentence slug，如把 summary/<md5> 写成拼音化 slug）。
    策略：
    1. 拉全库真实 slug 集合 + title→slug 索引（摘要/长句页）
    2. 对每个 [[tgt|显示名]]：tgt 存在 → 保留；不存在 → 按显示名反查真实 slug 替换；查不到 → 降级纯文本显示名
    """
    # 全库真实 slug + 显示名反查索引
    real = set()
    title_idx = {}
    page = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 200})
        ps = r.get('pages') or []
        for p in ps:
            if p.get('deleted_at'):
                continue
            s = p.get('slug', '')
            real.add(s)
            t = p.get('title') or ''
            if t and s.startswith(('summary/', 'longsentence/')):
                title_idx.setdefault(t.strip(), []).append(s)
        if len(ps) < 200:
            break
        page += 1
        if page > 100:
            break

    def norm_title(t):
        # 去文件扩展名/版本标识，用于宽松反查
        t = re.sub(r'\.(docx?|pdf|xlsx?)$', '', t)
        t = re.sub(r'\s+[A-Z]?\d+[.\d]*版?$', '', t)
        return t.strip()

    def resolve(tgt, disp):
        if tgt in real:
            return f"[[{tgt}|{disp}]]"
        # 按显示名反查
        cands = title_idx.get(disp.strip(), [])
        if not cands:
            nt = norm_title(disp)
            for t, sl in title_idx.items():
                if norm_title(t) == nt:
                    cands = sl
                    break
        if cands:
            # 摘要页优先（版本状态权威来源），其次长句
            sm = [s for s in cands if s.startswith('summary/')]
            if sm:
                return f"[[{sm[0]}|{disp}]]"
            return f"[[{cands[0]}|{disp}]]"
        return disp  # 降级纯文本

    def fix_link(m):
        tgt, _, disp = m.group(1).partition('|')
        return resolve(tgt.strip(), (disp or tgt).strip())

    return re.sub(r'\[\[([^\]]+)\]\]', fix_link, content)


def update_index(kb, family):
    """为指定根目录重建索引页（LLM 演进模板，失败回退程序骨架）。返回 (slug, doc_count) 或 None。"""
    # 1. 找根目录
    roots = wr.list_folders(kb_id=kb, parent_id='')
    root_id = None
    for f in roots.get('folders', []):
        if f['name'] == family:
            root_id = f['id']
            break
    if not root_id:
        print(f'  [SKIP] 根目录不存在: {family}')
        return None
    # 2. 递归拉根下全部目录
    folders = walk_folders(kb, root_id, {})
    fids = set(folders.keys()) | {root_id}
    # 3. 拉全库 summary 页，筛 folder 在根下
    pages, page = [], 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 200})
        ps = r.get('pages') or []
        for p in ps:
            if p.get('page_type') == 'summary' and not p.get('deleted_at'):
                pf = set(p.get('folder_ids') or [])
                if pf & fids:
                    pages.append(p)
        if len(ps) < 200:
            break
        page += 1
        if page > 200:
            break
    if not pages:
        print(f'  [SKIP] {family}: 根下无摘要页')
        return None
    # 4. 提取信息
    docs_info = []
    for p in pages:
        content = p.get('content', '') or ''
        fname = ''
        for sr in (p.get('source_refs') or []):
            if '|' in sr:
                fname = sr.split('|', 1)[1]
                break
        if not fname:
            fname = p.get('title', '?')
        one_liner, rels = extract_summary_info(content)
        docs_info.append({'fname': fname, 'slug': p['slug'], 'dtype': doc_type(fname),
                          'one_liner': one_liner, 'rels': rels})
    docs_info.sort(key=lambda d: (d['dtype'] != '制度正文', d['fname']))
    # 5. 建/更新索引页（2026-08-31 方案 B：整体用演进技能模板 LLM 生成，失败回退程序骨架）
    content = gen_index_content(kb, family, docs_info)
    if content:
        content = fix_fake_slugs(kb, content, docs_info, family)
        print(f'  [索引] LLM 演进模板生成+slug校验（{len(docs_info)} 篇文档）')
    else:
        content = build_index_content(family, docs_info)
        print(f'  [索引] LLM 失败，回退程序骨架（{len(docs_info)} 篇文档）')
    slug = f'index/{family}'
    r = wr.read_page(slug, kb_id=kb)
    if 'error' in r:
        wr.create_page(**{'kb_id': kb, 'slug': slug, 'title': f'{family} 制度族索引',
                          'content': content, 'folder_id': root_id, 'page_type': 'concept',
                          'source_refs': []})
        print(f'  [索引] 创建: {slug}')
    else:
        wr.update_page(**{'kb_id': kb, 'slug': slug, 'content': content, 'folder_id': root_id,
                          'page_type': 'concept', 'source_refs': []})
        print(f'  [索引] 更新: {slug}')
    return slug, len(docs_info)


def main():
    ap = argparse.ArgumentParser(description='根目录索引页维护')
    ap.add_argument('--kb', default=None)
    ap.add_argument('--family', default=None, help='根目录名')
    ap.add_argument('--family-all', action='store_true', help='全库所有根目录')
    args = ap.parse_args()

    wr.load_config()
    wr.mcp_init()
    kb = args.kb or wr.load_kb()
    wr.KB = kb

    if args.family_all:
        roots = wr.list_folders(kb_id=kb, parent_id='')
        fams = [f['name'] for f in roots.get('folders', [])]
        print(f'全库根目录 {len(fams)} 个')
        for fam in fams:
            update_index(kb, fam)
    elif args.family:
        update_index(kb, args.family)
    else:
        print('需 --family 或 --family-all')


if __name__ == '__main__':
    main()
