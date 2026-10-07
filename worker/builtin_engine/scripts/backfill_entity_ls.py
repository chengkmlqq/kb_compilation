"""
全库统一实体原文关联补齐脚本（2026-08-26 合并版）
合并原 backfill_entity_ls.py（跨文档）、backfill_entity_ls_all.py（业务实体）、
backfill_rule_entity_ls.py（规则实体+空节创建）三个脚本的完整能力：

- 支持 entity-b（业务本体）+ entity-r（规则本体）全部实体页
- 按来源文档分组匹配长句（实体名/核心词匹配，取前 max-per-doc 条）
- 缺失「原文关联」节的页面自动补建空节（模板要求 6 节）
- 有节但无匹配长句 → 合理空置（符合 2026-08-25 判定标准）
- 更新时保留 folder_id + source_refs + page_type + status（坑 1/18 强化）

用法：
    python3 backfill_entity_ls.py --kb <kb_id> [--type all/biz/rule] [--dry-run] [--max-per-doc 3] [--slugs entity-r-xxx,...]

坑（2026-08-26 实测）：
- 部分长句 source_refs 为空数组：匹配时必须允许空 refs 的长句（
  `if source_kids and ls_refs and not ls_refs.intersection(source_kids)`），
  原写法 `if source_kids and not ls_refs.intersection(source_kids)` 会把
  空 refs 长句全部误过滤，导致匹配数=0。
"""
import sys, os, re, json, argparse
from collections import defaultdict
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
DEFAULT_KB = wr.load_kb()

def _ref_kid(ref):
    return (ref or '').split('|')[0]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_all_pages(wr, kb):
    """分页拉取全库页面（全量，page_size=200）。"""
    pages = []
    pw = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': pw, 'page_size': 200})
        data = r.get('pages') or r.get('data') or []
        if not data:
            break
        pages.extend(data)
        if len(data) < 200:
            break
        pw += 1
    return pages

def load_doc_map(wr):
    """拉取文档 id -> file_name 映射。"""
    doc_map = {}
    pw = 1
    while True:
        r = wr.list_docs(page=pw, page_size=50)
        data = r.get('data', [])
        if not data:
            break
        for d in data:
            doc_map[d['id']] = d.get('file_name', '')
        if len(data) < 50:
            break
        pw += 1
    return doc_map

def build_long_sentences_by_doc(pages):
    """按文档收集长句: {kid: [(slug, title, content)]}"""
    by_doc = defaultdict(list)
    for p in pages:
        slug = p.get('slug', '')
        if not slug.startswith('longsentence/'):
            continue
        refs = p.get('source_refs') or []
        if not refs:
            continue
        kid = _ref_kid(refs[0])
        by_doc[kid].append({'slug': slug, 'title': p.get('title', ''), 'content': p.get('content') or ''})
    return by_doc
COMMON_SUFFIXES = ['规则', '原则', '要求', '说明', '定义', '流程', '规定', '办法', '制度', '条件', '约束', '审批', '决策', '职责', '引用', '例外', '时间', '金钱', '版本', '模式', '情形', '事项', '工作', '管理', '部门', '人员', '对象', '文件', '报告', '类型', '方式', '结果', '确认', '审核', '审查', '监督', '备案', '签署', '编制', '发布', '变更', '调整', '终止', '归档', '保存', '记录', '处理', '执行', '实施', '采购', '供应', '合同', '订单', '框架', '协议', '方案', '计划', '预算', '资金', '费用', '价格', '成本', '金额', '标准', '规范', '细则', '体系', '机制', '程序', '环节', '步骤', '方法', '形式', '类型', '类别', '分类', '清单', '目录', '台账', '记录', '报告', '通知', '公告', '公示', '文件', '资料', '档案', '凭证', '单据', '表单', '指标', '参数', '系数', '数值', '比率', '比例', '额度', '份额', '分配', '权重', '等级', '层级', '级别', '序列', '排序', '评分', '评价', '评估', '评审', '筛选', '准入', '资格', '资质', '条件', '标的', '产品', '物资', '设备', '工程', '服务', '技术', '成果', '交付', '验收', '检测', '测试', '实验', '研发', '设计', '规划', '建设', '改造', '扩容', '优化', '升级', '维护', '保养', '维修', '抢修', '应急', '抢险', '备份', '恢复', '容灾', '搬迁', '部署', '上线', '运行', '运维', '监控', '巡检', '调度', '指挥', '协调', '沟通', '对接', '配合', '组织', '牵头', '参与', '协助', '指导', '监督', '检查', '考核', '测评', '统计', '分析', '汇总', '上报', '下达', '转办', '承办', '受理', '办理', '反馈', '答复', '回复', '批复', '审批', '核准', '签发', '发布', '印发', '转发', '归档', '销毁']

