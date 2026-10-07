"""规则实体分批抽取脚本（2026-08-20 新增，解决单次大 prompt 导致规则=0 的问题）
用法：python3 extract_rules_batch.py --ls-file <ls_all.json> --cat-ids <cat_ids.json> --kid <kid> --kb <kb_id> --source-file <文件名> [--max-tokens 20000]
将长句按批次(20条)送入 LLM 抽取规则实体，避免 reasoning tokens 耗尽/超时。
"""
import json
import time, sys, os, time, hashlib, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
from extract_entities_single import RULE_PROMPT, rule_slug, find_ls_slugs, apply_schema
BATCH = 8
RULES_OUT = None

def main():
    global BATCH, RULES_OUT
    ap = argparse.ArgumentParser()
    ap.add_argument('--ls-file', required=True)
    ap.add_argument('--cat-ids', required=True)
    ap.add_argument('--kid', required=True)
    ap.add_argument('--kb', default=None)
    ap.add_argument('--source-file', default='')
    ap.add_argument('--max-tokens', type=int, default=20000)
    ap.add_argument('-o', '--output', default=None)
    ap.add_argument('--batch', type=int, default=20)
    args = ap.parse_args()
    BATCH = args.batch
    RULES_OUT = args.output or f'/tmp/rules_{args.kid[:8]}.json'
    wr.load_config()
    wr.set_phase('rules')
    apply_schema(args.kb)
    wr.mcp_init()
    KB = args.kb
    KID = args.kid
    SOURCE = args.source_file
    with open(args.ls_file) as f:
        ls_all = json.load(f)
    with open(args.cat_ids) as f:
        catids = json.load(f)
    RULE_CAT_IDS = {k: v for k, v in catids['rule'].items()}
    RULE_CAT_SHORT_R = catids['rule_short']
    RO_NUM = {full: full.split('-')[0].zfill(3) for full in RULE_CAT_IDS}
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run_rule_batch(batch_idx):
        i = batch_idx * BATCH
        batch = ls_all[i:i + BATCH]
        ls_text = '\n'.join((f"{n}. {item['title']}: {item['text'][:250]}" for n, item in enumerate(batch, start=i + 1)))
        for attempt in range(3):
            try:
                result = wr.llm_call([{'role': 'system', 'content': '你是市场监管法规解析专家，只输出JSON，不要任何思考过程。'}, {'role': 'user', 'content': RULE_PROMPT + '\n\n长句列表：\n' + ls_text}], max_tokens=args.max_tokens)
                result = wr.ensure_full_json(result)
                parsed = json.loads(result)
                if isinstance(parsed, list):
                    print(f'批次 {batch_idx + 1}: {len(parsed)} 条')
                    return parsed
                print(f'批次 {batch_idx + 1}: 非列表，重试')
            except Exception as e:
                print(f'批次 {batch_idx + 1} 失败: {e}, 重试 {attempt + 1}')
            if attempt < 2:
                time.sleep(2)
        return []

    total_rule_batches = (len(ls_all) + BATCH - 1) // BATCH
    all_rules = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = []
        for batch_res in pool.map(_run_rule_batch, range(total_rule_batches)):
            results.append(batch_res)
    for batch_res in results:
        all_rules.extend(batch_res)
    print(f'规则实体总计: {len(all_rules)}')
    with open(RULES_OUT, 'w') as f:
        json.dump(all_rules, f, ensure_ascii=False, indent=2)
    created = skipped = 0
    for r in all_rules:
        title = r.get('title', '').strip()
        category = r.get('category', '').strip()
        constraint = r.get('constraint', '').strip()
        structure = r.get('structure', {}) or {}
        desc = r.get('description', '').strip()
        if not title or not category:
            continue
        cat_dir = None
        for short, full in RULE_CAT_SHORT_R.items():
            if short in category or category in full:
                cat_dir = full
                break
        if not cat_dir:
            print(f'  [SKIP] {title}: 类别 {category} 无法映射')
            continue
        cat_id = RULE_CAT_IDS.get(cat_dir)
        if not cat_id:
            print(f'  [SKIP] {title}: 类别目录 {cat_dir} 无ID')
            continue
        evidence = (r.get('evidence') or '').strip()
        ro_num = int(RO_NUM.get(cat_dir, '000'))
        slug = rule_slug(ro_num, title, evidence=evidence)
        rr = wr.read_page(**{'kb_id': KB, 'slug': slug})
        if 'error' not in rr:
            skipped += 1
            continue
        # 2026-09-02 修改：规则 title 改为 LLM 概括，原文关联改由 evidence 匹配长句
        ls_links = find_ls_slugs(ls_all, evidence, text_only=True) if evidence else []
        if not ls_links:
            print(f'  [SKIP] {title}: 未匹配到任何长句原文，不允许创建无原文关联的规则页')
            continue
        ls_rows = '\n'.join((f'- [[{l["slug"]}|{l["title"]}]]' for l in ls_links))
        parts = []
        for k, v in structure.items():
            if v:
                parts.append(f'- {k}：{v}')
        struct_str = '\n'.join(parts) if parts else '（无）'
        content = f'# {title}\n\n**{category}**\u3000{desc[:30]}\n\n## 基本信息\n\n| 字段 | 内容 |\n|---|---|\n| 规则类别 | {category} |\n| 约束对象 | [[{constraint}]] |\n| 来源制度 | {wr.bookname(SOURCE)} |\n\n## 规则结构\n\n{struct_str}\n\n## 原文关联\n\n**{wr.bookname(SOURCE)}**\n{ls_rows}'
        rr = wr.create_page(**{'kb_id': KB, 'slug': slug, 'title': title, 'content': content, 'folder_id': cat_id, 'page_type': 'rule_ontology', 'source_refs': [f'{KID}|{SOURCE}']})
        if 'error' in rr and '500' in str(rr['error']):
            skipped += 1
        elif 'error' not in rr:
            created += 1
    print(f'规则页: created={created} skipped={skipped}')
if __name__ == '__main__':
    main()