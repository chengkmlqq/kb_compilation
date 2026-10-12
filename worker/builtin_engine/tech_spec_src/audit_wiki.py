"""供管知识库 Wiki 全量审计 + 修复脚本"""
import json, re, os, sys
import domain
from collections import Counter, defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import weknora_rpc as wr
KB_ID = wr.load_kb()
wr.load_config()
wr.KB = KB_ID
wr.mcp_init()

def load_lint():
    path = '/home/ubuntu/.hermes/cache/spillover/chatcmpl-tool-b8bcbff3b58ad63c.txt'
    with open(path, 'r', encoding='utf-8') as f:
        raw = f.read().strip()
    outer = json.loads(raw)
    inner = outer['result']
    m_score = re.search("'health_score'\\s*:\\s*(\\d+)", inner)
    health_score = int(m_score.group(1)) if m_score else None
    m = re.search("'issues'\\s*:\\s*\\[(.*?)\\]\\s*,\\s*'health_score'\\s*:\\s*\\d+", inner, re.S)
    if not m:
        return ([], health_score)
    arr_text = m.group(1)
    issues = []
    for chunk in re.finditer("\\{'type'\\s*:", arr_text):
        start = chunk.start()
        depth = 0
        end = start
        while end < len(arr_text):
            if arr_text[end] == '{':
                depth += 1
            elif arr_text[end] == '}':
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        issue_str = arr_text[start:end]
        issue = {}
        for field in ['type', 'severity', 'page_slug', 'target_slug', 'description', 'auto_fixable']:
            fm = re.search("'" + field + '\'\\s*:\\s*(\'(?:[^\'\\\\]|\\\\.)*\'|\\"(?:[^\\"\\\\]|\\\\.)*\\"|True|False|None)', issue_str)
            if fm:
                val = fm.group(1)
                if len(val) >= 2 and (val[0] == "'" and val[-1] == "'" or (val[0] == '"' and val[-1] == '"')):
                    val = val[1:-1]
                    val = val.replace("\\'", "'").replace('\\\\', '\\')
                issue[field] = val
        issues.append(issue)
    return (issues, health_score)

def get_all_pages():
    raw = wr.list_wiki_pages(**{'kb_id': KB_ID, 'page': 1, 'page_size': 100})
    if isinstance(raw, str):
        raw = json.loads(raw)
    data = raw if isinstance(raw, dict) else {}
    total_pages = data.get('total_pages', 1)
    pages = data.get('pages', [])
    for p in range(2, total_pages + 1):
        raw = wr.list_wiki_pages(**{'kb_id': KB_ID, 'page': p, 'page_size': 100})
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            continue
        pages.extend(raw.get('pages', []))
    return pages

def read_page(slug):
    raw = wr.read_page(**{'kb_id': KB_ID, 'slug': slug})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if isinstance(raw, dict):
        return raw.get('result', raw)
    return None

def classify_slug(slug):
    if slug.startswith('summary/'):
        return 'summary'
    if slug.startswith('longsentence/'):
        return 'original_sentence'
    if slug.startswith('keyword/'):
        return 'frequent_keyword'
    if slug.startswith('entity-b-'):
        return 'business_ontology'
    if slug.startswith('entity-r-'):
        return 'rule_ontology'
    return 'other'
issues = []
lint_issues, health_score = load_lint()
for li in lint_issues:
    issues.append({'source': 'lint', 'type': li.get('type'), 'severity': li.get('severity'), 'slug': li.get('page_slug'), 'desc': (li.get('description') or '')[:200], 'auto_fixable': li.get('auto_fixable')})
pages = get_all_pages()
print(f'Total pages fetched: {len(pages)}')
by_type = Counter((p.get('page_type') or classify_slug(p.get('slug', '')) for p in pages))
print('By page_type:', dict(by_type))
mismatch = []
for p in pages:
    slug = p.get('slug', '')
    declared_type = p.get('page_type')
    inferred = classify_slug(slug)
    if declared_type and inferred != 'other' and (declared_type != inferred):
        mismatch.append({'slug': slug, 'expected': inferred, 'actual': declared_type})
        issues.append({'source': 'metadata', 'type': 'page_type_mismatch', 'slug': slug, 'expected': inferred, 'actual': declared_type, 'desc': f'page_type={declared_type} but slug prefix implies {inferred}'})
