"""
批量修复 source_refs：裸 kid → kid|文件名
存量数据修复脚本（2026-08-25）

用法：
    python fix_source_refs.py --kb <kb_id> [--dry-run]

说明：
    - 从 DB knowledges 表读取 kid → file_name 映射
    - 扫描全库 wiki_pages，source_refs 含裸 kid（不含 |）的页面
    - 用 MCP update_wiki_page 补全为 kid|文件名 格式
    - 只修 bare kid，已有的 kid|title 不处理
"""
import json
import os
import re
import subprocess
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
KB = wr.load_kb()

def psql_json(query):
    result = subprocess.run(['docker', 'exec', 'WeKnora-postgres', 'psql', '-U', 'weknora', '-d', 'weknora', '-t', '--no-align', '-c', query], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f'psql error: {result.stderr}')
    return result.stdout.strip()

def load_kid_file_map(kb_id):
    """从 knowledges 表加载 kid → file_name 映射。"""
    query = f"SELECT id, file_name FROM knowledges WHERE knowledge_base_id = '{kb_id}'"
    rows = psql_json(query)
    mapping = {}
    for line in rows.split('\n'):
        line = line.strip()
        if not line or '|' not in line:
            continue
        parts = line.split('|', 1)
        if len(parts) == 2:
            mapping[parts[0].strip()] = parts[1].strip()
    return mapping

def is_bare_kid(ref):
    """返回 True 如果 ref 是裸 kid（不含 |）。"""
    if not ref:
        return False
    return '|' not in ref

def fetch_all_pages(kb_id):
    """全库拉取 wiki 页面（MCP 分页）。"""
    pages = []
    pw = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb_id, 'page': pw, 'page_size': 100})
        data = r.get('pages') or r.get('data') or []
        if not data:
            break
        pages.extend(data)
        if len(data) < 100:
            break
        pw += 1
        time.sleep(0.2)
    return pages

def main():
    import argparse
    ap = argparse.ArgumentParser(description='批量修复 source_refs：裸 kid → kid|文件名')
    ap.add_argument('--kb', default=KB, help='知识库 id')
    ap.add_argument('--dry-run', action='store_true', help='只统计不写库')
    args = ap.parse_args()
    kb_id = args.kb or KB
    wr.load_config('/home/ubuntu/.hermes/config.yaml')
    wr.KB = kb_id
    wr.mcp_init()
    print(f'[1] 加载 kid→文件名 映射...')
    kid_map = load_kid_file_map(kb_id)
    print(f'    文档数: {len(kid_map)}')
    if not kid_map:
        print('[ERROR] 未找到文档映射，终止')
        return
    print(f'[2] 全库拉取页面...')
    pages = fetch_all_pages(kb_id)
    print(f'    总页面: {len(pages)}')
    to_fix = []
    for p in pages:
        refs = p.get('source_refs') or []
        bare = [r for r in refs if is_bare_kid(r)]
        if bare:
            to_fix.append({'slug': p.get('slug', ''), 'title': p.get('title', ''), 'folder_id': p.get('folder_id', ''), 'folder_ids': p.get('folder_ids') or [p.get('folder_id', '')], 'source_refs': refs, 'page_type': p.get('page_type', ''), 'content': p.get('content', ''), 'status': p.get('status') or 'published', 'bare_kids': bare})
    print(f'[3] 需修复页面: {len(to_fix)}')
    if not to_fix:
        print('    无需修复，退出')
        return
    kid_count = {}
    for p in to_fix:
        for kid in p['bare_kids']:
            kid_count[kid] = kid_count.get(kid, 0) + 1
    print('    按 kid 分布:')
    for kid, cnt in sorted(kid_count.items(), key=lambda x: -x[1]):
        fname = kid_map.get(kid, '???')
        print(f'      {kid[:8]}... ({cnt}页) → {fname}')
    if args.dry_run:
        print('[DRY-RUN] 不写入，统计完毕')
        return
    print(f'[4] 修复写入...')
    fixed = 0
    errors = 0
    skipped = 0
    for p in to_fix:
        new_refs = []
        for ref in p['source_refs']:
            if is_bare_kid(ref) and ref in kid_map:
                new_refs.append(f'{ref}|{kid_map[ref]}')
            else:
                new_refs.append(ref)
        seen = set()
        deduped = []
        for r in new_refs:
            if r not in seen:
                seen.add(r)
                deduped.append(r)
        args_update = {'kb_id': kb_id, 'slug': p['slug'], 'title': p['title'], 'content': p['content'], 'folder_id': p['folder_ids'][0] if p['folder_ids'] else p['folder_id'], 'folder_ids': p['folder_ids'], 'source_refs': deduped, 'status': p['status'], 'page_type': p['page_type']}
        r = wr.update_page(*args_update)
        if r.get('isError') or 'error' in r:
            errors += 1
            print(f"  [ERR] {p['slug']}: {str(r)[:100]}")
        else:
            fixed += 1
        time.sleep(0.15)
    print(f'\n完成: 修复={fixed}, 跳过={skipped}, 错误={errors}')
    print(f'总计: {len(to_fix)} 页')
if __name__ == '__main__':
    main()