#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Neo4j 知识图谱导出/写入模块 —— 供管制度 Wiki → Neo4j 语义图。

设计（2026-09-07 用户确认；2026-09-20 v2 语义化重构；2026-09-20 v3a 规则卡为中心）：
- 业务本体（entity-b 页）= 节点；规则本体（entity-r 页）= **规则节点**（不再压成边）
- 每文件独立 kg（= 该文档 kid）：构建时先删该 kid 子图再写入，重建同一文档干净无残留；
  跨文档同名实体在 Neo4j 存多份、查询/展示按 name 去重
- 数据源 = 已建 wiki 页面（entity-b/entity-r 的 content），权威、无需回源中间产物
- 写库：graph_add（MCP /wiki/graph/write → Neo4j apoc.merge）
    label = ENTITY<kb_id>:ENTITY<kg>（后端自动）；节点按 (name, kg) 合并；
    关系 apoc.merge.relationship 幂等；kg = 该文档 kid（每文件独立子图）。

v3a 模型（2026-09-20，用户反馈"边提取得太生硬、看不出在说什么"后重构，v2 见 git 历史）：
  知识的最小语义单元是**规则卡**（类别/主体/条件/动作/客体/来源/原文），不是"名词对"。
  v2 把规则卡压成 A-[动作]->B 的边，条件/目的/原文全丢、方向无校验 → 生硬。
  v3a 让规则卡成为节点，边只剩**稳定语义类型**：

  节点（3 类）：
    - 业务实体：name=canonical(title)，attributes=[类别, 定义：…, 关联实体：X（谓词）…]
    - 规则卡：name=canonical(「规则《标题》」)，attributes=[类别, 定义：…, 条件：…, 动作：…, 来源：…]
      （「规则《X》」前缀避免与同名实体节点合并，实测 5% 标题与实体同名）
    - 文档节点：制度版本/法规文档

  边（6 种稳定类型，全部带属性 rule_slug/note）：
    - 实体 -[约束]-> 规则   约束对象被该规则约束
    - 实体 -[主体]-> 规则   规则结构主体（多主体取前 2）
    - 实体 -[客体]-> 规则   关联对象中 role ∈ {客体,动作,结果,对象,涉及,禁止行为}（前 2）
    - 实体 -[条件]-> 规则   条件文本中出现的实体（len≥3，且未通过前三种连过）
    - 规则 -[出自]-> 文档   规则的来源制度
    - 实体 -[关联]-> 实体   关联实体表（谓词/关系说明进边属性 predicate/note，类型恒为「关联」）

  不再使用：动作短语/类别动词边（v2）、26 类视角谓词边（关联：被作用…）、制定于（实体→文档）。
  关联实体表降级为「单类型关联边 + 属性」，既保连通又不再碎片化类型。

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

# 业务/规则类别目录名（与 extract_entities_single / build_full 一致）
BIZ_CATS = ["1-制度对象", "2-采购业务对象", "3-采购方式", "4-组织角色", "5-参与角色",
            "6-采购成果", "7-业务事件", "8-资源对象", "9-制度概念"]
# 页面「所属本体」行写的是**无序号**类别名（目录才带序号前缀）→ 用无序号名匹配、
# 输出还原带序号名。用带序号常量匹配会全部落默认值（坑 125）。
BIZ_CAT_NAMES = {c.split('-', 1)[1]: c for c in BIZ_CATS}
RULE_CATS = ["1-定义规则", "2-条件规则", "3-约束规则", "4-职责规则", "5-审批规则",
             "6-流程规则", "7-引用规则", "8-例外规则", "9-决策规则", "10-时间规则",
             "11-金钱规则", "12-版本规则"]
RULE_CAT_NAMES = {c.split('-', 1)[1]: c for c in RULE_CATS}

