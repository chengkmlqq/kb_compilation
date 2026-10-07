#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摘要页生成脚本（市场监督管理法规域版，2026-09-07 自供管版域化）

与供管版差异：
  - 目录模型：法规库无家族/版本概念——根目录=法规名，三子目录
    （基础实体/长句原文/高频关键词）直接挂根，无版本级目录；摘要页挂根目录。
  - 摘要卡片：法规口径——位阶（法律/行政法规/部门规章）、制定机关/文号、
    发布日期/施行日期/修订记录；章节名与门禁同步：## 法规定位 / ## 与其他法律法规的关系。
  - 供管版「通知/附件/修订说明」特化在法规库不适用（法规库的「关于修改〈XX〉的决定」
    属修订记录信息，写进基本信息表修订历史行，不单独建通知目录）。

用法：python3 gen_summary.py <kid> <family(法规名)> <version(传空串"")> <source_file>
      [--kb <kb_id>] [--lineage <lineage.json>] [--title <自定义标题>] [--dry-run]
输出：摘要页 slug（stdout 末行），建页后可由 build_full.py 复用目录继续长句/关键词/实体。
"""
import json, sys, os, re, hashlib, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

KB = None
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 法规库：根目录名=法规名（去扩展名），无家族噪声词
FAMILY_NOISE = []

def _family_core(fname: str) -> str:
    """法规根目录核心名：去扩展名、去《》（文件名一般不含）、去首尾空白。
    法规库无家族归并需求，直接返回法规原名。"""
    fn = re.sub(r'\.(docx|doc|pdf|xlsx|xls)$', '', fname)
    fn = re.sub(r'^《|》$', '', fn)
    return fn.strip()


def ensure_folder(name, parent_id):
    r = wr.list_folders(kb_id=KB, parent_id=parent_id)
    for f in r.get('folders', []):
        if f['name'] == name:
            return f['id']
    r = wr.create_folder(name, parent_id=parent_id, kb_id=KB)
    if isinstance(r, dict) and 'error' in r:
        raise RuntimeError(f"create_folder 失败: {r['error']}")
    return r.get('folder', {}).get('id', r.get('id', ''))


def resolve_family_root(family: str):
    """返回法规根目录 id。复用同名根目录，不重复创建。"""
    core = _family_core(family)
    r = wr.list_folders(kb_id=KB, parent_id='')
    for f in r.get('folders', []):
        if _family_core(f.get('name', '')) == core:
            return f['id']
    return ensure_folder(family, '')


def build_dirs(family, version, fmt_suffix):
    """建法规根目录 + 三子目录 + 本体子目录（法规库无版本级目录）。
    返回 (root_id, root_id, leaf_ids, cat_ids)——第二个返回值兼容供管版签名（vid=root）。"""
    root_id = resolve_family_root(family)
    print(f"[1] 法规根: {root_id}")
    leaf = {
        "基础实体": ensure_folder("基础实体", root_id),
        "长句原文": ensure_folder("长句原文", root_id),
        "高频关键词": ensure_folder("高频关键词", root_id),
    }
    print(f"[2] 子目录: {json.dumps(leaf, ensure_ascii=False)}")
    cat_ids = {"biz": {}, "rule": {}, "biz_short": {}, "rule_short": {}}
    biz_cats = ["1-法律法规", "2-监管对象", "3-许可事项", "4-违法行为", "5-行政处罚",
                "6-监管职责", "7-标准规范", "8-程序事项", "9-时限要求"]
    rule_cats = ["1-定义规则", "2-条件规则", "3-约束规则", "4-职责规则", "5-审批规则",
                 "6-流程规则", "7-引用规则", "8-例外规则", "9-处罚规则",
                 "10-时限规则", "11-版本规则", "12-程序规则"]
    base_id = leaf["基础实体"]
    for oname, out_key, cats in [("业务本体", "biz", biz_cats), ("规则本体", "rule", rule_cats)]:
        onto_id = ensure_folder(oname, base_id)
        for cat in cats:
            cat_ids[out_key][cat] = ensure_folder(cat, onto_id)
    cat_ids["biz_short"] = {c.split('-', 1)[1]: c for c in biz_cats}
    cat_ids["rule_short"] = {c.split('-', 1)[1]: c for c in rule_cats}
    print("[3] 本体子目录: 完成")
    return root_id, root_id, leaf, cat_ids


def load_lineage(path):
    """读取 build_relations.py 产物 lineage_<kid>.json，提取血缘关系行。
    返回 [{ref, kid, fname, rel_type}]（去重）。"""
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    rels = []
    seen = set()
    for src in ('revision_explicit', 'explicit'):
        for e in data.get(src, []):
            key = (e.get('ref'), e.get('kid'), e.get('rel_type'))
            if key in seen:
                continue
            seen.add(key)
            rels.append({
                'ref': e.get('ref', '').replace('\n', ''),
                'kid': e.get('kid'),
                'fname': e.get('fname'),
                'rel_type': e.get('rel_type', ''),
            })
    return rels


def clean_chunks(chunks):
    """清洗切片噪音：锚点/markdown 标记/纯页码行/PDF 页码线（与 wr.clean_chunk_text 同口径）。"""
    texts = []
    for c in sorted(chunks, key=lambda x: x.get('chunk_index', 0)):
        t = c.get('content', '') or ''
        t = wr.clean_chunk_text(t)
        lines = [ln for ln in t.split('\n') if ln.strip()]
        texts.append('\n'.join(lines))
    return '\n\n'.join(texts).strip()


REL_CN = {
    'supersedes': '替代/废止',
    'based_on': '依据',
    'specializes': '细化/执行',
    'revision_target': '被修订对象',
    'new_version': '新版本印发',
}


def gen_summary_content(kid, source_file, full_text, rels, family, version):
    """LLM 生成法规摘要卡片（模板 templates/summary-page.md）。"""
    rel_lines = []
    if rels:
        for r in rels:
            rt = REL_CN.get(r.get('rel_type'), r.get('rel_type') or '关联')
            if r.get('kid'):
                # 库内文档：相关制度列用 wikilink 指向其摘要页（摘要可能尚未建，链接先写，建后生效）
                ref_md5 = hashlib.md5(r['kid'].encode()).hexdigest()
                ref_link = f"[[summary/{ref_md5}|{r.get('fname') or r.get('ref')}]]"
                rel_lines.append(f"- {ref_link}（关系类型：{rt}，关系说明请按原文概括）")
            else:
                # 库内无对应文档用纯文本《引用名》，禁止「知识库中暂无对应文档」
                rel_lines.append(f"- {wr.bookname(r['ref'])}（关系类型：{rt}）")
    rel_block = '\n'.join(rel_lines) if rel_lines else '（本文件未发现与其他法律法规的明确引用关系）'

    # 截断全文控制 prompt 长度（大文件取头尾）
    if len(full_text) > 14000:
        full_text = full_text[:10000] + '\n\n……（中段省略）……\n\n' + full_text[-4000:]

    family_name = family or os.path.splitext(source_file)[0]
    prompt = f"""你是市场监管法律法规知识库的文档摘要专家。请根据下面给定的法规原文切片，生成一张「法规文档卡片」形式的 markdown 摘要。

