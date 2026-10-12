#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主题聚类 wiki 生成（2026-09-08 新增，配套 weknora-topic-knowledge / weknora-topic-cluster-management）。

按 0830 基准库结构，在目标库生成「金额」「审批」主题聚类：
  主题聚类/金额：总览 + 预算管控 / 计算口径 / 授权金额门槛 / 决策阈值
  主题聚类/审批：总览 + 需求审批 / 方案决策 / 结果确认 / 执行审批
每子页四要素（weknora-topic-knowledge）：版本状态说明 + 关联制度清单(状态列) + 规则要点(真实双链) + ⚠️版本差异/矛盾 + 知识推导。
总览页：主题概述 / 子主题导航 / ⚠️差异重点导航 / 版本演进导航 / 知识推导。

用法：python3 gen_topic_pages.py --kb <kb_id> [--topic 金额|审批]（默认两个都跑）
"""
import sys, os, re, json, hashlib, argparse
import domain

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
from update_family_index import fix_fake_slugs

TOPIC_ROOT = '主题聚类'

TOPICS = {
    '金额': {
        'subs': ['预算管控', '计算口径', '授权金额门槛', '决策阈值'],
        'keywords': {
            '预算管控': ['预算', '年度计划', '份额', '资金计划'],
            '计算口径': ['金额', '不含税', '含税', '预估金额'],
            '授权金额门槛': ['授权', '审批权限', '分级授权', '分层分级', '权限标准', '门槛'],
            '决策阈值': ['阈值', '限额', '决策层级', '决策权限', '万元以上'],
        },
    },
    '审批': {
        'subs': ['需求审批', '方案决策', '结果确认', '执行审批'],
        'keywords': {
            '需求审批': ['需求审批', '需求', '需求提交', '需求确认'],
            '方案决策': ['方案', '决策', '集体决策', '会议决策', '决策层级', '呈批件'],
            '结果确认': ['结果确认', '成交', '中标', '结果', '评审'],
            '执行审批': ['订单', '单据', '合同', '履约', '变更', '审批'],
        },
    },
}


def m5(s):
    return hashlib.md5(s.encode()).hexdigest()


def search_union(kb, kws, limit=100):
    """逐单关键词 wiki_search（技能坑：多词查询常返回空），并集去重。"""
    seen, order = {}, []
    for kw in kws:
        try:
            r = wr.wiki_search(kw, kb_id=kb, limit=limit)
            hits = (r or {}).get('pages') or []
        except Exception:
            hits = []
        for p in hits:
            s = p.get('slug') or ''
            if s and s not in seen:
                seen[s] = p
                order.append(s)
    return [seen[s] for s in order]


def load_summary_meta(kb):
    """全量 summary 页 → {kid: {slug,title,fname,status,one_liner}} + 全部 summary list。
    状态解析：从 content 基本信息表状态行或「状态」字段抽取；失败留空（LLM 从文件名/摘要自行判断时会被告知用全库 43 页状态）。"""
    allp, page = [], 1
    while True:
        ps = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 200}).get('pages') or []
        allp += ps
        if len(ps) < 200:
            break
        page += 1
    metas = []
    for p in allp:
        if p.get('page_type') != 'summary' or p.get('deleted_at'):
            continue
        srs = p.get('source_refs') or []
        fname = next((s.split('|', 1)[1] for s in srs if '|' in s), p.get('title', '?'))
        kid = next((s.split('|', 1)[0] for s in srs if '|' in s), '')
        c = p.get('content') or ''
        # 状态：查基本信息行/字段
        status = ''
        for m in re.finditer(r'状态[^。\n|]*?[:|：]\s*([^\n|，。]{1,30})', c):
            status = m.group(1).strip()
            break
        if not status:
            m = re.search(r'[|]\s*(已被[^\n|]+替代|已废止|生效|现行)[^\n|]*[|]', c)
            if m:
                status = m.group(1).strip()
        metas.append({'kid': kid, 'slug': p['slug'], 'title': p.get('title', ''),
                      'fname': fname, 'status': status, 'content': c})
    return metas


def build_material(kb, topic, sub, meta_by_kid, all_meta):
    """为子主题聚合材料：命中规则页全文 + 实体页名单 + 关联制度映射。"""
    kws = TOPICS[topic]['keywords'][sub]
    hits = search_union(kb, kws)
    rule_pages, biz_names, ls_names = [], [], []
    rel_docs = {}   # kid -> fname
    for p in hits:
        s = p.get('slug') or ''
        pt = p.get('page_type') or ''
        if pt == 'rule_ontology' or s.startswith('entity-r'):
            rp = wr.read_page(s, kb_id=kb)
            c = rp.get('content', '') if 'error' not in rp else ''
            if c:
                rule_pages.append({'slug': s, 'title': p.get('title') or s, 'content': c[:700]})
                for sr in (rp.get('source_refs') or []):
                    if '|' in sr:
                        rel_docs.setdefault(sr.split('|', 1)[0], sr.split('|', 1)[1])
        elif pt == 'business_ontology' or s.startswith('entity-b'):
            biz_names.append((s, p.get('title') or s))
        elif s.startswith('longsentence'):
            ls_names.append((s, p.get('title') or s))
    # 关联制度 → summary slug + 状态
    rel_rows = []
    for kid, fname in sorted(rel_docs.items()):
        m = meta_by_kid.get(kid)
        if m:
            rel_rows.append({'fname': fname, 'slug': m['slug'], 'status': m['status'] or '未知（见摘要页）'})
    # 兜底：rule content 里 [[summary/xxx|…]] 出现的也在关联中
    return {'sub': sub, 'rules': rule_pages, 'biz': biz_names[:60], 'ls': ls_names[:60],
            'rels': rel_rows[:20], 'rels_docs': rel_docs, 'kws': kws, 'total_hits': len(hits)}


def prompt_subs(topic):
    return '、'.join(f'「{s}」' for s in TOPICS[topic]['subs'])


def build_system():
    return """你是企业采购管理制度知识库的主题知识聚合专家。基于提供的检索材料（规则页/实体/长句/关联制度摘要状态），为「金额」「审批」等跨制度业务主题的子主题生成 wiki 页。