# 关联对象可带角色标注的合法值（0830 实测分布；解析时只认这些为角色，防把
# "预算/预估（含税）金额"这类名称中的括号误剥）
OBJ_ROLES = {'主体', '客体', '条件', '动作', '引用', '工具', '基础', '标准', '载体',
             '方法', '前置条件', '前置依据', '方式', '属性', '依据', '对象',
             '禁止行为', '结果', '无'}

# 可作为「客体」边的终点角色（其余角色不建边，条件等写进规则节点 attributes）
OBJECT_ROLES = {'客体', '动作', '结果', '对象', '涉及', '禁止行为'}

REL_TYPE_MAX = 30
DOC_NODE_MAX = 60
ATTRS_MAX = 12          # 每节点 attributes 条数上限（超出写"另有 N 条"汇总）
ACTORS_MAX = 2          # 规则结构主体取前 N 个
OBJECTS_MAX = 2         # 关联对象客体取前 N 个
COND_ENTITY_MIN = 3     # 条件文本匹配实体名的长度下限（防通用短词误匹配）
DOC_NODE_ATTR = '制度版本'


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


def _struct_field(content, label):
    """「## 规则结构」段的 - 主体/条件/动作：<值> 行 → 值（无则空串）。"""
    m = re.search(r'-\s*' + label + r'\s*[：:]\s*([^\n]+)', content)
    return m.group(1).strip() if m else ''


def _split_actors(text):
    """主体字段可写多个（'药品监督管理部门、专业技术机构及其工作人员'）→ 列表。"""
    if not text:
        return []
    parts = [p.strip(' 《》') for p in re.split(r'[、，,;；]', text)]
    return [p for p in parts if len(p) >= 2 and p not in ('无', '-')]


def parse_rule_page(page):
    """entity-r 规则卡 → dict。

    subject=约束对象、objects=[(名,角色)]、actors=规则结构主体、
    condition=规则结构条件、action=规则结构动作。
    """
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
    # 一句话描述：类别标记后的描述文本。有的规则卡只有类别标记没有描述段
    # （"**流程规则**　" 后直接是 "## 基本信息"）——desc 置空（note 回退到动作），
    # 严禁把 "## 基本信息" 当 desc（2026-09-20 实测 98/1019 条约束边 note 写成 "## 基本信息"）
    desc = ''
    m = re.search(r'\*\*' + re.escape(cat) + r'\*\*\s*([^\n#|>]{3,})', content, re.S)
    if m:
        desc = m.group(1).strip(' \t　。；;')
    if not desc:
        # blockquote 描述模板：> **类别** 或 **类别** 后跟 > 描述
        m = re.search(r'\*\*' + re.escape(cat) + r'\*\*\s*>\s*([^\n]+)', content, re.S)
        if m:
            desc = m.group(1).strip()
    if not desc:
        for line in content.split('\n')[1:]:
            line = line.strip()
            if line and not line.startswith(('#', '|', '**', '>')) and not line.startswith('##') \
               and not re.match(r'-\s*(主体|条件|动作|客体)\s*[：:]', line):
                desc = line
                break
    # 规则结构段（主体/条件/动作）
    actors = _split_actors(_struct_field(content, '主体'))
    condition = _struct_field(content, '条件')
    action = _struct_field(content, '动作')
    return {'slug': page.get('slug'), 'title': title, 'category': cat,
            'subject': subj, 'subj_linked': subj_linked, 'objects': objs,
            'sources': sources, 'desc': desc,
            'actors': actors, 'condition': condition, 'action': action}


def parse_biz_page(page):
    """entity-b 业务实体页 → dict。

    related = [(显示名, [[slug]], 谓词), ...]（「关联实体」表，2026-09-20 起入图）。
    """
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
    # 关联实体表：| [[entity-b-xxx|名]] | 谓词(26类) | 说明 | 来源 |
    # 第 3 列「关系说明」是唯一能解释"两实体什么关系"的人话（规则《X》规定：…）——
    # v3a 曾只取前 3 列、note 写占位符《X》关联＝扔掉证据，用户反馈"还是看不懂"（2026-09-20）
    related = []
    m = re.search(r'## 关联实体\n(.*?)(?=\n## |\Z)', content, re.S)
    if m:
        for row in m.group(1).splitlines():
            rm = re.match(r'\|\s*\[\[([^\]|]+)\|([^\]]+)\]\]\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|', row.strip())
            if rm:
                related.append((rm.group(2).strip(), rm.group(1).strip(),
                                rm.group(3).strip(), rm.group(4).strip()))
    return {'slug': page.get('slug'), 'title': title,
            'canonical': canonicalize(title), 'category': category,
            'desc': desc, 'sources': _split_source_line(content),
            'related': related}


