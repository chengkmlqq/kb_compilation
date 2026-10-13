#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摘要页生成脚本（2026-08-29 新增，补全技能摘要步骤脚本化缺口）

背景：build_full.py 不建摘要页（SKILL.md 设计如此），此前摘要由 agent 手动
wr.llm_call 生成内容再 create_wiki_page。本脚本将该步骤固化：
  1. 建家族根 + 版本目录 + 三子目录 + 本体子目录（与 build_full 同口径，幂等复用）
  2. 拉切片 → LLM 生成摘要卡片（基于模板 templates/summary-page.md）
  3. 建 summary 页（slug=summary/<md5(kid)32位>，title=<制度名> <版本标识>）
  4. update 补 source_refs + read_page 读回核验（坑 2）
  5. clean_llm_tail 清洗聊天残留（坑 74）

用法：python3 gen_summary.py <kid> <family> <version> <source_file>
      [--format pdf|doc] [--kb <kb_id>] [--lineage <lineage.json>]
      [--title <自定义标题>] [--dry-run]
输出：摘要页 slug（stdout 末行），建页后可由 build_full.py 复用目录继续长句/关键词/实体。
"""
import json, sys, os, re, hashlib, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_wiki_rpc as wr

KB = None
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 与 build_full.py 保持一致的家族核心名提取（避免同名家族分裂成两套根目录）
FAMILY_NOISE = ['修订说明', '主要修订情况', '一览表', '分层分级授权清单', '通知', '附件', '授权清单', '主要']

def fetch_chunks(kid, kb_id, retries=3, delay=3):
    """拉切片；list_chunks 偶发返回 0（索引缓存一致性抖动），重试后再判死。
    坑：chunks=0 直接 abort 会把正常文档误判为「切片缺失」（843bc5e4/89b06bb5 两次假 abort）。"""
    import time
    for i in range(retries):
        r = wr.list_chunks(kid, kb_id=kb_id, page_size=100)
        chunks = r.get('data', []) if isinstance(r, dict) else []
        if chunks:
            return chunks
        if i < retries - 1:
            time.sleep(delay)
    # 再试小页（page_size=100 偶发空页）
    r = wr.list_chunks(kid, kb_id=kb_id, page=1, page_size=20)
    chunks = r.get('data', []) if isinstance(r, dict) else []
    return chunks

def _family_core(fname: str) -> str:
    fn = re.sub(r'\.(docx|doc|pdf|xlsx|xls)$', '', fname)
    fn = fn.replace('中国移动通信集团上海有限公司', '')
    fn = re.sub(r'^[^《]*?〔[^〕]*〕\s*', '', fn)
    fn = re.sub(r'^[^《]*?号\s*', '', fn)
    m = re.search(r'《([^》]+)》', fn)
    if m:
        fn = m.group(1)
    fn = re.sub(r'^[A-Z]\d+\s*[\d.]*\s*年?[，,]?\s*', '', fn)
    fn = re.sub(r'^(附件\d*[：.:]?)\s*', '', fn)
    fn = re.sub(r'[（(]V[\d.]+[)）]', '', fn)
    fn = re.sub(r'[\d.]+年?$', '', fn)
    for nz in FAMILY_NOISE:
        fn = fn.replace(nz, '')
    fn = fn.strip(' ：《》')
    fn = fn.replace('实施细则', '细则')
    return fn


def ensure_folder(name, parent_id):
    r = wr.list_folders(kb_id=KB, parent_id=parent_id)
    for f in r.get('folders', []):
        if f['name'] == name:
            return f['id']
    r = wr.create_folder(name, parent_id=parent_id, kb_id=KB)
    return r.get('folder', {}).get('id', r.get('id', ''))


def resolve_family_root(family: str):
    core = _family_core(family)
    r = wr.list_folders(kb_id=KB, parent_id='')
    for f in r.get('folders', []):
        if _family_core(f.get('name', '')) == core:
            return f['id']
    return ensure_folder(family, '')


def build_dirs(family, version, fmt_suffix):
    """建家族根/版本目录/三子目录/本体子目录（与 build_full.py 步骤 1-4 同口径）。
    返回 (root_id, version_id, leaf_ids, cat_ids)。"""
    root_id = resolve_family_root(family)
    print(f"[1] 家族根: {root_id}")
    children = wr.list_folders(kb_id=KB, parent_id=root_id)
    vname = f"{version}{fmt_suffix}"
    vid = None
    for c in children.get('folders', []):
        if c['name'] == vname:
            vid = c['id']
            break
    if vid is None:
        vid = ensure_folder(vname, root_id)
    print(f"[2] 版本目录: {vid} ({vname})")
    leaf = {
        "基础实体": ensure_folder("基础实体", vid),
        "长句原文": ensure_folder("长句原文", vid),
        "高频关键词": ensure_folder("高频关键词", vid),
    }
    print(f"[3] 子目录: {json.dumps(leaf, ensure_ascii=False)}")
    cat_ids = {"biz": {}, "rule": {}, "biz_short": {}, "rule_short": {}}
    biz_cats = ["1-制度对象","2-采购业务对象","3-采购方式","4-组织角色","5-参与角色",
                "6-采购成果","7-业务事件","8-资源对象","9-制度概念"]
    rule_cats = ["1-定义规则","2-条件规则","3-约束规则","4-职责规则","5-审批规则",
                 "6-流程规则","7-引用规则","8-例外规则","9-决策规则",
                 "10-时间规则","11-金钱规则","12-版本规则"]
    base_id = leaf["基础实体"]
    for oname, out_key, cats in [("业务本体", "biz", biz_cats), ("规则本体", "rule", rule_cats)]:
        onto_id = ensure_folder(oname, base_id)
        for cat in cats:
            cat_ids[out_key][cat] = ensure_folder(cat, onto_id)
    cat_ids["biz_short"] = {c.split('-',1)[1]: c for c in biz_cats}
    cat_ids["rule_short"] = {c.split('-',1)[1]: c for c in rule_cats}
    print("[4] 本体子目录: 完成")
    return root_id, vid, leaf, cat_ids


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
    """清洗切片噪音：锚点/markdown 标记/纯页码行（与技能 clean_chunk_text 同口径）。"""
    texts = []
    for c in sorted(chunks, key=lambda x: x.get('chunk_index', 0)):
        t = c.get('content', '') or ''
        t = re.sub(r'\[.*?\]\(#_Toc[^)]*\)', '', t)
        t = re.sub(r'^#{1,6}\s*', '', t, flags=re.M)
        t = re.sub(r'#{2,6}', '', t)
        lines = [ln for ln in t.split('\n') if ln.strip() and not re.fullmatch(r'\s*\d+\s*', ln)]
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
    """LLM 生成摘要卡片（模板 summary-page.md）。"""
    is_notice = '通知' in source_file
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
                # 2026-08-29：库内无对应文档用纯文本《引用名》，不再写「知识库中暂无对应文档」
                rel_lines.append(f"- {wr.bookname(r['ref'])}（关系类型：{rt}）")
    rel_block = '\n'.join(rel_lines) if rel_lines else '（本文件未发现与其他制度的明确引用关系）'

    # 2026-08-29 通知类文档：从文件名《》提取被通知制度名（最后一个《》通常是主制度）
    notice_target = ''
    if is_notice:
        books = re.findall(r'《([^》]+)》', source_file)
        if books:
            notice_target = books[-1]

    # 截断全文控制 prompt 长度（大文件取头尾）
    if len(full_text) > 14000:
        full_text = full_text[:10000] + '\n\n……（中段省略）……\n\n' + full_text[-4000:]

    prompt = f"""你是企业制度知识库的文档摘要专家。请根据下面给定的制度文档原文切片，生成一张「制度文档卡片」形式的 markdown 摘要。