【文档信息】
- 文件名：{source_file}

【文档原文切片】
{full_text}

【血缘关系（其他法律法规引用）】
{rel_block}

【输出要求——严格按以下 markdown 结构，只输出卡片本体，禁止任何问候/聊天内容】：
# {family_name}

<一句话定位>（本法规管什么，一句话）

## 基本信息

| 字段 | 内容 |
|---|---|
| 位阶 | <法律 / 行政法规 / 部门规章 / 规范性文件 / 标准等，按制定机关与文号判断> |
| 领域 | <药品、医疗器械、化妆品、食品、特种设备、市场监管综合等> |
| 制定机关 | <全国人大常委会 / 国务院 / 国家市场监督管理总局等，从原文提取> |
| 文号 | <如：国家市场监督管理总局令第9号；法律无文号填"无"> |
| 发布日期 / 施行日期 | <从原文提取；修订记录中也可能有施行日期> |
| 修订记录 | <历次修订（据《关于修改〈XX〉的决定》第X次修订 / 修正），原文无则填"无"> |
| 状态 | <现行有效 / 已废止（原文判断，不编造）> |

## 法规定位

<本法规在法律体系中的位置：上位法/同位阶关系、效力层级、与相关法规的衔接，2~4 句>

## 重点内容概览

这部法规规定<监管领域>的规范，重点覆盖以下内容：

- <要点1>
- <要点2>
- <要点3>
- <要点4>

## 与其他法律法规的关系

| 相关法律法规 | 关系说明 | 原文依据 |
|---|---|---|
| <库内关联法规用 [[summary/<md5(关联kid)>|法规名]] 双链（血缘给出 kid 的），库内无对应文档用纯文本 《引用名》> | <关系说明，基于原文概括> | <原文句子摘录，逐字来自切片；禁止"暂无引用原文"> |

