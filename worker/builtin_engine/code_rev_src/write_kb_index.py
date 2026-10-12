#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写入「代码反向生成」库的索引页（语义导航：链路 + 三大结构化库 + 入口）。"""
import sys

sys.path.insert(0, '/home/ctyun/.hermes/skills/devops/code-reverse-wiki-compiler/scripts')
KB = '5f247336-d22b-4c04-9f74-e7c07ab57693'

CONTENT = """# 代码反向生成知识库 · 索引

**是什么**：智能取数知识库——把「工单描述 → SQL → 指标 → 数据表 → 值代码」的取数链路沉淀成可检索知识，
支撑「问一句取数需求 → 生成/校验取数 SQL」。

## 一、取数算子（按链路顺序）

| 序 | 算子 | 在链路里做什么 | 源脚本 |
|---|---|---|---|
| 1 | [[entity-b-d2d1058c6c\\|SQL血缘提取与口径生成增强算子]] | 工单描述 → 原始 SQL；抽泛化输入表/输出字段与口径 | order_list.py |
| 2 | [[entity-b-e6b3614760\\|SQL脚本清洗与变量回填]] | 去注释、补分号、清临时表变量、抽变量字典并回填 | 0.sql脚本清洗.py |
| 3 | [[entity-b-cc807d0f6c\\|SQL日期月实体变量化算子]] | 实体名（日期/月份等）→ 变量占位符 | 1.原始sql实体名替换成变量脚本.py |
| 4 | [[entity-b-d5f8f623eb\\|SQL日期变量实体化替换]] | 变量 → 直 SQL（按 lastday/taskid 字典回填实体值） | 2.var_sql_to_zhi_sql_20260106.py |
| 5 | [[entity-b-584e4b1207\\|同工单结果表名聚合去重写盘]] | 结果表名解析、同工单聚合去重 | 结果表名解析.py |
| 6 | [[entity-b-5a58a63fb3\\|工单表名Hive映射与DB路由]] | 完全映射→Hive，未完全映射→Gbase | 1.2hive映射.py |

> 每个算子卡内含：职责 / 链路位置 / 输入输出契约 / **处理单元（函数级代码单元页）** / **可迁移经验** / 关联规则。

## 二、结构化知识（1.6 万页，程序化生成）

| 库 | 规模 | 内容 |
|---|---|---|
| 指标库 | 6773 | 指标编码、**指标 SQL**、说明、业务口径、粒度、分析主题、需求部门 |
| 表映射库 | 7939 | 表名/别名、owner、周期（备份/保存/建表）、同步标识与过程 |
| 取数样例库 | 1531 | 需求工单号、需求描述、**原始 SQL / 修正 SQL / 变量 SQL**（问答出 SQL 的 few-shot 金矿） |

互链关系：指标页 ↔ 表页（从指标 SQL 抽表名）、样例页 ↔ 表页（从工单 SQL 抽表名，仅链已登记表）。

## 三、其他入口

- **规则卡 126 张**：映射 / 清洗 / 生成 / 口径 / 约束 / 版本规则，均带代码或原文证据
- **代码单元 68 个**：函数级语义标题 + 处理步骤 + 复用提示 + 源码逐字证据（表格）
- **高频关键词 53、资产摘要 3**（湖北政企短彩业务本体 v1.0、字段级数据流图、经分接口规范 V13.3）

## 四、怎么问

- 「类似‘宽带离网清单’这种工单，取数 SQL 怎么写？」→ 召回：算子卡链路 + 相似样例 SQL + 涉及表的属性与周期
- 「XX 指标的口径和 SQL 是什么？」→ 指标库
- 「XX 表是 Hive 还是 Gbase，周期多长？」→ 表映射库
"""


def main():
    import weknora_rpc as wr
    wr.load_config()
    wr.KB = KB
    wr.mcp_init()
    r = wr.update_page('index', kb_id=KB, title='代码反向生成知识库 · 索引', content=CONTENT,
                       page_type='index', status='published')
    print('update index:', str(r)[:200])
    v = wr.read_page('index', kb_id=KB) or {}
    print('读回长度:', len(v.get('content') or ''), '| 标题:', v.get('title'))


if __name__ == '__main__':
    main()
