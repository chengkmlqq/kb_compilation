---
name: 监督管理制度文档转wiki
description: "Use when building wiki knowledge for a market regulation KB (市场监管法规, ported from supply-management-policy-compiler)."
---

# 市场监管法规 Wiki 知识构建（market-regulation-policy-compiler）

**状态（2026-09-07 实跑验证，2026-09-08 Neo4j 图谱写入已接入）**：本技能已从「移植脚手架」补齐为**可运行版**并完成 3 篇试点验证
（进口药材管理办法/医疗器械监督管理条例/药品管理法）。补齐项：config.yaml（kb_id=06c769e2-...）、
templates/ 4 个（法规域）、build_full.py（主链路，无家族版本层）、gen_summary.py（法规摘要卡片）、
weknora_rpc.py llm_call timeout=300 + PDF 页码线清洗、filter_terms_llm 域化、extract_entities_single
system prompt 域化。**2026-09-08：graph_export.py（Neo4j 图谱写入）自供管版域化移植并接入 build_full
Step 9.5（默认开启，--no-graph 跳过），存量 3 篇已全量实写验证（732 节点/753 关系，见下节）**。
待办：references/ 未建、build_relations.py 血缘未域化（家族归并逻辑不适配，引用匹配可用但需按法规
语义核对）、check_folder_structure.py 未移植。

**场景**：为「市场监督管理知识库」（kb_id=06c769e2-4b14-4714-9ef7-e8b366f79bed，500 篇国家法律/
行政法规/部门规章）构建 wiki。文档为市场监管法规（药品/医疗器械/食品/特种设备/网络交易等），
**不是公司采购制度**。与 supply-management-policy-compiler 同族：脚本复用（域化点已改）。

## 与供管版的关键差异（2026-09-07 实跑定稿）

1. **目录模型：无家族/版本层**——法规库一篇法规=一个根目录（法规名，去扩展名），根下直接
   `基础实体`/`长句原文`/`高频关键词` 三子目录，**无版本级目录**（供管版：家族根→版本目录→三子目录）。
   摘要页 folder_id=法规根目录。
2. **业务本体 9 类**（extract_entities_single.py BIZ_CAT_SHORT）：
   1-法律法规 2-监管对象 3-许可事项 4-违法行为 5-行政处罚 6-监管职责 7-标准规范 8-程序事项 9-时限要求
3. **规则本体 12 类**（RULE_CAT_SHORT）：
   1-定义规则 2-条件规则 3-约束规则 4-职责规则 5-审批规则 6-流程规则 7-引用规则 8-例外规则
   9-**处罚规则** 10-**时限规则** 11-版本规则 12-**程序规则**（对比供管：决策/金钱 → 处罚/时限/程序）
4. 提示词域化：BIZ_PROMPT/RULE_PROMPT/JOINT_PROMPT 均为"市场监管法规解析专家"；禁止纯日期/时间点
   成实体；指示定语拦截词 = 本法/本规定/本办法/本细则/本部门/本机关/各级市场监督管理部门/国务院
5. **摘要卡片=法规口径**（gen_summary.py + templates/summary-page.md）：基本信息表 =
   位阶/领域/制定机关/文号/发布日期施行日期/修订记录/状态；章节 = `## 法规定位` +
   `## 与其他法律法规的关系`（门禁 REQUIRED_SECTIONS 同步）。供管版「制度定位/与其他制度的关系」不适用。
6. **PDF 页码线清洗**（2026-09-07 实测黄河保护法「— ８４３ —」）：weknora_rpc.py + extract_long_sentences_v2.py
   clean_chunk_text 均加 `^\s*[-—－]\s*[\d０-９0-9\s]{1,6}\s*[-—－]\s*$` 行级删除。
7. **entity_registry.py SYNONYMS 为空**（跨文档合并累积真同义对后填充，判定原则见供管坑 15）。
8. discover_terms.py STOPWORDS/CORP_FRAG：去公司专名（中国移动/上海等），法规文种词
   （条例/办法/规定/细则/规章/法规/法律/施行/修订…）在 filter_terms_llm.COMMON_WORDS（与 check_ls_template 同步）。