print('page_type mismatches:', len(mismatch))
for m in mismatch[:10]:
    print(' ', m)
samples = defaultdict(list)
for p in pages:
    t = p.get('page_type') or classify_slug(p.get('slug', ''))
    if len(samples[t]) < 5:
        samples[t].append(p)
content_issues = []
for ptype, plist in samples.items():
    for p in plist:
        slug = p.get('slug', '')
        page = read_page(slug)
        if not page:
            continue
        content = page.get('content', '')
        page_type = page.get('page_type', '')
        if ptype == 'business_ontology':
            required = ['## 基本信息', '## 规范要求', '## 关联对象', '## 原文关联']  # 可回答问题已从模板移除（2026-08-28）
            missing = [s for s in required if s not in content]
            if missing:
                content_issues.append({'source': 'content', 'type': 'missing_sections', 'slug': slug, 'desc': f'missing {missing}'})
            if '| 关联对象 | 关系类型 | 关系说明 | 来源 |' in content:
                table_text = content.split('## 关联对象')[1].split('## 原文关联')[0]
                data_rows = [l for l in table_text.splitlines() if l.startswith('|') and '---' not in l and ('关联对象' not in l)]
                if not data_rows:
                    content_issues.append({'source': 'content', 'type': 'empty_relation_table', 'slug': slug, 'desc': '关联对象 table has no data rows'})
            if '（待补充）' in content or '（待关联规范要求）' in content:
                content_issues.append({'source': 'content', 'type': 'placeholder_content', 'slug': slug, 'desc': 'contains placeholder text'})
        if ptype == 'rule_ontology':
            required = ['## 基本信息', '## 要求结构', '## 原文关联']  # 可回答问题已从模板移除（2026-08-28）
            missing = [s for s in required if s not in content]
            if missing:
                content_issues.append({'source': 'content', 'type': 'missing_sections', 'slug': slug, 'desc': f'missing {missing}'})
            m = re.search('\\| 约束对象 \\| ([^|\\n]+) \\|', content)
            if m:
                obj = m.group(1).strip()
                if not obj.startswith('[[') and obj not in ('', '-'):
                    content_issues.append({'source': 'content', 'type': 'constraint_not_linked', 'slug': slug, 'desc': f"约束对象 '{obj}' not linked"})
            if '（待补充）' in content:
                content_issues.append({'source': 'content', 'type': 'placeholder_content', 'slug': slug, 'desc': 'contains placeholder text'})
        if ptype == 'frequent_keyword':
            if '## 原文出现位置' not in content and '## 原文出现' not in content:
                content_issues.append({'source': 'content', 'type': 'missing_kw_section', 'slug': slug, 'desc': 'missing 原文出现位置 section'})
            if '频次：' in content and '[[longsentence/' not in content:
                content_issues.append({'source': 'content', 'type': 'old_keyword_format', 'slug': slug, 'desc': 'keyword page uses old format without longsentence links'})
        if ptype == 'original_sentence':
            if '**相关关键词**' not in content and '相关关键词' not in content:
                content_issues.append({'source': 'content', 'type': 'missing_kw_backlink', 'slug': slug, 'desc': 'longsentence missing 相关 keywords backlinks'})
print(f'\nContent issues found: {len(content_issues)}')
for ci in content_issues[:30]:
    print(ci)
report = {'total_pages': len(pages), 'page_type_counts': dict(by_type), 'health_score': health_score, 'lint_issues': len(issues), 'content_issues': len(content_issues), 'lint_summary': dict(Counter((i.get('type') for i in issues))), 'content_summary': dict(Counter((i.get('type') for i in content_issues))), 'lint_details': issues[:100], 'content_details': content_issues[:100]}
with open('/tmp/wiki_audit_report.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print('\nReport saved to /tmp/wiki_audit_report.json')