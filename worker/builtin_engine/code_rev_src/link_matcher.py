#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原文关联匹配器：实体卡原文摘录/规则明细 ↔ 长句页 slug 匹配。
实测 2026-08-12 三次修复沉淀（见 references/extraction-rules.md 原文关联匹配节）：
  1. 引号归一化：全角/英文引号全部删除
  2. 多级核心片段：前 20/15/10 字任一命中即匹配
  3. 逐明细条目匹配，全部列出（不止第一条）

Usage:
    from link_matcher import build_matcher, match_all
    matcher = build_matcher(ls_list)   # ls_list=[{'slug','text','title'},...]
    links = matcher.match_all('采购需求应按照公司规定的审批流程进行审批')
"""
import re


def norm(t):
    """归一化：去 markdown 标记、删除所有引号（全角/英文）、去空格与书名号。"""
    t = re.sub(r'[*#>]', '', t)
    t = t.replace('“', '').replace('”', '').replace('‘', '').replace('’', '')
    t = t.replace('"', '').replace("'", '')
    t = re.sub(r'[《》]', '', t)
    return re.sub(r'\s+', '', t)


class LongSentenceMatcher:
    def __init__(self, ls_list):
        """ls_list: [{'slug','text','title'}, ...] 本文件全部长句页"""
        self._items = [{'slug': i['slug'], 'title': i['title'],
                        'norm': norm(i['text'])} for i in ls_list]

    def match_all(self, excerpt, limit=3):
        """返回 [[longsentence/xxx|标题]] 链接列表（最多 limit 条）。"""
        ne = norm(excerpt)
        if len(ne) < 10:
            return []
        hits = []
        for it in self._items:
            lt = it['norm']
            # 多级核心片段：前 20/15/10 字任一命中即匹配
            matched = any(ne[:k] in lt or lt[:k] in ne for k in (20, 15, 10))
            if matched:
                hits.append(f'[[{it["slug"]}|{it["title"]}]]')
                if len(hits) >= limit:
                    break
        return hits

    def match_first(self, excerpt):
        hits = self.match_all(excerpt, limit=1)
        return hits[0] if hits else None


def build_matcher(ls_list):
    return LongSentenceMatcher(ls_list)


if __name__ == '__main__':
    demo = [{'slug': 'longsentence/x-abc', 'text': '纳入"三重一大"管理范畴的项目，应严格按照"三重一大"确定的流程要求进行集体决策。',
             'title': '纳入"三重一大"管理范畴的项目，应严格按照"三重一大"确定的流程要求'}]
    m = build_matcher(demo)
    print(m.match_all('纳入三重一大管理范畴的项目，应严格按照三重一大确定的流程要求进行集体决策'))