## Neo4j 图谱写入（2026-09-08 移植自供管版 graph_export.py，域化同款图模型）

**默认开启**：`build_full.py` 每篇法规构建完实体/规则页后（Step 9.5），自动把该文档的图写入
Neo4j。`--no-graph` 可跳过。失败不中断 wiki 构建（打印修复命令）。

**图模型（供管版 2026-09-07 用户确认，法规域同款）**：
- **业务本体（entity-b 页）= 节点**：name=canonical 名，attributes=[业务类别（法规域 9 类：
  法律法规/监管对象/许可事项/违法行为/行政处罚/监管职责/标准规范/程序事项/时限要求）, 定义…]
- **规则本体（entity-r 页）= 关系（边）**：每条规则卡的约束对象=主语，关联对象=宾语（带角色），
  **边类型=规则类别动词**（法规域 12 类映射：定义/触发条件/约束/管理/审批/执行/引用/豁免/
  **处罚/时限/版本/程序**——供管 9-决策/10-时间/11-金钱 域化为 处罚/时限/程序），不从句中挖
  动作动词（坑 115 同款）
- **kg = 该文档 kid**（每文件独立子图）：写前先 `graph_delete` 删该 kid 旧子图再写 → 重建同一
  文档干净无残留；跨文档同名实体在 Neo4j 存多份，查询按 name 去重
- **完整多边模型**：角色对象（主体/客体/条件/引用/工具/载体/标准/方法…）也建节点接入主语；
  主体角色反转为边起点（宾语-[类别动词]->约束对象）；条件→`触发条件`、引用→`引用`、工具→`使用`、
  载体/基础/标准→`依据`、方式/方法→`采用`
- **法规文档节点**：每个来源文档补一个节点（attributes=[法规文档]，供管版为「制度版本」——
  法规库无版本目录概念），规则主语 -[制定于]-> 文档节点
- 无宾语可建边的规则（纯定义/约束自身）→ 语境并入主语节点 attributes
- 规则涉及但尚未抽成业务实体的宾语 → 一并补节点（规则对象=业务概念）

**脚本**：`scripts/graph_export.py`（核心模块，可独立 CLI / 被 build_full import）
```bash
# 单文档：先删该 kid 子图再写（build_full Step 9.5 内部同款）
python3 scripts/graph_export.py doc --kid <kid> --kb <kb_id>
# 全库逐文档（存量补齐）
python3 scripts/graph_export.py export --kb <kb_id> [--dry-run]
# 删单文档子图
python3 scripts/graph_export.py delete --kid <kid> --kb <kb_id>
```
- **数据源 = 已建 wiki 页面**（entity-b/entity-r 的 content），权威、无需回源长句/中间产物。
  跨文档合并后的共享页按 source_refs 过滤出本 kid 的归属实体/规则
- **解析要点**：entity-b「所属本体」行是**无序号类别名**（`业务本体 > 许可事项 > 首次进口药材
  审批`，目录才带 `3-` 序号前缀）——类别校验用 `BIZ_CAT_NAMES`（剥序号集合），不能用带序号的
  BIZ_CATS（否则全部落默认「业务实体」）；entity-r「约束对象/关联对象/来源制度」单元格可含
  `[[slug|名]]`（内部竖线）——取单元格必须用行锚定正则 `\|\s*字段\s*\|\s*(.*?)\s*\|\s*\n`
  （re.S）跨过 wikilink；关联对象拆 token 只认 OBJ_ROLES 白名单角色
- **每次文档重建自动触发**（幂等：先删后写）；失败不中断 wiki 构建（打印修复命令）
- **图谱 ≠ 前端图谱 tab**：Neo4j 语义图（本技能写的）与前端 WikiBrowser「图谱」页（wiki 页面
  wikilink 图，`wiki/graph` 接口）是两套数据，互不相干