【文档信息】
- 文件名：{source_file}
- 家族：{family}，版本：{version}

【文档原文切片】
{full_text}

【血缘关系（其他制度引用）】
{rel_block}

【输出要求——严格按以下 markdown 结构，只输出卡片本体，禁止任何问候/聊天内容】：
# {os.path.splitext(source_file)[0]}

（开头第一句用一句话定位本制度管什么，不要任何尖括号标记）

## 基本信息

| 字段 | 内容 |
|---|---|
| 制度类型 | （主制度/实施细则/通知/附件/修订说明，按文件性质选一） |
| 领域 | （如：采购管理） |
| 适用范围 | （适用主体和事项，从原文提炼） |
| 归口/解释部门 | （归口部门，原文提取；无则填"未明确"） |
| 状态 | （生效或废止） |
| 替代/废止 | （替代废止的旧制度名+文号；原文无则填"无"） |

## 制度定位

（本制度在制度体系中的位置，2~4句）

## 重点内容概览

这份制度（管理领域）的基础框架，重点覆盖以下内容：

- （要点1）
- （要点2）
- （要点3）
- （要点4）

## 与其他制度的关系

| 相关制度 | 关系说明 | 原文依据 |
|---|---|---|
| （库内有关联文档用 [[summary/<md5(kid)>|文档名]] 双链，库内无则纯文本《引用名》） | （关系说明，基于原文概括） | （原文句子摘录，逐字；禁止"暂无引用原文"；通知类可用标题/文号） |