必须严格遵循以下结构（weknora-topic-knowledge 四要素 + 实证要求）：

# <子主题标题>

> ⚠️ **版本状态说明**：本页面以现行有效制度为准，已废止版本仅作沿革参考。正文开头须点明本子主题依据的现行制度名；引用到的废止版本条款一律标注「已被XX替代」。

## 关联制度清单

| 制度名称 | 摘要页双链 | 状态 | 关联点 |
|---|---|---|---|
（每行：制度名 → [[summary/<md5>|制度名]]，状态列必须写 生效 或 已被XX替代——按材料给的关联制度状态填，材料缺失状态时写 见摘要页；关联点写该制度与本子主题相关的规则要点）

## 规则清单（本子主题核心规则）

按业务场景分组（可用 ### 小节），每条规则含：
- 规则要点（来自规则页标题+内容归纳）
- 原文依据：链**真实规则页** [[entity-r-xxxx|规则标题]] 与**实体页** [[entity-b-xxxx|实体名]]；材料中出现的长句/摘要 slug 也可链
（规则清单优先用现行制度；废止版本的规则并入下方历史沿革或差异表，不充当现行实操）

## ⚠️ 版本差异/矛盾点凸显

| # | 维度 | 历史版本（已废止） | 现行版本（生效） | 差异解读 |
|---|---|---|---|---|
（每格尽量链真实 slug 佐证；无差异时写明「本子主题未发现跨版本差异」；区分版本矛盾与跨制度口径差异）

## 实操提醒

1. ...（面向执行的注意点，源自上述规则）

## 知识推导过程

1. **检索**：关键词「xxx」检索命中 N 页，其中规则页 X、实体页 Y。
2. **溯源**：规则来自《制度A》（现行）等；状态核验方式。
3. **归纳**：本子主题呈现「xxx」特征。

---

**相关页面**：
- [[topic/<主题>主题总览|<主题>主题总览]]
（若有对应版本演进总览：- [[evolution/<制度>版本演进总览|<制度> 版本演进]]）

