#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长句页模板结构门禁（2026-08-28 新增）。

校验 page_type=original_sentence 页面的 content 是否符合 v3 知识单元模板：
    # <标题>

    <汇总描述（多句单元必须有，单句可省）>

    | 序号 | 原文 |
    |---|---|
    | 1 | <原文句1，逐字，| 已转义为 \\|> |
    ...

校验项（error 级必须修）：
  1. 表头存在：`| 序号 | 原文 |` + 分隔行 `|---|---|`
  2. 表格完好：每行竖线数 = 4（行首 + 序号 + 原文 + 行尾），多出 = 未转义 | 破表
  3. 序号连续：1..N 无跳号无重复
  4. summary 存在：多句单元（表格 >1 行）必须有汇总描述段
warning 级（建议）：
  5. title 长度 >30 或含标点
  6. content 为空

用法：
  python check_ls_template.py [--kb <kb_id>] [--slug <slug>] [--limit N]
                              [--dry-run] [--fix] [-v]
  --fix     仅做安全修复：旧格式（v2 平铺 / v1 待补）页面 → 新表格格式转换
  --dry-run 只报告不写库（默认即只报告；--fix 需显式开启）
"""
import json
import re
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

HEADER = '| 序号 | 原文 |'
TITLE_RE = re.compile(r'^#\s+(.+)$')


def check_title(title):
    """warning 级：title 长度/标点。"""
    problems = []
    if not title:
        return ['title 为空']
    if len(title) > 30:
        problems.append(f'title 过长（{len(title)}字 > 30）')
    if re.search(r'[。！？；，、：“”‘’《》〈〉|]', title):
        problems.append('title 含标点')
    return problems


def _protect_wikilinks(row):
    """把 [[...]] 整体保护（内部 | 换占位 \x01）。
    不能正则 [[[^\]]+\]\]：wikilink 文本可能含单 ]（如 [2015]103号），
    正则会被内部单 ] 截断 → 误判（2026-08-28 实测）。用配对查找 ']]'。"""
    out = []
    i = 0
    while i < len(row):
        if row.startswith('[[', i):
            j = row.find(']]', i + 2)
            if j > 0:
                seg = row[i:j + 2]
                out.append(seg.replace('|', '\x01'))
                i = j + 2
                continue
        out.append(row[i])
        i += 1
    return ''.join(out)


def _split_table_cells(row):
    """拆表格行。返回 (ok, num, cell_texts)：
    ok=False 表示竖线数异常（未转义 | 破表）；num=序号（None 解析失败）。
    保护两类非分隔竖线：① 转义竖线 \\|（占位 \\x00）；② wikilink 内部竖线
    [[slug|text]]（占位 \\x01，坑 25/55：表格解析不能直接 split('|')）。"""
    if not row.startswith('|'):
        return (False, None, [])
    protected = _protect_wikilinks(row)
    protected = protected.replace('\\|', '\x00')
    parts = protected.split('|')
    # 期望：['', ' 序号 ', ' 原文 ', ''] → 4 段；含未转义 | 会 > 4
    if len(parts) != 4:
        return (False, None, parts)
    try:
        num = int(parts[1].strip())
    except ValueError:
        num = None
    return (True, num, parts)


def check_content(slug, content, page_type='original_sentence'):
    """按页面类型分发模板校验。返回 problems 列表。"""
    if page_type == 'frequent_keyword':
        return _check_kw_content(slug, content)
    if page_type in ('business_ontology', 'rule_ontology'):
        return _check_entity_content(page_type, content)
    if page_type == 'summary':
        return _check_summary_content(content)
    if page_type == 'index':
        return _check_index_content(content)
    return _check_ls_content(slug, content)


# 各 page_type 的必需节（2026-08-28：所有模板移除「可回答问题」）
REQUIRED_SECTIONS = {
    'business_ontology': ['## 基本信息', '## 关键规则', '## 关联实体', '## 原文关联'],
    'rule_ontology': ['## 基本信息', '## 规则结构', '## 原文关联'],
    'summary': ['## 基本信息', '## 法规定位', '## 重点内容概览', '## 与其他法律法规的关系'],
}

# 纯通用词表（2026-08-29：关键词门禁用，与 filter_terms_llm.py COMMON_WORDS 同步。
# 2026-09-04：市场监督域化——去公司专名、补法规文种通用词；改动须两边同步）
# 只剔纯通用词——无业务含义、可出现在任何文档；不碰业务词（药品/医疗器械/经营者/行政处罚等）
COMMON_WORDS = set(
    '确定 明确 完善 规范 加强 开展 落实 推进 建立 健全 强化 提高 促进 实现 '
    '做好 抓好 严格 认真 积极 深入 全面 充分 不断 进一步 有关 相关 主要 '
    '重要 具体 有效 合理 科学 统一 直接 其他 各类 各种 部分 相应 一定 必要 '
    '特此 年月日 本细则 本通知 本办法 本实施细则 有关要求 相关要求 具体规定 '
    '遵照 印发 纳入 引入 专业 市场 决策 管理制度 业务 制度 '
    '条例 办法 规定 细则 规章 法规 法律 施行 修订 公布 实施 废止 之日起 '
    '使用 完成 增加 提交 支撑 负责 调整 变更 合作 计划 组织 监督 评审 审批 '
    '领导 框架 份额 媒介 级别 层级 分级 办公 电子 技术 程序 配置 预研 集体 '
    '集中 管理办法 实施细则 管理规定 相关管理 相关规定 工作计划 达到或超过 '
    '经营管理 负责部门'.split()
)


def _check_entity_content(page_type, content):
    """实体页（business_ontology/rule_ontology）模板校验：
    必需节齐全、无「可回答问题」残留、基本信息表存在。
    rule_ontology 加强（2026-09-02）：必须含原文关联（规则来自长句，evidence 原文片段匹配，无关联=违规）。"""
    problems = []
    if not content or not content.strip():
        return ['content 为空']
    for sec in REQUIRED_SECTIONS.get(page_type, []):
        if sec not in content:
            problems.append(f'缺必需节: {sec}')
    if '## 可回答问题' in content:
        problems.append('含「可回答问题」残留（2026-08-28 已从所有模板移除）')
    if '| 字段 | 内容 |' not in content and '| 规则类别 |' not in content:
        problems.append('缺基本信息表（| 字段 | 内容 |）')
    if page_type == 'rule_ontology':
        # 规则本体必须有关联原文长句（2026-09-02：规则 title 改为 LLM 概括，不再校验 title 逐字原文；
        # 改为校验页面是否包含 longsentence 链接，无链接=无依据规则卡）
        ls_links = re.findall(r'\[\[longsentence/', content)
        if not ls_links:
            problems.append('无原文关联长句链接（规则本体必须有关联原文，禁止无依据规则卡）')
    if page_type == 'business_ontology':
        # 业务实体关联实体表必须非占位（2026-08-28 用户要求：关联由规则本体驱动）
        rel_section = re.search(r'## 关联实体\s*\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
        if rel_section and '（待补充）' in rel_section.group(1):
            problems.append('关联实体表含占位符（（待补充）——业务实体间关联应由规则本体驱动生成）')
    # 基本信息表「来源制度」禁止截断 UUID（2026-08-29 门禁：merge_duplicate_entities 曾把来源制度写成《2ddafef1》8位前缀，坑 63）
    if re.search(r'\|\s*来源制度\s*\|\s*《[0-9a-f]{8}》\s*\|', content):
        problems.append('基本信息表「来源制度」为截断 UUID（《8位hex》），须为完整文档名（merge_duplicate_entities 合并时拆分 source_refs 的 kid|文件名）')
    if page_type == 'business_ontology':
        # 业务实体禁止动作短语句式（2026-08-29 门禁：LLM 曾把「按照相关业务条线管理规定执行」等
        # 介词+动词短语当实体抽取，坑 81）。从 content 首行 # 标题提取实体名校验。
        m = re.match(r'#\s*(.+)', content)
        if m:
            title = m.group(1).strip()
            # 动作短语判定（2026-08-29 收紧，避免误伤短动词实体如「施行」「废止」）：
            # ① 介词引导的动词短语：介词开头 + 整体长度>=6（如「按照相关业务条线管理规定执行」「经本单位领导班子集体（会议）决策后施行」）
            # ② 动作动词结尾的长描述：长度>=6 且以动词结尾且不含名词性成分（如「传统框架式执行」）
            # 短词（<6 字）如「废止」「备案」「施行」「电商化执行」是合法业务事件/概念，不拦。
            # 2026-09-08 修复：单字介词「经/由」无词边界会误伤「经营异常名录」「经常居住地址」等
            # 「经」字开头名词（经营/经常/经费/经验/经过/经销/经纪/经典…）——加负向先行排除
            _prep = bool(re.match(r'^(按照|遵照|根据|依据|经(?!营|常|费|纪|济|典)|由(?!于|此|衷)|对于|将|不纳入)', title)) and len(title) >= 6
            _verby = len(title) >= 6 and bool(re.search(r'(执行|办理|施行|审议|印发|废止|开展|引入|归集|备案|处罚)$', title))
            _hasnoun = bool(re.search(r'(管理|办法|细则|制度|规范|规定|文件|事项|部门|单位|机关|机构|人员|执法|监管|许可|处罚|项目|流程|机制|清单|目录|方案|计划|产品|服务|工程|采购|决策|审批|备案|药品|食品|医疗|器械|化妆|特种|设备|计量|标准|专利|商标|广告|价格|竞争|垄断|消费者|经营者|网络|交易|质量|认证|检验|检测|义务|责任|期限|行政|法律|法规|条例|规章|登记|公示|信用)', title))
            if _prep or (_verby and not _hasnoun):
                problems.append(f'业务实体名为动作短语句式（「{title}」——实体必须是名词性概念，禁止介词引导动词短语/动作动词结尾）')
            # 指示定语修饰的衍生表达（2026-09-04 市场域：LLM 把「本法所称药品」「本规定所称医疗器械」
            # 等"指代定语+核心实体"当独立实体，核心实体是「药品」「医疗器械」——指代定语不是实体一部分）
            # 2026-09-08：title 恰等于指示词本身（「各级市场监督管理部门」=泛称执法主体）是合法实体
            # 被多规则约束对象引用——豁免；只拦「指示词+更长衍生」（如「各级市场监督管理部门应当…」动作句）
            _det_words = ('本法', '本规定', '本办法', '本细则', '本条例', '本部门', '本机关',
                          '本单位', '该部门', '该机关', '各级市场监督管理部门', '各级人民政府')
            if title not in _det_words and any(title.startswith(w) and len(title) > len(w) for w in _det_words):
                problems.append(f'业务实体名为指示定语衍生表达（「{title}」——「本法/本规定」等指代定语不是实体一部分，应提取去定语后的核心实体）')
            # 纯日期实体（2026-08-29 门禁：LLM 把「2025年9月1日」当业务事件——日期是事件/规则的属性值，
            # 不是业务实体；事件必须是"动作+对象"完整事件）
            if re.match(r'^\d{4}年\d{1,2}月\d{1,2}日$', title) or re.match(r'^\d{4}年$', title):
                problems.append(f'业务实体名为纯日期（「{title}」——日期是事件/规则的属性值（施行日期/印发日期），不单独成实体）')
    return problems


def _check_summary_content(content):
    """摘要页（summary）模板校验：必需节齐全、无「可回答问题」残留、无聊天残留。"""
    problems = []
    if not content or not content.strip():
        return ['content 为空']
    for sec in REQUIRED_SECTIONS.get('summary', []):
        if sec not in content:
            problems.append(f'缺必需节: {sec}')
    if '## 可回答问题' in content:
        problems.append('含「可回答问题」残留（2026-08-28 已从所有模板移除）')
    if re.search(r'😊|有什么(?:我可以|需要我)帮', content):
        problems.append('聊天残留（😊/有什么我可以帮…，LLM 输出未清洗，须用 wr.clean_llm_tail）')
    # 2026-08-29 用户要求：相关制度列库内无文档用纯文本《引用名》，禁止「知识库中暂无对应文档」字样
    if '知识库中暂无对应文档' in content:
        problems.append('关系表含「知识库中暂无对应文档」（2026-08-29 已废弃——库内无对应文档用纯文本《引用名》，不写此句）')
    return problems


def _check_index_content(content):
    """索引页（index/，page_type=concept，2026-08-30 起；原 topic_cluster）模板校验（2026-08-29 新增）：

    坑 82：update_family_index 曾因裸 split('|') 拆 wikilink 表行 + [:40] 截断，
    生成残缺链接 [[summary/32位hex：标题]]。门禁兜底：① 必需节；② 所有 wikilink
    slug 部分必须合法（^[a-z]+/[0-9a-f]{32}$）；③ 无维护提示残留；④ 文档清单表非空。
    """
    problems = []
    if not content or not content.strip():
        return ['content 为空']
    # ① 必需节（2026-08-31 兼容新旧两种格式）：
    #    - 演进模板（方案 B）：## 一、版本演进链（LLM 生成，含 ## 四、知识推导过程）
    #    - 旧骨架（回退）：## 文档清单 + ## 关系概述
    has_evolution = '## 一、版本演进链' in content
    has_legacy = ('## 文档清单' in content) and ('## 关系概述' in content)
    if not has_evolution and not has_legacy:
        problems.append('缺必需节（演进模板须含 ## 一、版本演进链；回退骨架须含 ## 文档清单 + ## 关系概述）')
    # ② wikilink slug 合法性（残缺链接检测：[[summary/88eab...46f：标题]] 的 slug 部分含冒号/长度不对）
    #    2026-08-31 修正：真实 slug 有 summary/<md5 32位> 和 longsentence/<10位hex>-<8位hex> 两种，
    #    旧正则 {32} 会把合法长句 slug（如 longsentence/ad67d009cd-49314780）误报残缺
    links = re.findall(r'\[\[([^\[\]]+?)\]\]', content)
    for l in links:
        slug_part = l.split('|', 1)[0]
        if not re.match(r'^[a-z]+/[0-9a-f]{32}$', slug_part) \
                and not re.match(r'^[a-z]+/[0-9a-f]{10}-[0-9a-f]{8}$', slug_part):
            problems.append(f'非法 wikilink slug（残缺链接，坑 82）: [[{l[:60]}]')
    # ③ 维护提示残留（2026-08-29 用户要求移除模板底部「索引页由构建流程自动维护，勿手工编辑。」）
    if '索引页由构建流程自动维护' in content or '勿手工编辑' in content:
        problems.append('含「索引页由构建流程自动维护，勿手工编辑」残留（2026-08-29 已从模板移除）')
    # ④ 文档清单表非空：`| 文档 | 类型 | 一句话定位 | 族内关系 |` 表头后应有数据行
    lines = content.split('\n')
    for i, ln in enumerate(lines):
        if ln.strip().startswith('| 文档 | 类型 |'):
            data_rows = [l for l in lines[i + 2:] if l.strip().startswith('|') and '---' not in l]
            if not data_rows:
                problems.append('文档清单表无数据行（索引页为空壳）')
            break
    # ⑤ 原文依据双链校验（2026-08-31 用户要求：版本演进链/迭代表的「原文依据」列必须双链到真实页面）
    #    版本演进链表（6 列：版本|文号|状态|废止/替代关系|触发原因|原文依据）每行原文依据列须含 [[
    #    2026-08-31 修复：解析行须先保护 [[slug|标题]] 内竖线再 split（坑 25/55——废止/替代关系列
    #    也可能含 wikilink，裸 split('|') 会把行拆碎导致列错位误报）
    if has_evolution:
        def _cells(s):
            protected = ''
            row = s
            i = 0
            while i < len(row):
                if row.startswith('[[', i):
                    j = row.find(']]', i + 2)
                    if j > 0:
                        protected += row[i:j + 2].replace('|', '\x01')
                        i = j + 2
                        continue
                protected += row[i]
                i += 1
            return [x.strip().replace('\x01', '|') for x in protected.strip('|').split('|')]

        in_chain = False
        for ln in lines:
            s = ln.strip()
            if s.startswith('## 一、版本演进链'):
                in_chain = True
                continue
            if in_chain and s.startswith('## '):
                break
            if in_chain and s.startswith('|') and '|' in s[1:]:
                cells = _cells(s)
                if len(cells) >= 6 and '版本' not in cells[0] and '---' not in s:
                    if '[[' not in cells[5]:
                        problems.append(f'版本演进链「原文依据」列缺双链（{cells[0][:20]} 行，须 [[summary/...|标题]] 或 [[longsentence/...|标题]]）')
                    # 2026-08-31 用户要求：版本演进链只列制度正文版本，配套文档（通知/附件/授权清单/修订说明）
                    # 是附属品不得独立成行（应在废止/替代关系列说明）
                    if re.search(r'通知|公告|附件|授权清单|修订说明|印发|配套', cells[0]):
                        problems.append(f'版本演进链含配套文档行（{cells[0][:25]}——通知/附件等附属品不得独立成版本行，应在废止/替代关系列说明）')
        # 迭代表（2.x：| # | 迭代方向 | 具体变化 | 原文依据 |）每行原文依据列须含 [[
        in_iter = False
        for ln in lines:
            s = ln.strip()
            if re.match(r'^### 2\.\d+', s) and '迭代' in s:
                in_iter = True
                continue
            if in_iter and s.startswith('## '):
                break
            if in_iter and s.startswith('|') and '|' in s[1:]:
                cells = _cells(s)
                if len(cells) >= 4 and '迭代方向' not in cells[1] and '---' not in s and cells[0].strip().isdigit():
                    if '[[' not in cells[3]:
                        problems.append(f'迭代表「原文依据」列缺双链（第{cells[0]}条，须 [[summary/...|标题]] 或 [[longsentence/...|标题]]）')
    return problems


def _check_ls_content(slug, content):
    """长句页（original_sentence）模板校验：表头/表格完好/序号连续/summary 门禁。"""
    problems = []
    if not content or not content.strip():
        return ['content 为空']
    lines = content.split('\n')

    # 1. 表头 + 分隔行
    header_idx = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == HEADER or s.startswith('| 序号'):
            header_idx = i
            break
    if header_idx is None:
        problems.append('缺表头（| 序号 | 原文 |）')
        return problems  # 无表头则后续表格检查无意义
    if header_idx + 1 >= len(lines) or not re.match(r'^\|[\s\-|]+\|$', lines[header_idx + 1].strip()):
        problems.append('表头后缺分隔行（|---|---|）')

    # 4. summary 存在：表头之前（排除 # 标题行）有非空正文
    body_before = [ln.strip() for ln in lines[:header_idx]
                   if ln.strip() and not TITLE_RE.match(ln.strip())]
    has_summary = bool(body_before)

    # 2/3. 表格行：表头分隔行之后，到下一个 ## / ** / 空行结束前
    rows, nums = [], []
    for ln in lines[header_idx + 2:]:
        s = ln.strip()
        if not s:
            continue
        if s.startswith('##') or s.startswith('**'):
            break  # 后续章节（相关关键词等）
        if not s.startswith('|'):
            break
        ok, num, parts = _split_table_cells(s)
        if not ok:
            problems.append(f'表格行竖线数异常（未转义 | 破表）: {s[:60]}')
        rows.append(s)
        if num is not None:
            nums.append(num)
    if not rows:
        problems.append('表格无数据行')

    # 序号连续
    if nums and nums != list(range(1, len(nums) + 1)):
        problems.append(f'序号不连续: {nums[:12]}{"..." if len(nums) > 12 else ""}')

    # summary 门禁：多句单元必须有汇总描述
    # 2026-08-31 豁免：xlsx 表格模式长句页（title 含列前缀，如 "A: 41,B: 第五章,C:" 或
    # "B: 第三章 ,C: 职责分工"——修订说明/授权清单类表格条目）是程序拼接的表格条目知识单元
    # （一个修订条目=一个单元），结构就是「标题+表格」，无 LLM 汇总描述属设计如此，不判缺汇总。
    table_mode = bool(re.match(r'^#\s*(?:A|B|C|D|E|F|G):', content or ''))
    if len(rows) > 1 and not has_summary and not table_mode:
        problems.append('多句单元缺汇总描述段（表头前无正文）')

    # 图片标记残留（2026-08-31：doc 转 markdown 的 ![..](resource://..) 被切句成噪音长句页
    # 或截断正文——clean_chunk_text 已加清理，门禁兜底报未清洗的存量/漏网）
    img_hits = [ln.strip()[:60] for ln in lines if re.search(r'!\[[^\]]*\]\(resource://', ln)]
    if img_hits:
        problems.append(f'原文含 doc 图片标记 ![](resource://（未清洗）：{img_hits[0]}')

    return problems


def _check_kw_content(slug, content):
    """关键词页（frequent_keyword）模板校验（2026-08-28 精简模板 → 2026-08-31 多文档分组版）：
    ① 开头句存在（本关键词…高频出现）；② 分组标题格式（**《名》**（频次：N））；
    ③ 每组表头 | 序号 | 关联原文 | + 分隔行；④ 表格完好（竖线数=4）；⑤ 组内序号连续；
    ⑥ 无「参见实体页」残留；⑦ 表格后无多余章节；⑧ 纯通用词；⑨ # 残留；⑩ 图片标记残留。
    兼容旧单文档格式（无分组标题、单表格）。"""
    problems = []
    if not content or not content.strip():
        return ['content 为空']
    lines = content.split('\n')

    # ① 开头句：标题行后须有"本关键词…高频出现"句（新旧格式都匹配）
    head_ok = False
    for ln in lines:
        s = ln.strip()
        if s.startswith('# '):
            continue
        if s.startswith('本关键词') and '高频出现' in s:
            head_ok = True
            # 2026-08-31 新格式要求总频次：本关键词在 N 篇文档中高频出现（总频次：M）
            if '总频次' in s and '篇文档' not in s:
                problems.append('开头句缺少文档数（应为：本关键词在 N 篇文档中高频出现（总频次：M））')
            break
        if s.startswith('## '):
            break
    if not head_ok:
        problems.append('缺开头句（本关键词在…中高频出现）')

    # ② 分组标题：**《名》**（频次：N）——2026-08-31 新格式（文件名含《时无外层《，不强制《开头）；
    # 旧格式无分组标题跳过
    GROUP_RE = re.compile(r'^\*\*(.+?)\*\*（频次：(\d+)）\s*$')
    group_titles = []
    for ln in lines:
        s = ln.strip()
        m = GROUP_RE.match(s)
        if m:
            group_titles.append(m.group(1))
        elif s.startswith('**') and s.endswith('**') and '高频出现' not in s and '原文出现位置' not in s:
            # 旧格式分组标题 **《名》**（无频次）——兼容不报错
            pass
    # 若存在新格式分组标题，要求至少 1 组
    new_style = any(GROUP_RE.match(ln.strip()) for ln in lines)

    # ③④⑤ 表头 + 表格行：按分组（或单表）逐段扫描
    headers = []  # (行号, 组名)
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == '| 序号 | 关联原文 |' or s.startswith('| 序号'):
            # 找最近的组名（上一个分组标题）
            grp = ''
            for j in range(i - 1, -1, -1):
                m = GROUP_RE.match(lines[j].strip())
                if m:
                    grp = m.group(1)
                    break
            headers.append((i, grp))
    if not headers:
        problems.append('缺表头（| 序号 | 关联原文 |）')
    else:
        # 每个表头校验分隔行 + 表格行
        for hi, grp in headers:
            if hi + 1 >= len(lines) or not re.match(r'^\|[\s\-|]+\|$', lines[hi + 1].strip()):
                problems.append(f'表头后缺分隔行（|---|---|）{"（组：" + grp + "）" if grp else ""}')
            rows, nums = [], []
            for j in range(hi + 2, len(lines)):
                s = lines[j].strip()
                if not s:
                    continue
                if s.startswith('|'):
                    ok, num, parts = _split_table_cells(s)
                    if not ok:
                        problems.append(f'表格行竖线数异常（未转义 | 破表）: {s[:60]}')
                    rows.append(s)
                    if num is not None:
                        nums.append(num)
                else:
                    break  # 遇到分组标题/章节标题/空行结束（空行已 continue，实际遇 ** 或 ## 或 # 结束）
            if not rows:
                problems.append(f'表格无数据行{"（组：" + grp + "）" if grp else ""}')
            if nums and nums != list(range(1, len(nums) + 1)):
                problems.append(f'序号不连续: {nums[:12]}{"..." if len(nums) > 12 else ""}')
        # ⑦ 表格后无多余内容：最后一个表头之后、最后一个表格行之后的内容
        last_hi = headers[-1][0]
        # 找到最后一个表头对应的最后数据行
        last_data = last_hi + 1
        for j in range(last_hi + 2, len(lines)):
            s = lines[j].strip()
            if s.startswith('|'):
                last_data = j
            elif s.startswith('**') and not s.startswith('|'):
                # 新分组标题 = 还有后续表格，不作为"多余内容"
                pass
        tail = [ln.strip() for ln in lines[last_data + 1:] if ln.strip()]
        if tail:
            # 允许最后一个表格后出现新格式分组标题（表示还有更多组）——但若在最后一个表之后仍有多余非分组内容则报
            real_tail = [t for t in tail if not (GROUP_RE.match(t) or t.startswith('| 序号') or re.match(r'^\|[\s\-|]+\|$', t))]
            if real_tail:
                problems.append(f'表格后存在多余内容（应删除）: {real_tail[0][:40]}')

    # ⑥ 无「参见实体页」残留
    if '参见实体页' in content:
        problems.append('含「参见实体页」残留（2026-08-28 已从模板移除）')
    # ⑧ 纯通用词门禁（2026-08-29：统计层无法识别业务含义，「确定/完善/规范」等纯通用词
    # 不适合做关键词——无业务含义可出现在任何文档；应经 filter_terms_llm.py 语义过滤）
    title_m = re.match(r'#\s*(.+)', content)
    if title_m:
        kw_title = title_m.group(1).strip()
        if kw_title in COMMON_WORDS:
            problems.append(f'关键词为纯通用词（「{kw_title}」无业务含义，须经 filter_terms_llm.py 语义过滤剔除）')
        # ⑧b 形态垃圾门禁（2026-09-02：0829 库「25/决策的采购/招标的项目」实证——
        # 纯数字/无汉字/含"的"碎片是字符级 N-gram + tokenize 补充的统计噪音，须硬过滤剔除）
        if (re.fullmatch(r'\d+', kw_title)
                or not re.search(r'[\u4e00-\u9fff]', kw_title)
                or '的' in kw_title):
            problems.append(f'关键词为形态垃圾（「{kw_title}」纯数字/无汉字/含"的"粘连碎片，须 apply_hard_filter 剔除）')
    # ⑨ 片段 markdown 标题标记残留（2026-08-28：docx 转 markdown 的 # 污染片段 → 无效行；
    # 2026-08-29 修复：排除页面合法标题行（# / ## / ### 等后跟文字），只查行中/表格单元格内的 # 污染）
    polluted = [ln for ln in content.split('\n')
                if re.search(r'#{1,6}', ln) and not re.match(r'^\s*#{1,6}\s+\S', ln)]
    if polluted:
        problems.append(f'片段含 markdown 标题标记 #（原文未清洗，需 clean_chunk_text/# 清理）: {polluted[0][:40]}')
    # ⑩ 片段 doc 图片标记残留（2026-08-31：doc 转 markdown 的 ![..](resource://..) 污染，
    # 会切出「![](resource://」噪音长句页并截断正文；clean_chunk_text 已加清理，门禁兜底）
    img_polluted = [ln for ln in content.split('\n') if re.search(r'!\[[^\]]*\]\(resource://', ln)]
    if img_polluted:
        problems.append(f'片段含 doc 图片标记 ![](resource://（原文未清洗，需 clean_chunk_text 清理）: {img_polluted[0][:60]}')

    return problems


def load_ls_pages(kb, limit=None, page_type='original_sentence'):
    """分页拉取指定 page_type 的页面。返回 [(slug, title, content), ...]。"""
    pages, page = [], 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': page, 'page_size': 200})
        ps = r.get('pages') or []
        for p in ps:
            if p.get('deleted_at'):
                continue
            # index 页 page_type 是 concept（2026-08-30 起；原 topic_cluster），按 slug 前缀 index/ 识别
            if page_type == 'index':
                if p.get('slug', '').startswith('index/'):
                    pages.append((p.get('slug', ''), p.get('title', ''), p.get('content') or ''))
            elif p.get('page_type') == page_type:
                pages.append((p.get('slug', ''), p.get('title', ''), p.get('content') or ''))
        if len(ps) < 200:
            break
        if limit and len(pages) >= limit:
            break
        page += 1
        if page > 200:
            break
    if limit:
        pages = pages[:limit]
    return pages


def old_format_to_new(slug, title, content):
    """把旧格式长句页（v2 平铺 `# title\n\ntext` / v1 带 **相关关键词**：（待补））
    转换为新模板（标题 + 两列表格）。返回新 content，或 None（无法转换/已是新格式）。"""
    lines = content.split('\n')
    # 已是新格式（含表头）→ 不转换
    if any(ln.strip() == HEADER for ln in lines):
        return None
    # 定位标题行与正文起点
    body_start = 0
    for i, ln in enumerate(lines):
        if TITLE_RE.match(ln.strip()):
            body_start = i + 1
            break
    body = '\n'.join(lines[body_start:])
    # 去掉尾部"**相关关键词**：（待补）"等后续章节
    for marker in ('**相关关键词**', '## '):
        idx = body.find(marker)
        if idx >= 0:
            body = body[:idx]
    body = body.strip()
    if not body:
        return None
    # 按 \n 拆句 → 表格行（| 转义、句内换行拍平）
    sentences = [s.strip() for s in body.split('\n') if s.strip()]
    if not sentences:
        return None

    def _cell(s):
        return s.replace('|', '\\|').replace('\n', ' ')
    rows = '\n'.join(f'| {i} | {_cell(s)} |' for i, s in enumerate(sentences, 1))
    return f'# {title}\n\n| 序号 | 原文 |\n|---|---|\n{rows}'


def main():
    parser = argparse.ArgumentParser(description='Wiki 页面模板结构门禁（长句页/关键词页）')
    parser.add_argument('--kb', default=None, help='知识库 ID（默认技能 config.yaml）')
    parser.add_argument('--slug', default=None, help='只检查指定 slug')
    parser.add_argument('--limit', type=int, default=None, help='只检查前 N 页（调试用）')
    parser.add_argument('--page-type', default='original_sentence',
                        choices=['original_sentence', 'frequent_keyword', 'business_ontology',
                                 'rule_ontology', 'summary', 'index'],
                        help='校验的页面类型（长句页/关键词页/业务实体/规则实体/摘要页）')
    parser.add_argument('--dry-run', action='store_true', help='只报告不写库')
    parser.add_argument('--fix', action='store_true', help='启用安全修复（旧格式 → 新表格）')
    parser.add_argument('--check-dual-folder', action='store_true',
                        help='扫描同文档双分类实体（folder_ids>1 且 source_refs 唯一，坑 92）')
    parser.add_argument('-v', '--verbose', action='store_true', help='列出每个问题页')
    args = parser.parse_args()

    wr.load_config()
    wr.mcp_init()
    KB = args.kb or wr.load_kb()
    wr.KB = KB

    pages = load_ls_pages(KB, limit=args.limit, page_type=args.page_type)
    if args.slug:
        pages = [(s, t, c) for s, t, c in pages if s == args.slug]
    print(f'{args.page_type} 页面总数: {len(pages)} (kb={KB})')

    stat = {'error': 0, 'warn': 0, 'ok': 0}
    by_type = {}
    issue_pages = []
    fixable = []
    for slug, title, content in pages:
        problems = check_title(title) + check_content(slug, content, page_type=args.page_type)
        errors = [p for p in problems if p and not p.startswith('title')]
        warns = [p for p in problems if p and p.startswith('title')]
        if content.strip() == '' and 'content 为空' in problems:
            errors = ['content 为空']
        if errors:
            stat['error'] += 1
            issue_pages.append((slug, title, errors))
            for p in errors:
                by_type[p] = by_type.get(p, 0) + 1
        elif warns:
            stat['warn'] += 1
        else:
            stat['ok'] += 1
        # fixable：旧格式可转换（仅长句页）
        if args.fix and args.page_type == 'original_sentence':
            newc = old_format_to_new(slug, title, content)
            if newc is not None:
                fixable.append((slug, title, content, newc))

    print(f'\n=== 结果 ===')
    print(f'通过: {stat["ok"]} | 警告: {stat["warn"]} | 结构错误: {stat["error"]}')
    if by_type:
        print('\n问题类型分布:')
        for k, v in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f'  {v:4d}  {k}')
    if args.verbose:
        for slug, title, errors in issue_pages[:50]:
            print(f'\n  ✗ {slug} [{title}]')
            for e in errors:
                print(f'      - {e}')

    if fixable:
        print(f'\n可转换旧格式页面: {len(fixable)}')
        for slug, title, old, newc in fixable[:10]:
            print(f'  - {slug} [{title}]')
        if args.dry_run:
            print('\n(--dry-run：未写库)')
        else:
            done = 0
            for slug, title, old, newc in fixable:
                r = wr.read_page(**{'kb_id': KB, 'slug': slug})
                if 'error' in r:
                    continue
                wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': r.get('title', title),
                                  'content': newc, 'folder_id': r.get('folder_id', ''),
                                  'folder_ids': r.get('folder_ids') or [r.get('folder_id', '')],
                                  'page_type': 'original_sentence', 'status': r.get('status', 'published'),
                                  'source_refs': r.get('source_refs') or []})
                done += 1
            print(f'已转换: {done} 页（转换后需 wiki_rebuild_links）')

    # 同文档双分类实体扫描（2026-08-29 坑 92：LLM 把同一实体归到两个分类，merge_page 曾追加双目录）
    if args.check_dual_folder:
        print(f'\n=== 同文档双分类实体扫描 ===')
        dual = []
        page = 1
        while True:
            r = wr.list_wiki_pages(**{'kb_id': KB, 'page': page, 'page_size': 200})
            ps = r.get('pages') or r.get('data') or []
            for p in ps:
                if p.get('deleted_at'):
                    continue
                fids = p.get('folder_ids') or []
                refs = p.get('source_refs') or []
                if len(fids) > 1 and len(refs) == 1:
                    dual.append((p.get('slug', ''), p.get('title', ''), len(fids)))
            if len(ps) < 200:
                break
            page += 1
        if dual:
            for slug, title, n in dual:
                print(f'  ❌ {slug} | {title} | folder_ids={n} 但 source_refs=1（同文档双分类，坑 92）')
            print(f'共 {len(dual)} 页')
        else:
            print('  无（通过）')
    print('\n建议：转换/修复后执行 wiki_rebuild_links + wiki_lint 验证')


if __name__ == '__main__':
    main()
