"""分离式清空（跨文档实体归一化 P1，2026-08-15）。

重建某文件前的页面清理：**不再按目录全删**（会误删共享实体页），
改为按 source_refs 归属逐页判断：

- 专属页（folder_ids/source_refs 只有本文件）→ delete_wiki_page（软删，重建时重建）
- 共享页（挂到其他文档目录 或 来源含其他 kid）→ update 只移除本文件关联：
    folder_ids  -= [本文件目录]
    source_refs -= [本kid]
    （folder_ids 剩 1 个时主目录 folder_id 自动为剩余那个）

Usage:
    import clear_file_pages as cp
    cp.wr = wr
    cleared, shared = cp.clear_file_pages(wr, kb_id, kid, folder_id, file_label, all_folders=<list>)
    # cleared: 删除的专属页 slug 列表
    # shared:  摘除关联的共享页 slug 列表
"""
import time

def clear_file_pages(wr, kb_id, kid, folder_id, file_label='', page_size=100, dry_run=False, verbose=True, all_folders=None):
    """分离式清空：删除专属页 + 共享页摘除本文件关联。

    Args:
        wr: weknora_rpc 模块
        kb_id: 知识库 id
        kid: 本文件 knowledge_id（source_refs 含它即视为本文件页面）
        folder_id: 本文件基础实体目录 id（**仅用于共享判定兜底**）
        all_folders: 本文件全部目录 id 列表（实体/长句/关键词/版本目录）。
            **必传**（2026-08-15 修复）：只传实体目录会导致长句/关键词页
            folder_ids != [实体目录] 被误判为共享页，source_refs 被误清空。
        file_label: 日志用文件标识
        dry_run: True 只打印不执行
    Returns:
        (deleted_slugs, shared_slugs)
    """
    pages = []
    page_no = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb_id, 'page': page_no, 'page_size': page_size})
        batch = r.get('pages') or []
        if not batch:
            break
        pages.extend(batch)
        if len(batch) < page_size:
            break
        page_no += 1
    mine = []
    for p in pages:
        refs = p.get('source_refs') or []
        if isinstance(refs, list) and any((_ref_kid(r) == kid for r in refs)):
            mine.append(p)
        elif isinstance(refs, str) and kid in refs:
            mine.append(p)
    if verbose:
        print(f'[{file_label}] 本文件页面: {len(mine)}')
    folders = set(all_folders or ([folder_id] if folder_id else []))
    deleted, shared = ([], [])
    for p in mine:
        slug = p.get('slug')
        fids = [f for f in p.get('folder_ids') or [] if f]
        refs = [r for r in p.get('source_refs') or [] if r]
        other_kids = [r for r in refs if _ref_kid(r) != kid]
        other_folders = [f for f in fids if folders and f not in folders]
        is_shared = bool(other_kids) or bool(other_folders)
        if is_shared:
            other_folders = [f for f in fids if folders and f not in folders]
            new_fids = [f for f in fids if f not in folders]
            if not new_fids:
                new_fids = [fids[0]] if fids else []
            new_refs = [r for r in refs if r != kid]
            if verbose:
                print(f'  共享 {slug}: folder_ids {fids}->{new_fids}, refs {refs}->{new_refs}')
            if not dry_run:
                args = {'kb_id': kb_id, 'slug': slug, 'folder_ids': new_fids, 'folder_id': new_fids[0] if new_fids else folder_id, 'source_refs': new_refs}
                r = wr.update_page(**args)
                if r.get('isError') or 'error' in r:
                    print(f'  ⚠️ 摘除失败 {slug}: {str(r)[:150]}')
                time.sleep(0.2)
            shared.append(slug)
        else:
            if verbose:
                print(f'  专属 {slug} -> delete')
            if not dry_run:
                r = wr.delete_page(**{'kb_id': kb_id, 'slug': slug})
                if r.get('isError') or 'error' in r:
                    print(f'  ⚠️ 删除失败 {slug}: {str(r)[:150]}')
                time.sleep(0.2)
            deleted.append(slug)
    if verbose:
        print(f'[{file_label}] 删除专属页 {len(deleted)}, 共享页摘关联 {len(shared)}')
    return (deleted, shared)
if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import weknora_rpc as wr
    wr.load_config()
    args = sys.argv[1:]
    if len(args) < 3:
        print('Usage: python clear_file_pages.py <kb_id> <kid> <folder_id> [file_label] [--dry-run] [--all-folders <csv>]')
        sys.exit(1)
    dry = '--dry-run' in args
    args = [a for a in args if a not in ('--dry-run',)]
    kb_id, kid, folder_id = (args[0], args[1], args[2])
    label = args[3] if len(args) > 3 and (not args[3].startswith('--')) else kid[:8]
    all_folders = []
    for i, a in enumerate(args):
        if a == '--all-folders' and i + 1 < len(args):
            all_folders = [x.strip() for x in args[i + 1].split(',') if x.strip()]
            break
    wr.KB = kb_id
    wr.mcp_init()
    clear_file_pages(wr, kb_id, kid, folder_id, label, dry_run=dry, all_folders=all_folders)