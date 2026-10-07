"""
隐性关联逐实体对比描述生成脚本（2026-08-20 新增）
读取 mine_hidden_relations.py 的挖掘结果，对每个共享【业务实体】，从两关联文档各自的长句原文
提取该实体相关片段，用 LLM 对比生成「关系类型」+「关系说明」。

变更（2026-08-26）：
  - extract_snippets 改为 title + content 双字段匹配，解决实体名只出现在长句 title、
    不出现在 content 时的漏匹配问题。
  - 对两篇文档均无命中片段的实体，直接跳过，不再强行生成“单方缺失/关联”的伪说明。

用法：
  python3 describe_hidden_relations.py --kb <kb_id>       --rels /tmp/hidden_relations.json [-o /tmp/hidden_described.json] [--max-tokens 20000]

输出 JSON:
  { "<doc_kid>": { "<关联文档kid>": { "doc_name": ..., "same_family": ..., "level": ...,
        "entities": [ { "entity": "规模需求", "rel_type": "互补", "description": "..." }, ... ] } } }
"""
import json, sys, os, re, argparse, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
PROMPT_TMPL = '你是市场监管法规分析专家。以下是两部法律法规文档中针对同一个概念「{entity}」的原文描述片段。\n\n【文档A：{docA}】\n{a_snippets}\n\n【文档B：{docB}】\n{b_snippets}\n\n请对比这两部法规对「{entity}」的规定，输出 JSON（不要任何思考过程）：\n{{\n  "rel_type": "关系类型：关联/互补/差异/冲突/包含/细则细化/衔接 等，选最贴切的一个",\n  "description": "一句话说明：文档A规定…，而文档B规定…，二者关系是…（各不超过40字）"\n}}\n\n要求：\n- rel_type 简明定性，用2~4字（如：互补、细则细化、衔接、差异、包含、冲突、关联）\n- description 必须具体到两文档各自的规定内容，突出该实体在两文档中的异同，不要泛泛而谈\n- 只输出 JSON，两个字段\n'

def extract_snippets(wr, KB, doc_kid, entity, wiki_pages, max_snips=3):
    """从某文档的长句原文中提取包含该实体的片段（content + title 双字段匹配）"""
    snippet_files = {}
    entity_norm = entity.strip()
    for p in wiki_pages:
        if not p.get('slug', '').startswith('longsentence/'):
            continue
        refs = p.get('source_refs') or []
        if doc_kid not in refs:
            continue
        title = (p.get('title', '') or '').strip()
        content = (p.get('content', '') or '').strip()
        matched = bool(entity_norm) and (entity_norm in title or entity_norm in content)
        if not matched:
            continue
        if content:
            for m in re.finditer(re.escape(entity_norm), content):
                s = max(0, m.start() - 40)
                e = min(len(content), m.end() + 60)
                frag = content[s:e].replace('\n', ' ').strip()
                if frag and len(frag) > 5:
                    snippet_files[frag] = True
        elif title:
            if len(title) > 5:
                snippet_files[title] = True
        if len(snippet_files) >= max_snips:
            break
    return list(snippet_files.keys())[:max_snips]

def describe_entity(wr, KB, entity, docA, docB, a_snips, b_snips, max_tokens=20000):
    prompt = PROMPT_TMPL.format(entity=entity, docA=docA, docB=docB, a_snippets='\n'.join((f'- {s}' for s in a_snips)) if a_snips else '（该文档长句中未直接出现该实体）', b_snippets='\n'.join((f'- {s}' for s in b_snips)) if b_snips else '（该文档长句中未直接出现该实体）')
    for attempt in range(3):
        try:
            result = wr.llm_call([{'role': 'system', 'content': '你是市场监管法规解析专家，只输出JSON。'}, {'role': 'user', 'content': prompt}], max_tokens=max_tokens)
            result = wr.ensure_full_json(result)
            parsed = json.loads(result)
            if isinstance(parsed, dict) and 'rel_type' in parsed:
                return (parsed.get('rel_type', ''), parsed.get('description', ''))
            elif isinstance(parsed, list) and parsed:
                p = parsed[0]
                return (p.get('rel_type', ''), p.get('description', ''))
        except Exception as e:
            if attempt == 2:
                return ('关联', f'两文档均涉及「{entity}」，但自动对比分析失败。')
            time.sleep(3)
    return ('关联', f'两文档均涉及「{entity}」。')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--rels', default='/tmp/hidden_relations.json')
    ap.add_argument('-o', '--output', default='/tmp/hidden_described.json')
    ap.add_argument('--max-tokens', type=int, default=20000)
    ap.add_argument('--limit', type=int, default=0, help='调试用：只处理前 N 对')
    args = ap.parse_args()
    wr.load_config()
    wr.mcp_init()
    KB = args.kb
    wr.KB = KB
    with open(args.rels) as f:
        rels = json.load(f)
    doc_map = {d['id']: d.get('file_name', '') for d in wr.list_docs(page=1, page_size=50).get('data', [])}
    wiki_pages = []
    pw = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': KB, 'page': pw, 'page_size': 100})
        data = r.get('pages') or r.get('data') or []
        if not data:
            break
        wiki_pages.extend(data)
        if len(data) < 100:
            break
        pw += 1
    out = {}
    processed = 0
    done_pairs = set()
    for kid, links in rels.items():
        out[kid] = out.get(kid, {})
        for d2, info in links.items():
            if args.limit and processed >= args.limit:
                continue
            pair_key = tuple(sorted([kid, d2]))
            if pair_key in done_pairs:
                ref_kid, ref_d2 = pair_key
                src = out.get(ref_kid, {}).get(ref_d2, {})
                out[kid][d2] = {'doc_name': info.get('doc_name', ''), 'same_family': info.get('same_family', False), 'level': info.get('level', ''), 'summary': info.get('summary', ''), 'entities': src.get('entities', [])}
                continue
            docA = doc_map.get(kid, kid[:8])
            docB = info.get('doc_name', doc_map.get(d2, d2[:8]))
            described_ents = []
            skipped_ents = []
            for entity in info.get('entities', []):
                a_snips = extract_snippets(wr, KB, kid, entity, wiki_pages)
                b_snips = extract_snippets(wr, KB, d2, entity, wiki_pages)
                if not a_snips and (not b_snips):
                    skipped_ents.append(entity)
                    print(f'  [{docA[:16]} ↔ {docB[:16]}] {entity}: skipped（无命中片段）')
                    continue
                rel_type, desc = describe_entity(wr, KB, entity, docA, docB, a_snips, b_snips, args.max_tokens)
                described_ents.append({'entity': entity, 'rel_type': rel_type, 'description': desc})
                print(f'  [{docA[:16]} ↔ {docB[:16]}] {entity}: {rel_type}')
                processed += 1
            if skipped_ents:
                print(f'  [{docA[:16]} ↔ {docB[:16]}] 跳过 {len(skipped_ents)} 个无片段实体：{skipped_ents}')
            out[kid][d2] = {'doc_name': info.get('doc_name', ''), 'same_family': info.get('same_family', False), 'level': info.get('level', ''), 'summary': info.get('summary', ''), 'entities': described_ents}
            done_pairs.add(pair_key)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n=== 完成: 处理 {processed} 个实体对比 ===')
    print(f'结果已保存: {args.output}')
if __name__ == '__main__':
    main()