**盘查/验证（本机）**：Neo4j 节点 label = `ENTITY<kb_id 下划线化>`（`-`→`_`），kg 也是 label
（`ENTITY<kid 下划线化>`），**kg 不是属性**——按属性查 `WHERE n.kg=...` 查不到：
```bash
sudo docker exec WeKnora-neo4j cypher-shell -u neo4j -p password -a bolt://localhost:7687 \
  "MATCH (n) WHERE n:ENTITY06c769e2_4b14_4714_9ef7_e8b366f79bed RETURN count(n);"
sudo docker exec WeKnora-neo4j cypher-shell -u neo4j -p password -a bolt://localhost:7687 \
  "MATCH (a)-[r]->(b) WHERE a:ENTITY<kid下划线化> RETURN type(r) AS t, count(*) AS c ORDER BY c DESC;"
```
2026-09-08 存量实写验收：3 篇（进口药材管理办法/另两篇大部头）732 节点、753 关系（写入 851 条
payload，库内按 (端点,type) 幂等合并后 753），边型分布 处罚158/管理141/触发条件140/制定于112/
约束109/审批38/程序20/执行15/定义8/引用5/豁免4/时限2/依据1，哑节点（无 attributes）=0。

## 脚本清单（31 个，主链路与供管族共用管线）

管线：weknora_rpc.py（LLM/tool 统一通道，读技能根 config.yaml）→
**gen_summary.py（先建目录+摘要）** → **build_full.py（长句/关键词/实体/合并）** →
extract_long_sentences_v2.py / extract_entities_single.py / extract_rules_batch.py →
discover_terms.py + filter_terms_llm.py → 门禁 check_ls_template.py →
mine_hidden_relations.py / describe_hidden_relations.py（隐性关联）→
merge_duplicate_entities.py / 各类 backfill_/fix_/rebuild_ 脚本。

## 单篇构建流程（2026-09-07 试点实跑）

```bash
cd /home/ctyun/.hermes/skills/devops/market-regulation-policy-compiler/scripts
# 1. 摘要（建法规根目录 + 三子目录 + 本体子目录 + 摘要页）
python3 gen_summary.py <kid> <法规名> '' <源文件名>            # 法规名=去扩展名文件名
# 2. 完整构建（长句/关键词/实体；默认 Step 9.5 自动写 Neo4j 图谱，--no-graph 跳过；
#    --skip-final 用于批量，批次末统一合并）
python3 build_full.py <kid> <法规名> '' <源文件名> --skip-final
# 3. 批次末：合并 + 链接重建
python3 merge_duplicate_entities.py --kb <kb_id> && python3 -c "import weknora_rpc as wr; wr.KB='<kb_id>'; wr.mcp_init(); print(wr.rebuild_links())"
# 3b. 批次末必跑：占位符清理 + broken_link 批量修复（2026-09-08 十篇批次实测缺一不可）
#   fill_entity_placeholders: 业务实体页关键规则/关联实体（待补充）占位符清理（build_full 未内置，坑 87/100）
#   fix_broken_links: 实体/规则链接到未建页（规则对象补建 SKIP、跨文档分类不一致等）批量降级/换链
#     每篇：python3 fill_entity_placeholders.py <kid> --kb <kb_id> --summary-slug summary/<md5(kid)> --summary-title <摘要标题>
#     wiki_lint 出 broken_link 后：python3 fix_broken_links.py --kb <kb_id> --lint-file <wiki_lint输出文件>
#   （2026-09-08 十篇批次实测：fill 清占位符 10 篇×若干页；309 broken_link → fix 后 0）
# 4. 门禁验证
python3 check_ls_template.py --kb <kb_id> --page-type original_sentence|frequent_keyword|business_ontology|rule_ontology|summary
```

**注意**：build_full.py 的 family 参数=法规名（无版本，传空串 `''` 作 version）；
`--format` 仅 pdf/doc 双格式并存时用（法规库罕见，目录名带 `-pdf`/`-doc` 后缀）。
**图谱写入**：Step 9.5 在实体抽取后、跨文档合并前执行（数据源=本 kid 刚建页，
kg=该 kid，先删后写幂等）；实体页缺失/为 0 的文档写图=空图，属正常（该文档无
entity-b/entity-r 页可建模）。