要求：
1. 所有内容必须基于给定原文切片提炼，不编造。
2. 关系表只列原文中明确提到的法律法规关系（依据上面的血缘线索逐条核对原文是否有出处）。
3. 原文依据列必须是切片原文中的原句摘录（逐字），每条关系都要有。
4. 位阶判断依据：全国人大及其常委会制定=法律；国务院制定或"国务院令"发布=行政法规；部门令/总局令发布=部门规章；其他规范性文件=规范性文件。
5. 「与其他法律法规的关系」表若原文无任何明确引用 → 保留表头但不填行。
"""
    messages = [{"role": "user", "content": prompt}]
    content = wr.llm_call(messages, max_tokens=8000, temperature=0.2)
    content = wr.clean_llm_tail(content)
    return content.strip()


def main():
    global KB
    ap = argparse.ArgumentParser()
    ap.add_argument('args', nargs='+')
    ap.add_argument('--kb', default=None)
    ap.add_argument('--format', default=None)
    ap.add_argument('--lineage', default=None)
    ap.add_argument('--title', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ns, _ = ap.parse_known_args()
    if len(ns.args) < 4:
        print("用法: gen_summary.py <kid> <family(法规名)> <> <source_file> [--kb <kb_id>] [--lineage <lineage.json>] [--title <标题>] [--dry-run]")
        sys.exit(1)
    kid, family, version, source_file = ns.args[:4]
    KB = ns.kb or wr.load_kb()
    wr.load_config()
    wr.set_phase('summary')
    wr.KB = KB
    wr.mcp_init()

    md5_kid = hashlib.md5(kid.encode()).hexdigest()
    slug = f"summary/{md5_kid}"
    title = ns.title or os.path.splitext(source_file)[0]

    # 幂等：已有摘要页（非 dry-run）直接跳过
    if not ns.dry_run:
        r = wr.read_page(slug, kb_id=KB)
        if 'error' not in r and r.get('content'):
            print(f"摘要页已存在: {slug}，跳过（如需重建先清空该页）")
            print(slug)
            return

    # dry-run：只拉切片+血缘+LLM 生成预览，不建目录不建页
    if ns.dry_run:
        r = wr.list_chunks(kid, kb_id=KB, page_size=100)
        chunks = r.get('data', [])
        if not chunks:
            print("ERROR: 切片缺失（chunks=0），禁止自行恢复，跳过")
            sys.exit(2)
        full_text = clean_chunks(chunks)
        print(f"[4] 切片: {len(chunks)} chunks, {len(full_text)} 字")
        rels = load_lineage(ns.lineage)
        content = gen_summary_content(kid, source_file, full_text, rels, family, version)
        print(f"[5] 摘要生成: {len(content)} 字")
        print("=== DRY RUN 预览（未建目录/页面）===")
        print(content[:1200])
        print(slug)
        return

    root_id, vid, leaf, cat_ids = build_dirs(family, version, ns.format or '')

    # 切片
    r = wr.list_chunks(kid, kb_id=KB, page_size=100)
    chunks = r.get('data', [])
    if not chunks:
        print("ERROR: 切片缺失（chunks=0），禁止自行恢复，跳过")
        sys.exit(2)
    full_text = clean_chunks(chunks)
    print(f"[4] 切片: {len(chunks)} chunks, {len(full_text)} 字")

    # 血缘
    rels = load_lineage(ns.lineage)
    if rels:
        print(f"[5] 血缘关系: {len(rels)} 条")
    else:
        print("[5] 血缘关系: 无（未传 --lineage 或文件中无关系）")

    # LLM 生成摘要
    content = gen_summary_content(kid, source_file, full_text, rels, family, version)
    print(f"[6] 摘要生成: {len(content)} 字")

    sref = f"{kid}|{source_file}"
    r = wr.create_page(slug=slug, title=title, content=content, folder_id=vid,
                       kb_id=KB, page_type='summary', source_refs=[sref])
    if 'error' in r and '500' in str(r['error']):
        print("create 500（slug 已存在），按坑 3 读回验证")
    # 坑 2：create 传 source_refs 可能不生效，必须 update 补写 + 读回核验
    wr.update_page(slug, kb_id=KB, title=title, content=content, folder_id=vid,
                   page_type='summary', source_refs=[sref], status='published')
    verify = wr.read_page(slug, kb_id=KB)
    refs_ok = kid in json.dumps(verify.get('source_refs', []), ensure_ascii=False)
    folder_ok = str(verify.get('folder_id', '')) == vid
    print(f"[7] 摘要页建页完成: {slug} | source_refs 核验={'OK' if refs_ok else 'FAIL'} | folder 核验={'OK' if folder_ok else 'FAIL'}")
    wr.log_progress('agent_build_summary', knowledge_id=kid, doc_title=source_file,
                    summary=f'摘要页已建（{title}）', kb_id=KB)
    if not refs_ok or not folder_ok:
        print("WARN: 核验失败，需人工检查")
    print(slug)


if __name__ == '__main__':
    main()