def core_name(name):
    """实体名 → 候选核心词列表（原名、去通用后缀）。市场监督域不做领域前缀剥离
    （无「采购」类前缀惯例；«采购»等词首保留原样匹配，防过度放宽误配）。"""
    core = name
    for suf in COMMON_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf) + 1:
            core = name[:-len(suf)]
            break
    candidates = list(dict.fromkeys([name, core]))
    return [c for c in candidates if c and len(c) >= 2]

def match_ls_for_entity(entity_name, long_sentences, source_kids, max_per_doc=3):
    """在来源文档长句中按实体名/核心词匹配，返回 [(slug, title)]。"""
    candidates = core_name(entity_name)
    matched = []
    seen = set()
    for cand in candidates:
        if len(matched) >= max_per_doc:
            break
        for ls in long_sentences:
            if len(matched) >= max_per_doc:
                break
            slug = ls.get('slug', '')
            if slug in seen:
                continue
            ls_refs = set((_ref_kid(r) for r in ls.get('source_refs') or []))
            if source_kids and ls_refs and (not ls_refs.intersection(source_kids)):
                continue
            ls_title = ls.get('title', '')
            ls_body = re.sub('^# .+\\n\\n', '', ls.get('content', ''), count=1)
            if cand in ls_title or cand in ls_body:
                matched.append((slug, ls_title))
                seen.add(slug)
    return matched[:max_per_doc]

def ensure_ls_section(content):
    """确保 content 包含 原文关联 节（模板第5节）。缺失则补建空节。"""
    if '## 原文关联' in content:
        return (content, False)
    m = re.search('\\n## 可回答问题\\n', content)
    if m:
        new_content = content[:m.start()] + '\n## 原文关联\n\n\n' + content[m.start():]
    else:
        new_content = content.rstrip() + '\n\n## 原文关联\n\n\n'
    return (new_content, True)

def fill_ls_section(content, source_refs, by_doc, doc_map, entity_title, max_per_doc=3):
    """为已有 原文关联 节补齐长句链接。无可匹配长句 → 返回 None（合理空置）。"""
    m = re.search('## 原文关联\\n+', content)
    if not m:
        return (None, False)
    after_start = m.end()
    next_section = re.search('\\n## ', content[after_start:])
    if next_section:
        sec_end = after_start + next_section.start()
        post = content[sec_end:]
        sec_content = content[after_start:sec_end]
    else:
        sec_content = content[after_start:]
        post = ''
    existing_links = set(re.findall('\\[\\[(longsentence/[^\\]]+)\\]', sec_content))
    doc_groups = {}
    for ref in source_refs:
        kid = _ref_kid(ref)
        hits = match_ls_for_entity(entity_title, by_doc.get(kid, []), {kid}, max_per_doc)
        if hits:
            doc_groups[kid] = hits
    if not doc_groups:
        return (None, False)
    lines = ['## 原文关联', '']
    changed = False
    for kid, hits in doc_groups.items():
        doc_name = doc_map.get(kid, os.path.basename(kid))
        lines.append(f'**{wr.bookname(doc_name)}**')
        for slug, lst in hits:
            lines.append(f'- [[{slug}|{lst}]]')
            if slug not in existing_links:
                changed = True
        lines.append('')
    new_sec = '\n'.join(lines).rstrip() + '\n\n'
    new_content = content[:m.start()] + new_sec + post.lstrip('\n')
    return (new_content, changed)

