#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Neo4j 知识图谱导出/写入模块 —— 市场监管法规 Wiki → Neo4j 语义图
（2026-09-08 自供管版 graph_export.py 域化移植，逻辑同构）。

设计（2026-09-07 供管版用户确认，法规域同款图模型）：
- 业务本体（entity-b 页）= 节点；规则本体（entity-r 页）= 关系（边）
- 每文件独立 kg（= 该文档 kid）：构建时先删该 kid 子图再写入，重建同一文档干净无残留；
  跨文档同名实体在 Neo4j 存多份、查询/展示按 name 去重
- 数据源 = 已建 wiki 页面（entity-b/entity-r 的 content），权威、无需回源中间产物
- 规则卡（entity-r 页）三元组（页面"约束对象"=主语、"关联对象"=宾语带角色）：
    subject（约束对象）→ 规则动词 → 宾语（objects 中直接受规则作用的客体/工具/载体等）
    规则动作词/条件/引用制度等按角色建模进边类型或宾语节点（完整多边模型）
- 每条规则卡 = 1..n 条"主语 -[动词]-> 宾语"出边；主语=约束对象 canonical。
- 写库：graph_add（MCP /wiki/graph/write → Neo4j apoc.merge）
    label = ENTITY<kb_id>:ENTITY<kg>（后端自动）；节点按 (name, kg) 合并；
    关系 apoc.merge.relationship 幂等；kg = 该文档 kid（每文件独立子图）。

法规域差异（对照供管版）：
  - 业务 9 类：1-法律法规/2-监管对象/3-许可事项/4-违法行为/5-行政处罚/
    6-监管职责/7-标准规范/8-程序事项/9-时限要求
  - 规则 12 类：…/9-处罚规则/10-时限规则/11-版本规则/12-程序规则
    （供管 9-决策/10-时间/11-金钱 → 处罚/时限/程序）
  - 来源文档节点 attribute = '法规文档'（法规库无版本目录概念，一篇法规=一个文档节点；
    供管版为 '制度版本'）

规则页 ↔ 边的角色映射（对齐 0830 实测的角色分布）：
  宾语 role=动作/客体/结果         → 主语 -[类别动词]-> 宾语     （主要出边）
  宾语 role=工具/载体/基础/标准     → 主语 -[类别动词]-> 宾语     （如 审批/执行/管理）
  宾语 role=条件/前置条件/前置依据   → 主语 -[触发条件]-> 宾语     （条件角色）
  宾语 role=引用                   → 主语 -[引用]-> 宾语         （引用其他制度）
  宾语 role=主体                   → 宾语 -[类别动词]-> 主语     （主体反转为动作发出者）
  宾语无角色                        → 主语 -[类别动词]-> 宾语     （类别兜底）
关系 type = 规则类别动词（约束/执行/审批/处罚/时限/…），不从句中挖动作动词
（对象名是名词短语，挖动词产出语义错误边——坑 115）。

CLI:
  python3 graph_export.py export --kb <kb_id> [--dry-run]      # 全库逐文档
  python3 graph_export.py doc --kid <kid> --kb <kb_id> [--dry-run]   # 单文档(先删后写)
  python3 graph_export.py delete --kid <kid> --kb <kb_id>      # 删该 kid 子图