硬性要求：
1. 只基于材料，禁止编造；材料没有的点写「材料未覆盖」，禁止写「（依据摘要页）」「（推断）」「（依据）」占位
2. 双链 target 必须用提供的真实 slug（[[summary/<md5>|标题]] [[entity-r-xxx|标题]] [[entity-b-xxx|标题]] [[longsentence/xxx|标题]]）；禁止编造 slug
3. 状态硬要求：废止版本一律标注已被替代，不得当现行实操引用
4. 规则要点须具体到条款/阈值/金额/时限，禁止空泛"""


def gen_sub_page(kb, topic, sub, material, all_meta, evo_slugs):
    parts = []
    for r in material['rules'][:45]:
        parts.append(f"### 规则页：{r['title']}\nslug={r['slug']}\n{r['content']}")
    mat_txt = (f"主题「{topic}」子主题「{sub}」。检索关键词 {material['kws']}，命中 {material['total_hits']} 页。\n\n"
               f"【规则页材料 {len(material['rules'])} 条】\n" + '\n'.join(parts) +
               f"\n\n【关联对象】{ '、'.join(f'{t}（{s}）' for s,t in material['biz'][:40]) or '无' }" +
               f"\n\n【相关长句】{ '、'.join(t for s,t in material['ls'][:20]) or '无' }" +
               f"\n\n【关联制度（kid|名称 → 摘要slug/状态）】\n" +
               '\n'.join(f"- 《{r['fname']}》 [[{r['slug']}|{r['fname']}]] 状态={r['status']}" for r in material['rels']) +
               f"\n\n【可引用的版本演进页】\n" + '\n'.join(f"- [[{s}|标题]]" for s in evo_slugs))
    msgs = [{'role': 'system', 'content': build_system().replace('<子主题标题>', f'{topic}-{sub}').replace('<主题>', topic)},
            {'role': 'user', 'content': mat_txt}]
    for attempt in range(3):
        out = wr.llm_call(msgs, max_tokens=20000)
        out = (out or '').strip()
        if not out:
            continue
        out = wr.clean_llm_tail(out)
        must = ['关联制度清单', '版本差异/矛盾', '知识推导']
        miss = [s for s in must if s not in out]
        if miss or out.count('[[') == 0:
            print(f'  [门禁] {topic}-{sub} 第{attempt+1}次 缺={miss or "无链接"}，重试')
            continue
        # 修复 slug 后必须仍有链接
        out2 = fix_fake_slugs(kb, out, [], topic)
        if out2.count('[[') == 0:
            print(f'  [门禁] {topic}-{sub} 第{attempt+1}次 fix 后无链接，重试')
            continue
        return out2
    return ''


def gen_overview(kb, topic, sub_pages, evo_slugs, rules_by_sub):
    """总览页：概述 + 子主题导航 + 差异重点 + evolution 导航 + 推导。"""
    nav = '\n'.join(f"- [[topic/{topic}-{s}|{topic}-{s}]]" for s in TOPICS[topic]['subs'])
    evo = '\n'.join(f"- [[{s}|{s}]]" for s in evo_slugs)
    sys2 = f"""你是企业采购管理制度知识库的主题总览专家。生成「{topic}」主题总览页，结构：

# {topic}主题总览

> ⚠️ **版本状态说明**：以现行有效制度为准。

## 主题概述
（一段话：主题覆盖哪些业务场景，各子主题如何衔接）

## 子主题导航
{nav}

## ⚠️ 版本差异/矛盾点重点
| 子主题 | 版本差异/矛盾点 | 详情链接 |
（每行：概括该子主题核心差异 → 链到对应子主题页；无差异写「未见显著跨版本差异」）

## 版本演进导航
涉及多版本制度的纵向演进见对应演进总览页：
{evo}

## 知识推导过程
检索→溯源→状态核验→归纳

