#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构化知识程序化建页（不调 LLM）：指标库 / 表映射库 / 取数样例库。

数据源：全量指标20260814.xlsx、temp_qs_hxj_ysgxb_20251209_r1.xlsx、
       历史工单知识库*.xlsx、需生成变量sql的重复历史工单*.xlsx
规模：约 1.7 万页 → 走 WeKnora PG 批量 COPY（MCP 单页建会跑到天亮）。

用法：
  python3 build_structured_pages.py --kb <kb_id> --src <资产目录> --kind metrics|tables|samples|all
                                    [--limit N] [--dry-run] [--batch-dir /tmp/struct_pages]
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain
import xlsx_lite

KB = None
TENANT = 10000
TABLE_RE = re.compile(r'(?i)\b(?:from|join)\s+([a-z_][a-z0-9_]{2,})')


def esc(s):
    return (str(s or '').replace('\\', '\\\\').replace('\t', ' ').replace('\r', ' ')
            .replace('\n', '\\n'))


def table_slug(name):
    return 'struct/table/' + hashlib.md5(name.strip().lower().encode()).hexdigest()[:10]


def metric_slug(code, name):
    return 'struct/metric/' + hashlib.md5((str(code) + '|' + str(name)).encode()).hexdigest()[:10]


def sample_slug(kind, no, name):
    return 'struct/sample/' + hashlib.md5((kind + '|' + str(no) + '|' + str(name)).encode()).hexdigest()[:10]


def basic_table(rows):
    out = ['| 字段 | 内容 |', '|---|---|']
    out += [f'| {k} | {esc(v)[:300]} |' for k, v in rows if str(v).strip()]
    return '\n'.join(out)


def page_skeleton(title, cat, meta_rows, extra_sections=''):
    return (f'# {title}\n\n**业务对象**　{cat}\n\n## 基本信息\n\n{basic_table(meta_rows)}\n\n'
            f'## 关联规则\n\n| 规则 | 类别 | 内容 |\n|---|---|---|\n'
            f'## 关联对象\n\n| 关联对象 | 关系类型 | 关系说明 | 来源 |\n|---|---|---|---|\n'
            f'{extra_sections}'
            f'## 原文关联\n\n**《{esc(title)}》**（结构化知识：程序化生成，非长句）\n')


def known_table_slugs(src):
    """表属性与周期映射里登记过的表 → slug 集合（样例/指标页只链这些表）。"""
    path = domain.asset_path('table_map', src)
    if not os.path.exists(path):
        return set()
    rows = xlsx_lite.read_sheets(path)[0]['rows']
    out = set()
    for r in rows[1:]:
        if r and str(r[0]).strip():
            out.add(table_slug(str(r[0]).strip()))
    return out


# ---------------- 表映射库 ----------------
def build_tables(src, kb, catids, limit=0, dry=False):
    path = domain.asset_path('table_map', src)
    sheets = xlsx_lite.read_sheets(path)
    rows = sheets[0]['rows']
    head = rows[0]
    idx = {h: i for i, h in enumerate(head)}
    pages = []
    for r in rows[1:]:
        if not r or not (r[0] or '').strip():
            continue
        name = r[idx.get('table_name', 0)].strip()
        if not name:
            continue
        meta = [('表名', name), ('短名/别名', r[idx.get('table_short', 1)] if len(r) > 1 else ''),
                ('owner', r[idx.get('owner', 2)] if len(r) > 2 else ''),
                ('周期', r[idx.get('cyc_flag', 3)] if len(r) > 3 else ''),
                ('备份周期', r[idx.get('backup_cyc', 4)] if len(r) > 4 else ''),
                ('保存周期', r[idx.get('save_cyc', 5)] if len(r) > 5 else ''),
                ('建表周期', r[idx.get('create_Cyc', 6)] if len(r) > 6 else ''),
                ('是否忽略', r[idx.get('is_ignore', 7)] if len(r) > 7 else ''),
                ('更新时间', r[idx.get('update_date', 8)] if len(r) > 8 else ''),
                ('视图名', r[idx.get('vc_name', 9)] if len(r) > 9 else ''),
                ('同步标识', r[idx.get('sync_flag', 10)] if len(r) > 10 else ''),
                ('同步过程', r[idx.get('sync_proc', 13)] if len(r) > 13 else '')]
        content = page_skeleton(name, '1-数据表/接口', meta,
                                '## 数据来源\n\n《表属性与周期映射》（temp_qs_hxj_ysgxb）\n\n')
        pages.append({'slug': table_slug(name), 'title': name, 'content': content,
                      'folder': catids['table'], 'ptype': 'business_ontology'})
        if limit and len(pages) >= limit:
            break
    print(f'表映射库: {len(pages)} 页')
    return pages