build_full.py Step 9.5 调用: export_doc(kid, kb, source_file)
"""
import os
import re
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
from slugger import canonicalize

# =====================================================================
# 常量
# =====================================================================

# 业务/规则类别目录名（市场法规域 9+12，与 extract_entities_single / build_full 一致）
BIZ_CATS = ["1-法律法规", "2-监管对象", "3-许可事项", "4-违法行为", "5-行政处罚",
            "6-监管职责", "7-标准规范", "8-程序事项", "9-时限要求"]
# 页面「所属本体 | 业务本体 > 许可事项 > …」写的是无序号类别名（目录才带序号前缀）
BIZ_CAT_NAMES = {c.split('-', 1)[1] for c in BIZ_CATS}
RULE_CATS = ["1-定义规则", "2-条件规则", "3-约束规则", "4-职责规则", "5-审批规则",
             "6-流程规则", "7-引用规则", "8-例外规则", "9-处罚规则", "10-时限规则",
             "11-版本规则", "12-程序规则"]

# 关联对象可带角色标注的合法值（0830 实测分布；解析时只认这些为角色，防把
# "预算/预估（含税）金额"这类名称中的括号误剥）
OBJ_ROLES = {'主体', '客体', '条件', '动作', '引用', '工具', '基础', '标准', '载体',
             '方法', '前置条件', '前置依据', '方式', '属性', '依据', '对象',
             '禁止行为', '结果', '无'}

# 规则类别 → 中性关系动词（法规域 12 类；"约束/定义/处罚/时限"等是图边语义）
_CAT_VERB = {
    '定义规则': '定义', '条件规则': '触发条件', '约束规则': '约束',
    '职责规则': '管理', '审批规则': '审批', '流程规则': '执行',
    '引用规则': '引用', '例外规则': '豁免', '处罚规则': '处罚',
    '时限规则': '时限', '版本规则': '版本', '程序规则': '程序',
}


def _apply_schema_dynamic():
    """DB 本体 schema 驱动分类/边动词（2026-10-06 多领域扩展）；失败 fallback 硬编码。"""
    global BIZ_CATS, RULE_CATS, BIZ_CAT_NAMES, _CAT_VERB
    try:
        from weknora_rpc import load_ontology_schema
        ont = load_ontology_schema()
        if not ont or not ont.get('business') or not ont.get('rule'):
            print('[schema] graph_export: DB schema 不可用，fallback 硬编码', file=sys.stderr)
            return
        biz, rule = ont['business'], ont['rule']
        BIZ_CATS = [f"{r['cat_no']}-{r['cat_name']}" for r in biz]
        RULE_CATS = [f"{r['cat_no']}-{r['cat_name']}" for r in rule]
        BIZ_CAT_NAMES = {r['cat_name'] for r in biz}
        _CAT_VERB = {r['cat_name']: (r['neo4j_edge'] or '约束') for r in rule}
        print(f"[schema] graph_export: 已加载《{ont.get('schema_name')}》"
              f"业务{len(biz)}类/规则{len(rule)}类", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f'[schema] graph_export 动态化失败 fallback 硬编码: {e}', file=sys.stderr)

REL_TYPE_MAX = 30
DOC_NODE_MAX = 60


# =====================================================================
# 页面解析
# =====================================================================

def _strip_wikilink(text):
    if not text:
        return ''
    return re.sub(r'\[\[[^\]|]+\|([^\]]*)\]\]', r'\1', text)


def _split_objs_cell(cell):
    """entity-r「关联对象」单元格 → [(name, role), ...]。

    逐 token 扫描（wikilink+尾部角色标注 作为一个整体 token，或普通文本串），
    按顿号/逗号/分号切开。只把合法 OBJ_ROLES 当角色，其余尾巴保留在名称里
    （如 '预算/预估（含税）金额' 的括号不是角色）。
    """
    out = []
    if not cell or cell.strip() in ('-', ''):
        return out
    # token：[[...]]（可尾随（角色）） 或 非分隔符串
    tok = re.compile(r'\[\[[^\]\]]+\]\](?:（[^）]*）)?|[^、，,;；]+')
    for t in tok.findall(cell):
        t = t.strip()
        if not t:
            continue
        role = ''
        m = re.search(r'（([^）]*)）\s*$', t)
        if m and m.group(1).strip() in OBJ_ROLES:
            role = m.group(1).strip()
            t = t[:m.start()].strip()
        mm = re.match(r'\[\[([^\]|]+)\|([^\]]+)\]\]', t)
        name = (mm.group(2) if mm else t).strip()
        name = name.strip('《》')
        if name and name != '-':
            out.append((name, role))
    return out


def _split_source_line(content):
    """基本信息表「来源制度」行 → 文档名列表（去书名号）。"""
    m = re.search(r'\|\s*来源制度\s*\|\s*(.*?)\s*\|', content, re.S)
    if not m:
        return []
    raw = _strip_wikilink(m.group(1))
    parts = [p.strip() for p in re.split(r'[；;]|\n', raw) if p.strip()]
    return [p.strip('《》') for p in parts]


def parse_rule_page(page):
    """entity-r 规则卡 → dict（含 subject=约束对象、objects=[(名,角色)]）。"""
    content = page.get('content') or ''
    title = (page.get('title') or '').strip()
    cat = ''
    m = re.search(r'\|\s*规则类别\s*\|\s*(.*?)\s*\|\s*\n', content, re.S)
    if m:
        cat = m.group(1).strip()
    subj, subj_linked = '', False
    # 约束对象单元格可含 [[slug|名]]（内部竖线）——用行锚定跨过 wikilink 再剥壳
    m = re.search(r'\|\s*约束对象\s*\|\s*(.*?)\s*\|\s*\n', content, re.S)
    if m:
        raw = m.group(1).strip()
        if '[[' in raw:
            subj_linked = True
        subj = _strip_wikilink(raw).strip()
    objs = []
    m = re.search(r'\|\s*关联对象\s*\|\s*(.*?)\s*\|\s*\n', content, re.S)
    if m:
        objs = _split_objs_cell(m.group(1))
    sources = _split_source_line(content)
    # 一句话描述：标题下 '**类别**' 后首段
    desc = ''
    m = re.search(r'\*\*' + re.escape(cat) + r'\*\*\s*(.+?)\s*\n', content, re.S)
    if m:
        desc = m.group(1).strip()
    if not desc:
        for line in content.split('\n')[1:]:
            line = line.strip()
            if line and not line.startswith(('#', '|', '**', '##')):
                desc = line
                break
    return {'slug': page.get('slug'), 'title': title, 'category': cat,
            'subject': subj, 'subj_linked': subj_linked, 'objects': objs,
            'sources': sources, 'desc': desc}


def parse_biz_page(page):
    """entity-b 业务实体页 → dict（category 取所属本体中间段）。"""
    content = page.get('content') or ''
    title = (page.get('title') or '').strip()
    category = ''
    m = re.search(r'\|\s*所属本体\s*\|\s*业务本体\s*>\s*([^>|]+?)\s*>', content)
    if m:
        category = m.group(1).strip()
    desc = ''
    m = re.search(r'^#.*?\n\n(.*?)\n\n## ', content, re.S)
    if m:
        desc = m.group(1).strip()
    return {'slug': page.get('slug'), 'title': title,
            'canonical': canonicalize(title), 'category': category,
            'desc': desc, 'sources': _split_source_line(content)}


# =====================================================================
# 全库拉取
# =====================================================================

def fetch_pages(kb, page_type):
    out, page = [], 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 200})
        ps = r.get('pages') or []
        out.extend(p for p in ps if not p.get('deleted_at') and p.get('page_type') == page_type)
        if len(ps) < 200:
            break
        page += 1
    return out


# =====================================================================
# 规则 → 边
# =====================================================================

def _cat_verb(cat):
    return _CAT_VERB.get(cat, '约束')


def _edge_for(rule, obj_name, role):
    """决定规则 rule 对宾语 (obj_name, role) 的边类型。

    设计（2026-09-07 实测校准）：边类型 = 规则类别动词（约束/执行/审批/引用/豁免/
    触发条件/处罚/时限/…），不从句中挖动作动词——规则对象的名称常是名词短语，挖动词
    会产出语义错误的关系（如"不得无证销售药品"挖出 药品 边）。对象节点本身已承载
    语义差异，同类多条规则靠不同对象节点区分。

    方向：role=主体 → 宾语是动作发出者，反转为边起点（宾语 -[类别动词]-> 约束对象）；
    其余角色一律 subject -[类别动词]-> 宾语。
    工具/载体/标准/方法/依据 等角色用其专属动词（使用/依据/采用），保留语义区分。
    """
    cat = rule['category']
    if role == '主体':
        return obj_name, rule['subject'], _cat_verb(cat) or '管理'
    if role in ('引用',):
        verb = '引用'
    elif role in ('条件', '前置条件', '前置依据'):
        verb = '触发条件'
    elif role in ('工具',):
        verb = '使用'
    elif role in ('载体', '基础', '标准', '依据'):
        verb = '依据'
    elif role in ('方式', '方法'):
        verb = '采用'
    else:
        verb = _cat_verb(cat)
    return rule['subject'], obj_name, verb


# =====================================================================
# payload 构造
# =====================================================================

def graph_payloads_for_doc(biz_pages, rule_pages, kid, kb):
    """由某 kid 的 entity-b/entity-r 页 → (nodes, relations, stats)。

    nodes: [{name, attributes, chunks:[]}]; relations: [{node1,node2,type}]
    完整多边模型：
      - 每个业务实体 → 节点（name=canonical, attributes=[类别, 定义…]）
      - 每条规则卡 → 主语节点 + 各宾语出边（_edge_for 定 type）
      - 规则涉及但尚未被抽成业务实体的宾语 → 一并补节点（规则对象=业务概念）
      - 无宾语可建边的规则 → 主语节点 attributes 追加规则语境
      - 每来源文档 → '法规文档' 节点，规则主语 -[制定于]-> 文档节点
    """
    nodes, relations = [], []
    node_meta = collections.OrderedDict()   # canonical -> {'attrs':[...]}
    stats = {'biz_pages': len(biz_pages), 'rule_pages': len(rule_pages),
             'biz_nodes': 0, 'rule_subjects': 0, 'obj_nodes': 0,
             'edges': 0, 'role_edges': 0, 'skipped_rules': 0,
             'no_obj_rules': 0, 'doc_nodes': 0, 'edge_to_rule': collections.Counter()}

    def _node(name, attr=None):
        c = canonicalize(name)
        if not c:
            return ''
        meta = node_meta.setdefault(c, {'attrs': []})
        if attr and attr not in meta['attrs']:
            meta['attrs'].append(attr)
        return c

    def _rel(s, t, typ, rule_title=''):
        # 端点统一 canonicalize：_edge_for 返回的 subject 可能是未剥壳原始名，
        # 与 _node 注册的 canonical 名不一致会让 graph_add 自动补建无 attributes 哑节点
        s, t = canonicalize(s), canonicalize(t)
        if not s or not t or not typ:
            return
        if s == t:
            return  # 自环无意义
        relations.append({'node1': s, 'node2': t, 'type': typ[:REL_TYPE_MAX]})
        stats['edges'] += 1
        stats['edge_to_rule'][rule_title] += 1

    # 1) 业务实体节点
    for p in biz_pages:
        b = parse_biz_page(p)
        cat = b['category'] if b['category'] in BIZ_CAT_NAMES else ''
        attrs = [cat] if cat else ['业务实体']
        if b['desc']:
            attrs.append(b['desc'][:150])
        _node(b['canonical'], attrs[0])
        if len(attrs) > 1:
            _node(b['canonical'], attrs[1])
        stats['biz_nodes'] += 1

    # 2) 规则卡 → 边
    doc_rules = collections.defaultdict(list)  # docname -> [rule,...]
    for rp in rule_pages:
        r = parse_rule_page(rp)
        if not r['subject']:
            stats['skipped_rules'] += 1
            continue
        subj = canonicalize(r['subject'])
        # attr 兜底：desc 提取失败时也必须有 attributes，否则落库成无属性哑节点
        # （供管库 79 个整句名哑节点根因=此处 None 分支）
        _node(subj, f"规则《{r['title']}》" if r['desc'] else f"规则《{r['title']}》约束对象")
        stats['rule_subjects'] += 1
        made = False
        for (oname, role) in r['objects']:
            s, t, typ = _edge_for(r, canonicalize(oname), role)
            if not s or not t:
                continue
            # 边两端都保证注册节点（role=主体 时宾语反转成边起点，可能不在 payload
            # nodes 里——漏注册会让 graph_add 自动补建无 attributes 哑节点）
            _node(s, f"规则《{r['title']}》{role or '涉及'}"[:80])
            _node(t, f"规则《{r['title']}》{role or '涉及'}"[:80])
            _rel(s, t, typ, r['title'])
            if role and role != '客体':
                stats['role_edges'] += 1
            made = True
        if not made:
            # 无宾语出边的规则（纯定义/约束自身）→ 语境并入主语节点
            _node(subj, f"规则《{r['title']}》" + (f"：{r['desc'][:120]}" if r['desc'] else ''))
            stats['no_obj_rules'] += 1
        for src in r['sources']:
            doc_rules[src].append(r)

    # 3) 文档节点 + 制定于
    for src, rules in doc_rules.items():
        if not src:
            continue
        dnode = _node(canonicalize(src)[:DOC_NODE_MAX], '法规文档')
        stats['doc_nodes'] += 1
        seen = set()
        for r in rules:
            s = canonicalize(r['subject'])
            if s and s != dnode and s not in seen:
                seen.add(s)
                _rel(s, dnode, '制定于', '')

    for cname, meta in node_meta.items():
        nodes.append({'name': cname, 'attributes': meta['attrs'], 'chunks': []})
    return nodes, relations, stats


# =====================================================================
# graph_add / delete / 导出
# =====================================================================

def graph_add(kb, payload):
    return wr.tool_call('graph_add', {'kb_id': kb, **payload})


def delete_doc_graph(kb, kid):
    return wr.tool_call('graph_delete', {'kb_id': kb, 'knowledge_id': kid})


def _ref_has(page, kid):
    refs = page.get('source_refs') or []
    return any(str(r).split('|', 1)[0] == kid for r in refs)


def fetch_doc_pages(kb, kid):
    biz_all = fetch_pages(kb, 'business_ontology')
    rule_all = fetch_pages(kb, 'rule_ontology')
    biz = [p for p in biz_all if _ref_has(p, kid)]
    rules = [p for p in rule_all if _ref_has(p, kid)]
    return biz, rules


def export_doc(kid, kb, source_file=None, dry_run=False, quiet=False):
    """单文档图导出：解析该 kid 实体/规则页 → 先删该 kid 子图 → graph_add。

    供 build_full.py Step 9.5 调用。dry_run 只统计不写库。
    """
    biz, rules = fetch_doc_pages(kb, kid)
    nodes, relations, stats = graph_payloads_for_doc(biz, rules, kid, kb)
    if not dry_run:
        if nodes or relations:
            try:
                delete_doc_graph(kb, kid)
            except Exception as e:
                print(f"    ⚠️ graph_delete {kid[:8]} 失败: {e}（继续写入）")
            r = graph_add(kb, {'graphs': [{'node': nodes, 'relation': relations}],
                               'knowledge_id': kid})
            if isinstance(r, dict) and r.get('error'):
                raise RuntimeError(f"graph_add 失败: {r['error']}")
            stats['written_nodes'] = len(nodes)
            stats['written_rels'] = len(relations)
            if not quiet:
                print(f"    图写入: {len(nodes)} 节点 / {len(relations)} 关系 (kg={kid[:8]})")
    return stats


# =====================================================================
# CLI
# =====================================================================

def export_all(kb, dry_run=False):
    biz_all = fetch_pages(kb, 'business_ontology')
    rule_all = fetch_pages(kb, 'rule_ontology')
    kids = set()
    for p in biz_all + rule_all:
        for ref in (p.get('source_refs') or []):
            kids.add(str(ref).split('|', 1)[0])
    print(f"全库 {len(kids)} 个文档（业务实体 {len(biz_all)} 页 / 规则 {len(rule_all)} 页）")
    tn = tr = 0
    for i, kid in enumerate(sorted(kids), 1):
        biz = [p for p in biz_all if _ref_has(p, kid)]
        rules = [p for p in rule_all if _ref_has(p, kid)]
        nodes, rels, st = graph_payloads_for_doc(biz, rules, kid, kb)
        if dry_run:
            print(f"  [{i}/{len(kids)}] {kid[:8]}: {len(biz)}实体 {len(rules)}规则 "
                  f"→ {len(nodes)}节点 {len(rels)}关系")
        else:
            delete_doc_graph(kb, kid)
            graph_add(kb, {'graphs': [{'node': nodes, 'relation': rels}],
                           'knowledge_id': kid})
            tn += len(nodes)
            tr += len(rels)
            print(f"  [{i}/{len(kids)}] {kid[:8]}: 写入 {len(nodes)}节点 {len(rels)}关系")
    if not dry_run:
        print(f"完成：{tn} 节点 / {tr} 关系")


def _main():
    import argparse
    ap = argparse.ArgumentParser(description='市场监管法规 wiki → Neo4j 图谱导出')
    ap.add_argument('action', choices=['export', 'doc', 'delete'])
    ap.add_argument('--kb', default=None)
    ap.add_argument('--kid', default=None)
    ap.add_argument('--source-file', default=None)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    wr.load_config()
    _apply_schema_dynamic()
    kb = a.kb or wr.load_kb()
    wr.KB = kb
    wr.mcp_init()
    if a.action == 'export':
        export_all(kb, dry_run=a.dry_run)
    elif a.action == 'doc':
        if not a.kid:
            print('doc 需 --kid'); sys.exit(1)
        st = export_doc(a.kid, kb, a.source_file, dry_run=a.dry_run)
        if a.dry_run:
            n = st.get('biz_nodes', 0) + st.get('obj_nodes', 0) + st.get('doc_nodes', 0)
            print(f"dry-run: 实体页 {st.get('biz_pages',0)} 规则页 {st.get('rule_pages',0)} "
                  f"→ 预估 {n} 节点 {st.get('edges',0)} 关系"
                  + (f"（skipped_rules={st.get('skipped_rules',0)}, no_obj={st.get('no_obj_rules',0)}）"
                     if st.get('skipped_rules') or st.get('no_obj_rules') else ''))
        else:
            print(f"已写入: {st.get('written_nodes',0)} 节点 / {st.get('written_rels',0)} 关系 (kg={a.kid[:8]})")
    elif a.action == 'delete':
        if not a.kid:
            print('delete 需 --kid'); sys.exit(1)
        r = delete_doc_graph(kb, a.kid)
        # 2026-09-08: 不校验返回值会假报成功(624d9230 实测: 文档已删的 kid graph_delete 静默失败)
        err = (r or {}).get('error') if isinstance(r, dict) else None
        if err:
            print(f"⚠️ graph_delete 返回错误: {err}\n  可 Cypher 清理: MATCH (n) WHERE n:ENTITY{a.kid.replace('-','_')} DETACH DELETE n;")
        else:
            print(f'已删除 {a.kid[:8]} 子图')


if __name__ == '__main__':
    _main()
