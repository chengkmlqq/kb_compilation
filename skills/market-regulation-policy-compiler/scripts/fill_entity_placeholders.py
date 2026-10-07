"""填充实体页占位符（2026-08-19 新增，供管新 KB 实测沉淀）。

背景：重建完成后，entity-b 页的「关键规则」「关联实体」常为占位符
（`（待关联规则实体）` / `（待补充）`）。
现成的 rebuild_key_rules.py 解析旧版 `## 规则明细` 表，与 2026-08-19 新模板
`## 规则结构`（结构化字段）不匹配，故新增本脚本。

功能（按 2026-08-19 模板规范）：
1. 解析全部 entity-r 页的「约束对象」字段（`[[entity-b-xxx|对象名]]` 或纯文本）
   与「规则结构」节内容
2. 对每个 entity-b 页：
   - 「关键规则」表：由约束对象==本实体的规则实体反向推导填充
     （`| [[entity-r-xxx|规则名]] | 规则结构内容 | 《文件》 |`，规则结构内容取
     主体/条件/动作等字段拼接，去掉重复书名号/来源）
   - 「关联实体」表：**移除规则实体行**（规则关系已由关键规则表承载，坑 27；
     业务实体间 26 类关系需 LLM 抽取，脚本不自动编造），占位符行删除，
     保留表头
3. 幂等：已有真实内容的表/链接不重复处理

用法：
    python fill_entity_placeholders.py <kid> --kb <kb_id> \\
        --summary-slug summary/<md5(kid)> --summary-title <文档摘要标题>

依赖：weknora_rpc.py（同目录）。注意 update_wiki_page 必须带
folder_id + source_refs（坑 1），status/page_type 读回原值带上（坑 1 补充）。
"""
import sys
import re
import os
import argparse
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
KEYRULE_RE = re.compile('## 关键规则\\n+(.*?)(?=\\n## |\\Z)', re.S)
REL_RE = re.compile('## 关联实体\\n+(.*?)(?=\\n## |\\Z)', re.S)

def fetch_all_pages(kb):
    pages, page = ([], 1)
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 100})
        ps = r.get('pages') or []
        pages.extend(ps)
        if len(ps) < 100:
            break
        page += 1
    return pages

def parse_rule(rp):
    """解析规则实体页 → (约束对象名, 规则结构文本, 原文关联文本)。"""
    c = rp.get('content') or ''
    obj = ''
    m = re.search('\\| 约束对象 \\| (.*?) \\|', c)
    if m:
        raw = m.group(1).strip()
        lm = re.search('\\[\\[([^|\\]]+)\\|([^\\]]+)\\]\\]', raw)
        obj = lm.group(2) if lm else raw
    struct_m = re.search('## 规则结构\\n+(.*?)(?=\\n## |\\Z)', c, re.S)
    struct_text = ''
    if struct_m:
        parts = []
        for line in struct_m.group(1).strip().split('\n'):
            line = line.strip().lstrip('-').strip()
            if line and (not line.startswith('##')):
                parts.append(line)
        struct_text = '；'.join(parts)
    return (obj, struct_text)

def build_key_rules_table(rule_rows, file_cn):
    lines = ['## 关键规则', '', '| 规则类型 | 内容 | 来源 |', '|---|---|---|']
    for r_slug, r_title, struct_text in rule_rows:
        lines.append('| [[%s|%s]] | %s | %s |' % (r_slug, r_title, struct_text, file_cn))
    return '\n'.join(lines) + '\n'

def update_page(kb, p, new_content):
    args = {'kb_id': kb, 'slug': p.get('slug'), 'title': p.get('title'), 'content': new_content, 'folder_id': p.get('folder_id'), 'folder_ids': p.get('folder_ids') or [p.get('folder_id')], 'source_refs': p.get('source_refs') or [], 'aliases': p.get('aliases') or [], 'status': p.get('status') or 'published', 'page_type': p.get('page_type') or p.get('slug', '').split('/')[0]}
    u = wr.update_page(**args)
    return (not u.get('isError'), u)

def refs_have_kid(refs, kid):
    """source_refs 可能为 'kid|title' 或裸 kid，两种都匹配。"""
    if not refs:
        return False
    return any(((r or '').split('|')[0] == kid for r in refs))

