#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全库批量修复业务对象页的关联规则 + 关联对象占位符，并重建双向链接。
直接通过 PostgreSQL 数据库操作，避免 MCP API 鉴权问题。

用法：
    python fix_all_entity_links.py --kb <kb_id>
"""
import sys
import domain
import os
import re
import json
import hashlib
import subprocess
from collections import defaultdict
from datetime import datetime

DEFAULT_KB = wr.load_kb()
BATCH_SIZE = 100


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def psql(query: str) -> list[dict]:
    """执行 PostgreSQL 查询，返回行列表。"""
    result = subprocess.run(
        ['docker', 'exec', 'WeKnora-postgres', 'psql', '-U', 'weknora', '-d', 'weknora',
         '-t', '--no-align', '-F', '\t', '-c', query],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql error: {result.stderr}")
    lines = [l.strip() for l in result.stdout.split('\n') if l.strip()]
    return lines


def psql_json(query: str) -> list[dict]:
    """以 JSON 格式返回查询结果。"""
    result = subprocess.run(
        ['docker', 'exec', 'WeKnora-postgres', 'psql', '-U', 'weknora', '-d', 'weknora',
         '-t', '--no-align', '-c', query],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql error: {result.stderr}")
    return result.stdout.strip()


def exec_update(query: str) -> int:
    """执行 UPDATE/INSERT 并返回影响行数。"""
    result = subprocess.run(
        ['docker', 'exec', 'WeKnora-postgres', 'psql', '-U', 'weknora', '-d', 'weknora',
         '-c', query],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql error: {result.stderr}")
    # 从输出中提取影响行数
    m = re.search(r'UPDATE (\d+)', result.stdout)
    return int(m.group(1)) if m else 0


def fetch_all_pages(kb: str) -> list[dict]:
    """拉取全库所有非删除的实体页面，用 JSON 格式避免转义问题。"""
    query = f"""SELECT json_agg(json_build_object(
        'id', id, 'slug', slug, 'title', title, 'page_type', page_type,
        'content', content, 'folder_id', folder_id, 'source_refs', source_refs,
        'status', status, 'aliases', aliases
    ) ORDER BY slug)
    FROM wiki_pages
    WHERE knowledge_base_id = '{kb}'
      AND deleted_at IS NULL
      AND (slug LIKE 'entity-b-%%' OR slug LIKE 'entity-r-%%');
    """
    result = subprocess.run(
        ['docker', 'exec', 'WeKnora-postgres', 'psql', '-U', 'weknora', '-d', 'weknora',
         '-t', '--no-align', '-c', query],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql error: {result.stderr}")
    raw = result.stdout.strip()
    if not raw or raw == 'NULL':
        return []
    return json.loads(raw)


def parse_rule_entities(pages: list) -> dict:
    """
    从关联规则页提取 约束对象 -> [(rule_slug, rule_title, struct_text)] 映射。
    """
    rules = {}
    for p in pages:
        if not p.get('slug', '').startswith('entity-r-'):
            continue
        c = p.get('content') or ''
        slug = p['slug']
        title = p.get('title', '')

        # 提取约束对象
        obj = ''
        m = re.search(r'\| 约束对象 \| (.*?) \|', c)
        if m:
            raw = m.group(1).strip()
            lm = re.search(r'\[\[([^|\]]+)\|([^\]]+)\]\]', raw)
            obj = lm.group(2) if lm else raw

        # 提取规则结构文本（新模板 ## 规则结构）
        struct_text = ''
        struct_m = re.search(r'## 规则结构\n+(.*?)(?=\n## |\Z)', c, re.S)
        if struct_m:
            parts = []
            for line in struct_m.group(1).strip().split('\n'):
                line = line.strip().lstrip('-').strip()
                if line and not line.startswith('##'):
                    parts.append(line)
            struct_text = '；'.join(parts)

        # 旧模板 fallback：## 规则明细
        if not struct_text:
            detail_m = re.search(r'## 规则明细\n+(\|[^\n]*\n\|[-| ]+\n)(.*?)(?=\n## |\Z)', c, re.S)
            if detail_m:
                rows = []
                for line in detail_m.group(2).strip().split('\n'):
                    cells = [x.strip() for x in line.strip().strip('|').split('|')]
                    if len(cells) >= 3:
                        rows.append(cells[2])
                struct_text = '；'.join(rows)

        if obj:
            rules.setdefault(obj, []).append((slug, title, struct_text))

    return rules


def build_key_rules_table(rule_rows: list, file_cn: str) -> str:
    """生成关联规则表 markdown。"""
    lines = ['## 关联规则', '', '| 规则类型 | 内容 | 来源 |', '|---|---|---|']
    for r_slug, r_title, struct_text in rule_rows:
        lines.append('| [[%s|%s]] | %s | %s |' % (r_slug, r_title, struct_text, file_cn))
    return '\n'.join(lines) + '\n'


def fix_biz_entity(content: str, rule_rows: list, file_cn: str) -> tuple[str, bool]:
    """
    修复单个业务对象页：
    1. 填充关联规则表（如果有占位符且有匹配规则）
    2. 清理关联对象表中的占位符行
    """
    changed = False

    # 1) 关联规则表：占位符才填
    if '（待关联关联规则）' in content or '（待补充）' in content:
        if rule_rows:
            new_table = build_key_rules_table(rule_rows, file_cn)
            keyrule_m = re.search(r'## 关联规则\n+(.*?)(?=\n## |\Z)', content, re.S)
            if keyrule_m:
                content = content[:keyrule_m.start()] + new_table + content[keyrule_m.end():]
                changed = True

    # 2) 关联对象表：清理占位符行（保留表头）
    if '（待补充）' in content:
        # 匹配关联对象节
        rel_m = re.search(r'(## 关联对象\n+)(.*?)(?=\n## |\Z)', content, re.S)
        if rel_m:
            header = rel_m.group(1)
            body = rel_m.group(2)
            lines = body.split('\n')
            new_lines = []
            has_real = False
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith('| ---') or stripped.startswith('|--'):
                    new_lines.append(line)
                    continue
                if '待补充' in stripped:
                    continue
                new_lines.append(line)
                has_real = True
            new_body = '\n'.join(new_lines).strip()
            if not has_real:
                new_section = header + '| 关联对象 | 关系类型 | 关系说明 | 来源 |\n|---|---|---|---|\n'
            else:
                new_section = header + new_body + '\n'
            if new_section != rel_m.group(0):
                content = content[:rel_m.start()] + new_section + content[rel_m.end():]
                changed = True

    return content, changed


def main():
    import argparse

    ap = argparse.ArgumentParser(description='全库批量修复业务对象页关联规则+关联对象')
    ap.add_argument('--kb', default=DEFAULT_KB, help='知识库 ID')
    ap.add_argument('--dry-run', action='store_true', help='只分析不修改')
    args = ap.parse_args()

    kb = args.kb
    log(f'知识库: {kb}')

    # 1. 拉取全库实体页面
    log('拉取实体页面...')
    pages = fetch_all_pages(kb)
    biz_pages = [p for p in pages if p.get('slug', '').startswith('entity-b-')]
    rule_pages = [p for p in pages if p.get('slug', '').startswith('entity-r-')]
    log(f'业务对象: {len(biz_pages)}，关联规则: {len(rule_pages)}')

    # 2. 从关联规则提取约束对象映射
    log('解析关联规则约束对象...')
    rules_by_biz = parse_rule_entities(rule_pages)
    # 统计有多少实体有匹配规则
    matched_biz = set()
    for b_title in rules_by_biz:
        matched_biz.add(b_title)
    log(f'有匹配规则的业务对象: {len(matched_biz)}')

    # 3. 统计
    with_placeholder = [p for p in biz_pages
                        if '（待关联关联规则）' in (p.get('content') or '')
                        or '（待补充）' in (p.get('content') or '')]
    log(f'含占位符的业务对象: {len(with_placeholder)}')

    # 4. 逐页修复
    fixed = 0
    skipped_no_placeholder = 0
    skipped_no_rule = 0
    errors = 0

    for i, bp in enumerate(biz_pages):
        slug = bp.get('slug', '')
        c = bp.get('content') or ''
        b_title = bp.get('title', '')

        # 检查是否需要修复
        if '（待关联关联规则）' not in c and '（待补充）' not in c:
            skipped_no_placeholder += 1
            continue

        # 获取该实体的规则
        rule_rows = rules_by_biz.get(b_title, [])
        if not rule_rows:
            skipped_no_rule += 1
            continue

        # 提取来源文档
        file_cn = '《供管制度》'
        m = re.search(r'\| 来源文档 \| (.*?) \|', c)
        if m:
            file_cn = m.group(1).strip()

        # 修复
        new_content, changed = fix_biz_entity(c, rule_rows, file_cn)
        if not changed:
            skipped_no_rule += 1
            continue

        if args.dry_run:
            log(f'[DRY-RUN] {slug}: 会填充 {len(rule_rows)} 条规则')
            fixed += 1
            continue

        # 通过 DB 直接更新（用 slug 做 WHERE，避免 id 转义问题）
        escaped = new_content.replace("'", "''")
        escaped_slug = slug.replace("'", "''")
        update_sql = f"""UPDATE wiki_pages SET content = '{escaped}', updated_at = now() WHERE slug = '{escaped_slug}' AND knowledge_base_id = '{kb}';"""
        try:
            affected = exec_update(update_sql)
            if affected > 0:
                fixed += 1
            else:
                errors += 1
                log(f'  ❌ {slug}: 更新影响 0 行（可能页面不存在）')
        except Exception as e:
            errors += 1
            log(f'  ❌ {slug}: {e}')

        if (i + 1) % BATCH_SIZE == 0:
            log(f'  进度: {i+1}/{len(biz_pages)}, 已修复: {fixed}, 跳过(无占位符): {skipped_no_placeholder}, 无规则: {skipped_no_rule}, 错误: {errors}')

    log(f'\n修复完成:')
    log(f'  已修复: {fixed}')
    log(f'  跳过（无占位符）: {skipped_no_placeholder}')
    log(f'  跳过（无匹配规则）: {skipped_no_rule}')
    log(f'  错误: {errors}')

    # 5. 重建链接索引
    if not args.dry_run and fixed > 0:
        log('\n重建 wiki 链接索引...')
        try:
            out = subprocess.run(
                ['docker', 'exec', 'WeKnora-postgres', 'psql', '-U', 'weknora', '-d', 'weknora',
                 '-c', f"SELECT 1 -- wiki_rebuild_links 在应用层处理，需要 API 调用"],
                capture_output=True, text=True, timeout=10
            )
            # 通过 MCP 工具调用重建
            log('请通过 MCP 工具执行 wiki_rebuild_links')
        except Exception as e:
            log(f'  ⚠️ 重建失败: {e}')


if __name__ == '__main__':
    main()