# =====================================================================
# 全库拉取 / 索引
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


def build_slug_index(pages):
    """页面 slug → {canonical 名, id, slug}：关联实体表用 [[slug|名]] 定位目标节点页面。"""
    idx = {}
    for p in pages:
        s = (p.get('slug') or '').strip()
        if s and s not in idx:
            idx[s] = {'name': canonicalize(p.get('title') or ''),
                      'id': p.get('id') or '', 'slug': s}
    return idx


def build_page_index(biz_all, rule_all):
    """canonical 名 → {'id','slug'}：给图节点带上 wiki 页面标识（page_id/page_slug）。

    同名多页（跨文档合并页/历史软删残留）取 source_refs 最多的一页 = 合并主页。
    """
    idx = {}
    for p in list(biz_all) + list(rule_all):
        c = canonicalize(p.get('title') or '')
        if not c:
            continue
        n = len(p.get('source_refs') or [])
        cur = idx.get(c)
        if cur is None or n > cur.get('_n', 0):
            idx[c] = {'id': p.get('id') or '', 'slug': p.get('slug') or '', '_n': n}
    for v in idx.values():
        v.pop('_n', None)
    return idx


def build_doc_page_index(summary_all):
    """文档 kid → {'id','slug'}：该文档对应的摘要页（文档节点用它回溯）。"""
    idx = {}
    for p in summary_all:
        for ref in (p.get('source_refs') or []):
            k = str(ref).split('|', 1)[0]
            if k and k not in idx:
                idx[k] = {'id': p.get('id') or '', 'slug': p.get('slug') or ''}
    return idx


def _ref_has(page, kid):
    refs = page.get('source_refs') or []
    return any(str(r).split('|', 1)[0] == kid for r in refs)


# =====================================================================
# payload 构造（v3a：规则卡为中心）
# =====================================================================

