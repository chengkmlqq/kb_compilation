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

用法：python3 gen_summary.py <kid> <知识库> <资料集> <版本> <源文件>
      [--format pdf|doc] [--kb <kb_id>] [--lineage <lineage.json>]
      [--title <自定义标题>] [--dry-run]
输出：摘要页 slug（stdout 末行），建页后可由 build_full.py 复用目录继续长句/关键词/实体。
"""
import json, sys, os, re, hashlib, argparse
import domain

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

KB = None
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_chunks(kid, kb_id, retries=3, delay=3):
    """拉切片；list_chunks 偶发返回 0（索引缓存一致性抖动），重试后再判死。"""
    import time
    for i in range(retries):
        r = wr.list_chunks(kid, kb_id=kb_id, page_size=100)
        chunks = r.get('data', []) if isinstance(r, dict) else []
        if chunks:
            return chunks
        if i < retries - 1:
            time.sleep(delay)
    r = wr.list_chunks(kid, kb_id=kb_id, page=1, page_size=20)
    chunks = r.get('data', []) if isinstance(r, dict) else []
    return chunks


def ensure_folder(name, parent_id):
    """按名幂等创建目录（同名复用）。"""
    r = wr.list_folders(kb_id=KB, parent_id=parent_id)
    for f in r.get('folders', []):
        if f['name'] == name:
            return f['id']
    r = wr.create_folder(name, parent_id=parent_id, kb_id=KB)
    return r.get('folder', {}).get('id', r.get('id', ''))


def build_dirs(spec_system, volume, version, fmt_suffix):
    """建知识库根/资料集目录/版本目录/三子目录/本体子目录（与 build_full.py 步骤 1-4 同口径）。
    返回 (root_id, vol_id, vid, leaf_ids, cat_ids)。"""
    core = re.sub(r'\s+', '', spec_system)
    root_id = None
    for f in wr.list_folders(kb_id=KB, parent_id='').get('folders', []):
        if re.sub(r'\s+', '', f.get('name', '')) == core:
            root_id = f['id']
            break
    if root_id is None:
        root_id = ensure_folder(spec_system, '')
    print(f"[1] 知识库根: {root_id}")
    FLAT = not (volume or '').strip()
    vol_id = root_id if FLAT else ensure_folder(volume, root_id)
    print(f"[1.5] 资料集目录: {vol_id} ({volume or '（平铺模式，目录共享）'})")
    if FLAT:
        vid = root_id
        print(f"[2] 平铺模式：跳过版本层，子目录直接建在库根 ({vid})")
    else:
        children = wr.list_folders(kb_id=KB, parent_id=vol_id)
        vname = f"{version}{fmt_suffix}"
        vid = None
        for c in children.get('folders', []):
            if c['name'] == vname:
                vid = c['id']
                break
        if vid is None:
            vid = ensure_folder(vname, vol_id)
        print(f"[2] 版本目录: {vid} ({vname})")
    leaf = {
        "基础实体": ensure_folder("基础实体", vid),
        "长句原文": ensure_folder("长句原文", vid),
        "高频关键词": ensure_folder("高频关键词", vid),
    }
    print(f"[3] 子目录: {json.dumps(leaf, ensure_ascii=False)}")
    cat_ids = {"biz": {}, "rule": {}, "biz_short": {}, "rule_short": {}}
    biz_cats = domain.BIZ_CATS
    rule_cats = domain.RULE_CATS
    base_id = leaf["基础实体"]
    for oname, out_key, cats in [(domain.BIZ_DIR, "biz", biz_cats), (domain.RULE_DIR, "rule", rule_cats)]:
        onto_id = ensure_folder(oname, base_id)
        for cat in cats:
            cat_ids[out_key][cat] = ensure_folder(cat, onto_id)
    cat_ids["summary"] = ensure_folder("资产摘要", vid)
    cat_ids["biz_short"] = domain.BIZ_CAT_SHORT
    cat_ids["rule_short"] = domain.RULE_CAT_SHORT
    print("[4] 本体子目录: 完成")
    return root_id, vol_id, vid, leaf, cat_ids


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

def gen_summary_content(kid, source_file, full_text, rels, spec_system, volume, version):
    """LLM 生成技术规范资料集卡片（模板 templates/summary-page.md）。"""
    rel_lines = []
    if rels:
        for r in rels:
            rt = REL_CN.get(r.get('rel_type'), r.get('rel_type') or '关联')
            if r.get('kid'):
                ref_md5 = hashlib.md5(r['kid'].encode()).hexdigest()
                ref_link = f"[[summary/{ref_md5}|{r.get('fname') or r.get('ref')}]]"
                rel_lines.append(f"- {ref_link}（关系类型：{rt}，关系说明请按原文概括）")
            else:
                rel_lines.append(f"- {wr.bookname(r['ref'])}（关系类型：{rt}）")
    rel_block = '\n'.join(rel_lines) if rel_lines else '（本资料集未发现与其他数据集文档的明确引用关系）'

    if len(full_text) > 14000:
        full_text = full_text[:10000] + '\n\n……（中段省略）……\n\n' + full_text[-4000:]

    prompt = f"""你是{domain.SUMMARY_ROLE}。请根据下面给定的技术规范资料集原文切片，生成一张「技术规范资料集卡片」形式的 markdown 摘要。

【文档信息】
- 文件名：{source_file}
- 知识库：{spec_system}；资料集：{volume}；版本：{version}

【文档原文切片】
{full_text}