# ---------------- 指标库 ----------------
def build_metrics(src, kb, catids, limit=0, dry=False, known=None):
    path = domain.asset_path('metric', src)
    rows = xlsx_lite.read_sheets(path)[0]['rows']
    head = rows[0]
    i = {h: n for n, h in enumerate(head)}
    pages = []
    for r in rows[1:]:
        name = (r[i.get('指标名称', 1)] if len(r) > 1 else '').strip()
        if not name:
            continue
        code = (r[i.get('指标编码', 0)] if r else '').strip()
        sql = (r[i.get('指标SQL', 2)] if len(r) > 2 else '').strip()
        meta = [('指标编码', code), ('指标说明', r[i.get('指标说明', 3)] if len(r) > 3 else ''),
                ('归属报表', r[i.get('归属应用报表名', 4)] if len(r) > 4 else ''),
                ('底层数据字段', r[i.get('底层数据字段名', 5)] if len(r) > 5 else ''),
                ('单位', r[i.get('单位', 6)] if len(r) > 6 else ''),
                ('用途', r[i.get('指标用途', 7)] if len(r) > 7 else ''),
                ('粒度', r[i.get('指标粒度', 8)] if len(r) > 8 else ''),
                ('需求部门', r[i.get('需求部门', 9)] if len(r) > 9 else ''),
                ('业务口径', r[i.get('业务口径', 11)] if len(r) > 11 else ''),
                ('所属分析主题', r[i.get('所属分析主题', 13)] if len(r) > 13 else '')]
        sec = f'## 指标 SQL\n\n```sql\n{sql[:1500]}\n```\n\n' if sql else ''
        # 从 SQL 抽表名 → 关联表映射库
        tables = sorted({t.lower() for t in TABLE_RE.findall(sql)})[:8]
        if known is not None:
            tables = [t for t in tables if table_slug(t) in known]
        if tables:
            sec += '## 关联对象\n\n| 关联对象 | 关系类型 | 关系说明 | 来源 |\n|---|---|---|---|\n'
            sec += '\n'.join(f'| [[{table_slug(t)}|{t}]] | 使用表 | 指标 SQL 引用 | 《全量指标字典》 |'
                             for t in tables) + '\n\n'
        content = page_skeleton(name, '2-指标对象', meta, sec)
        pages.append({'slug': metric_slug(code, name), 'title': name, 'content': content,
                      'folder': catids['metric'], 'ptype': 'business_ontology'})
        if limit and len(pages) >= limit:
            break
    print(f'指标库: {len(pages)} 页')
    return pages


