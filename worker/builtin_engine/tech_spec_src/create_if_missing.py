#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""幂等建页模板（create_if_missing）：create 前先 read_page 查 slug，存在即跳过。
根因（2026-08-13 确认）：500 = wiki_pages.idx_wiki_pages_kb_slug 唯一约束冲突（23505）。
先查再建从源头消除 23505，且天然幂等（断点续跑安全）。"""

import time
import domain


def create_if_missing(wr, slug, title, content, folder_id, page_type=None,
                      summary='', source_refs=None, check_first=True):
    """幂等建页。check_first=True 时先 read_page 查 slug：
    - 已存在 → 直接返回（不 create，避免 23505 唯一约束冲突）
    - 不存在 → create；若 create 仍报 500 → 再 read_page 验证（存在即成功，不重试）

    page_type 必传：知识对象用 'business_ontology'，规范要求用 'rule_ontology'，
    长句用 'original_sentence'，关键词用 'concept'，摘要用 'summary'。
    """
    if page_type is None:
        raise ValueError("page_type is required for create_if_missing. Use 'business_ontology' for entity-b, 'rule_ontology' for entity-r.")
    if check_first:
        rr = wr.read_page(slug)
        if 'error' not in rr:
            return rr  # slug 已存在，跳过（幂等）

    r = wr.create_page(slug, title, content, folder_id, page_type=page_type,
                       summary=summary, source_refs=source_refs)
    if 'error' not in r and not r.get('isError') and r.get('success', True):
        return r

    # create 报错（500=唯一约束冲突/网络异常）→ read_page 验证存在性
    rr = wr.read_page(slug)
    if 'error' not in rr:
        return rr  # 实际已创建（23505 或首次成功响应丢失）
    # 真的不存在且 create 失败 → 重试一次
    time.sleep(2)
    r = wr.create_page(slug, title, content, folder_id, page_type=page_type,
                       summary=summary, source_refs=source_refs)
    if 'error' not in r and not r.get('isError') and r.get('success', True):
        return r
    return {'error': 'create failed', 'resp': str(r)[:200]}