## 试点实测数据（2026-09-07，3 篇）

| 文档 | 位阶 | chunks | 长句 | 关键词 | 业务实体 | 规则实体 |
|---|---|---|---|---|---|---|
| 进口药材管理办法 | 部门规章 | 17 | 31 | 15(12新+3合) | 20 | 9 |
| 医疗器械监督管理条例 | 行政法规 | 78 | ~100 | 15 | ~90 | 部分（aiaaa.cc 超时丢批，补抽兜底） |
| 药品管理法 | 法律 | 76 | ~190 | 15(11新+4合) | 注册表 218 页 | 进行中 |

**坑（试点发现）**：
- **aiaaa.cc 大输出超时（2026-09-07 实测）**：实体抽取 JOINT_BATCH=25 句/批 + max_tokens 20000
  在法规大文档（78+ chunks）上频繁 `read operation timed out`/`Remote end closed connection`，
  且 finish_reason=length 0 chars（坑 39 同款）→ 联合批失败由 extract_rules_batch 补抽兜底，
  但补抽同样可能超时，**导致大文档规则实体缺失**。规避：① 大文档实体抽取跑前先
  `export HERMES_CUSTOM_AIAAA_CC_API_KEY=...`（terminal 前台跑，execute_code 沙箱无此 env 会 401）；
  ② 失败批事后单独重跑 extract_entities_single.py（JOINT_BATCH 可在脚本内临时调小到 15 句/批）；
  ③ 单篇串行构建，禁止多篇并发（坑 112 同款）。
- **LLM 过滤子进程 600s 超时降级硬过滤**（坑 104 同款）：filter_terms_llm 在 build_full 内
  subprocess timeout=600，超时→apply_hard_filter，不阻断构建（法规试点 1/3 篇中招，正常）。
- 法规正文「第X条」长句 title 可能以条款号开头（如「第二十八条 国家药品监督管理局」）——
  门禁不拦（长句 title 非实体），属正常。
- **门禁介词「经」无词边界误报（2026-09-08 十篇批次实测，2026-09-08 修复，供管版同款回补）**：
  `_prep` 正则 `^(按照|…|经|由|…)` 中单字「经」匹配任何「经」字开头标题——「经营异常名录」
  「经常居住地址」「经营主体登记」全被误报为介词引导动作短语（坑 81 同源缺陷）。
  **修复**：`经(?!营|常|费|纪|济|典)`（只排除名词性前缀；**不能**排除 验/过/手/销——否则
  「经验收合格的药品」「经检验合格的样品」等真介词伪实体漏网）。供管版 check_ls_template.py
  同款修复。**回归单测**：经营* 全放行、经检验/经验收/经本单位…后施行 仍拦。
- **read_page 偶发 HTTP 404 崩溃（2026-09-08 化妆品篇实测：整篇实体=0）**：网关对不存在页面
  惯例返回 HTTP 200+body error，但**偶发返回真 HTTP 404** → `rpc()` 重试 5 次后 raise
  HTTPError → `extract_entities_single.create_page` 幂等查重（453 行 `wr.read_page(...)`）崩溃，
  脚本中断 esm 未保存 → build_full 判「实体抽取产物缺失」→ 该文档实体页=0（化妆品 38 chunks
  实测中招，联合抽取 91 实体/68 规则全丢）。**修复**：`weknora_rpc.read_page` 包 try/except
  HTTPError 404 → 返回 `{'error': ...}`（维持「页面不存在」语义，坑 110），惠及全技能所有
  read_page 幂等查重点；extract_rules_batch 同受益。供管版同款回补。**补跑**：重跑
  extract_entities_single 即恢复（化妆品补跑 325 实体/143 规则，错误 0）。