# ---------------- 取数样例库 ----------------
def _sample_pages_from_rows(rows, kind, source_label, catids, limit=0, known=None):
    head = rows[0]
    i = {h: n for n, h in enumerate(head)}
    pages = []
    for r in rows[1:]:
        if not r or not any(str(x).strip() for x in r):
            continue

        def g(col, default=''):
            n = i.get(col)
            return (r[n] if (n is not None and len(r) > n) else default) or ''

        desc = g('需求描述') or g('任务描述') or g('需求名称')
        name = g('需求名称') or g('任务名称') or (desc[:20] if desc else '取数样例')
        no = g('需求工单号') or g('任务编号')
        if not (desc or g('原始sql') or g('修改后sql')):
            continue
        meta = [('需求工单号', no), ('需求名称', name), ('业务分类', g('业务分类')),
                ('任务编号', g('任务编号')), ('输出字段要求', g('输出字段要求')[:200] if g('输出字段要求') else ''),
                ('取数日期', g('取数日期')), ('是否可用', g('是否可用')),
                ('是否有外部输入', g('是否有外部输入')), ('来源', source_label)]
        sec = ''
        if desc:
            sec += f'## 需求描述\n\n{desc[:1200]}\n\n'
        orig, fixed, var = g('原始sql'), g('修改后sql'), g('var_sql')
        if orig:
            sec += f'## 原始 SQL（可直接作为 few-shot 参考）\n\n```sql\n{orig[:2000]}\n```\n\n'
        if fixed and fixed != orig:
            sec += f'## 修正后 SQL\n\n```sql\n{fixed[:2000]}\n```\n\n'
        if var:
            sec += f'## 变量化 SQL（模板）\n\n```sql\n{var[:2000]}\n```\n\n'
        tables = sorted({t.lower() for t in TABLE_RE.findall(' '.join([orig, fixed, var]))})[:10]
        if known is not None:
            tables = [t for t in tables if table_slug(t) in known]
        if tables:
            sec += '## 关联对象\n\n| 关联对象 | 关系类型 | 关系说明 | 来源 |\n|---|---|---|---|\n'
            sec += '\n'.join(f'| [[{table_slug(t)}|{t}]] | 使用表 | SQL 引用 | 《{source_label}》 |'
                             for t in tables) + '\n\n'
        title = f'取数样例：{name[:26]}'
        content = page_skeleton(title, '3-取数需求/工单', meta, sec)
        pages.append({'slug': sample_slug(kind, no, name), 'title': title, 'content': content,
                      'folder': catids['sample'], 'ptype': 'business_ontology'})
        if limit and len(pages) >= limit:
            break
    return pages


def build_samples(src, kb, catids, limit=0, dry=False, known=None):
    pages = []
    for key, label in (('worksheet', '历史工单知识库（全量）'),
                       ('worksheet_sample', '历史工单知识库（样例）'),
                       ('var_worksheet', '重复工单变量 SQL 清单')):
        p = domain.asset_path(key, src)
        if not os.path.exists(p):
            continue
        for sh in xlsx_lite.read_sheets(p):
            if not sh['rows']:
                continue
            pages += _sample_pages_from_rows(sh['rows'], key, f'{label} / {sh["name"]}', catids,
                                             limit=max(0, limit - len(pages)) if limit else 0, known=known)
    print(f'取数样例库: {len(pages)} 页')
    return pages