def graph_payloads_for_doc(biz_pages, rule_pages, kid, kb,
                           page_index=None, doc_page_index=None,
                           slug_index=None):
    """由某 kid 的 entity-b/entity-r 页 → (nodes, relations, stats)。

    nodes: [{name, attributes, chunks, page_id, page_slug}]
    relations: [{node1, node2, type, properties}]
    v3a 边模型见模块 docstring：6 种稳定类型，全部带 rule_slug/note 属性。
    """
    nodes, relations = [], []
    node_meta = collections.OrderedDict()   # canonical -> {'attrs':[...], 'page':...}
    stats = {'biz_pages': len(biz_pages), 'rule_pages': len(rule_pages),
             'biz_nodes': 0, 'rule_nodes': 0, 'skipped_rules': 0,
             'edges': 0, 'rel_edges': 0, 'cond_edges': 0, 'doc_nodes': 0,
             'edge_to_rule': collections.Counter(), 'edge_types': collections.Counter()}

    def _node(name, attr=None, page=None):
        c = canonicalize(name)
        if not c:
            return ''
        meta = node_meta.setdefault(c, {'attrs': [], 'page': None})
        if attr and attr not in meta['attrs']:
            meta['attrs'].append(attr)
        if page and not meta.get('page'):
            meta['page'] = page
        return c

    def _rel(s, t, typ, rule_title='', props=None):
        # 端点统一 canonicalize：与 _node 注册的 canonical 名不一致会让 graph_add
        # 自动补建无 attributes 哑节点
        s, t = canonicalize(s), canonicalize(t)
        if not s or not t or not typ:
            return
        if s == t:
            return  # 自环无意义
        rel = {'node1': s, 'node2': t, 'type': typ[:REL_TYPE_MAX]}
        if props:
            clean = {k: str(v)[:200] for k, v in props.items() if v}
            if clean:
                rel['properties'] = clean
        relations.append(rel)
        stats['edges'] += 1
        stats['edge_to_rule'][rule_title] += 1
        stats['edge_types'][typ] += 1

    # 1) 业务实体节点
    for p in biz_pages:
        b = parse_biz_page(p)
        cat = BIZ_CAT_NAMES.get(b['category'], '')
        attrs = [cat] if cat else ['业务实体']
        if b['desc']:
            attrs.append(f"定义：{b['desc'][:120]}")
        for (tname, tslug, pred, _pnote) in b['related']:
            if pred in ('反向关联', '—', '-'):
                continue
            attrs.append(f"关联实体：{tname}（{pred}）"[:120])
        _page = {'id': p.get('id') or '', 'slug': p.get('slug') or ''}
        _node(b['canonical'], attrs[0], page=_page)
        for a in attrs[1:]:
            _node(b['canonical'], a, page=_page)
        stats['biz_nodes'] += 1

    # 2) 规则节点 + 稳定边（约束/主体/客体/条件/出自）
    kid_entities = {canonicalize(p.get('title') or '') for p in biz_pages if p.get('title')}
    for rp in rule_pages:
        r = parse_rule_page(rp)
        rtitle = (r['title'] or '').strip()
        rnode = canonicalize(f"规则《{rtitle}》") if rtitle else ''
        if not rnode:
            stats['skipped_rules'] += 1
            continue
        rpage = {'id': rp.get('id') or '', 'slug': rp.get('slug') or ''}
        rcat = RULE_CAT_NAMES.get(r['category'], r['category'] or '规则')
        rattrs = [rcat]
        if r.get('desc'):
            rattrs.append(f"定义：{r['desc'][:120]}")
        if r.get('condition'):
            rattrs.append(f"条件：{r['condition'][:80]}")
        if r.get('action'):
            rattrs.append(f"动作：{r['action'][:80]}")
        if r['sources']:
            rattrs.append(f"来源：{'；'.join(r['sources'])[:100]}")
        _node(rnode, rattrs[0], page=rpage)
        for a in rattrs[1:]:
            _node(rnode, a, page=rpage)
        stats['rule_nodes'] += 1

        note = r.get('desc') or r.get('action') or rtitle
        base_props = {'rule_slug': rp.get('slug') or '', 'note': note[:200]}
        linked = set()
        # 约束对象 → 约束
        if r['subject']:
            s = canonicalize(r['subject'])
            if s and s != rnode:
                if s not in node_meta:
                    _node(s, f"约束对象：规则《{rtitle}》"[:80], page=(page_index or {}).get(s))
                _rel(s, rnode, '约束', rtitle, base_props)
                linked.add(s)
        # 规则结构主体 → 主体
        for a in (r.get('actors') or [])[:ACTORS_MAX]:
            ca = canonicalize(a)
            if ca and ca != rnode:
                if ca not in node_meta:
                    _node(ca, f"规则《{rtitle}》的主体"[:80], page=(page_index or {}).get(ca))
                _rel(ca, rnode, '主体', rtitle, base_props)
                linked.add(ca)
        # 关联对象（客体类）→ 客体
        for (oname, role) in r['objects']:
            if role not in OBJECT_ROLES:
                continue
            co = canonicalize(oname)
            if co and co != rnode:
                if co not in node_meta:
                    _node(co, f"规则《{rtitle}》的{role or '客体'}"[:80], page=(page_index or {}).get(co))
                _rel(co, rnode, '客体', rtitle, base_props)
                linked.add(co)
        # 条件文本中出现且未连过的实体 → 条件
        cond = r.get('condition') or ''
        if cond:
            for e in kid_entities:
                if e in linked or len(e) < COND_ENTITY_MIN or e not in cond:
                    continue
                _rel(e, rnode, '条件', rtitle,
                     {'rule_slug': rp.get('slug') or '', 'note': f"条件：{cond[:200]}"})
                linked.add(e)
                stats['cond_edges'] += 1
        # 来源制度 → 出自
        for src in r['sources']:
            if not src:
                continue
            dnode = _node(canonicalize(src)[:DOC_NODE_MAX], DOC_NODE_ATTR,
                          page=(doc_page_index or {}).get(kid))
            _rel(rnode, dnode, '出自', rtitle, {'rule_slug': rp.get('slug') or '', 'note': ''})

    # 3) 关联实体表 → 稳定「关联」边（谓词/关系说明进属性；类型恒为「关联」）
    for p in biz_pages:
        b = parse_biz_page(p)
        s = b['canonical']
        if not s:
            continue
        for (tname, tslug, pred, pred_note) in b['related']:
            if pred in ('反向关联', '—', '-'):
                continue
            tgt = slug_index.get(tslug) if slug_index else None
            t = (tgt or {}).get('name') or canonicalize(tname)
            if not t or t == s:
                continue
            if t not in node_meta:
                tpage = {'id': tgt['id'], 'slug': tgt['slug']} if tgt else None
                _node(t, '关联实体', page=tpage)
            _rel(s, t, '关联', p.get('title') or '',
                 {'predicate': pred, 'page_slug': p.get('slug') or '',
                  'note': (pred_note or '')[:200] if pred_note else f"《{p.get('title')}》关联"})
            stats['rel_edges'] += 1

    # 兜底网：relation 端点必须都已注册节点。后端 rel_import 用
    # apoc.merge.node(..., {}, {}) 给缺失端点补建"无 attributes"哑节点（坑 123），
    # 这里兜底注册，保证 0 哑节点。
    for rel in relations:
        for ep in (rel['node1'], rel['node2']):
            if ep and ep not in node_meta:
                _node(ep, '关系端点（自动补建）')

    # 属性条数上限（枢纽节点防长文本淹没）
    for cname, meta in node_meta.items():
        attrs = meta['attrs']
        if len(attrs) > ATTRS_MAX:
            attrs = attrs[:ATTRS_MAX - 1] + [f"（另有 {len(attrs) - (ATTRS_MAX - 1)} 条引用）"]
        pg = meta.get('page') or {}
        nodes.append({'name': cname, 'attributes': attrs, 'chunks': [],
                      'page_id': pg.get('id', ''), 'page_slug': pg.get('slug', '')})
    return nodes, relations, stats