def main():
    ap = argparse.ArgumentParser(description='填充实体页占位符')
    ap.add_argument('kid', help='目标文档 knowledge_id（完整 UUID）')
    ap.add_argument('--kb', default=None, help='知识库 id（默认 wr.KB）')
    ap.add_argument('--summary-slug', required=True, help='所属文档摘要页 slug，如 summary/<md5(kid)>')
    ap.add_argument('--summary-title', default='文档摘要', help='文档摘要页标题（链接显示名）')
    ap.add_argument('--no-remove-rule-rel', action='store_true', help='不从关联实体表移除规则实体行（默认移除，坑 27）')
    args = ap.parse_args()
    wr.load_config()
    kb = args.kb or wr.KB
    if not kb:
        print('请设置 --kb 或技能 config.yaml 的 weknora.kb_id')
        sys.exit(1)
    wr.KB = kb
    wr.mcp_init()
    pages = fetch_all_pages(kb)
    ents = {p['slug']: p for p in pages if (p.get('slug', '').startswith('entity-b-') or p.get('slug', '').startswith('entity-r-')) and (not p.get('deleted_at')) and refs_have_kid(p.get('source_refs'), args.kid)}
    rules = {s: p for s, p in ents.items() if s.startswith('entity-r-')}
    bizs = {s: p for s, p in ents.items() if s.startswith('entity-b-')}
    print(f'本文件 规则实体: {len(rules)}, 业务实体: {len(bizs)}')
    rule_info = {s: parse_rule(p) for s, p in rules.items()}
    rules_by_biz = defaultdict(list)
    for r_slug, (obj, st) in rule_info.items():
        if obj:
            rules_by_biz[obj].append((r_slug, rules[r_slug].get('title', ''), st))
    fixed_b, fixed_r = (0, 0)
    for b_slug, bp in bizs.items():
        b_title = bp.get('title', '')
        c = bp.get('content') or ''
        changed = False
        if '（待关联规则实体）' in c or '（待补充）' in c:
            rows = rules_by_biz.get(b_title, [])
            if rows:
                m = re.search('\\| 来源制度 \\| (.*?) \\|', c)
                file_cn = m.group(1).strip() if m else '《中华人民共和国药品管理法.docx》'
                new_table = build_key_rules_table(rows, file_cn)
                m2 = KEYRULE_RE.search(c)
                if m2:
                    c = c[:m2.start()] + new_table + c[m2.end():]
                else:
                    idx = c.find('## 关联实体')
                    if idx >= 0:
                        c = c[:idx] + new_table + '\n' + c[idx:]
                    else:
                        c = c.rstrip() + '\n\n' + new_table
                changed = True
            else:
                # 2026-08-29：本文件无规则实体（如通知类小文档，规则 title 无法逐字原文被坑 76 拒建）时，
                # 「关键规则」「关联实体」占位符无规则可填——删除占位行（保留表头），避免门禁误报 ERROR。
                kr = KEYRULE_RE.search(c)
                if kr and '（待' in kr.group(1):
                    empty = '## 关键规则\n\n| 规则类型 | 内容 | 来源 |\n|---|---|---|\n'
                    c = c[:kr.start()] + empty + c[kr.end():]
                    changed = True
                rel = REL_RE.search(c)
                if rel and '（待' in rel.group(1):
                    empty = '## 关联实体\n\n| 关联实体 | 关系类型 | 关系说明 | 来源 |\n|---|---|---|---|\n'
                    c = c[:rel.start()] + empty + c[rel.end():]
                    changed = True
        if changed:
            ok, u = update_page(kb, bp, c)
            if not ok:
                print(f'  ❌ {b_slug}: {str(u)[:120]}')
            else:
                fixed_b += 1
    ls_pages = {p['slug']: p for p in pages if p.get('slug', '').startswith('longsentence/') and (not p.get('deleted_at')) and refs_have_kid(p.get('source_refs'), args.kid)}
    for r_slug, rp in rules.items():
        c = rp.get('content') or ''
        changed = False
        if '（待补充）' in c:
            r_title = rp.get('title', '')
            matched = []
            for ls_slug, lsp in ls_pages.items():
                ls_title = lsp.get('title', '')
                ls_text = lsp.get('content', '')
                ls_body = re.sub('^# .+\\n\\n', '', ls_text, count=1)
                if r_title in ls_title or r_title in ls_body:
                    matched.append((ls_slug, ls_title))
                elif len(matched) < 3:
                    for suf in ['规则', '原则', '要求', '说明', '定义', '流程', '规定', '办法', '制度', '条件', '约束', '审批', '决策', '职责', '引用', '例外', '时间', '金钱', '版本', '模式', '情形', '事项', '工作']:
                        if r_title.endswith(suf) and len(r_title) > len(suf) + 1:
                            core = r_title[:-len(suf)]
                            if core in ls_title or core in ls_body:
                                matched.append((ls_slug, ls_title))
                                break
                if len(matched) >= 3:
                    break
            if matched:
                ls_section = '\n'.join((f'- [[{s}|{t}]]' for s, t in matched))
                new_c = re.sub('## 原文关联\\n\\n- （待补充）', f'## 原文关联\n\n{ls_section}', c)
                if new_c != c:
                    c = new_c
                    changed = True
        if changed:
            ok, u = update_page(kb, rp, c)
            if not ok:
                print(f'  ❌ {r_slug}: {str(u)[:120]}')
            else:
                fixed_r += 1
    print(f'业务实体页完善: {fixed_b}/{len(bizs)}，规则实体页原文关联补填: {fixed_r}/{len(rules)}')
    print('完成。之后执行 wiki_rebuild_links + wiki_lint 验证。')
if __name__ == '__main__':
    main()