要求：
1. 所有内容必须基于给定原文切片提炼，不编造。
2. 关系表只列原文中明确提到的制度关系（依据上面的血缘线索逐条核对原文是否有出处）。
3. 原文依据列必须是切片原文中的原句摘录（逐字），每条关系都要有。
4. 制度类型按文件性质判断：实施细则/管理办法=实施细则或主制度；含"通知"=通知；含"附件"=附件；修订说明/一览表=修订说明。
{{notice_rule}}"""
    # 通知类文档特化指令（2026-08-29：通知的「与其他制度的关系」只聚焦"本通知是哪个制度的通知"，
    # 不罗列通知正文提到的所有制度；原文依据可用标题/文号）
    if is_notice:
        notice_rule = f"""
5. 【通知类文档特殊要求】本文件是「通知」。与其他制度的关系表**只列核心关系**：本通知是《{notice_target}》的印发/修订通知（相关制度=被通知制度，库内有对应文档用 [[summary/<md5(kid)>|文档名]] 双链，库内无则纯文本《制度名》；关系说明=印发通知/修订通知；原文依据=通知标题/文号，如"沪移供管部〔2025〕55号关于印发《{notice_target}》的通知"——即使正文无逐字句，标题即依据）。
不要把通知正文中提到的其他制度（制定依据、执行依据、集团公司办法等）列进关系表。"""
    else:
        notice_rule = ''
    messages = [{"role": "user", "content": prompt.format(notice_rule=notice_rule)}]
    content = wr.llm_call(messages, max_tokens=8000, temperature=0.2)
    content = wr.clean_llm_tail(content)
    # 坑 111：LLM 在 wikilink 内用 \\| 转义竖线 → 统一清洗 \\| → |（否则 slug 带反斜杠、链接断裂）
    content = re.sub(r'\\\|', '|', content)
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
        print("用法: gen_summary.py <kid> <family> <version> <source_file> [--format pdf|doc] [--kb <kb_id>] [--lineage <lineage.json>] [--title <标题>] [--dry-run]")
        sys.exit(1)
    kid, family, version, source_file = ns.args[:4]
    KB = ns.kb or wr.load_kb()
    wr.load_config()
    wr.KB = KB
    wr.mcp_init()

    fmt_suffix = f"-{ns.format}" if ns.format else ""
    # 防呆（坑 2026-09-08：version 参数误带 -pdf/-doc 后缀又叠 --format，产生 <版本>-pdf-pdf 双叠壳目录）
    if ns.format and ns.args[2].endswith(fmt_suffix):
        print(f"  [防呆] version '{ns.args[2]}' 已含 {fmt_suffix} 后缀，自动剥离避免双叠目录")
        ns.args[2] = ns.args[2][:-len(fmt_suffix)]
    md5_kid = hashlib.md5(kid.encode()).hexdigest()
    slug = f"summary/{md5_kid}"
    # title 修复（2026-09-07 实测，maomaoai 复现）：文件名常已含版本标识（A1 24 / A2 24.9 / V2.0 / C9 18 / B1 18），
    # 若再拼 " {version}" 会重复（如 'A1 24 采购管理办法 A1 24'）。文件名（去扩展名）已含版本 → 不拼 version。
    fname_noext = os.path.splitext(source_file)[0]
    ver_in_fname = bool(re.search(r'(^|[^0-9])(A\d(?:\.\d+)?\s*[0-9.]*|B\d\s*[0-9.]*|C\d\s*[0-9.]*|V\d+\.\d+|附件\d*)[^0-9]', fname_noext))
    title = ns.title or (fname_noext if ver_in_fname else f"{fname_noext} {version}")

    # 幂等：已有摘要页（非 dry-run）直接跳过
    if not ns.dry_run:
        r = wr.read_page(slug, kb_id=KB)
        if 'error' not in r and r.get('content'):
            print(f"摘要页已存在: {slug}，跳过（如需重建先清空该页）")
            print(slug)
            return

    # dry-run：只拉切片+血缘+LLM 生成预览，不建目录不建页（2026-08-29 修复：原逻辑 dry-run 也执行 build_dirs，残留空家族根目录）
    if ns.dry_run:
        chunks = fetch_chunks(kid, KB)
        if not chunks:
            print("ERROR: 切片缺失（chunks=0），禁止自行恢复，跳过")
            sys.exit(2)
        full_text = clean_chunks(chunks)
        print(f"[5] 切片: {len(chunks)} chunks, {len(full_text)} 字")
        rels = load_lineage(ns.lineage)
        if rels:
            print(f"[6] 血缘关系: {len(rels)} 条")
        else:
            print("[6] 血缘关系: 无（未传 --lineage 或文件中无关系）")
        content = gen_summary_content(kid, source_file, full_text, rels, family, version)
        print(f"[7] 摘要生成: {len(content)} 字")
        print("=== DRY RUN 预览（未建目录/页面）===")
        print(content[:1200])
        print(slug)
        return

    root_id, vid, leaf, cat_ids = build_dirs(family, version, fmt_suffix)

    # 切片
    chunks = fetch_chunks(kid, KB)
    if not chunks:
        print("ERROR: 切片缺失（chunks=0），禁止自行恢复，跳过")
        sys.exit(2)
    full_text = clean_chunks(chunks)
    print(f"[5] 切片: {len(chunks)} chunks, {len(full_text)} 字")

    # 血缘
    rels = load_lineage(ns.lineage)
    if rels:
        print(f"[6] 血缘关系: {len(rels)} 条")
    else:
        print("[6] 血缘关系: 无（未传 --lineage 或文件中无关系）")

    # LLM 生成摘要
    content = gen_summary_content(kid, source_file, full_text, rels, family, version)
    print(f"[7] 摘要生成: {len(content)} 字")

    if ns.dry_run:
        print("=== DRY RUN 预览 ===")
        print(content[:1200])
        print(slug)
        return

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
    print(f"[8] 摘要页建页完成: {slug} | source_refs 核验={'OK' if refs_ok else 'FAIL'} | folder 核验={'OK' if folder_ok else 'FAIL'}")
    if not refs_ok or not folder_ok:
        print("WARN: 核验失败，需人工检查")
    print(slug)


if __name__ == '__main__':
    main()