# =====================================================================
# graph_add / delete / 导出
# =====================================================================

def graph_add(kb, payload):
    return wr.tool_call('graph_add', {'kb_id': kb, **payload})


def delete_doc_graph(kb, kid):
    return wr.tool_call('graph_delete', {'kb_id': kb, 'knowledge_id': kid})


def purge_doc_graph(kb, kid):
    """删该 kid 旧子图并**校验返回**（失败重试一次 + 响亮告警）。

    坑 124：graph_delete 会静默失败，不校验返回值 = 脏数据留在图里且无人察觉。
    """
    for attempt in (1, 2):
        try:
            r = delete_doc_graph(kb, kid)
        except Exception as e:
            r = {'error': str(e)}
        err = r.get('error') if isinstance(r, dict) else None
        if not err and isinstance(r, dict) and r.get('success') is False:
            err = 'success=false'
        if not err:
            return True
        print(f"    ⚠️ graph_delete {kid[:8]} 第 {attempt} 次失败: {err}")
    print(f"    ⚠️⚠️ {kid[:8]} 子图删除失败——旧数据会残留！修复："
          f"python3 scripts/graph_export.py delete --kid {kid} --kb {kb}，再重跑 export")
    return False


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
    biz_all = fetch_pages(kb, 'business_ontology')
    rule_all = fetch_pages(kb, 'rule_ontology')
    summary_all = fetch_pages(kb, 'summary')
    biz = [p for p in biz_all if _ref_has(p, kid)]
    rules = [p for p in rule_all if _ref_has(p, kid)]
    nodes, relations, stats = graph_payloads_for_doc(
        biz, rules, kid, kb,
        page_index=build_page_index(biz_all, rule_all),
        doc_page_index=build_doc_page_index(summary_all),
        slug_index=build_slug_index(biz_all))
    if not dry_run:
        if nodes or relations:
            purge_doc_graph(kb, kid)
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
    summary_all = fetch_pages(kb, 'summary')
    page_index = build_page_index(biz_all, rule_all)
    doc_page_index = build_doc_page_index(summary_all)
    slug_index = build_slug_index(biz_all)
    print(f"全库 {len(kids)} 个文档（业务实体 {len(biz_all)} 页 / 规则 {len(rule_all)} 页）")
    tn = tr = 0
    agg_types = collections.Counter()
    agg = collections.Counter()
    for i, kid in enumerate(sorted(kids), 1):
        biz = [p for p in biz_all if _ref_has(p, kid)]
        rules = [p for p in rule_all if _ref_has(p, kid)]
        nodes, rels, st = graph_payloads_for_doc(
            biz, rules, kid, kb, page_index=page_index, doc_page_index=doc_page_index,
            slug_index=slug_index)
        agg_types.update(st['edge_types'])
        for k in ('biz_nodes', 'rule_nodes', 'edges', 'rel_edges', 'cond_edges', 'doc_nodes'):
            agg[k] += st[k]
        if dry_run:
            print(f"  [{i}/{len(kids)}] {kid[:8]}: {len(biz)}实体 {len(rules)}规则 "
                  f"→ {len(nodes)}节点 {len(rels)}关系")
        else:
            purge_doc_graph(kb, kid)
            graph_add(kb, {'graphs': [{'node': nodes, 'relation': rels}],
                           'knowledge_id': kid})
            tn += len(nodes)
            tr += len(rels)
            print(f"  [{i}/{len(kids)}] {kid[:8]}: 写入 {len(nodes)}节点 {len(rels)}关系")
    if not dry_run:
        print(f"完成：{tn} 节点 / {tr} 关系")
    print(f"统计：实体节点 {agg['biz_nodes']}、规则节点 {agg['rule_nodes']}、"
          f"边 {agg['edges']}（关联边 {agg['rel_edges']}、条件边 {agg['cond_edges']}）")
    print("边类型 Top20：")
    for t, c in agg_types.most_common(20):
        print(f"  {c:6d}  {t}")