- **指示定语门禁误伤真实机关（2026-09-08 化妆品篇「国务院卫生行政主管部门」）**：市场版指示
  定语表含「国务院」→ 「国务院卫生行政主管部门」等**真实机关实体**（监管职责）被误报
  （坑 91 拦的是本法/本规定/本单位类指代，国务院不是指代）。**修复**：表移除「国务院」
  （保留 本法/本规定/…/各级市场监督管理部门/各级人民政府）。判断：实体名是否指真实可指称的
  机关/主体——国务院+机关名 是，本法/各级 不是。
- **graph_export 移植坑（2026-09-08 实测修复）**：
  ① entity-b「所属本体」行是**无序号**类别名（`业务本体 > 许可事项 > …`，目录才带 `3-`），
  类别校验必须剥序号（`BIZ_CAT_NAMES`）——用带序号的 BIZ_CATS 比对恒失配，全部节点
  attributes 落默认「业务实体」，真实业务类别静默丢失（Cypher 抽查 attrs 才暴露）；
  ② 边端点必须与节点注册同口径 canonicalize：`_edge_for` 返回的 subject 是未剥壳原始名，
  `_rel` 直接落 payload 会让 graph_add 对 (原始名) 自动补建**无 attributes 哑节点**
  （实测 7 个，名=含括号注释的长名词短语）——`_rel` 内部统一 canonicalize(s/t)；
  ③ role=主体 反转边时宾语变边起点，循环只注册了终点——起点漏注册同样补建哑节点，
  两端都要 `_node()` 注册（实测残留 1 个「直辖市人民政府」）。
  验收：全量重写后 Cypher `count(n)` == payload 节点数 && 无 attributes 节点 = 0。

## 域化提示词要点

- BIZ_PROMPT 名词性硬约束（2026-08-29 供管同款 + 法规域化）：禁止介词引导动词短语、
  动作动词结尾描述、指示定语衍生表达（本法/本规定/本办法…）；判断标准=能填"XX是什么/是谁"名词框架。
- 规则卡 title=LLM 概括（≤20字）、evidence=原文关键片段（逐字）、无长句链接 SKIP、规则卡不合并。
- 摘要「与其他法律法规的关系」：库内文档双链 [[summary/<md5(kid)>|法规名]]、库外纯文本《引用名》，
  原文依据列逐字摘录（禁止"暂无引用原文"）；backfill_summary_relation_ls.py 建后回填长句双链。

## agent-gateway 触发模式（2026-09-10 部署 50 环境后新增）

本技能部署在 50 环境 agent-gateway 的 `/srv/gateway/skills/market-regulation-policy-compiler/`
（宿主机 `/home/jenkins/.hermes/skills/` 只读挂载，改宿主文件后需重启 agent-gateway 容器生效）。
由 WeKnora 文档处理完成事件 → agent-gateway `POST /tasks` 触发 agent 执行。

### 传参方式（多知识库动态触发，2026-09-08 供管版同款机制）

- 任务提交时在 `config_json` 里带 `kb_id` / `knowledge_id` / `doc_name`，gateway runner.py 会注入
  进程环境变量 `WEKNORA_KB_ID` / `WEKNORA_KNOWLEDGE_ID` / `WEKNORA_DOC_NAME`。
- `wr.load_config()` / `wr.load_kb()` 自动让 **环境变量优先于技能 config.yaml 的固定 kb_id**——
  被触发的文档在哪个库，脚本就处理哪个库，无需改 config 也无需传 `--kb`。
- 脚本内请勿在读取 KB 前覆盖这些环境变量。

### 入口脚本

```bash
# agent 触发统一入口（查文档元数据 → gen_summary → build_full → 合并/链接）
python3 scripts/run_one.py <kid> [--kb <kb_id>] [--format doc|docx|pdf|xls|xlsx]
                                [--title <摘要标题>] [--dry-run] [--skip-final] [--no-graph]

# ⚠️ 标准别名（agent 提示词里的 script 名，等价 run_one.py，无参亦可）
python3 scripts/build_wiki.py <kid> [--kb <kb_id>]   # kid 缺省从 WEKNORA_KNOWLEDGE_ID 读
```

