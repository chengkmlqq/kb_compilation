#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务 slug 生成器（2026-08-19 去 pypinyin 依赖，slug 改用 md5）：
双标识体系 —— 存储用 ASCII slug（md5 前缀），展示用中文 title/业务标识。
slug 格式：<family_code(md5[:10])>-<版本代码>-<类型代码>[-<章节代码>][-<条款代码>][-<topic_code(md5[:10])|序号>]

用法：
    python3 slugger.py --test   # 自测
    或 import slugger; slugger.business_slug(...)

唯一性：实体=章节+类别内唯一；规则=RO编号全局唯一；长句=同条款内序号递增；版本=版本代码区分。
"""
import hashlib
import re
from typing import Optional

# 类型代码（存储）↔ 类型中文（展示）
TYPE_CODE = {
    'summary': '摘要',
    'entity-b': '业务实体',
    'entity-r': '规则实体',
    'ls': '长句原文',
    'kw': '高频关键词',
}
TYPE_CN_TO_CODE = {v: k for k, v in TYPE_CODE.items()}

# 需过滤的常见噪音词（法规名/实体名转全拼前；市场监督域：法律全称前缀等）
NOISE = ['中华人民共和国']


def clean_zh(s: str) -> str:
    """去公司名/噪音词，保留制度名/实体名主体。"""
    t = s
    for nz in NOISE:
        t = t.replace(nz, '')
    t = re.sub(r'[（(]V[\d.]+[)）]', '', t)
    t = re.sub(r'[\d.]+年?$', '', t)
    t = re.sub(r'^\d+[\.、．]?\s*', '', t)
    return t.strip(' ：《》')


def family_code(family_cn: str) -> str:
    """家族根目录中文名 → 稳定代码（md5[:10]，2026-08-19 去 pypinyin 依赖）。
    family_cn: '中国移动通信集团上海有限公司集中采购需求管理办法' → 'a1b2c3d4e5'"""
    return hashlib.md5(clean_zh(family_cn).encode('utf-8')).hexdigest()[:10]


def version_code(version_cn: str) -> str:
    """版本标识 → 版本代码（点转-，空格去）。'B1 18' → 'B1-18'；'V2.0' → 'V2-0'；'2025版' → '2025'"""
    v = version_cn.replace('版', '').strip()
    v = re.sub(r'\s+', '-', v)
    v = v.replace('.', '-')
    return v


CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
          '十': 10, '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15, '十六': 16,
          '十七': 17, '十八': 18, '十九': 19, '二十': 20, '二十一': 21, '二十二': 22,
          '二十三': 23, '二十四': 24, '二十五': 25, '二十六': 26, '二十七': 27,
          '二十八': 28, '二十九': 29, '三十': 30}


def _cn_to_int(s: str) -> Optional[int]:
    """中文数字 → int（支持 一~三十 及阿拉伯数字）。"""
    if s.isdigit():
        return int(s)
    return CN_NUM.get(s)


def chapter_code(ch: str) -> str:
    """章节号 → c<两位>。'第二章' → 'c02'；'2' → 'c02'"""
    m = re.search(r'([\d一二三四五六七八九十]+)', ch)
    n = _cn_to_int(m.group(1)) if m else None
    return f'c{n:02d}' if n else ''


def clause_code(cl: str) -> str:
    """条款号 → t<数字>。'第五条' → 't05'；'5' → 't05'"""
    m = re.search(r'([\d一二三四五六七八九十]+)', cl)
    n = _cn_to_int(m.group(1)) if m else None
    return f't{n:02d}' if n else ''


def topic_code(topic_cn: str) -> str:
    """实体名/关键词/规则名 → 稳定代码（md5[:10]）。'规模需求' → 'b2c3d4e5f6'"""
    return hashlib.md5(topic_cn.encode('utf-8')).hexdigest()[:10]


# ============ 跨文档实体归一化（2026-08-15 P1 新增）============

_CN_DIGITS = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
              '六': 6, '七': 7, '八': 8, '九': 9}
_CN_TENS = {'十': 10, '百': 100}


def _cn_num_to_int(s: str) -> int:
    """中文数字串 → int（支持 零~九十九）。'十二'→12 '二十'→20 '一百'→100"""
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_TENS:
            cur = max(cur, 1) * _CN_TENS[ch]
            total += cur
            cur = 0
        elif ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
    return total + cur


def canonicalize(name: str) -> str:
    """实体规范名（跨文档判重键）。

    规则：
    1. 去首尾空白、折叠内部空白
    2. 剥离括号注释（保留主体）：'需求部门（采购）' → '需求部门'
    3. NFKC 归一（全角→半角，兼容标点/数字/字母）
    4. 中文数字归一：'实体1'/'实体一'/'实体壹' → 同一 canonical（'壹'→'一'→1 数字）
    5. 去书名号/引号/常见连接标点
    """
    import unicodedata
    if not name:
        return ''
    s = re.sub(r'\s+', '', name)
    s = re.sub(r'[（(][^）)]*[)）]', '', s)          # 括号注释剥离
    s = unicodedata.normalize('NFKC', s)
    # 大写中文数字（壹贰叁…）→ 小写（几乎必然是数字语义），再统一转阿拉伯
    s = s.translate(str.maketrans('壹贰叁肆伍陆柒捌玖拾', '一二三四五六七八九十'))
    # 中文数字 → 阿拉伯（仅词尾整段数字，如 '实体一'→'实体1'、'十二'→'12'；
    #   不转换词首/词中数字——'三重一大'的'三''一'、'一次性'的'一'是语义不是序号）
    def _num_repl(m):
        try:
            return str(_cn_num_to_int(m.group(0)))
        except Exception:
            return m.group(0)
    s = re.sub(r'[零一二两三四五六七八九十百]+$', _num_repl, s)
    s = re.sub(r'[《》“”‘’·、，。；：:，]', '', s)   # 书名号/引号/标点
    return s


def entity_slug(ontology: str, cname: str) -> str:
    """全局稳定实体 slug（去文档化，跨文档归一）。

    - 业务本体：entity-b-<md5(ontology|canonical)[:10]>
    - 规则本体：entity-r-ro<编号>-<md5(canonical)[:10]>（ontology 形如 'RO001'）
    同一规范名在任何文档抽取 → 同一 slug，天然支持跨文档合并。
    章节/条款信息不再编码进 slug（移入页面'来源制度'表）。
    """
    h = hashlib.md5(f"{ontology}|{cname}".encode('utf-8')).hexdigest()[:10]
    if ontology.startswith('RO') and ontology[2:].isdigit():
        return f"entity-r-ro{ontology[2:].lower()}-{h}"
    return f"entity-b-{h}"


def legacy_entity_slug(family_cn: str, version_cn: str, page_type: str,
                       chapter: str = '', clause: str = '', topic: str = '') -> str:
    """旧版实体 slug（带文档标识）——仅存量迁移/兼容用，新构建一律用 entity_slug。"""
    return business_slug(family_cn, version_cn, page_type, chapter, clause, topic)


def business_slug(family_cn: str, version_cn: str, page_type: str,
                  chapter: str = '', clause: str = '', topic: str = '',
                  seq: Optional[int] = None) -> str:
    """生成业务 slug（英文全拼存储）。

    Args:
        family_cn: 家族根目录中文名（含公司名）
        version_cn: 版本目录中文名（如 'B1 18版' / 'V2.0版' / '2025版'）
        page_type: summary / entity-b / entity-r / ls / kw
        chapter: 章节（'第二章'），可空
        clause: 条款（'第五条'），可空
        topic: 实体名/关键词/规则名（中文），可空
        seq: 长句序号（同条款内），可空
    """
    parts = [family_code(family_cn), version_code(version_cn), page_type]
    if page_type == 'entity-r':
        # 规则实体：RO编号放 topic 前（topic 形如 'RO001-金额条件'）
        if topic:
            parts.append(topic_code(topic.replace('-', '')))
    else:
        if chapter:
            parts.append(chapter_code(chapter))
        if clause:
            parts.append(clause_code(clause))
        if topic:
            parts.append(topic_code(topic))
        elif seq is not None:
            parts.append(f'{seq:02d}')
    return '-'.join(parts)


def display_id(family_cn: str, version_cn: str, page_type: str,
               chapter: str = '', clause: str = '', topic: str = '',
               ro_no: str = '', seq: Optional[int] = None) -> str:
    """展示标识（中文）：'集中采购需求管理办法 / B1-18版 / 业务实体 / 第二章 / 规模需求'"""
    type_cn = TYPE_CODE.get(page_type, page_type)
    parts = [family_cn, version_cn, type_cn]
    if page_type == 'entity-r' and ro_no:
        parts.append(ro_no)
    if chapter:
        parts.append(chapter)
    if clause:
        parts.append(clause)
    if topic:
        parts.append(topic)
    elif seq is not None:
        parts.append(f'第{seq}句')
    return ' / '.join(parts)


def display_title(page_type: str, topic: str = '', family_cn: str = '',
                  version_cn: str = '', chapter: str = '', clause: str = '',
                  ro_no: str = '') -> str:
    """展示 title（**不带类型前缀**，2026-08-13 修正）：类型只在 slug 和页面业务标识行体现。
    - 摘要：'<制度名> <版本>'
    - 实体：'<实体名>（<制度> <版本> <章>）' 或仅 '<实体名>'
    - 规则：'<规则名>（RO-001）'
    - 长句/关键词：'<句首/词>'"""
    head = topic if topic else ''
    ctx = []
    if ro_no:
        ctx.append(ro_no)
    if family_cn:
        ctx.append(clean_zh(family_cn))
    if version_cn:
        ctx.append(version_cn)
    if chapter:
        ctx.append(chapter)
    if clause:
        ctx.append(clause)
    if ctx:
        head += f'（{" ".join(ctx)}）'
    return head


if __name__ == '__main__':
    # 自测
    fam = '中国移动通信集团上海有限公司集中采购需求管理办法'
    print('family:', family_code(fam))
    print('summary slug:', business_slug(fam, 'B1 18版', 'summary'))
    print('entity slug:', business_slug(fam, 'B1 18版', 'entity-b', '第二章', '', '规模需求'))
    print('entity display:', display_id(fam, 'B1 18版', 'entity-b', '第二章', '', '规模需求'))
    print('entity title:', display_title('entity-b', '规模需求', fam, 'B1 18版', '第二章'))
    print('rule slug:', business_slug(fam, 'B1 18版', 'entity-r', topic='RO001-金额条件'))
    print('rule display:', display_id(fam, 'B1 18版', 'entity-r', ro_no='RO-001', topic='金额条件'))
    print('ls slug:', business_slug(fam, 'B1 18版', 'ls', '第三章', '第五条', seq=1))
    print('kw slug:', business_slug(fam, 'B1 18版', 'kw', topic='标的物'))
