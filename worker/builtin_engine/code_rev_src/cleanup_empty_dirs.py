#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理供管库空目录（depth>=2，无页面、无子目录）
这些是多次重建产生的残留空壳目录。
"""
import sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

KB = wr.load_kb()
wr.KB = KB


def get_empty_dirs(kb_id=None):
    """获取所有空目录（depth>=2，无页面、无子目录）"""
    if kb_id is None:
        kb_id = wr.KB
    import subprocess
    sql = """
    WITH empty_dirs AS (
      SELECT f.id, f.name, f.parent_id, f.path, f.depth
      FROM wiki_folders f
      LEFT JOIN wiki_page_folders v ON v.folder_id = f.id
      LEFT JOIN wiki_folders c ON c.parent_id = f.id AND c.deleted_at IS NULL
      WHERE f.knowledge_base_id = '""" + kb_id + """'
        AND f.deleted_at IS NULL
        AND f.depth >= 2
        AND v.folder_id IS NULL
        AND c.id IS NULL
    )
    SELECT id, name, parent_id, path, depth
    FROM empty_dirs
    ORDER BY depth DESC, path
    """
    r = subprocess.run(
        ["docker", "exec", "WeKnora-postgres", "psql", "-U", "weknora", "-d", "weknora", "-t", "-A", "-F", "|", "-c", sql],
        capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        print(f"SQL error: {r.stderr}")
        return []
    
    dirs = []
    for line in r.stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|')
        if len(parts) >= 5:
            dirs.append({
                'id': parts[0],
                'name': parts[1],
                'parent_id': parts[2],
                'path': parts[3],
                'depth': int(parts[4])
            })
    return dirs


def delete_dir(folder_id, retries=2):
    """删除单个空目录，带重试"""
    for attempt in range(retries + 1):
        try:
            r = wr.delete_folder(folder_id)
            if isinstance(r, dict) and r.get('success'):
                return True, 'ok'
            return False, str(r)
        except Exception as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return False, f"{type(e).__name__}: {e}"
    return False, 'unknown'


def main():
    print("扫描空目录...")
    empty_dirs = get_empty_dirs()
    print(f"找到 {len(empty_dirs)} 个空目录")
    
    if not empty_dirs:
        print("无需清理")
        return
    
    success = 0
    failed = 0
    failed_list = []
    
    for i, d in enumerate(empty_dirs):
        ok, msg = delete_dir(d['id'])
        if ok:
            success += 1
        else:
            failed += 1
            failed_list.append((d['path'], msg))
            if failed <= 10:
                print(f"  [{i+1}/{len(empty_dirs)}] FAIL {d['path']}: {msg}")
        
        if (i + 1) % 20 == 0:
            print(f"  进度: {i+1}/{len(empty_dirs)} (ok={success}, fail={failed})")
            time.sleep(1)
    
    print(f"\n清理完成: 成功 {success}, 失败 {failed}, 总计 {len(empty_dirs)}")
    if failed_list:
        print(f"\n失败列表 (前20):")
        for path, msg in failed_list[:20]:
            print(f"  {path}: {msg}")


if __name__ == '__main__':
    main()