def _main():
    import argparse
    ap = argparse.ArgumentParser(description='供管 wiki → Neo4j 图谱导出')
    ap.add_argument('action', choices=['export', 'doc', 'delete'])
    ap.add_argument('--kb', default=None)
    ap.add_argument('--kid', default=None)
    ap.add_argument('--source-file', default=None)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    wr.load_config()
    kb = a.kb or wr.load_kb()
    wr.KB = kb
    wr.mcp_init()
    if a.action == 'export':
        export_all(kb, dry_run=a.dry_run)
    elif a.action == 'doc':
        if not a.kid:
            print('doc 需 --kid'); sys.exit(1)
        export_doc(a.kid, kb, a.source_file, dry_run=a.dry_run)
    elif a.action == 'delete':
        if not a.kid:
            print('delete 需 --kid'); sys.exit(1)
        r = delete_doc_graph(kb, a.kid)
        err = (r or {}).get('error') if isinstance(r, dict) else None
        if err:
            print(f"⚠️ graph_delete 返回错误: {err}\n  可 Cypher 清理: MATCH (n) WHERE n:ENTITY{a.kid.replace('-','_')} DETACH DELETE n;")
        else:
            print(f'已删除 {a.kid[:8]} 子图')


if __name__ == '__main__':
    _main()