硬性要求：只基于下述各子页标题与要点归纳，禁止编造；不得写「（推断）」「（依据摘要页）」占位；双链用真实 slug。"""
    sub_lines = '\n'.join(f"- 子页 [[topic/{slug}|{title}]]" for slug, title in sub_pages)
    user = f"主题「{topic}」共 {len(sub_pages)} 个子页，各子主题检索命中的核心规则标题如下：\n\n"
    for s, t in sub_pages:
        short = s.split('-', 1)[1] if '-' in s else s
        titles = rules_by_sub.get(short, [])
        user += f"### {short}（子页 {s}）\n" + ('、'.join(titles[:25]) or '无材料') + '\n\n'
    for attempt in range(3):
        out = wr.llm_call([{'role': 'system', 'content': sys2}, {'role': 'user', 'content': user}], max_tokens=12000)
        out = (out or '').strip()
        if not out:
            continue
        out = wr.clean_llm_tail(out)
        must = ['子主题导航', '版本差异/矛盾点重点', '知识推导']
        if [s for s in must if s not in out] or out.count('[[') == 0:
            print(f'  [门禁] {topic}总览 重试')
            continue
        out2 = fix_fake_slugs(kb, out, [], topic)
        if out2.count('[[') == 0:
            continue
        return out2
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--topic', default=None, help='金额|审批，缺省两个')
    args = ap.parse_args()
    wr.load_config()
    wr.mcp_init()
    kb = args.kb or wr.load_kb()
    wr.KB = kb
    topics = [args.topic] if args.topic else list(TOPICS.keys())
    # summary 元数据一次加载
    all_meta = load_summary_meta(kb)
    meta_by_kid = {m['kid']: m for m in all_meta if m['kid']}
    print(f'summary 元数据: {len(all_meta)} 页')
    # evolution 页 slug（可链）
    evo_pages = []
    page = 1
    while True:
        ps = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 200}).get('pages') or []
        evo_pages += [p for p in ps if (p.get('slug') or '').startswith('evolution/')]
        if len(ps) < 200:
            break
        page += 1
    evo_slugs = [p['slug'] for p in evo_pages]
    print(f'evolution 页: {len(evo_slugs)} 个')
    # 主题聚类根目录
    roots = wr.list_folders(kb_id=kb, parent_id='').get('folders', [])
    tr = next((f['id'] for f in roots if f['name'] == TOPIC_ROOT), None)
    if not tr:
        wr.tool_call('create_wiki_folder', {'kb_id': kb, 'name': TOPIC_ROOT, 'parent_id': ''})
        roots = wr.list_folders(kb_id=kb, parent_id='').get('folders', [])
        tr = next((f['id'] for f in roots if f['name'] == TOPIC_ROOT), None)
        print(f'[目录] 创建主题聚类根: {tr}')
    for topic in topics:
        tdir = next((f['id'] for f in wr.list_folders(kb_id=kb, parent_id=tr).get('folders', []) if f['name'] == topic), None)
        if not tdir:
            wr.tool_call('create_wiki_folder', {'kb_id': kb, 'name': topic, 'parent_id': tr})
            tdir = next((f['id'] for f in wr.list_folders(kb_id=kb, parent_id=tr).get('folders', []) if f['name'] == topic), None)
            print(f'[目录] 创建主题目录: {topic}')
        subs_ok = []
        rules_by_sub = {}
        for sub in TOPICS[topic]['subs']:
            material = build_material(kb, topic, sub, meta_by_kid, all_meta)
            rules_by_sub[sub] = [r['title'] for r in material['rules']]
            print(f'[{topic}-{sub}] 材料: 规则{len(material["rules"])} 实体{len(material["biz"])} 关联制度{len(material["rels"])} 命中{material["total_hits"]}')
            content = gen_sub_page(kb, topic, sub, material, all_meta, evo_slugs)
            if not content:
                print(f'  [SKIP] {topic}-{sub}: 生成失败')
                continue
            slug = f'topic/{topic}-{sub}'
            # source_refs: 关联制度 kid|fname（命中规则页的来源文档）
            srs = [f"{k}|{v}" for k, v in sorted(material['rels_docs'].items())]
            r = wr.read_page(slug, kb_id=kb)
            if 'error' in r:
                wr.create_page(**{'kb_id': kb, 'slug': slug, 'title': f'{topic}-{sub}',
                                  'content': content, 'folder_id': tdir,
                                  'page_type': 'topic_cluster', 'source_refs': srs})
                print(f'  [页] 创建: {slug}（src {len(srs)}）')
            else:
                wr.update_page(**{'kb_id': kb, 'slug': slug, 'content': content, 'folder_id': tdir,
                                  'page_type': 'topic_cluster', 'source_refs': srs,
                                  'title': f'{topic}-{sub}'})
                print(f'  [页] 更新: {slug}')
            subs_ok.append((slug, f'{topic}-{sub}'))
        # 总览页
        ov = gen_overview(kb, topic, subs_ok, evo_slugs, rules_by_sub)
        if ov:
            slug = f'topic/{topic}主题总览'
            r = wr.read_page(slug, kb_id=kb)
            if 'error' in r:
                wr.create_page(**{'kb_id': kb, 'slug': slug, 'title': f'{topic}主题总览',
                                  'content': ov, 'folder_id': tdir,
                                  'page_type': 'topic_cluster', 'source_refs': []})
                print(f'  [页] 创建: {slug}')
            else:
                wr.update_page(**{'kb_id': kb, 'slug': slug, 'content': ov, 'folder_id': tdir,
                                  'page_type': 'topic_cluster', 'source_refs': [], 'title': f'{topic}主题总览'})
                print(f'  [页] 更新: {slug}')


if __name__ == '__main__':
    main()
