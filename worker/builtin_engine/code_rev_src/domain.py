#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「代码反向生成 / 智能取数」域常量（本体类别 / 提示词 / 词表 / 目录名）。

技能：code-reverse-wiki-compiler（代码反向生成知识转wiki）
域：智能取数知识体系——工单→SQL、指标字典、表/Hive-Gbase 映射、经分接口规范、业务本体、取数算子代码
本体来源：kb_compilation ontology_schema「代码反向生成」（10 业务 + 6 规则，2026-10-10 已在平台配置）

所有脚本的域化常量统一从此模块导入——改类别/提示词只改这一处。
"""
import json
import os
import re

# ---------------- 目录模型（平铺：一个库 = 一套实体目录，无版本层）----------------
KB_ROOT = '代码反向生成'
BIZ_DIR = '业务本体'
RULE_DIR = '规则本体'
STRUCT_DIR = '结构化知识'          # 程序化生成：指标库 / 表映射库 / 取数样例库
SUMMARY_DIR = '资产摘要'           # 每篇源文档一张「资产卡」
METRIC_DIR = '指标库'
TABLE_DIR = '表映射库'
SAMPLE_DIR = '取数样例库'

# ---------------- 本体类别（10 + 6）----------------
BIZ_CATS = [
    '1-数据表/接口', '2-指标对象', '3-取数需求/工单', '4-代码/算子', '5-数据字段/口径',
    '6-业务域/主题', '7-系统/平台', '8-组织/角色', '9-业务事件/流程', '10-规范/版本',
]
RULE_CATS = [
    '1-映射规则', '2-清洗规则', '3-生成规则', '4-口径规则', '5-约束规则', '6-版本规则',
]
BIZ_CAT_SHORT = {c.split('-', 1)[1]: c for c in BIZ_CATS}
RULE_CAT_SHORT = {c.split('-', 1)[1]: c for c in RULE_CATS}
RO_NUM_MAP = {c.split('-', 1)[1]: int(c.split('-', 1)[0]) for c in RULE_CATS}
# 规则类别 → Neo4j 边类型（沿用平台 schema 的 neo4j_edge）
RULE_EDGE = {'映射规则': 'MAPS_TO', '清洗规则': 'CLEANS', '生成规则': 'GENERATES',
             '口径规则': 'DEFINES', '约束规则': 'CONSTRAINS', '版本规则': 'VERSION_OF'}

SYSTEM_ROLE = '中国移动智能取数/代码反向生成知识解析专家'
SUMMARY_ROLE = '中国移动智能取数知识库的资产文档摘要专家'

INDICATIVE = ('本脚本', '本算子', '本文档', '本规范', '该脚本', '该工单', '本条', '本单位', '本部门')

_BIZ_LIST = '\n'.join(f'{c.split("-", 1)[0]}. {c.split("-", 1)[1]}' for c in BIZ_CATS)
_RULE_LIST = '\n'.join(f'{c.split("-", 1)[0]}. {c.split("-", 1)[1]}' for c in RULE_CATS)

_BIZ_HINT = {
    '数据表/接口': '数据表/视图/接口/报表实体（temp_qs_hxj_ysgxb、结果表、rel_table_list、经分接口、hive 映射表）',
    '指标对象': '指标字典中的指标名称或文档衡量对象（全量指标、集团短彩收入、消息下发量）',
    '取数需求/工单': '需求/工单/任务类目（取数需求、变量 sql 生成需求、数据需求工单、重复历史工单）——非某张具体工单实例',
    '代码/算子': 'py 脚本/SQL 模板/算子（hive 映射、sql 清洗、实体名替换、变量 sql 生成、结果表名解析、order_list、重复变量模板）',
    '数据字段/口径': '贯穿文档的业务口径/字段概念（上月出账收入口径）——非表头列名',
    '业务域/主题': '业务阶段/分析主题/业务分类（智能取数、AI 场景、短彩、政企、规划、营销、计费）',
    '系统/平台': '系统平台（Hive、Gbase、集中化经分系统、AI 网关、大数据平台、数据库）',
    '组织/角色': '部门/单位/角色（需求部门、规划部门、大数据负责人、开发方、客户经理、省公司）',
    '业务事件/流程': '"动作+对象"完整事件（取数、变量 sql 生成、sql 清洗、数据同步、建表、表映射、接口调用）',
    '规范/版本': '规范文档/版本概念（省级数据接口规范经分分册、V13.3、业务本体 v1.0）',
}
_RULE_HINT = {
    '映射规则': 'hive 映射/表名解析规则（完全映射→hive、未完全映射→Gbase；无前缀表名小写比对）',
    '清洗规则': 'sql 脚本清洗规则（去注释、补分号）、实体名替换为变量规则',
    '生成规则': '变量 sql 生成/重复工单转变量模板/由工单描述生成 SQL 的规则',
    '口径规则': '业务口径/指标口径定义（计算口径、统计范围、时间口径）',
    '约束规则': '同步周期/存储周期/可用状态/是否可用/数据上传时限等约束',
    '版本规则': '接口规范/业务本体/脚本的版本演进与变更记录',
}

RULE_PROMPT = (
    f'你是{SYSTEM_ROLE}。从以下长句中提取「规则」（智能取数域中可复用的方法性约束）。\n\n'
    f'规则共 6 类，每条规则一页：\n{_RULE_LIST}\n\n'
    '各类识别要点：\n'
    + '\n'.join(f'{c.split("-", 1)[0]}. {c.split("-", 1)[1]}：{_RULE_HINT.get(c.split("-", 1)[1], "")}' for c in RULE_CATS)
    + '\n\n输出JSON：[{"title":"规则标题（≤20字概括）","category":"映射规则",'
      '"structure":{"主体":"x","条件":"x","动作":"x","结果":"x"},"description":"规则具体内容",'
      '"evidence":"原文关键片段（逐字）","constraint":"约束/作用对象（业务本体名）",'
      '"objects":[{"name":"规则涉及的其他对象名","role":"主体/条件/动作/客体"}]}]\n\n'
      '【硬性要求】title 一句话概括规则核心（≤20字，可提炼不必逐字）；'
      'constraint、objects[].name、evidence 必须逐字来自长句原文；description 可自由组织。'
      'structure 只填相关字段（主体/条件/动作/结果/变量/周期/映射结果等）。'
      'constraint 是这条规则作用的对象（如"hive映射表""变量sql""结果表"），无则填空串 ""。\n'
      '【禁止】不得把表头列名（table_name、originaltable、task_id、指标编码、原始sql、修改后sql）、'
      '纯码值、单元格值当规则对象。'
)

BIZ_PROMPT = (
    f'你是{SYSTEM_ROLE}。请从以下长句中提取「业务对象」。\n\n'
    f'业务对象共 10 类，每类提取 1-8 个实体：\n{_BIZ_LIST}\n\n'
    '各类识别要点：\n'
    + '\n'.join(f'{c.split("-", 1)[0]}. {c.split("-", 1)[1]}：{_BIZ_HINT.get(c.split("-", 1)[1], "")}' for c in BIZ_CATS)
    + '\n\n【重要参考】以下是从规则中提取的「规则对象」清单，它们都是规则作用的知识对象，'
      '必须作为实体提取（若长句原文确实存在），并按实际语义归类：\n{constraints}\n\n'
      '【高频候选术语】以下是从原文中新词发现的高频短语，通常是术语或对象，'
      '请在语义合适且有原文依据时优先提取为实体：\n{terms}\n\n'
      '【硬性要求】实体名（name）必须逐字出现在下方长句原文中（字符级匹配）。'
      '禁止把行首编号、摘要标题或自行概括的词汇当作实体名；原文中没有明确名称的概念不提取。\n\n'
      '【名词性硬约束】业务对象必须是**名词性概念**，禁止提取：\n'
      '① 介词引导的动词短语（按照/遵照/根据/依据/经…后/由/对于/将/不纳入 开头）；\n'
      '② 以动作动词结尾的描述（执行/办理/生成/清洗/替换/映射/解析/同步/建表 等结尾）；\n'
      '③ 指示定语修饰的衍生表达（以"本脚本/本算子/本文档/该工单"等指代性定语开头的，取去定语后的核心）；\n'
      '④ 表头列名/字段名（table_name、originaltable、task_id、指标编码）、纯码值、纯日期、SQL 关键字。\n'
      '判断标准：实体名应能填入"XX是什么/XX是谁"的名词框架。\n\n'
      '只输出JSON数组，格式：\n'
      '[{"name":"实体名","category":"数据表/接口","definition":"一句话定义","description":"详细描述"}]\n\n'
      'category 用上面 10 类名称（不带序号）。'
)

JOINT_PROMPT = (
    f'你是{SYSTEM_ROLE}。请从以下长句（编号列表，同一知识库的原文知识单元）中**同时**抽取：\n'
    '① 业务对象（entities）——长句中的名词性对象/表/指标/算子/字段口径/系统/角色/事件/版本；\n'
    '② 规则（rules）——长句蕴含的可复用方法性规则卡。\n\n'
    f'【业务对象 10 类】（实体须能填入"XX是什么/是谁"名词框架）\n{_BIZ_LIST}\n\n'
    f'【规则 6 类】\n{_RULE_LIST}\n\n'
    '【主谓配对硬约束（最重要）】\n'
    '- 一条规则通常是"某对象（主语）被施加某方法性约束（谓词）"。\n'
    '- rules[].constraint 与 rules[].objects[].name **必须逐字等于本次输出 entities[].name 之一**'
    '（同一 JSON 内引用，禁止同义改写/自创变体/缩写）。\n'
    '- 若规则作用的对象未出现在 entities 中，**先在 entities 里补出该实体**再在 rule 中引用。\n'
    '- 同一概念在长句中多次出现只输出一次。\n'
    '- 规则确实无明确约束对象时 constraint 填空串 ""。\n\n'
    '【高频候选术语】以下短语语义合适且有原文依据时优先提取为实体：\n{terms}\n\n'
    '【质量硬约束】\n'
    '- entities[].name / rules[].constraint / objects[].name / rules[].evidence 必须**逐字来自长句原文**。\n'
    '- 禁止提取/引用：① 介词引导动词短语；② 动作动词结尾描述；③ 指示定语衍生表达；\n'
    '  ④ 纯日期/码值/表头列名（table_name/originaltable/task_id/指标编码/原始sql/修改后sql）、SQL 关键字；\n'
    '  ⑤ 具体工单实例号（如 AITR00020260520236103）——工单类目才是实体，实例号是属性。\n'
    '- rules[].title：一句话概括（≤20字）；description 自由组织；evidence 原文关键片段（逐字）。\n'
    '- rules[].structure：只填相关字段（主体/条件/动作/结果/变量/周期/映射结果）。\n'
    '- rules[].objects：除 constraint 外规则还涉及的对象，带 role（主体/条件/动作/客体）；无则 []。\n'
    '- 实体 category 用 10 类名称、规则 category 用 6 类名称（均不带序号）。\n\n'
    '只输出一个 JSON 对象（无任何其他文字）：\n'
    '{{"entities":[{{"name":"实体名","category":"代码/算子","definition":"一句话定义"}}],\n'
    '  "rules":[{{"title":"规则概括","category":"生成规则","structure":{{"主体":"x"}},"description":"...",\n'
    '            "evidence":"原文关键片段","constraint":"实体名","objects":[{{"name":"实体名","role":"客体"}}]}}]}}'
)

# ---------------- 词表 ----------------
COMMON_WORDS = set(
    '确定 明确 完善 规范 加强 开展 落实 推进 建立 健全 强化 提高 促进 实现 做好 抓好 '
    '严格 认真 积极 深入 全面 充分 不断 进一步 有关 相关 主要 重要 具体 有效 合理 '
    '科学 统一 直接 其他 各类 各种 部分 相应 一定 必要 特此 年月日 本脚本 本文档 '
    '有关要求 相关要求 具体规定 遵照 印发 纳入 引入 专业 业务 使用 完成 增加 提交 支撑 '
    '负责 调整 变更 合作 计划 组织 监督 评审 审批 领导 框架 级别 层级 分级 办公 电子 '
    '技术 程序 配置 集体 集中 规范 文档 系统 平台 功能 模块 要求 规定 说明 概述 原则 '
    '目标 范围 内容 方式 方法 流程 过程 阶段 环节 情况 信息 文件 标准 能力 支持 实现 '
    '提供 采用 包括 进行 满足 保证 确保 提高 提升 优化 建设 设计 开发 实施 部署 运行 '
    '维护 管理 中国移动 省公司 集团公司 有限 责任 通信 相关管理 相关规定 达到或超过'.split()
)

STOPWORDS = set(
    '的 了 在 是 有 为 与 和 或 及 被 把 从 以 对 于 到 让 由 向 将 并 而 但 可 如 若 且 '
    '该 其 这 那 所 各 每 按 应 当 须 需 请 能 要 会 已 正 不 未 非 无 上 下 中 外 内 前 后 '
    '之 等 得 进行 相关 按照 根据 通过 以及 或者 应当 必须 不得 可以 用于 包括 属于 '
    '低于 高于 超过 以上 以下 技术 文档 说明 概述 原则 目标 范围 内容 方式 方法 流程 '
    '过程 情况 信息 文件 服务 工作 项目 方案 时间 阶段 环节 结果 部门 单位 实施 执行 '
    '管理 建设 设计 开发 运行 维护 支持 实现 提供 采用 满足 保证 中国 移动 通信 集团 '
    '有限 责任 数据 字段 表格 序号'.split())
CORP_FRAG = ('移动通信', '通信集团', '中国移动', '有限公司', '公司文件', '集团公司', '湖北省')

# ---------------- 结构化资产（程序化建页，不调 LLM）----------------
ASSET_FILES = {
    'metric': '全量指标20260814.xlsx',
    'table_map': '智能取数算子需求/db值代码(1)/temp_qs_hxj_ysgxb_20251209_r1.xlsx',
    'worksheet': '智能取数算子需求/db值代码(1)/历史工单知识库20260604_1.xlsx',
    'worksheet_sample': '历史工单知识库20260115-样例.xlsx',
    'var_worksheet': '智能取数算子需求/重复变量模板代码(1)/需生成变量sql的重复历史工单20260604.xlsx',
}


def asset_path(name, base_dir):
    return os.path.join(base_dir, ASSET_FILES.get(name, name))


# ---------------- 文件名 → 中文展示名/分类 ----------------
ASSET_LABELS = {
    '全量指标20260814.xlsx': '全量指标字典',
    '历史工单知识库20260115-样例.xlsx': '历史工单知识库（样例）',
    '历史工单知识库20260604_1.xlsx': '历史工单知识库（全量）',
    'temp_qs_hxj_ysgxb_20251209_r1.xlsx': '表属性与周期映射',
    '需生成变量sql的重复历史工单20260604.xlsx': '重复工单变量 SQL 清单',
    '样例数据-取数(1)(1).xlsx': '取数样例数据',
    '2026年湖北移动AI场景化支撑项目建设清单.xlsx': 'AI 场景化支撑项目建设清单',
    '集中化经分系统省级数据接口规范_经分分册V13.3.docx': '集中化经分系统省级数据接口规范（经分分册 V13.3）',
    '湖北政企短彩业务本体 v1.0.docx': '湖北政企短彩业务本体 v1.0',
    '智能取数_字段级数据流图(1).html': '智能取数字段级数据流图',
}


def asset_label(file_name):
    base = os.path.basename(file_name)
    return ASSET_LABELS.get(base, os.path.splitext(base)[0])


def asset_kind(file_name):
    """返回资产类型：metric / table_map / worksheet / var_sheet / doc / code / html / other"""
    b = os.path.basename(file_name)
    if b in ('全量指标20260814.xlsx',):
        return 'metric'
    if b in ('temp_qs_hxj_ysgxb_20251209_r1.xlsx',):
        return 'table_map'
    if b in ('历史工单知识库20260604_1.xlsx', '历史工单知识库20260115-样例.xlsx', '样例数据-取数(1)(1).xlsx'):
        return 'worksheet'
    if b in ('需生成变量sql的重复历史工单20260604.xlsx',):
        return 'var_sheet'
    if b.endswith('.py'):
        return 'code'
    if b.endswith(('.docx', '.doc')):
        return 'doc'
    if b.endswith(('.html', '.htm')):
        return 'html'
    return 'other'


def ontology_payload():
    items = []
    for c in BIZ_CATS:
        items.append({'dimension': 'business', 'cat_no': int(c.split('-', 1)[0]),
                      'cat_name': c.split('-', 1)[1], 'cat_label': c,
                      'prompt_hint': _BIZ_HINT.get(c.split('-', 1)[1], ''), 'neo4j_edge': None, 'state': '1'})
    for c in RULE_CATS:
        n = c.split('-', 1)[1]
        items.append({'dimension': 'rule', 'cat_no': int(c.split('-', 1)[0]),
                      'cat_name': n, 'cat_label': c,
                      'prompt_hint': _RULE_HINT.get(n, ''), 'neo4j_edge': RULE_EDGE.get(n), 'state': '1'})
    return {'schema_name': KB_ROOT, 'schema_label': KB_ROOT,
            'schema_desc': '智能取数/代码反向生成知识体系：工单→SQL 语料、指标字典、表与 Hive/Gbase 映射、经分接口规范、取数算子代码',
            'items': items}


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'references',
                       f'ontology-{KB_ROOT}.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(ontology_payload(), f, ensure_ascii=False, indent=2)
    print('written:', os.path.abspath(out))