# ---------------- 批量写库（COPY） ----------------
def run_copy(kb, pages, batch_dir, dry=False):
    from datetime import datetime, timezone
    NOW = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S+00')
    os.makedirs(batch_dir, exist_ok=True)
    pages_tsv = os.path.join(batch_dir, 'pages.tsv')
    links_tsv = os.path.join(batch_dir, 'links.tsv')
    seen = set()
    with open(pages_tsv, 'w', encoding='utf-8', newline='') as fp, \
            open(links_tsv, 'w', encoding='utf-8', newline='') as fl:
        for p in pages:
            if p['slug'] in seen:
                continue
            seen.add(p['slug'])
            pid = str(uuid.uuid4())
            fp.write('\t'.join([pid, str(TENANT), kb, esc(p['slug']), esc(p['title']), p['ptype'],
                                'published', esc(p['content']), '', '', p['folder'], '[]', p['slug'],
                                '0', '0', '[]', '[]', '[]', '[]', '{}', '[]', '1', NOW, NOW]) + '\n')
            fl.write('\t'.join([pid, p['folder'], kb, str(TENANT), NOW, NOW]) + '\n')
    print(f'  TSV: {len(seen)} 页 → {pages_tsv}')
    if dry:
        return
    sql = """
\\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE _np (id varchar, tenant_id bigint, knowledge_base_id varchar, slug varchar, title varchar,
 page_type varchar, status varchar, content text, summary text, parent_slug varchar, folder_id varchar,
 category_path jsonb, wiki_path varchar, depth int, sort_order int, source_refs jsonb, chunk_refs jsonb,
 in_links jsonb, out_links jsonb, page_metadata jsonb, aliases jsonb, version int,
 created_at timestamptz, updated_at timestamptz) ON COMMIT DROP;
CREATE TEMP TABLE _nl (page_id varchar, folder_id varchar, created_at timestamptz, updated_at timestamptz) ON COMMIT DROP;
COPY _np FROM STDIN;
"""
    # 用 psql 分两步：先 COPY 两个 TSV（各自一次 stdin），再 INSERT/UPDATE
    steps = [
        (f"\\copy _np FROM '{pages_tsv}' WITH (FORMAT text)", None),
        (f"\\copy _nl FROM '{links_tsv}' WITH (FORMAT text)", None),
    ]
    # _np/_nl 是 TEMP 表，必须同一会话：合成一个脚本 + 用文件路径 copy（容器内需可见路径）
    full = """\\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE _np (id varchar, tenant_id bigint, knowledge_base_id varchar, slug varchar,
 title varchar, page_type varchar, status varchar, content text, summary text, parent_slug varchar,
 folder_id varchar, category_path jsonb, wiki_path varchar, depth int, sort_order int,
 source_refs jsonb, chunk_refs jsonb, in_links jsonb, out_links jsonb, page_metadata jsonb,
 aliases jsonb, version int, created_at timestamptz, updated_at timestamptz) ON COMMIT DROP;
CREATE TEMP TABLE _nl (page_id varchar, folder_id varchar, knowledge_base_id varchar, tenant_id bigint, created_at timestamptz, updated_at timestamptz) ON COMMIT DROP;
""" + '\n'.join(s[0] for s in steps) + """
UPDATE wiki_pages p SET deleted_at = now()
 WHERE p.knowledge_base_id = :'kb' AND p.deleted_at IS NULL
   AND p.slug IN (SELECT slug FROM _np);
DELETE FROM wiki_page_folders f USING wiki_pages p
 WHERE f.page_id = p.id AND p.deleted_at IS NOT NULL AND p.slug IN (SELECT slug FROM _np);
INSERT INTO wiki_pages (id,tenant_id,knowledge_base_id,slug,title,page_type,status,content,summary,parent_slug,folder_id,category_path,wiki_path,depth,sort_order,source_refs,chunk_refs,in_links,out_links,page_metadata,aliases,version,created_at,updated_at) SELECT * FROM _np;
INSERT INTO wiki_page_folders SELECT * FROM _nl;
COMMIT;
SELECT count(*) AS inserted FROM wiki_pages WHERE knowledge_base_id = :'kb' AND deleted_at IS NULL;
"""
    sqlfile = os.path.join(batch_dir, 'load.sql')
    open(sqlfile, 'w', encoding='utf-8').write(full)
    # TSV 需要容器内可见 → 拷进容器
    subprocess.run(['docker', 'cp', pages_tsv, 'WeKnora-postgres:/tmp/_np.tsv'], check=True)
    subprocess.run(['docker', 'cp', links_tsv, 'WeKnora-postgres:/tmp/_nl.tsv'], check=True)
    full = full.replace(pages_tsv, '/tmp/_np.tsv').replace(links_tsv, '/tmp/_nl.tsv')
    open(sqlfile, 'w', encoding='utf-8').write(full)
    subprocess.run(['docker', 'cp', sqlfile, 'WeKnora-postgres:/tmp/_load.sql'], check=True)
    r = subprocess.run(['docker', 'exec', 'WeKnora-postgres', 'psql', '-U', 'weknora', '-d', 'weknora',
                        '-v', f'kb={kb}', '-f', '/tmp/_load.sql'],
                       capture_output=True, text=True)
    print(r.stdout[-800:])
    if r.returncode != 0:
        print('STDERR', r.stderr[-500:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--src', required=True)
    ap.add_argument('--kind', default='all', choices=['metrics', 'tables', 'samples', 'all'])
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--batch-dir', default='/tmp/struct_pages')
    ns = ap.parse_args()
    kb = ns.kb or wr_kb()
    catids = json.load(open(f'/tmp/catids_{kb[:8]}.json', encoding='utf-8'))
    pages = []
    known = known_table_slugs(ns.src)
    print(f'已登记表 slug: {len(known)}')
    if ns.kind in ('tables', 'all'):
        pages += build_tables(ns.src, kb, catids, ns.limit, ns.dry_run)
    if ns.kind in ('metrics', 'all'):
        pages += build_metrics(ns.src, kb, catids, ns.limit, ns.dry_run, known)
    if ns.kind in ('samples', 'all'):
        pages += build_samples(ns.src, kb, catids, ns.limit, ns.dry_run, known)
    if not pages:
        print('无页面生成'); return
    run_copy(kb, pages, ns.batch_dir, ns.dry_run)


def wr_kb():
    import weknora_rpc as w
    w.load_config()
    return w.load_kb()


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