【血缘/引用线索（其他数据集文档引用）】
{rel_block}

【输出要求——严格按以下 markdown 结构，只输出卡片本体，禁止任何问候/聊天内容】：
# {os.path.splitext(source_file)[0]}

（开头第一句用一句话定位本资料集规定什么，不要任何尖括号标记）

## 基本信息

| 字段 | 内容 |
|---|---|
| 知识库 | （如：中国移动省级NG2-BASS技术规范） |
| 资料集 | （如：总册 / 逻辑模型资料集 / 附录一：核心元模型规范） |
| 版本标识 | （v5.0 / v4.5 / 已修订；原文有版本号则一并写） |
| 文档类型 | （按**文件名**判定：以「附录」开头 = 附录；含「已修订」= 修订版；其余 = 技术规范正文） |
| 规范编号 | （原文提取；无则填"未标注"） |
| 发布/编制单位 | （原文提取；无则填"中国移动通信集团公司"） |
| 适用范围 | （适用主体和事项，从原文提炼） |
| 上下位/配套资料集 | （同体系其他资料集名；原文无则填"无"） |
| 状态 | （现行版/旧版/修订版） |

## 资料集定位

（本资料集在知识库中的位置与作用，2~4句）

## 重点内容概览

本资料集重点覆盖以下内容：

- （要点1）
- （要点2）
- （要点3）
- （要点4）

## 关键技术要求

| 要求 | 原文依据 |
|---|---|
| （性能/安全/时间/设备/接口等方面的硬性要求，逐条） | （原文句子摘录，逐字，≤60字；禁止"暂无引用原文"） |

## 与其他数据集文档的关系

| 相关文档 | 关系说明 | 原文依据 |
|---|---|---|
| （库内有对应文档用 [[summary/<md5(kid)>|文档名]] 双链，库内无则纯文本《引用名》） | （关系说明，基于原文概括） | （原文句子摘录，逐字；禁止"暂无引用原文"） |

要求：
1. 所有内容必须基于给定原文切片提炼，不编造。
2. 「关键技术要求」只收录带硬性约束的条款（应/必须/不得/小于/不少于/支持等），每条都要有逐字原文依据（≤60字，可截取关键片段）。
3. 关系表只列原文中明确提到的数据集文档关系（引用/依据/上下位/配套资料集）；不要把正文提到的所有标准都列进来。
4. 原文依据列必须是切片原文中的原句摘录（逐字），每条都要有。
5. 文档类型只看**文件名**：以「附录」开头=附录；含「已修订」=修订版；其余=技术规范正文（不要因为正文里出现"附录"二字就判成附录）。"""
    messages = [{"role": "user", "content": prompt}]
    content = wr.llm_call(messages, max_tokens=8000, temperature=0.2)
    content = wr.clean_llm_tail(content)
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
    if len(ns.args) < 5:
        print("用法: gen_summary.py <kid> <知识库> <资料集> <版本> <源文件> [--format pdf|doc] [--kb <kb_id>] [--lineage <lineage.json>] [--title <标题>] [--dry-run]")
        sys.exit(1)
    kid, spec_system, volume, version, source_file = ns.args[:5]
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
    # 摘要标题：文件名（去扩展名）本身已含 体系+版本+资料集（如「中国移动省级NG2-BASS(v5.0)技术规范总册」）
    fname_noext = os.path.splitext(source_file)[0]
    title = ns.title or fname_noext

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
        content = gen_summary_content(kid, source_file, full_text, rels, spec_system, volume, version)
        print(f"[7] 摘要生成: {len(content)} 字")
        print("=== DRY RUN 预览（未建目录/页面）===")
        print(content[:1200])
        print(slug)
        return

    root_id, vol_id, vid, leaf, cat_ids = build_dirs(spec_system, volume, version, fmt_suffix)

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
    content = gen_summary_content(kid, source_file, full_text, rels, spec_system, volume, version)
    print(f"[7] 摘要生成: {len(content)} 字")

    if ns.dry_run:
        print("=== DRY RUN 预览 ===")
        print(content[:1200])
        print(slug)
        return

    sref = f"{kid}|{source_file}"
    sfold = cat_ids.get('summary') or vid
    r = wr.create_page(slug=slug, title=title, content=content, folder_id=sfold,
                       kb_id=KB, page_type='summary', source_refs=[sref])
    if 'error' in r and '500' in str(r['error']):
        print("create 500（slug 已存在），按坑 3 读回验证")
    # 坑 2：create 传 source_refs 可能不生效，必须 update 补写 + 读回核验
    wr.update_page(slug, kb_id=KB, title=title, content=content, folder_id=sfold,
                   page_type='summary', source_refs=[sref], status='published')
    verify = wr.read_page(slug, kb_id=KB)
    refs_ok = kid in json.dumps(verify.get('source_refs', []), ensure_ascii=False)
    _fids = verify.get('folder_ids') or []
    if not _fids and verify.get('folder_id'):
        _fids = [verify.get('folder_id')]
    folder_ok = sfold in [str(x) for x in _fids] or str(verify.get('folder_id','')) == sfold
    print(f"[8] 摘要页建页完成: {slug} | source_refs 核验={'OK' if refs_ok else 'FAIL'} | folder 核验={'OK' if folder_ok else 'FAIL'}")
    if not refs_ok or not folder_ok:
        print("WARN: 核验失败，需人工检查")
    print(slug)


if __name__ == '__main__':
    main()