def main():
    ap = argparse.ArgumentParser(description='全库统一实体原文关联补齐')
    ap.add_argument('--kb', default=DEFAULT_KB)
    ap.add_argument('--type', default='all', choices=['all', 'biz', 'rule'])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--max-per-doc', type=int, default=3)
    ap.add_argument('--slugs', default='', help='逗号分隔的指定 slug 列表（只处理这些页面）')
    args = ap.parse_args()
    KB = args.kb
    wr.load_config()
    wr.mcp_init()
    wr.KB = KB
    log('拉取全库页面...')
    all_pages = load_all_pages(wr, KB)
    log(f'全库页面: {len(all_pages)}')
    by_doc = build_long_sentences_by_doc(all_pages)
    doc_map = load_doc_map(wr)
    page_map = {p['slug']: p for p in all_pages}

    def _ok(slug):
        if args.slugs:
            return slug in [s.strip() for s in args.slugs.split(',') if s.strip()]
        if args.type == 'biz':
            return slug.startswith('entity-b-')
        if args.type == 'rule':
            return slug.startswith('entity-r-')
        return slug.startswith('entity-b-') or slug.startswith('entity-r-')
    ents = [p for p in all_pages if _ok(p.get('slug', ''))]
    log(f'目标实体: {len(ents)}')
    created_section = 0
    filled_links = 0
    skipped_empty = 0
    errors = 0
    for pg in ents:
        slug = pg.get('slug', '')
        title = pg.get('title', '')
        source_refs = pg.get('source_refs') or []
        folder_id = pg.get('folder_id', '') or (pg.get('folder_ids') or [''])[0]
        page_type = pg.get('page_type', '')
        status = pg.get('status', '') or 'published'
        r = wr.read_page(**{'kb_id': KB, 'slug': slug})
        if 'error' in r:
            log(f"⚠️ wiki_read_page 失败: {slug} -> {r.get('error')}")
            skipped_empty += 1
            continue
        live_content = r.get('content') or ''
        live_title = r.get('title', title)
        live_folder_id = r.get('folder_id', folder_id)
        live_refs = r.get('source_refs') or source_refs
        if '## 原文关联' not in live_content:
            nc, _ = ensure_ls_section(live_content)
            if args.dry_run:
                log(f'[DRY-RUN] 将创建空节: {slug} | {live_title}')
                created_section += 1
                live_content = nc
            else:
                upd = wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': live_title, 'content': nc, 'folder_id': live_folder_id, 'source_refs': live_refs, 'page_type': page_type, 'status': status})
                if 'error' in upd:
                    log(f"❌ 创建节失败: {slug} -> {upd.get('error')}")
                    errors += 1
                    continue
                log(f'✅ 已创建空节: {slug} | {live_title}')
                created_section += 1
                live_content = nc
        nc, changed = fill_ls_section(live_content, live_refs, by_doc, doc_map, live_title, args.max_per_doc)
        if nc is None:
            log(f'⏭️ 合理空置（无匹配长句）: {slug} | {live_title}')
            skipped_empty += 1
            continue
        if not changed:
            log(f'⏭️ 无新增长句可匹配: {slug} | {live_title}')
            skipped_empty += 1
            continue
        if args.dry_run:
            old_links = set(re.findall('\\[\\[(longsentence/[^\\]]+)\\]', live_content))
            new_links = set(re.findall('\\[\\[(longsentence/[^\\]]+)\\]', nc))
            added = new_links - old_links
            log(f'[DRY-RUN] {slug} | {live_title} 会新增 {len(added)} 条')
            for ls in sorted(added):
                log(f'    + [[{ls}|...]]')
            filled_links += 1
            continue
        upd = wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': live_title, 'content': nc, 'folder_id': live_folder_id, 'source_refs': live_refs, 'page_type': page_type, 'status': status})
        if 'error' in upd:
            log(f"❌ 补齐链接失败: {slug} -> {upd.get('error')}")
            errors += 1
        else:
            old_links = set(re.findall('\\[\\[(longsentence/[^\\]]+)\\]', live_content))
            new_links = set(re.findall('\\[\\[(longsentence/[^\\]]+)\\]', nc))
            added_count = len(new_links - old_links)
            log(f'✅ 已补齐 {added_count} 条: {slug} | {live_title}')
            filled_links += 1
    log(f'\n=== 完成 ===')
    log(f'  创建空节（原无节）: {created_section}')
    log(f'  补齐长句链接: {filled_links}')
    log(f'  合理空置/跳过: {skipped_empty}')
    log(f'  错误: {errors}')
    log(f"  {('DRY-RUN 未写库' if args.dry_run else '已写库')}")
    if not args.dry_run and created_section + filled_links > 0:
        log('\n执行 wiki_rebuild_links 重建索引...')
        rb = wr.rebuild_links(**{'kb_id': KB})
        if 'error' in rb:
            log(f"⚠️ wiki_rebuild_links 返回: {rb.get('error')}")
        else:
            log('wiki_rebuild_links 完成')
if __name__ == '__main__':
    main()