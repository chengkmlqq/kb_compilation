"""
移除 keyword/ 页面中的 slug 显示行（按新模板）。
"""
import weknora_rpc as wr
import time
wr.load_config()
wr.mcp_init()
KB = wr.load_kb()
LOG_PATH = '/tmp/remove_keyword_slug.log'

def log(msg):
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")
    print(msg)

def get_all_keyword_pages():
    pages, page = ([], 1)
    while True:
        r = wr.list_wiki_pages(**{'kb_id': KB, 'page': page, 'page_size': 200})
        ps = r.get('pages') or []
        pages.extend(ps)
        if len(ps) < 200:
            break
        page += 1
    return [p for p in pages if str(p.get('slug', '')).startswith('keyword/')]

def remove_slug_line(content):
    """移除 `slug: ...` 行"""
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        if line.strip().startswith('`slug:') and line.strip().endswith('`'):
            continue
        new_lines.append(line)
    return '\n'.join(new_lines)

def main():
    start = time.time()
    log('开始移除 keyword/ 页面的 slug 显示行...')
    kw_pages = get_all_keyword_pages()
    log('共拉取 ' + str(len(kw_pages)) + ' 个 keyword 页面')
    updated = 0
    skipped = 0
    errors = 0
    for idx, page in enumerate(kw_pages, 1):
        slug = page.get('slug', '')
        content = page.get('content', '') or ''
        folder_id = page.get('folder_id', '')
        source_refs = page.get('source_refs') or []
        status = page.get('status', '')
        if '`slug:' not in content:
            skipped += 1
            continue
        new_content = remove_slug_line(content)
        if new_content == content:
            skipped += 1
            continue
        try:
            res = wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': page.get('title', ''), 'content': new_content, 'folder_id': folder_id, 'source_refs': source_refs, 'page_type': page.get('page_type', 'frequent_keyword'), 'status': status})
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
            log('  进度 [' + str(idx) + '/' + str(len(kw_pages)) + '] 已更新=' + str(updated) + ' 跳过=' + str(skipped) + ' 失败=' + str(errors))
        time.sleep(0.15)
    elapsed = time.time() - start
    log('\n移除完成: 更新=' + str(updated) + ' 跳过=' + str(skipped) + ' 失败=' + str(errors) + ' 耗时=' + str(round(elapsed, 1)) + 's')
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