**⚠️ 重要**：`build_full.py` 是底层阶段脚本，需要 `<kid> <法规名> '' <源文件名>` 四个位置
参数，**不是 agent 入口**——直接 `run_skill_script(script='build_full.py')` 无参调用会报
"the following arguments are required: args"。agent 构建一律走 `run_one.py` /
`build_wiki.py`。

也可直接分步：
```bash
python3 scripts/gen_summary.py <kid> <法规名> '' <源文件名> --kb <kb_id>
python3 scripts/build_full.py  <kid> <法规名> '' <源文件名> --kb <kb_id> [--skip-final] [--no-graph]
```

### trace 日志写入（wiki_log_write）

- `weknora_rpc.py` 提供 `wr.log_progress(action, knowledge_id, doc_title, summary, kb_id, page_slugs)`，
  底层调 MCP `wiki_log_write`（需 API key 具备 write 能力），**失败仅告警不阻断**构建主流程。
- `build_full.py` 已埋点：`agent_build_start`（构建开始）→ `agent_build_dirs`（目录就绪）→
  `agent_build_entities`（实体抽取开始）→ `agent_build_done`（完成）；`gen_summary.py` 埋
  `agent_build_summary`（摘要页建成）。
- 前端进度面板 `/ui/tasks/{task_id}` + WeKnora 侧 wiki 处理日志可看到这些进度。

### 配置（config.yaml）

- `url` 指向 50 环境 mcp-gateway：`http://10.1.215.50:8001/mcp/`（容器内经宿主机 IP 访问）。
- `api_key`：WeKnora tenant API key（部署时从供管版 config.yaml 复制，具备 wiki write 能力）。
- `kb_id`：市场监督管理知识库 `06c769e2-4b14-4714-9ef7-e8b366f79bed`（环境变量 WEKNORA_KB_ID 优先）。

### 模型配置（2026-09-10 起：任务级 > Agent 主进程 > config.yaml）

- **技能内所有 LLM 调用（llm_call）的配置优先级**：
  1. **任务级** `WEKNORA_LLM_MODEL` / `WEKNORA_LLM_BASE_URL` / `WEKNORA_LLM_API_KEY`
     （WeKnora 按知识库绑定的模型随任务 config 传入，gateway runner.py 注入——每个 KB 可用自己的模型）
  2. **Agent 主进程** `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY`（agent-gateway compose .env 注入）
  3. 技能 config.yaml 的 `model:` 段（兜底）
  4. 内置默认（deepseek）
- 改全局模型/端点/key：改 `~/chengkai/agent-gateway/.env` + `docker-compose up -d`，技能脚本实时生效。
- 实现：`weknora_rpc.py` 的 `_default_model()` 与 `llm_config()`（2026-09-10 WEK-46 起支持任务级）。
- 注意：本机（非容器）跑脚本时无 WEKNORA_LLM_*/LLM_* 环境变量，自动回退 config.yaml，行为不变。

## 已知待办

- references/（folder-structure/ontology/verification）未建——先参照 supply-management-policy-compiler 的
  references/，再按法规目录模型调整（无家族层）。
- build_relations.py 未域化（供管版逐字）：家族归并逻辑不适配法规库，明确引用匹配（《XX法》→库内文档）
  可用；试点未跑血缘（gen_summary 不传 --lineage，关系表由 LLM 从原文直接提炼，实测够用）。
- check_folder_structure.py 未移植：目录门禁暂用 check_ls_template + wiki_lint 替代。
- 500 篇全量构建成本高（试点单篇 15-60 分钟，LLM 为主）：建议按领域/位阶分批，每批 10-20 篇，
  并发 ≤2 个 LLM 进程。

## 相关

- `supply-management-policy-compiler`：同族本源（脚本/坑/门禁直接复用，域化差异见上表）；
  `sichuan-network-ops-wiki` / `yijing-data-assets-wiki`：知识蒸馏库精简先例。
