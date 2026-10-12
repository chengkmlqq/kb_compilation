"""
全量修复 keyword/ 页面（用 list_wiki_pages 分页，覆盖 wiki_search 漏掉的页面）。
"""
import hashlib
import weknora_rpc as wr
import re, time, sys
wr.load_config()
wr.mcp_init()
KB = wr.load_kb()
LOG_PATH = '/tmp/fix_keyword_pages_full.log'

def log(msg):
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")
    print(msg)

def get_all_keyword_pages():
    """分页拉取全库，筛选 keyword/ 页面。"""
    pages, page = ([], 1)
    while True:
        r = wr.list_wiki_pages(**{'kb_id': KB, 'page': page, 'page_size': 200})
        ps = r.get('pages') or []
        pages.extend(ps)
        if len(ps) < 200:
            break
        page += 1
    return [p for p in pages if str(p.get('slug', '')).startswith('keyword/')]

def get_all_longsentences_indexed():
    """全量拉取后按 kid 索引。"""
    pages, page = ([], 1)
    while True:
        r = wr.list_wiki_pages(**{'kb_id': KB, 'page': page, 'page_size': 200})
        ps = r.get('pages') or []
        pages.extend(ps)
        if len(ps) < 200:
            break
        page += 1
    ls_pages = [p for p in pages if str(p.get('slug', '')).startswith('longsentence/')]
    index = {}
    for p in ls_pages:
        for ref in p.get('source_refs') or []:
            if '|' in ref:
                kid = ref.split('|', 1)[0]
                index.setdefault(kid, []).append(p)
    return index

def get_longsentences_for_doc(kid):
    prefix = 'longsentence/' + hashlib.md5(kid.encode()).hexdigest()[:10]
    ls_pages, seen, page = ([], set(), 1)
    while True:
        try:
            r = wr.wiki_search(**{'kb_id': KB, 'query': prefix, 'limit': 100})
            raw = r.get('error') or r
            data = raw.get('pages') or []
        except Exception:
            data = []
        if not data:
            break
        matched = [p for p in data if p.get('slug', '').startswith(prefix) and p.get('slug') not in seen]
        ls_pages.extend(matched)
        seen.update((p.get('slug') for p in matched))
        if len(matched) < 100:
            break
        page += 1
        time.sleep(0.3)
    return ls_pages

def build_new_content(keyword, slug, matches, freq_hint):
    slug_line = '`slug: ' + slug + '`'
    if matches:
        rows = []
        for i, m in enumerate(matches, 1):
            text = m.get('text', '') or m.get('title', '')
            if keyword in text:
                highlighted = text.replace(keyword, '**' + keyword + '**')
            else:
                highlighted = text
            if len(highlighted) > 300:
                highlighted = highlighted[:300] + '...'
            rows.append('| ' + str(i) + ' | [[' + m['slug'] + '|' + highlighted + ']] |')
        table = '\n'.join(rows)
    else:
        table = '| - | 暂无关联长句 |'
    content = '# ' + keyword + '\n\n' + slug_line + '\n\n' + '本关键词出现在该版本长句原文中 **' + str(freq_hint) + '** 次。\n\n' + '## 原文出现位置\n\n| 序号 | 关联原文 |\n|------|----------|\n' + table + '\n'
    return content

def main():
    start = time.time()
    log('全量修复 keyword/ 页面（list_wiki_pages 分页）...')
    kw_pages = get_all_keyword_pages()
    log('共拉取 ' + str(len(kw_pages)) + ' 个 keyword 页面')
    old_pages = [p for p in kw_pages if '| 序号 | 关联原文 |' not in (p.get('content') or '')]
    log('旧格式（需修复）: ' + str(len(old_pages)))
    if not old_pages:
        log('没有需要修复的页面')
        return
    updated = 0
    skipped = 0
    errors = 0
    for idx, page in enumerate(old_pages, 1):
        slug = page.get('slug', '')
        title = page.get('title', '')
        content = page.get('content', '') or ''
        folder_id = page.get('folder_id', '')
        source_refs = page.get('source_refs') or []
        status = page.get('status', '')
        keyword = title
        if not keyword:
            skipped += 1
            continue
        kid = None
        for ref in source_refs:
            if '|' in ref:
                kid = ref.split('|', 1)[0]
                break
        if not kid:
            log('  跳过 ' + slug + ': 无 source_refs')
            skipped += 1
            continue
        ls_pages = get_longsentences_for_doc(kid)
        log('  [' + str(idx) + '] ' + slug + ' kid=' + kid[:8] + ' 找到长句: ' + str(len(ls_pages)))
        matches = []
        for ls in ls_pages:
            ls_text = ls.get('content', '') or ''
            ls_text_clean = re.sub('^#.*?\\n', '', ls_text).strip()
            if keyword in ls_text_clean:
                mid = ls_text_clean.find(keyword)
                ctx_start = max(0, mid - 20)
                ctx_end = min(len(ls_text_clean), mid + len(keyword) + 20)
                context = ls_text_clean[ctx_start:ctx_end]
                matches.append({'slug': ls.get('slug', ''), 'title': ls.get('title', ''), 'text': context})
            if len(matches) >= 15:
                break
        freq_hint = len(matches) if matches else 1
        new_content = build_new_content(keyword, slug, matches, freq_hint)
        try:
            res = wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': title, 'content': new_content, 'folder_id': folder_id, 'source_refs': source_refs, 'page_type': 'frequent_keyword', 'status': status})
            err = res.get('error')
            if err:
                errors += 1
                log('  [' + str(idx) + '] 失败 ' + slug + ': ' + str(err)[:120])
            else:
                updated += 1
        except Exception as e:
            errors += 1
            log('  [' + str(idx) + '] 异常 ' + slug + ': ' + str(e))
        if idx % 10 == 0:
            log('  进度 [' + str(idx) + '/' + str(len(old_pages)) + '] 已更新=' + str(updated) + ' 跳过=' + str(skipped) + ' 失败=' + str(errors))
        time.sleep(0.2)
    elapsed = time.time() - start
    log('\n修复完成: 更新=' + str(updated) + ' 跳过=' + str(skipped) + ' 失败=' + str(errors) + ' 耗时=' + str(round(elapsed, 1)) + 's')
    if updated > 0:
        log('重建链接索引...')
        try:
            wr.rebuild_links()
            log('链接索引重建完成')
        except Exception as e:
            log('链接重建异常: ' + str(e))
    print('\n日志: ' + LOG_PATH)
if __name__ == '__main__':
    main()