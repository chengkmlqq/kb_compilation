# KB Compilation Platform — 进度记录

> 本文档用于跨会话续接工作。每次工作结束更新「当前状态」；新会话开始先读本文档。

最后更新：2026-10-14（移除直连 LLM 文档摘要链路，摘要统一走编排）

## 16. 当前状态（2026-10-14 移除直连 LLM 文档摘要链路）

- **背景**：查「测试知识库」中『湖北政企短彩业务本体 v1.0.docx』摘要来源时确认两套并行链路：① 前端自动摘要（KbDocsPane useEffect）→ POST /{kb_id}/documents/{document_id}/summary → kb_admin.generate_document_summary 直连 ChatClient（用 KB summary_model_id，未配 thinking=disabled → 思考草稿混入，实测该 doc 的 kb_document.summary 713 字符以「150~250 字」复述+思考过程开头）；② 上传链摘要编排（doc_process._enqueue_summary_task → SUMMARY_RUN_* Job → kb_tape_run，产物是 wiki 摘要页，不写 kb_document.summary）。用户确认该 doc 的编排 run（03:24:26 Map-Reduce / 03:28:22 Refine 均 success，wiki 页 doc-1737d45155-* 时间戳与 run 完成秒级吻合）与数据库态（summary 字段直连 LLM 特征）后决定：**删直连 LLM 链路，摘要只允许走编排**
- **改动（后端 2 文件 + 前端 2 文件 + 注释 3 处）**：
  1. `api/services/kb_admin.py`：删 generate_document_summary 函数 + SUMMARY_SYSTEM_PROMPT / SUMMARY_CONTENT_LIMIT 常量
  2. `api/routers/kbs.py`：删 POST /{kb_id}/documents/{document_id}/summary 端点 + import
  3. `worker/tasks/table_ingest.py`：删表摄取第 4 步（generate_document_summary 复用调用），步骤注释 4/5 → 4；docstring 同步。保留 _generate_table_usage（表用途说明，属 wiki 构建非文档摘要，与 KB summary_model_id 无关——注意它仍直连 ChatClient 但产出是 wiki 页段落，不在本次删除范围）
  4. `web/src/lib/api.ts`：删 apiGenerateDocSummary
  5. `web/src/components/kb/KbDocsPane.tsx`：删自动摘要 useEffect（autoSummaryRunning/autoSummarySeen/autoTick）+ import
  6. 注释：kb_admin.py 删除处留说明、api/models/knowledge.py summary 字段注释更新、seed_framework_db.py 摘要编排注释不再引用已删函数
- **保留不动**：kb_document.summary/summary_status/summary_error 列（存量数据展示，DocDetailDrawer/KbDocsPane 表格摘要列仍渲染历史值）；kb_datasource.summary_strategy（off/auto/stuff/mapreduce/refine）与 SUMMARY_TAPE_* 参数（编排路由需要）；summary_model_id 列（KB 配置 UI 仍可设，编排 bindings 注入仍在 orchestration_engine.py:689）
- **验证**：py_compile 4 文件 OK；pytest 全量 575 passed / 3 failed（3 红为既有基线 test_agent_skills_scope/test_files/test_storage，见 §15，无新增回归）；web tsc --noEmit exit 0
- **部署**：api/worker 改动 → CI 重建 kb-api-server/kb-worker/kb-agent-worker；前端源码改动 → 重建 kb-web。容器内热修可用 docker cp + restart，正式走 CI
- **遗留（未做，另行确认）**：测试知识库 kb_document.summary 存量脏值（思考草稿混入）未清洗；该 doc 两个编排摘要 wiki 页 source_refs=[]（删文档会残留孤儿页，契约要求 {document_id}| 前缀）

## 15. 当前状态（2026-10-14 全量 pytest 既有红修复：11 failed → 3 failed）

- **背景**：修数据查询左树（§14）后跑全量 pytest 得 564 passed / 11 failed；用父提交 1c21c3f 的干净 worktree 复跑同样 11 个失败（一模一样的用例）证明均早于本次改动，非新增回归。逐条归因后修掉两类：
- **① `api/services/chat_sessions.py` 两处调用漏传 db（真 bug，非测试问题）**：`set_message_feedback`（原 271 行）与 `append_message`（原 304 行）调用 `_message_dict(m)`，而该函数签名早已是 `_message_dict(db, m)` → `TypeError: _message_dict() missing 1 required positional argument: 'm'`。回归来自 ba53a28（「问答对齐 WeKnora 交互样式 7 项」给签名加 db 时改了 4 处调用、漏了这 2 处）。**影响面不止测试**：`append_message` 是消息持久化必经路径、`set_message_feedback` 是反馈成功路径，部署后这两条路都会 500。已修（两处补 `db`），test_chat_sessions 6 例转绿
- **② `tests/test_framework_models.py` 白名单漂移**：KNOWLEDGE_BUSINESS_TABLES 缺 `wiki_page_folder`（迁移 0015）、`kb_quality_run` / `kb_quality_report`（迁移 0018，随 5f52ff0 引入）→ 补白名单（PROGRESS 坑位 6 的同款：他人新增表未同步白名单，补上即绿），2 例转绿
- **剩余 3 红（不在本次范围，留给对应负责人）**：`test_agent_skills_scope::test_agent_qa_default_has_no_tools`（默认 agent 现已注入 wiki_write_page/database_query/get_document_info/data_analysis 等内置工具，断言仍写死 `{kb_search}`，属他人功能改动后的用例过期）；`test_storage::test_is_remote_and_parse_build`、`test_files::test_files_minio_tree_zip_and_raw_download`（storage `prod/` 前缀断言未同步，PROGRESS 已在案的既有失败，勿动 storage 语义）
- **验证**：全量 `.venv/bin/python -m pytest tests/ -q` → **575 passed / 3 failed**（失败均为上述既有项）

## 14. 当前状态（2026-10-14 数据查询页左树看不到数据源修复）

- **症状**：数据查询（/datagrid）页左侧 Database 目录树永远为空，数据源管理页配置的数据源一个都不显示（连 mysql 这类可查询类型也不显示）
- **实测证据**：playwright 驱动真实 Chromium 打开线上 /wiki/datagrid（admin/ROOT）——`GET /api/v1/datagrid/datasources` 返回 200（含 kb_biz/mysql 一条），但 DOM 中 `.ant-tree-title` 节点数 = 0；同账号 `GET /api/v1/open/datasources`（数据源管理页）有 2 条（kb_biz + Minio）
- **根因（前端状态初始化）**：`datagrid/components/db-tree.tsx` 用 `useState(() => (data || []).map(...))` 建树，初始化只在挂载时执行一次；而数据源列表由父组件 `components/client.tsx` 的 `useEffect` **异步**拉取（首帧 dsList=[]），数据到位后组件只重渲染、`treeData` 状态不更新 → 树永远停在空态。父组件 PanelGroup 的 `key`（`h-{key}-{leftSize}`）在挂载后会因 leftSize 计算而 +1 触发一次重挂载，但该重挂载发生在 fetch 返回之前，救不回来（上游 data-synth 传的是服务端预取的 initialData，迁到 kb 后改成客户端拉取，漏了 prop→state 同步）
- **修复**：抽出 `buildInitialTree(data)`，`useState` 初始化复用它，并新增 `useEffect(..., [data])`——data 变化时重建 treeData / expandedKeys 并清空懒加载缓存
- **验证（本机生产构建 + 线上真实后端）**：`tsc --noEmit` exit 0；`bun run build` 通过；本地起构建产物（:3111）+ 反向代理（:3112，/api/* 转发线上后端），playwright 实测：初始树出现「kb_compilation 业务库」→ 展开数据源得到 表/视图/函数/过程/序列 → 展开「表」经 /datagrid/tables 拉回 kb_frame 真实 80 张表
- **附带口径说明（非 bug）**：数据查询只列**可 SQL 查询**的类型（`api/services/datagrid.py` SUPPORTED_DS_TYPES = mysql/postgresql/postgres/dm/oracle/elasticsearch/kingbasees8/hive/trino/trinodb）+ state=1；对象存储类（minio/s3/oss/obs）与 goldendb/pg 不在列，故数据源管理里的 Minio 本就不会出现在数据查询树（数据源管理页无类型/状态过滤，两页口径不同）。非 admin 用户还受团队授权限制（modo_team_ds_map，当前为空 → 非 ROOT/默认团队成员看不到任何数据源）
- **部署**：前端为源码改动 → 需 CI 重建 kb-web 镜像后 `docker-compose pull web && up -d web && restart nginx` 才生效

## 13. 当前状态（2026-10-14 问答反馈报「消息不存在」修复，commit 待推）

- **症状**：智能问答页点「喜欢 / 不喜欢」报错 `消息不存在: local-ai-1791941609271`（HTTP 404）
- **根因**：前端流式渲染时助手消息用**本地占位 id**（`local-ai-<时间戳>`，chat/page.tsx 的 placeholder；用户消息 `local-user-*`、续传占位 `resume-ai-*`），而后端 `done` 事件只发 `{"type":"done"}` 不带持久化消息 id → 前端永远保留占位 id；反馈接口 `POST /api/v1/sessions/{sid}/messages/{mid}/feedback` 按 id 查库 → 404。刷新页面后消息 id 来自 `GET .../messages`（服务端 id），反馈又正常——这正是该问题「看起来偶发」的原因
- **修复**：
  1. 后端 `api/routers/qa.py`（知识库问答流）：持久化用户/助手消息时取出返回的 `id`，`done` 事件回传 `{"type":"done","message_id":<助手消息id>,"user_message_id":<用户消息id>}`
  2. 后端 `api/routers/agents.py`（智能体问答流）：会话模式同款回传
  3. 前端 `chat/page.tsx`：`send()` 收到 done 时经 `adoptServerMessageIds()` 把占位 id 替换为服务端 id（同时改写 `placeholder.id`，后续 streaming / 追问建议更新继续命中同一条消息）；续传 `tryResume()` 收到 done 同样对齐（`resume-ai-*` → 服务端 id）
  4. 前端兜底：`toggleFeedback()` 遇到本地占位 id（`^(local|resume)-`）先经 `resolveServerMessageId()` 从 `GET .../messages` 回捞真实 id（内容一致，或为本地内容的续写——停止生成时本地只收到前半段），回捞不到则提示「该回答尚未保存，请稍后重试」，不猜测
  5. `web/src/lib/api.ts`：`QaStreamEvent` 补 `message_id` / `user_message_id` 字段
- **验证**：本轮按用户指定未跑 pytest / tsc，改动已静态复核（占位 id 相关分支逐一核对）；实机验证需重建 kb-web / kb-api-server 镜像（前端为源码改动）

## 12. 当前状态（2026-10-13 任务监控页「看不到编排任务」全面修复，commit 待推）

- **问题现象**：任务监控页看不到编排执行（编排管理页执行后，监控页像没有该任务）
- **根因（3 处盲区，全部实测确认）**：
  1. **状态筛选大小写不匹配**：前端筛选下拉传大写（RUNNING/SUCCESS/... job-filter.tsx），后端对编排 run 直接 `TapeRun.status == state`（小写库值）→ **选任何状态筛选，编排 run 全被过滤掉**
  2. **「全部任务」视图不含编排**：list_jobs 只查 modo_job，编排 run（kb_tape_run）完全不在列
  3. **主任务视图 run 只在第 1 页**：list_parent_jobs 里 `if page == 1:` 才查 run，且 run append 在 20 条 job **后面**（注释写"并置最前"但实现相反）；total 又是 job+run 相加 → 数字对不上，翻页 run 消失
  4. **附带**：statistics 只 group by Job 表，统计卡不含编排；get_job/stop/delete 对 run id 全 404（点编排行「日志/停止/删除」都不可用）
- **修复（api/routers/jobs.py 5 处 + 前端 1 处）**：
  1. 新增状态映射 `_RUN_STATE_TO_JOB`/`_JOB_STATE_TO_RUN` + `_run_state_to_job`/`_job_state_to_run`：筛选（前端大写→查库小写）与序列化（库小写→输出大写）双向归一化，两个视图两处筛选统一经此
  2. list_parent_jobs / list_jobs 均重写为**窄列全量载入（只取 id+时间戳，不触碰大 JSON 列）+ 应用层合并排序（时间倒序 job/run 混排）+ 切片 + 按 id 回表**：既根治 §8/§11 的 1038 filesort，又让 run 真正混入时间线（翻页不再消失、最新 run 排最前、total 与行数对账）
  3. list_jobs 并入编排 run（task_class/queue_name 显式筛选非 OrchestrationRun/orch 时 run 不参与）
  4. job_statistics 并入 run 状态（映射大写后合入 counts），统计卡口径与列表一致
  5. get_job / stop_job / delete_job 支持 TapeRun：详情返回 `_run_to_job_dict`；停止置 run.status=cancelled（引擎 advance 终态守卫 status not in (queued,running) 即不再推进）；删除删 run 行
  6. 前端 job-table.tsx（全部任务视图）补 `OrchestrationRun: "编排执行"` 标签（主任务视图 parent-job-table 早已适配）；顺手修 delete_job 里 `logger` 从未定义（原 revoke 失败分支会 NameError）
- **验证**：test_jobs_monitor 14 passed（新增 `test_parents_run_state_filter_mapping_and_mixed_paging` 覆盖状态映射/混排/全部视图/statistics/get/stop）+ test_jobs_monitor_ext + test_orchestration_engine 17 passed；`tsc --noEmit` 通过；.venv py_compile OK
- **卡住 run 处理（闭环 §8 遗留）**：`5e9d3f1b`（03:53 429）与 `ee8d9044`（06:16 read timeout）两条 Refine run 卡 running 数小时——最后节点 LLM failed 但旧镜像 advance 未把终态写回 run。确认 5 个 celery worker active 队列全空（无活跃执行者）后置 status=failed + error 标注"最后节点失败未传播"，现库内无 running/queued 残留
- **部署**：本机容器为镜像版本（api-server 42 分钟前 Up 但未含本次改动）→ 本次已提交推送，等 GitHub Actions 构建后 pull 生效

## 11. 当前状态（2026-10-13 编排「运行历史」列表 500 修复，commit 待定）

- **症状**：编排管理「运行历史」抽屉 `GET /api/v1/orchestrations/{tape_id}/runs?page=1&pageSize=20` 500（nginx 实测连续 500，body 21B）；同 tape 的 `runs/{run_id}` 详情轮询 200（447KB body）
- **根因**：`list_tape_runs`（api/services/orchestration.py）`db.query(TapeRun)...ORDER BY created_at DESC` 全列排序 —— kb_tape_run 大 JSON 列（step_results/bindings 单行可达 ~940KB，见 §8 同一 run `ee8d9044` 详情响应 447KB），16 行小表优化器选 filesort，宽行塞 256KB sort_buffer → MySQL 1038 `Out of sort memory`。容器内直跑 SQL 复现：`SELECT * FROM kb_tape_run WHERE tape_id=... ORDER BY created_at DESC LIMIT 20` → 1038
- **修复**：与 §8 jobs parents 同模式——排序只取窄列 id（filesort 只碰 id 行），再 `IN` 回表取全行按 pos 保持顺序；count 用窄列子查询。容器内直调 list_tape_runs 正常（total 17/17 全返 + 翻页空）；HTTP 全链（admin 登录 → /runs）200 total 17；pytest test_orchestration_engine + test_jobs_monitor* 30 passed
- **坑位（同类再踩）**：**凡是 `kb_tape_run`（及任何含大 JSON 列的表）的 ORDER BY 全列查询，一律先排窄列 id 再回表**——§8 parents 只修了 jobs.py，编排 runs 端点同款未修，隔几小时又爆。排查此类 500 的快捷实锤：容器内直接跑同款 SQL 看 1038
- **部署**：api/services/orchestration.py 已 docker cp 热修 + restart api-server（重启不丢，重建容器回镜像），正式代码已提交，下次 CI 构建固化

## 9. 当前状态（2026-10-13 问答反馈「喜欢/不喜欢」，commit 待推）

- **功能**：智能问答页每条 AI 回复答案工具栏（复制旁）新增「喜欢 / 不喜欢」按钮，再点一次取消；选中态高亮（喜欢主色 / 不喜欢红色）。未选中的会话级别无入口限制，仅对 AI 回复可反馈
- **后端**：`chat_message` 新增 `feedback` / `feedback_at` 两列（迁移 **0014_chat_message_feedback**，链 0013，幂等 ALTER）；`POST /api/v1/sessions/{sid}/messages/{mid}/feedback` 体 `{feedback_type: like|dislike|null}`（null=清除；仅 assistant 消息，越权/消息不存在 404 拦截）；`_message_dict` 透出 feedback/feedback_at 供前端回显
- **前端**：chat/page.tsx `toggleFeedback`（乐观更新 + 失败回滚 + toast）；api.ts 新增 `apiSubmitMessageFeedback`
- **测试**：test_chat_sessions.py +2 例（like→切换 dislike→清除、用户消息拒绝、他人会话越权、消息不存在），全量 **571 passed**（2 个既有失败 test_files/test_storage 仍为 storage prod/ 前缀断言未同步，与本次无关）

## 10. 当前状态（2026-10-13 编排「摘要-Refine（增量修订）」质量优化，commit 待定）

- **背景**：用户要求测试该编排（tape `103e45f4`，draft，7 节点链：读切片→分批→循环→拼Prompt→LLM→回写→写页），摘要质量差
- **实测质量结论**：初版输出整段思考草稿（英文推理+逐字数字数+自我修正，2281 字符写进 wiki 页）；换 system/prompt 约束后模型行为仍不稳定（中文复述/英文条款草稿/草稿+最终版混排）
- **根因**：inferai 网关的 deepseek-v4-pro **默认 thinking 开启**，思考过程直接写进 content（reasoning_content 通道之外的 content 内输出）。ChatClient 有 `thinking` 参数（payload `{"type":"disabled"}`）但编排节点不透传
- **修复（三层）**：
  1. **引擎**（`api/services/orchestration_engine.py` KbLlmChatStep）：① config.system 支持（{{ }} 模板渲染，此前只发 user 消息）；② **config.thinking 透传**（"disabled" 根治思考草稿）；③ **429/403/5xx 退避重试**（此前无重试，多轮循环撞 inferaiapi 限流 ~3 次/分必失败——凌晨 run 第 6 轮 429 实证）；max_attempts 默认 5、429 退避 15s→45s
  2. **编排数据**（kb_tape_step + kb_tape.nodes + kb_tape_template 0104d61d 同步）：n5 加 system（禁思考/复述）+ thinking=disabled；n4 prompt 结构化（已有摘要/新内容分块、去字数数字避免数数焦虑）；n2 batch_size 3→5（少轮次降限流）；n6 逐行过滤思考特征词兜底
  3. **测试夹具**：test_kb_llm_chat_skip_empty 的 fake_chat 补 thinking 参数
- **验证**：小样本（山东省厂务公开条例 docx，5 chunks/2 轮）实测 6 轮 run 全 success；thinking=disabled 后输出干净（613 字符：主题句+6 要点，无草稿/复述/数数），wiki 页纯净写入；引擎测试 15 passed + build/jobs 24 passed
- **坑位**：deepseek-v4-pro（inferai）做自由文本生成（摘要类）默认输出思考过程到 content——**编排内 LLM 任务一律配 thinking=disabled**；wr.llm_call 脚本（带 system「只输出JSON」）不受影响
- **注意**：编排数据为库内修改，用户在服务编排页「保存设计」会覆盖式同步回库（模板/tape 均已同步，覆盖也只回同一配置）

## 8. 当前状态（2026-10-13 任务监控页 500 修复，commit 待定）

- **症状**：任务监控页首屏 `GET /api/v1/jobs/parents?page=1&page_size=20` 500
- **根因**：`kb_tape_run` 含大 JSON 列（step_results/bindings，实测单行 ~940KB：来自 03:53 的 Refine 编排 run `5e9d3f1b`，step_results 330KB + bindings 607KB）。端点按 `created_at DESC, id DESC` 全列排序，表只有 19 行 → 优化器按成本模型选 filesort（EXPLAIN key=NULL, Using filesort，不认新建索引），宽行塞 256KB sort_buffer → MySQL 1038 `Out of sort memory`
- **修复（双层）**：① `api/routers/jobs.py` run 查询改为**先取窄列 id 排序分页 → IN 回表取全行**（filesort 只碰 id 行，永不爆 buffer，不依赖优化器选索引）；② 迁移 **0013_job_sort_indexes** 补 `kb_tape_run(created_at DESC, id DESC)` + `modo_job(create_time DESC, id DESC)` 降序索引（数据量大后优化器走索引序；已在运行库手动建，迁移幂等跳过）
- **验证**：容器内直调 list_parent_jobs/list_jobs 正常（parents 1057 / jobs 1066）；HTTP 全链（admin 登录 → /jobs/parents）200；jobs 相关 pytest **17 passed**（test_jobs_monitor* + test_job_trace_api）
- **坑位**：宽行 JSON 表 + 小表 filesort 是隐蔽炸弹——优化器按行数估成本不会为 filesort 预留 sort_buffer，行宽上千 KB 时任何 ORDER BY 都可能 1038；排序查询要么走索引要么只排窄列
- **遗留**：Refine 编排 run（`5e9d3f1b`，tape「xx-Refine」）status=running 数小时且 bindings/step_results 异常大，疑似把大上下文快照写 bindings，待查（未删数据）；容器 jobs.py 为 docker cp 热修（重启不丢，重建容器会回镜像），正式代码已提交，下次 CI 构建固化

## 7. 当前状态（2026-10-12 智能问答页对齐 WeKnora，commit ad3b132，已推送 main）

- **背景**：与 WeKnora（`/home/jenkins/chengkai/WeKnora` views/chat）逐项比对样式与交互后，完成 8 项对齐（kb 问答页此前已是 WeKnora 的 antd 移植版，本次补齐差距）：
  1. **代码块高亮 + 标题栏/复制按钮**：新增 `web/src/lib/highlight.ts`（自研零依赖 token 高亮，js/python/sql/shell/java/go/rust/c/json/yaml 等 + 别名归一化，One Dark 配色适配 #282c34）；`MarkdownViewer.tsx` 新增 CodeBlock
  2. **引用 hover 浮层**：接通既有 `.kb-citation-float` 样式（fixed 定位、底部空间不足自动上翻）
  3. **拖放上传**：输入卡片 onDragEnter/onDragOver/onDrop + 拖入高亮（图片→预览条、文件→直传）
  4. **打字机平滑输出**：新增 `web/src/components/TypewriterText.tsx`（流式慢速追平、结束加速追平）
  5. **联网搜索开关**：前端输入区 GlobalOutlined 开关 → SSE `web_search_enabled`；后端 `websearch.py` 抽出 `_resolve_websearch_provider`/`_build_websearch_tool_context`，新增 `request_websearch_tool_context`（请求级）+ `agent_websearch_tool_context(enabled_override=...)`；`qa.py`/`agents.py` 请求模型加字段
  6. **深色模式**：新增 `web/src/lib/theme-mode.tsx`（Provider + 首帧防闪烁脚本 + prefers-color-scheme）；antd-registry 暗色叠加 darkAlgorithm；globals.css 新增 `--kb-c-*` 语义变量（亮/暗两套）并把 chat 区硬编码色变量化；AppHeader 加切换按钮；RagPipelineProgress 改 token
  7. **web 引用胶囊**：citation.ts 解析 `<web url="" title=""/>`；MarkdownViewer 渲染品牌色 web 胶囊（点击新窗口 + hover 显示 URL）；`chat.py` CITATION_INSTRUCTIONS 增加 web 标签输出规范
  8. **嵌入模式 + @提及分组 + 会话输入态恢复**：chat 页 iframe/?embed=1/?noframe=1 时隐藏会话列表；@提及弹窗改分组列表+搜索过滤；`chat_session.last_request_state`（迁移 **0012_session_last_request_state**，JSON 快照 agent/kb/模型/联网开关/@目标）——发送成功/停止后写入、openSession 恢复
- **改动文件（21 个）**：前端 `MarkdownViewer.tsx`/`TypewriterText.tsx`（新）/`highlight.ts`（新）/`theme-mode.tsx`（新）/`RagPipelineProgress.tsx`/`citation.ts`/`api.ts`/`antd-registry.tsx`/`layout.tsx`/`globals.css`/`chat/page.tsx`/`AppHeader.tsx`；后端 `websearch.py`/`chat_sessions.py`/`chat.py`/`qa.py`/`agents.py`/`chat_session.py` + 迁移 `0012_session_last_request_state.py`（新）+ `tests/test_websearch_agent_inject.py`（新增 5 例）
- **验证**：`tsc --noEmit` exit 0；`pytest tests/test_websearch_agent_inject.py tests/test_chat.py tests/test_chat_sessions.py tests/test_chat_fork_rewind.py` → **34 passed**（bun build 未跑；tsc 阶段修掉一处真实缺陷：atTarget state 声明在 openSession 之后会 TDZ 崩溃，已上移）
- **部署注意**：改码 ≠ 生效——需 GitHub Actions 手动触发 `build-images.yml` → 本机 `docker-compose pull` + `up -d` + `restart nginx`；部署后 api-server 启动自动 `alembic upgrade head` 执行 0012
- **明确不做**：MCP OAuth 行内授权卡（kb agent 工具链是自研 kb_search/web_search/run_skill_script，无 MCP 工具调用，语义不适用）；@提及的文件/tag/MCP/skill 分组（后端无对应资源端点，本次只做 kb/agent 两分组 UI）
- **遗留（逐页跟进）**：深色模式目前只变量化了 chat 工作台 + 组件 token，其他业务页（kbs/wiki/datagrid 等）的硬编码色在暗色下仍偏亮，建议后续逐页收敛

## 6. 当前状态（2026-10-12 知识库创建「构建引擎」改造）

- **知识库创建表单重构（commit 待定）**：
  - 去除「知识库类型 / 本体 Schema（抽取分类结构）/ 自定义 Wiki 生成」三个表单项；新增「构建引擎」下拉——选项 = 服务编排中**已发布**（kb_tape.status=effective）的编排程序（`GET /api/v1/kbs/build-engines`）
  - KB 绑定存储：`kb_datasource.build_tape_id`（迁移 0009 + scripts/knowledge_business_schema.sql 同步；create/update_kb 校验编排存在且已发布，_kb_dict 序列化回显）
  - **上传链改造**（`worker/tasks/doc_process.py`）：文档解析 READY 后，KB 绑定构建引擎 → `_enqueue_build_engine_task`（建 kb_tape_run env=prod + Job 行 KbOrchestrationRunTask + 投 orch 队列），**不再走内置 skill 链**；未绑定回落原逻辑。编排节点模板用 `{{kb_id}}`/`{{document_id}}` 即可构建 wiki（种子编排「wiki构建-基础原子版」直接可用，需先发布）
  - **orch_runner env 透传**（2026-10-12 环境隔离配套）：execute_run/advance_run/run_node 及各 db 读写函数加 env 参数（None=沿用历史 dev）；execute_tape 拆出 `_create_tape_run`（只建 run 不投递），worker 上传链显式传 "prod" 与文档同库；orch_loop.dispatch_next_round 透传 env
  - 测试：`tests/test_build_engine.py` 新增 8 例（build-engines 端点只返已发布 / create-update 绑定与校验 / 上传链触发 Job+run 行）；修复 test_step_result_upsert_semantics 的 `_run_db` 0 参 mock 适配新签名；顺手补 test_framework_models 白名单 `kb_tape_template`（55c7c00 引入模型但白名单未同步，既有缺陷类，同 bd31f68/dfd8234）
  - 验证：pytest **549 passed**（仅剩 2 个既有失败 tests/test_storage.py::test_is_remote_and_parse_build 与 tests/test_files.py::test_files_minio_tree_zip_and_raw_download——2026-10-12 环境隔离给 storage 路径加 `prod/` 前缀后测试断言未同步，与本次无关，勿动 storage 语义）；前端 tsc 0 错误 + bun build 通过
  - 前端：`kbs/page.tsx` 创建抽屉表单重构（删 type/ontology/custom_wiki 三项 + 清理 Radio/Switch/useWatch），`api.ts` 加 apiListBuildEngines/BuildEngineItem/KbCreatePayload.build_tape_id

## 5. 当前状态（2026-10-10 菜单重新划分）

- **菜单重新划分（方案B，commit c0abe93，已推送）**：
  - 背景：页面梳理发现 ①数据与任务 8 项/系统管理 13 项分组失衡；②切片核对 /chunk-verify 挂在「数据与任务」但属知识域工具；③前端 FALLBACK_MENUS 与后端 seed KB_MENUS 结构漂移，且残留指向不存在页面的死链（/notification 通知管理、/system/retrieval 检索参数——仅存 API `/api/v1/system/retrieval-config`，无页面）；④FALLBACK_MENUS 缺 datagrid/orchestrations/orch_defines/ont_schemas/sys_dims/sys_metadata_collection 等新页面
  - 调整（保持 4 顶级分组）：切片核对从 grp_data 移至 **grp_knowledge**（sort=3），数据与任务恢复 7 项；删除两条死链；FALLBACK_MENUS 与 seed KB_MENUS 完全对齐（均 29 项 = 4 分组 + 25 页面）
  - 改动文件：`scripts/seed_framework_db.py`（KB_MENUS + 注释计数修正）、`web/src/app/(main)/_components/MenuContext.tsx`（FALLBACK_MENUS）
  - 验证：重跑 seed（`docker cp` 新脚本进 kb-api-server 执行，因容器跑镜像代码）后 my-menus API 返回 29 项、结构正确；pytest **495 passed**；tsc 无新增错误（datagrid 的 @codemirror/lang-sql 缺失为已知基线红）
  - 注意：`DocCardView.tsx`（Dropdown 冒泡修复）为工作区未提交改动，非本次任务，未纳入提交
- **代码同步**：从 origin/main 拉取到 bd31f68（文档目录 doc_folder 迁移 + KbDocsPane 更新），推送时 rebase 了他人新提交 690e9cc（上传处理配置）

## 4. 当前状态（2026-10-03 wiki 构建迁入 worker 改造）

- **wiki 构建从 agent-gateway 迁入 worker（改造完成，commit 待定）**：
  - 背景：原链路 `KbDocumentProcessTask` 成功后自动 enqueue `KbAgentGatewayTask` → agent-gateway 内跑技能 `kb-wiki-builder`（build_wiki.py）。依赖 gateway 可用性/技能安装/agent 决策，且 gateway LLM 账户曾 403。
  - 改造：新增 **`KbSkillWikiBuildTask`**（`worker/tasks/wiki_build.py`），worker 内直接：读文档 chunks（`_load_document_chunks` 直查 doc_chunk 表）→ 调 LLM（`WIKI_LLM_*` env，默认 Infer AI deepseek-v4-pro）→ `wiki_create_page/wiki_update_page` 幂等写 wiki 页（slug 冲突走 update）→ `wiki_rebuild_links` 重建互链；**进度实时写 modo_job.error_message**（`[step 1/4]...`，任务监控页 + SSE 日志流可见）
  - 自动链：`doc_process.py` 的 `_enqueue_wiki_build_task`（替代 `_enqueue_wiki_skill_task`），任务名 `WIKI_BUILD_*`，task_class=`KbSkillWikiBuildTask`；`worker/celery_app.py` 导入注册（Tasks registered 现含 6 类）
  - 实测：上传 md → `DOC_2003acd7` SUCCESS → `WIKI_BUILD_065679e729bf` SUCCESS（`[step 4/4] wiki pages written: 7, elapsed=14s`），wiki 页 12→14，**全程 worker 内、不经 gateway**
  - 测试：`test_wiki_build_task.py`（4 例：写页/幂等/缺chunks/LLM错）+ `test_wiki_skill_chain.py` 重写为 KbSkillWikiBuildTask 触发断言，全量 275 passed
  - 改动文件：`worker/tasks/wiki_build.py`（新）、`worker/tasks/doc_process.py`、`worker/celery_app.py`、`tests/test_wiki_build_task.py`（新）、`tests/test_wiki_skill_chain.py`
  - 说明：kb_skill 表 + agent-gateway 的 `kb-wiki-builder` 技能**保留**（平台技能管理可见/可复用），但自动链不再走它；chunks HTTP 端点保留（其他消费方可能用）

## 3. 当前状态（2026-10-03 追加）

- **上传文档自动触发「基于技能的 wiki 构建」（方案 1，commit 待定）**：
  - 链路：上传 → `KbDocumentProcessTask`（解析/向量化）SUCCESS（`parse_state==READY`）→ 自动 enqueue `KbAgentGatewayTask`（WIKI_SKILL_* 任务，任务监控可见）→ agent-gateway 执行技能 `kb-wiki-builder` 的 `build_wiki.py` → 读文档 chunks（新增 `GET /kbs/{kb_id}/documents/{doc_id}/chunks` 端点）→ LLM 生成摘要+实体页 → `POST /wiki/pages` 写入
  - 技能包 `kb-wiki-builder`：SKILL.md + config.yaml + `scripts/kb_rpc.py`（登录/API/LLM 封装）+ `scripts/build_wiki.py`；需**同时安装到两处**：kb_skill 表（scope=system，平台技能管理页可见）+ **agent-gateway 的 `/skills/install`**（gateway 按本地 SKILLS_DIR 扫描技能工具，kb_skill 里的技能 agent 看不到）
  - LLM 供应商：**Infer AI**（`https://inferaiapi.com/v1` + `deepseek-v4-pro`，与 Hermes 一致）；配置走 `WIKI_LLM_BASE_URL/MODEL/API_KEY` env（deploy/.env 已加 WIKI_LLM_API_KEY）；worker 自动链把 model/base_url/api_key 注入任务 config → gateway runner 转 `WEKNORA_LLM_*` 环境变量给技能脚本
  - gateway runner 技能工具契约：任务 config 的 `kb_id/doc_name` → `WEKNORA_KB_ID/WEKNORA_DOC_NAME`；`model/base_url/api_key` → `WEKNORA_LLM_*`；`skill` → `WEKNORA_SKILL`。**input 必须写明用 `run_skill_script(skill_name=..., script='build_wiki.py')` 执行，并禁止 agent 用 MCP 工具查库**（否则 agent 会自作主张调 WeKnora MCP 而失败）
  - 实测：上传 md → 自动链触发 → 技能脚本 exit 0 → wiki 页自动生成（摘要页 + 实体页）
  - **坑位**：① 文档解析器不支持 .txt，要传 .md/pdf 等；② gateway `/skills/install` 要求 ZIP 带 `<name>/SKILL.md` 结构或传 `name=` 参数，否则技能名推断错（config.yaml 被当技能名）；③ 技能脚本从 10.1.215.50 调 kb API（gateway 容器 → 宿主 nginx 可达）
  - scope.is_admin 修复：对齐 `AUTH_ADMIN_USERS` env（此前 admin 因角色 role_type 全是 plat-mgr 被判非管理员，技能管理页装不了 system 技能）
  - 测试：新增 `test_doc_chunks.py`（3 例）+ `test_wiki_skill_chain.py`（5 例），全量 262 passed
  - 改动文件：`api/config.py`（WIKI_* 配置）、`api/routers/kbs.py`（chunks 端点）、`api/services/scope.py`（is_admin）、`worker/tasks/doc_process.py`（自动链）、`tests/test_doc_chunks.py`、`tests/test_wiki_skill_chain.py`、`deploy/.env`（WIKI_LLM_API_KEY）

## 2. 当前状态（2026-10-02 晚间追加）

- **测试：228 个全部通过**（.venv/bin/python -m pytest）；前端 tsc + bun build 通过
- **框架层/样式层对齐 data-synth（本次，commit 0f8bd4c）**：
  - 主题体系全量迁入：`web/src/theme/modo-algorithm.ts`（15 组色系 1-10 全色阶注入 + preset 色系映射）+ `web/src/theme/antd-theme-token.tsx`（完整 token：主色阶/Hover/Active、controlHeight 28/24/32、boxShadow 三级、Input/Select/Form/Tree/Checkbox/Radio/Steps 组件级）+ `web/src/lib/antd-registry.tsx`（cssinjs SSR + **zhCN locale + dayjs zh-cn**）包装根布局；`lib/theme.ts` 基础值回写（fontSize 12 / radius 2 / 次文字 #79879C / Table.headerBg #F9FBFD，**废弃 globals.css 中自创的 !important 硬覆盖，回归 token 体系**）
  - **两级菜单体系**（对齐 data-synth MenuContext/AppHeader/AppSider）：`(main)/_components/MenuContext.tsx`（topMenus 顶级 + siderMenus 子级，sessionStorage 记忆选中顶级，路径自动同步，flatMode 单级回退）+ `AppHeader.tsx`（45px、logo、Header 顶级横向导航 600/14px、用户下拉）+ `AppSider.tsx`（200px 折叠 45、**右下角悬浮圆形折叠钮**、Menu itemHeight 36 / itemColor #4D5E7D / 选中悬浮 #EFF4F9 / radius 4，CSS 类 `side-menu-kb`）；`(main)/layout.tsx` 改为服务端包装 LayoutContent
  - **my_menus 祖先补全**（对齐 data-synth expandedIds 逻辑）：角色关联菜单 + 向上补全所有祖先，前端可构建完整树
  - **kb 菜单种子**：`scripts/seed_framework_db.py` 新增 `root_kb`（知识库平台）+ 8 子菜单（kbs/chat/agents/datasources/wiki/jobs/models/system）并关联种子角色；联调库已种入 + huqiang 挂 kb_role（摘除 normal_user 的 data-synth 旧菜单关联）——my-menus 实测返回 kb 两级树
  - 登录页：记住密码（localStorage 回填，对齐 data-synth saveRemember）
- **剩余差距补齐（本次，commit 待定）**：
  - **全局水印**：`web/src/components/GlobalWatermark.tsx`（antd Watermark 轻量版，显示 用户名+用户ID，NEXT_PUBLIC_WATERMARK_ENABLED 控制，默认开）包在 LayoutContent Content 内
  - **Header 通知铃**：`NotificationBell.tsx`（未读 Badge + 下拉 Tabs 未读/全部 + 标记已读/全部已读 + 分页，轮询模式）+ 后端 `api/routers/notifications.py`（modo_system_message 表：unread-count / list / {id}/read / read-all；**create_date UTC→CST +8h 转换**）
  - **Header 团队切换**：后端 system.py 加 my-teams / switch-team（校验成员→重编码 identity_cookie 返回，前端写回，对齐 rawSwitchTeamAction 契约）/ default-team GET+POST；前端 AppHeader 加团队面板（选择团队 Tag）+ 设置默认团队弹窗
  - **菜单 icon 下拉**：system.py 加 /system/icons（10 个 antd 图标名）；前端菜单 Tab icon 字段 Input → Select（对齐 icon-actions）
  - **登录强校验 fail-open→fail-closed**：middleware 受控路径无身份 → 401（未登录）；test_rbac_guard/jobs/notifications 测试同步更新
  - **SSO 支持**：auth.py 加 GET /auth/auth-mode（SSO_ENABLED env 控制，默认 local）；前端登录页探测 auth-mode，ssoEnabled 时显示提示条
- **联调库数据**：huqiang 挂 kb_role + 团队 ROOT/test（默认 ROOT）；测试通知已插一条
- **待办（未做）**：WebSocket 实时推送（当前通知轮询）、SSO 真实对接（仅探测+提示）、远程注入式水印（当前 antd 本地渲染）


## 1. 项目概况

| 项 | 值 |
|---|---|
| 仓库 | `/home/jenkins/chengkai/kb_compilation`（git remote: git@github.com:chengkml/kb_compilation.git） |
| 目标 | 从 data-synth 剔除数据合成业务，保留系统框架层；在其上迁移 WeKnora 知识库能力 |
| 最终形态 | Next.js 纯前端 + Python FastAPI 后端 + Celery 任务；知识库**业务表**走框架关系库（MySQL 默认，可换任意关系库），**向量**走独立向量库（PG+pgvector，预留 ES） |
| 上游参考 | `/home/jenkins/chengkai/data-synth`（框架层来源）、`/home/jenkins/chengkai/WeKnora`（知识库功能来源） |

## 2. 当前状态（2026-10-02）

- **测试：217 个全部通过**（`uv run pytest` / `.venv/bin/python -m pytest`）；前端 tsc + build 通过
- **样式交互对齐 data-synth（2026-10-02 落地）**：全局 MODO 主题（主色 #3261CE、文字 #242E43、圆角 4/6）——`web/src/lib/theme.ts` + `globals.css` 全局覆盖（卡片白底圆角 6 浅阴影 / 表格白表头无竖线行底 #EFF4F9 / 按钮扁平无阴影）；主布局改 data-synth 结构（Header 顶部白条 + **浅色树形 Sider** + Content #F5F7FA 灰底 padding 10/12），Sider 对齐 side-menu 交互（一级加粗 600 高 44、选中悬浮 #EFF4F9、圆角 4、折叠按钮、当前路径自动展开父级、菜单图标映射）；登录页配色对齐（浅蓝底 + 白卡片圆角 6 阴影）
- **菜单授权迁移（2026-10-02 落地）**：系统管理从只读升级为完整 RBAC——角色 CRUD、角色-菜单分配（saveRoleMenus 语义）、角色-用户配置（plat-mgr 角色）、菜单树 CRUD、my-menus（当前用户可见菜单）；新增 FastAPI 中间件守卫（对齐上游 proxy.ts：白名单 + AUTH_ADMIN_USERS 绕过 + check_path_permission，未受控路径放行）；前端 Sider 按 my-menus 动态渲染（空回退内置菜单）。**现在可通过角色菜单分配控制用户侧边栏与页面访问**（真实库验证：受控+未授权 → 403，受控+已授权 → 放行）

- **测试：142 个全部通过**（`uv run pytest` / `.venv/bin/python -m pytest`）
- **存储拆分（2026-10-01 落地）**：知识库 7 张业务表（kb_datasource/kb_document/doc_chunk/wiki_folder/wiki_page/wiki_link/kb_agent）迁到**框架关系库**（MySQL，可移植类型，DDL 经 MySQL 8 真库验证）；PG 只留 1 张向量表 `kb_embedding`（pgvector+HNSW）；向量 IO 全部走 `api/services/vector_store.py` 的 `VectorStore` 抽象（`VECTOR_STORE_TYPE=pg|es`，es 已留桩）
- **API：23 个端点**——auth/login、me、open/datasources(/test)、qa/stream、agents CRUD+流式、kbs 管理全套（CRUD/文档上传/wiki 树/页面详情/JSON 检索）、**system 管理（users/roles/teams/menus/operation-logs）**、health
- **Celery：4 个 task_class 注册**——KbDocumentProcessTask、KbDocumentEmbedTask、KbWikiBuildTask、KbGraphBuildTask（+ beat 扫描 scan_cron_tasks）
- **MCP：3 个工具**——kb_list / kb_search / kb_answer（mcp SDK v2，MCPServer）
- **前端：Next.js 16 应用就绪**（web/ 目录）——登录 / 知识库管理 / KB 详情（文档上传+wiki 树+检索）/ wiki 页面详情 / 智能问答(SSE) / 智能体配置 / Wiki 总览 / 数据源 / 系统管理（用户/角色/团队/菜单/日志）共 12 路由
- **git**：全部已提交推送，工作区干净
- 核心链路全部真实验证过（真实 PG17+pgvector + 真实框架 MySQL）

## 3. 已完成功能清单

### 3.1 框架层（从 data-synth 抽离）✅
- [x] 30 张框架表 1:1 迁移（表/列/索引名与原库一致，可直连共享库）
- [x] 双引擎 DB：框架表 + 知识库**业务表**（MySQL，DATABASE_URL）+ 向量表 kb_embedding（PG+pgvector，KNOWLEDGE_DATABASE_URL，经 VectorStore 抽象可切 ES）
- [x] auth/RBAC：AES/DES 字节级兼容（Node crypto-js 黄金密文交叉验证 + 真实库 roundtrip 通过）、身份 cookie 编解码、RBAC 三级检查
- [x] 数据源：连接测试（mysql/postgres/kingbase/trino/minio）、分页列表
- [x] 配置缓存：modo_dim SYSTEM_CONFIG TTL+single-flight
- [x] Celery 调度：scan_cron_tasks 完整复刻（初始化/校准/乐观锁防双触发），TASK_CLASS_REGISTRY 分发
- [x] 审计日志：oper_log 写入（字段钳制、动作分类）

### 3.2 知识库域（迁移 WeKnora）✅
- [x] docreader：8772 行解析引擎（3 引擎 9 格式：md/pdf/docx/xlsx/pptx/epub/mhtml/图片）
- [x] 知识库模型：7 张业务表（KbDatasource/KbDocument/DocChunk/WikiFolder/WikiPage/WikiLink/KbAgent，可移植关系类型，随框架库）+ 1 张向量表（KbEmbedding，PG pgvector）
- [x] 混合检索：向量臂（余弦距离+阈值，经 VectorStore 抽象）+ 关键词臂（可移植 ilike + 应用侧评分，无 PG 扩展依赖）+ RRF 融合（k=60, 0.7/0.3，公式与 WeKnora 逐项一致）
- [x] 文档入库：解析→分块（800/80）→向量化→落库 全链路 Celery 任务
- [x] RAG 问答：SSE 流式 + search_results 上下文注入 + chunk_id 引用标注
- [x] wiki 生成：实体分组去重→建页（幂等追加）→bigram 关联建链
- [x] Neo4j 图谱：LLM 实体/关系抽取 + MERGE 写入（提示词模板迁自 WeKnora）
- [x] 智能体配置：kb_agent CRUD + 按 agent 流式问答 + KB 范围解析（all/selected/none）
- [x] MCP 服务端：3 个知识库工具对外暴露（sessionId 参数约定）

### 3.3 KB 管理 API（P12 前置，本次新增）✅
- [x] KB CRUD：list（分页/关键字/doc+page 计数）/ create / update / delete（软删 + 删除保护：有文档禁止删）
- [x] 文档：list / multipart 上传（落盘 KB_STORAGE_DIR → 建 kb_document(PENDING) → 入队 Celery）/ delete（级联清 chunk + 本地文件）
- [x] wiki 浏览：目录树+页面列表 / 页面详情（含关联链接）
- [x] JSON 检索端点（非流式，前端搜索框用）
- [x] worker 从 storage_path 读文件字节（上传→解析闭环打通，实测 READY+chunk 落库）

### 3.4 前端（P12 第一段+收尾，Next.js 16 纯前端）✅
- [x] 架构：`web/` 独立 Next.js 应用，`/api/[...path]` 代理路由转发到 FastAPI（cookie 透传 + SSE 透传），同源契约
- [x] 页面：/login（登录写 x-next-identity cookie）、主布局（Sider 菜单+登录守卫）、/kbs、/kbs/[id]（上传+wiki 树+检索+**解析状态自动轮询**）、/kbs/[id]/wiki/[slug]（**react-markdown+GFM 渲染**）、/chat（SSE 流式）、/agents、/wiki、/datasources、**/system（用户/角色/团队/菜单/操作日志）**
- [x] 构建：bun install / build / type-check 全过；生产模式联调验证（页面渲染、代理转发、SSE event-stream 头透传）

### 3.5 系统管理 API（P12 收尾 + 菜单授权 2026-10-02）✅
- [x] 只读查询：users（分页/关键字）/ roles / teams / menus（排序树）/ operation-logs（分页/关键字）
- [x] 写操作（对齐上游 role-actions/menu-actions）：角色 CRUD（save_role/delete_role）、角色-菜单分配（get/save_role_menus，全量替换语义）、角色-用户配置（get/save_role_users，仅 plat-mgr 类型角色前端展示）、菜单 CRUD（save_menu/delete_menu，含 menu_ext_conf 组装、子菜单/被引用删除保护）
- [x] my-menus：当前用户可见菜单（Sider 渲染；plat-mgr/admin 全量，普通用户按 role_menu_rela 关联）
- [x] RBAC 路径守卫：`api/middleware.py` —— 白名单（/health、/api/v1/auth/、/api/v1/me、/api/v1/open/、/api/v1/system/my-menus）→ 身份 cookie → AUTH_ADMIN_USERS 绕过（env，默认 admin；**不用 role_type 判定**，共享库 normal_user 等非管理员角色也标了 plat-mgr）→ check_path_permission；未受控路径/无身份请求放行（登录强校验留 auth 加固阶段）
- [x] 前端：Sider 按 my-menus 动态渲染（空回退内置 8 项，平滑过渡）；角色 Tab 增新建/编辑/删除/分配菜单（Tree 勾选）/用户配置（Transfer）；菜单 Tab 增树 CRUD（新增根/子、编辑、删除）

## 4. 关键文件地图

```
api/
  main.py                 FastAPI 入口（挂载 auth/datasources/qa/agents/kbs 路由）
  config.py               配置（DB_TYPE/DATABASE_URL/KNOWLEDGE_DATABASE_URL/KB_STORAGE_DIR）
  db.py                   双引擎：Base(框架) + KnowledgeBase(知识库)，SCHEMA_NAME 注入 search_path
  lib/crypto.py           AES/DES 字节级兼容（DataSource 密码列/身份 cookie 共用）
  models/framework.py     30 张框架表（共享旧库）
  models/knowledge.py     7 张知识库表（PG+pgvector）
  services/
    identity.py           auth/RBAC/身份 cookie（含 to_payload() camelCase 输出）
    datasource.py         数据源连接测试
    config_cache.py       modo_dim 缓存     runtime_config.py 三级解析
    retrieval.py          RRF 混合检索（向量臂+关键词臂+融合）
    embedding.py          OpenAI 兼容 /v1/embeddings
    ingest.py             文档入库编排（解析→分块→向量化→落库）
    chat.py               RAG 问答（上下文注入+流式）
    graph.py              LLM 实体/关系抽取 + Neo4j 写入
    wiki.py               页面生成 + 链接
    agents.py             智能体配置服务
    kb_admin.py           KB 管理服务（CRUD/文档/wiki 树/JSON 检索/删除保护）
    audit_log.py          oper_log 写入
    mcp_server.py         MCP 服务端（kb_* 工具）
  routers/                auth.py / datasources.py / qa.py(SSE) / agents.py / kbs.py
  assets/prompts/         graph_extraction.yaml / generate_summary.yaml（WeKnora 资产）
worker/
  celery_app.py           Celery 应用（beat 扫描 modo_cron_task）
  tasks/scheduler.py      cron 扫描 + execute_modo_job 分发（TASK_CLASS_REGISTRY）
  tasks/doc_process.py    文档解析/向量化任务（process_document 支持从 storage_path 读字节）
  tasks/wiki_graph.py     wiki/图谱构建任务
docreader/                WeKnora 解析引擎迁入（parser/models/splitter/utils/config）
web/                      Next.js 16 前端（纯前端 + /api/[...path] 代理）
  src/app/                login / (main)/{kbs,kbs/[id],kbs/[id]/wiki/[slug],chat,agents,wiki,datasources}
  src/lib/api.ts          fetch 封装（登录/KB/文档/wiki/检索/智能体/数据源）
scripts/
  knowledge_schema.sql           向量库 DDL（PG 专用：vector 扩展 + kb_embedding 单表 + HNSW）
  knowledge_business_schema.sql  知识库业务表 DDL（可移植关系语法，MySQL 8 真库验证，PG/SQLite 方言解析通过）
  verify_schema_alignment.py  框架模型↔真实库列级对齐检查
  verify_chat_stream.py   RAG 流式链路验证（mock OpenAI SSE）
  gen_env.py              本地联调 .env 生成（从 data-synth/WeKnora env 动态读凭据）
  gen_deploy_env.py       部署 .env 生成（容器内地址，凭据留空手动补）
deploy/                   部署落地（docker-compose + nginx + Dockerfile）
  docker-compose.yml      8 服务：nginx/web/api-server/celery worker+beat/redis/pg/mysql
  Dockerfile.api          uv 构建（api+worker+docreader 同镜像多命令）
  Dockerfile.web          bun 构建（standalone 运行）
  nginx/default.conf      统一入口（SSE 关缓冲）
  README.md               部署步骤/初始化/运维/验证
tests/                    217 个测试（test_*.py，含 test_kb_admin.py / test_system_admin.py / test_rbac_guard.py）
```

## 5. 验证方式（每次改动后必跑）

```bash
cd /home/jenkins/chengkai/kb_compilation
.venv/bin/python -m pytest tests/ -q          # 全量测试（217）
.venv/bin/python -c "from fastapi.testclient import TestClient; from api.main import app; print(list(TestClient(app).get('/openapi.json').json()['paths']))"
cd web && bun run build && bun run type-check  # 前端构建（web/ 下；type-check 用 ./node_modules/.bin/tsc --noEmit，宿主机无 tsgo）
```

真实库联调配方（已实测通过）：
1. `scripts/gen_env.py --write` 生成 .env（读 data-synth/.env.development + WeKnora/.env，host 改写为本机 13308/5433）
2. `psql -h 127.0.0.1 -p 5433 -U <user> -d <db> -f scripts/knowledge_schema.sql`（幂等，注意分号切块法会吞「注释+建表」整块，必须用 psql 整文件执行）
3. `mysql -h 127.0.0.1 -P 13308 -u <user> -p<pass> data_synth_neo < scripts/knowledge_business_schema.sql`（首次建 7 张知识库业务表；重跑报 Duplicate key name / Duplicate foreign key 可忽略。凭据从 data-synth/.env.development 动态读，勿写命令行）
4. `.venv/bin/uvicorn api.main:app --port 8002`（8000 被 dataos-agent-nginx 占用）
5. `cd web && API_BASE_URL=http://127.0.0.1:8002 bun run start`（生产模式；dev 模式会撞系统 inotify watch 限制）
6. curl 验证：/api/v1/auth/login（huqiang/sys）→ /kbs CRUD → upload → /wiki → /search → /qa/stream（SSE 头透传）

## 6. 环境与连接信息

| 组件 | 地址 | 凭据来源 |
|---|---|---|
| 框架 MySQL（data_synth_neo） | 127.0.0.1:13308（synth-mysql-seed-test 容器） | data-synth/.env.development（host 替换为本机） |
| 知识库 PostgreSQL | 127.0.0.1:5433（WeKnora-postgres 容器，PG17） | WeKnora/.env（DB_USER/DB_PASSWORD/DB_NAME） |
| 前端 | 127.0.0.1:3100（next start / dev -p 3100） | API_BASE_URL 指向后端 |
| Python | `.venv`（uv 创建，3.11.15） | — |
| 包管理 | `uv sync` / `uv add <pkg>` | — |

测试账号：`huqiang / sys`（真实库，seed 明文经 AES 解密确认）。
注意：`.env` 是敏感文件（read_file 被拒），连接凭据一律**从 env 文件动态读取**，绝不写进命令行/代码。

## 7. 关键坑位记录（务必先读）

1. **Hermes 脱敏会写坏含 URL/凭据字面量的代码**：写含 `redis://host:***@`、密码样式的字符串会被改写/截断。规避：字面量用字符串拼接（`"jdbc:mysql:" + "//h1/db1"`），或从 env 动态读。往写文件时如发现 `***` 或行被吞，立即改用拼接重写。
2. **关键词臂不依赖 PG 扩展**：早期用 ILIKE + pg_trgm word_similarity（PG 原生 tsvector 无法分词中文，整句一个 token），2026-10-01 改造为可移植 `ilike` 子串匹配 + 应用侧评分（retrieval.py `_keyword_score`），MySQL/PG/SQLite 通用，勿改回 websearch_to_tsquery。
3. **pgvector 扩展在 public schema**：表在其他 schema 时 search_path 必须含 public（`SET search_path TO x, public`；api/db.py get_knowledge_engine 通过 connect_args options 注入 SCHEMA_NAME）。DDL 文件顶部有注释说明。
4. **SQLite 不能建 pgvector/HNSW 索引**：向量表 kb_embedding 的 HNSW 索引只写在 knowledge_schema.sql（PG 专用），不在 SQLAlchemy 模型里（否则 create_all 在 sqlite 测试崩）。
5. **SQLAlchemy 2.0 update().execute() 返回类型**：rowcount 用 `getattr(result, "rowcount", 0)` 规避 Pyright 误报。
6. **mcp SDK 用 v2 语法**：`from mcp.server.mcpserver import MCPServer`（FastMCP 在 2.x 已改名）。工具逻辑抽成模块级 handle_* 函数便于测试（Tool 对象不暴露底层 fn）。
7. **docreader 的 textract 已改惰性导入**（上游因 SSRF 禁用该方法，勿改回顶层 import）。
8. **embedding 维度必须与 DDL 一致**：knowledge_schema.sql 的 vector(1024) 与 EMBEDDING_DIM 默认 1024；测试假向量也用 1024，否则 DataError expected 1024 dimensions。
9. **对齐脚本/execute_code 的 python 是系统解释器**：没有项目依赖，跑 SQLAlchemy 必须用 `.venv/bin/python`。
10. **psql 跑 knowledge_schema.sql 必须整文件执行**：若用 python 按 `;` 分块，注释行+CREATE TABLE 会整块被当成注释跳过（kb_datasource 等表静默没建）。用 `psql -f` 或逐句保持注释不粘连。
11. **本地 MySQL 用 pymysql 驱动**：DATABASE_URL 必须是 `mysql+pymysql://`，裸 `mysql://` 会去找 MySQLdb。gen_env.py 已处理。
12. **前端 dev 模式撞系统 inotify watch 限制**（antd 模块解析失败 "OS file watch limit reached"）：联调用 `bun run build` + `bun run start`（生产模式）验证，dev 只留给真正开发时用。
13. **浏览器工具（agent-browser）在本机不可用**：GLIBC 2.29+ 缺失（老 kylin aarch64）。UI 验证用 curl 断言 HTML/接口，不用 browser_* 工具。
14. **登录响应契约**：identity_cookie 在响应**顶层**（不在 data 内）；data 是 camelCase payload（loginId/userId/userName...，Identity.to_payload()）。
15. **页面初始 HTML 为空是预期**：MainLayout 在客户端校验身份（useEffect）通过前 `return null`，服务端 SSR 不渲染子树——curl 验证页面只能拿到 HTML 壳，属正常。
16. **MySQL 忽略列级内联 REFERENCES**：`col VARCHAR(64) REFERENCES x(id)` 在 MySQL 只解析不建外键（PG 会建）。跨库 DDL 必须用表级 `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY`，且放在 CREATE INDEX 之后（复用已有索引，避免 MySQL 为 FK 自动补索引造成重复）。knowledge_business_schema.sql 已按此落地并经 MySQL 8 真库验证（4 个 CASCADE 外键全建出）。
17. **MySQL 8 不支持 `CREATE INDEX IF NOT EXISTS`**（PG/SQLite/MariaDB 支持）：跨库 DDL 用裸 CREATE INDEX；幂等靠「docker init 全新库 + 表级 IF NOT EXISTS」，对已存在 schema 重跑报 Duplicate key name / Duplicate foreign key 可忽略。
18. **MySQL 8 禁止 TEXT/BLOB 字面默认值**（`content TEXT DEFAULT ''` 直接报错）：跨库 DDL 的 TEXT 列不写 DEFAULT，ORM 端 `default=` 兜底。
19. **知识库存储拆分后的 DDL 验证配方**：真库验证 `docker run -d --name kb-ddl-verify -e MYSQL_ALLOW_EMPTY_PASSWORD=yes -p 13309:3306 mysql:8.0` → `docker exec -i kb-ddl-verify mysql -uroot <scripts/knowledge_business_schema.sql`（无库则先 CREATE DATABASE）；方言解析校验 `uv run --with sqlglot python -c "..."`（mysql/postgres/sqlite 三方言全过，22 语句）。
20. **新增模型列不落到已有库（create_all 只建不更）**：模型加列后老库报 `Unknown column 'xxx' in 'field list'`（2026-10-08 chat_session.parent_session_id、chat_attachment.media_type/file_data 均踩中）。改模型后必须跑对应幂等迁移（scripts/migrate_chat_parent_session_column.py、migrate_chat_attachment_columns.py），并用 `scripts/check_schema_drift.py` 审计模型↔库列对齐（容器内 `.venv/bin/python` 跑；宿主跑会读错根 .env 指向 data_synth 库）。
21. **Alembic 已接管框架库 schema（2026-10-08 方案 A 落地）**：alembic.ini + migrations/（基线 0001_baseline = 当前库状态，已 stamp），api-server 启动 lifespan 自动 `alembic upgrade head`（AUTO_MIGRATE_ON_START 控制，默认开；pytest 经 tests/conftest.py 置 0 防止对 data_synth 库误迁移）。向量库 kb_embedding 不走 alembic（HNSW 索引表达不了，仍走 knowledge_schema.sql）。模型改列流程：改模型 → 容器内 `alembic revision --autogenerate` → **人工裁剪**（见 22）→ 重启自动生效。
22. **alembic 1.20 autogenerate 的 compare_indexes/compare_foreign_keys=False 不生效**（源码 1.20 重构，比较器不读该 flag）：autogenerate 会把库 DDL 手建的索引名（idx_*/fk_*）与模型默认名（ix_*/op.f）差异、nullable 差异全量生成进迁移（含会失败的 drop_constraint）。生成后必须人工删除"历史噪音"行，只保留真实变更（缺表/缺列）。基线已 stamp，正常开发不受影响。
23. **部署镜像已含 alembic**：4 个 Dockerfile 均 COPY alembic.ini + migrations/；pyproject.toml 加了 alembic>=1.13（uv.lock 同步）。部署新镜像前需重建（构建机跑 build-images workflow 手动触发）。
24. **数据查询(datagrid)对 SANSSEC1 国密信封口令数据源按设计拒连**（2026-10-08 实测）：共享库 modo_datasource 里 data_synth_dev 的 ds_auth 以 `SANSSEC1|` 开头（data-synth 老 Java 平台写入的国密信封，HSM 不可达，Python 无解密能力），datagrid 明确拒绝并提示「改用明文或 AES 口令」（api/services/datagrid.py `_reveal_datagrid_secret`/`_connect`，提交 7549117+7c23f48，tests/test_datagrid.py 有 2 条正例断言）。**修复=数据层**：数据源管理编辑该数据源、口令改明文或 AES(enc:v1)（PUT /api/v1/datasources/{ds_id}，口令留空不修改），或 MCP update_datasource 重存；不要拿密文撞库。data-synth 老平台侧 test_datasource 仍能连（Java 端有解密能力），仅 kb Python 端受限。
25. **Popoto 图谱 `Cannot assign to read only property 'provider'`（2026-10-08 定位+已修复）**：根因是旧版 PopotoGraphView（f0ebdfb）用 `await import("popoto")` 拿 ESM 命名空间后执行 `popoto.provider = ...`（101 行）——**模块命名空间对象属性只读**，必抛 TypeError。修复提交 7961c81 已改为「d3 npm 包挂 window.d3 + 动态 `<script>` 加载 vendored UMD（`web/public/vendor/popoto.min.js`）+ 用 `window.popoto`」，不再对命名空间赋值。知识图谱 Tab 已由 Neo4jGraphView 切到 PopotoGraphView（f0ebdfb），后端 `POST /api/v1/cypher` 只读代理（graph.py，775218f）。当前 main 已验证：bun install（装 popoto@4.0.8 + d3@7.9.0 + @types/d3）+ build + tsc + 477 pytest 全过。
26. **模型类型(chat→rerank)保存后不生效（2026-10-08 定位+已修复）**：`update_model`（api/services/models.py）原本**没有 type 字段分支**，PUT 保存静默丢弃 type（前端已提交但后端不写），导致模型误配后改类型「保存成功但类型不变」，连接测试仍按旧类型走 chat/completions（SiliconFlow 对 rerank 模型名打 chat 端点 → 400）。连带修复：前端 doTest 改用表单值 `v.type` 而非已保存 `item.type` 决定探测方式（models/page.tsx）。已补 2 条回归测试（test_update_switches_type / test_update_rejects_invalid_type）。注意：**存量被配错的模型行需要修复部署后重新保存一次** type 才会落库。另：`POST /models/test`（连接测试）只探 /models 与 /embeddings，对 rerank 不适用，rerank 连通性验证走「调试」抽屉或保存后用 debug 分支（按 type 打 /rerank）。
27. **rerank 连接测试 404（2026-10-08 定位+已修复）**：kb 平台是本机 docker 部署（kb-* 容器，nginx 80 入口），查容器库 kb_model 发现 rerank 模型 base_url 填成了**完整端点** `https://api.siliconflow.cn/v1/rerank`，而 debug rerank 分支无脑拼 `/rerank` → 实际打 `.../v1/rerank/rerank` → 404「rerank 端点返回 404: Not Found」（SiliconFlow 真 404 是 JSON，纯文本 Not Found 即路径不存在）。修复：`_rerank_endpoint()` 归一化——base_url 已以 /rerank 结尾则原样用，否则补 /rerank（对齐 chat.py 对 /chat/completions 的 endswith 处理），+1 条单测。SiliconFlow rerank 端点为 `POST https://api.siliconflow.cn/v1/rerank`（无 key 探测返回 401 即路径存在）。**诊断捷径**：本机部署容器里核对代码 `docker exec kb-api-server grep -n 'if "type" in payload' /srv/kb/api/services/models.py`、查模型行 `docker cp` 脚本进容器跑（DATABASE_URL 在容器 env，勿打印）。
28. **列表页分页参数名不匹配——每页只有 10 条（2026-10-08 定位+已修复）**：前端 api.ts 曾有 9 个列表函数用 camelCase `pageSize` 发参，而后端（kbs/agents/datasources/system.users/roles/teams/operation-logs/dims/documents）Query 参数全是 snake_case `page_size`——FastAPI 只绑定声明参数，未知参数静默忽略 → 回落到默认 10（logs/documents 为 20），于是分页组件显示「一页 20 条」而表格只有 10 行。已全部改为 `page_size`（api.ts：apiListKbs/apiListDocuments/apiListAgents/apiListDatasources/apiListUsers/apiListRoles/apiListTeams/apiListOperationLogs/apiListDims）。**例外勿改**：apiListCronTasks 的 `pageNum/pageSize/keyWord` 保持 camelCase——cron 后端(api/routers/cron.py)就是 camelCase 契约（对齐 data-synth）。核对方法：`python -c "from fastapi.testclient import TestClient; from api.main import app; ..."` 打印 OpenAPI 各端点 query 参数名。
29. **全量拉取下拉 422（2026-10-08 定位+已修复）**：团队管理/用户管理/本体 Schema 等页面对树形/下拉/成员列表用 `apiList*(1, 500)` 全量拉取，而后端 users/roles/teams/kbs 的 `page_size` 上限 `le=100` → FastAPI 422。已将 4 个端点上限提到 `le=1000`（api/routers/system.py 的 get_users/get_roles/get_teams + kbs.py 的 get_kbs；注意 dims/operation-logs/agents 等仍 le=100，前端翻页不超限勿动）。诊断入口：报错 URL 的 page_size 值 > 后端 maximum（OpenAPI 可查）。
30. **任务刚开始耗时显示负值（2026-10-08 定位+已修复）**：任务监控页 RUNNING 实时耗时 = `浏览器 now − parseIsoUtc(start_time)`（job-table.tsx，每秒 setInterval 刷新 now）。浏览器对**后台标签页**定时器节流/挂起 → 切走再回来后（或上传文档期间停在别的页），now 落后于刚启动任务的 start_time → 显示 -12s 级负值（瞬时，切回即恢复）。后端无问题（容器 UTC 时钟一致，库里所有任务 duration 均为正确正值）。修复：`Math.max(0, now - st)` 负数钳 0。也可防御浏览器/服务器时钟轻微偏斜。
31. **delete_document 级联清理两处运行时缺陷（2026-10-08 定位+已修复，源自 46b5b95）**：① `select(DocChunk.id)` 单列查询 `.scalars()` 直接产出**字符串值**，原代码对每个值再取 `.id` → 删任何带 chunks 的文档 500（test_delete_document_cascades_chunks 抓到）；② `doc.vector_store_id` 属性不存在（vector_store_id 是 **KbDatasource KB 级**列，文档行没有）→ AttributeError 被 except 吞掉 → 向量清理**静默失效**。均已修复：chunk_ids 直接用 scalars 值；向量 store 按 ingest.py 同款取法 `get_vector_store(kb_row.vector_store_id if kb_row else None)`。
32. **tokenerpgw/erp 网关用 404 表达「模型名不存在」+ agent 模式模型选择被静默丢弃（2026-10-12 定位，代码已修复 de4e29e）**：
   - **症状**：智能问答报 `Client error '404 Not Found' for url 'https://tokenerpgw.asiainfo.com/erp/v1/chat/completions'`，且「选模型好像不生效」。
   - **根因①（配置，非代码）**：`tokenerpgw.asiainfo.com`（亚信 ERP 网关）**用 HTTP 404 + `{"error":{"code":"model_not_found","message":"Model not exist."}}` 表达「模型名在网关上不存在」**，而前端把它渲染成裸 httpx 字符串无法定位。实测（容器内带真实 key 探测）：无 key 打 `/erp/v1/chat/completions` → 401（路径存在需鉴权）；`aliyun/deepseek-v4-flash` → **404 model_not_found（该模型虽在 GET /models 列表里，但实际 chat 路由缺失）**；`aliyun/deepseek-v4-pro` / `qwen3.6-plus` / `qwen3.6-flash` / `qwen3-vl-plus` / `qwen3.7-max` / `qwen3.7-plus` / `MiniMax/MiniMax-M3` → **全部 200**。→ **排查捷径**：`docker exec kb-api-server python -c "...从 kb_model 取 base_url+key 打网关 /chat/completions 与 /models..."`，能立刻区分「key 无效」「模型名不存在」「地址错」。
   - **根因②（代码，已修复）**：`AgentQARequest`（api/routers/agents.py）**缺 model_id 字段**——前端在 agent 模式照常发送 model_id，但 pydantic 默认忽略多余字段 → 模型选择被静默丢弃，agent 问答永远走默认模型。修复：补 model_id 字段 + 透传 `chat_cfg=requested_cfg if req.model_id else None` 到 answer_question（对齐 qa.py `_run_generation` 逻辑）。测试 test_agent_qa_request_accepts_model_id。
   - **报错信息修复**：ChatClient 遇网关 404 时改抛中文可定位提示（含模型名+接口地址，指向「模型配置」页自查），不再裸抛 httpx 串。测试 test_chat_client_404_becomes_actionable_message。
   - **数据修复（待用户在模型配置页操作）**：默认 chat 模型 `aliyun/deepseek-v4-flash`（kb_model，is_default=1）需改成网关可用的名字（如 aliyun/deepseek-v4-pro）。
   - **教训**：接入企业内网网关（tokenerpgw/erp 等）时，模型名必须**实测打一次 chat/completions 验证**——`GET /models` 列出 ≠ 可调用，且这类网关普遍用 404（非标准 400）表达 model_not_found。

33. **前端本地占位消息 id 不能直接调服务端接口（2026-10-14 定位+已修复）**：流式问答期间助手消息 id 是前端自造的 `local-ai-<ts>`（用户消息 `local-user-<ts>`、续传占位 `resume-ai-<ts>`），服务端库里没有对应记录；任何按消息 id 的服务端调用（反馈、删除）都会 404「消息不存在」，而**刷新页面后又能用**（id 变成 `GET .../messages` 返回的库内 id），极易被当成偶发问题。修复后两个问答流端点的 `done` 事件回传持久化 id，前端在 done / 续传时替换占位 id，并对残留占位 id 做回捞兜底（`toggleFeedback`）。**同类注意**：后续新增任何按消息 id 的接口（编辑、引用、回溯、分享）前，先确认前端拿到的确实是服务端 id。

## 8. 剩余工作（按优先级）

### 8.1 P12 前端对接（剩余小项，按需）✅ 主体完成
- [x] 上传进度轮询（有 PENDING/PARSING/EMBEDDING 文档时 3s 自动刷新）+ 状态 Spin 指示
- [x] wiki 页 markdown 渲染升级（react-markdown + remark-gfm + 样式）
- [x] 系统管理页（用户/角色/团队/菜单/操作日志，只读）
- [ ] 可选：问答页多轮历史持久化（本地存储）、引用点击跳文档稿、智能体编辑弹窗（config 可视化）、数据源页连接测试按钮

### 8.2 部署落地 ✅ 已交付（deploy/）
- [x] docker-compose：nginx 统一入口 + web(Next.js) + api-server(FastAPI) + celery worker/beat + **flower(worker 监控)** + redis + pg(pgvector) + mysql 共 9 服务
- [x] Dockerfile.api（uv 构建，api+worker+docreader 同镜像多命令）/ Dockerfile.web（bun 构建，standalone 运行）
- [x] nginx 配置（参照 data-synth 精简：SSE 关缓冲、健康检查端点）
- [x] deploy/.env 生成脚本（scripts/gen_deploy_env.py，凭据留空手动补）+ deploy/README.md（步骤/初始化/运维/验证）
- [x] 待实际起容器验证（本机无 docker compose 插件，已做 YAML/变量一致性校验）；MySQL 知识库业务表已挂载 DDL 自动建（docker-entrypoint-initdb.d），框架 modo_* 表仍需手动建表+种子

### 8.3 增强项（按需）
- 登录强校验：当前 RBAC 守卫对「无身份请求」放行（fail-open），完整登录拦截（401/重定向）留待 auth 加固阶段统一做
- Open API 鉴权层（当前 auth 只登录，open/* 数据源路由未加 X-API-Key 校验）
- 上传文件校验（扩展名白名单 / 病毒扫描 / 去重）
- 菜单管理增强：菜单 icon 下拉选择（当前手填组件名）、菜单 ext-conf 可视化编辑（当前仅基本字段 + JSON）

### 8.4 assistant-ui 试验问答页（2026-10-14 新增，与现有问答页并存）
- 新页面 `/wiki/chat-next`（`web/src/app/(main)/chat-next/`，page.tsx + chat-next.css），依赖 `@assistant-ui/react ^0.15.28`（React 19 兼容）
- 形态：`useExternalStoreRuntime` 接现有后端 `POST /api/v1/qa/stream`（自定义 SSE：stream_meta/context/thinking/delta/stage/done/error），**无会话模式**（前端组 history 传参，不建 ChatSession 不留库）——**后台接口对接 kb-compilation，页面交互/布局/视觉完全遵循 assistant-ui 官方 elements 体系**（2026-10-14 二次改造，替代首版 primitives+自绘 css）
- 官方 elements 全套落地：`web/src/components/assistant-ui/elements/`（thread.aui/composer/reasoning.aui/tool-group.aui/attachment.aui/markdown-text 等 ~25 文件，从 GitHub main 拉取）+ `web/src/components/ui/`（button/skeleton/dialog/tooltip/avatar/textarea，Base UI 版）+ `web/src/lib/utils.ts`（cn）+ `web/src/hooks/`（use-copy-to-clipboard 等）+ Tailwind v4 分层导入（theme.css + utilities.css，**跳过 preflight**，@source 限定 assistant-ui 目录与 chat-next 页面，不影响现有 antd 页面）+ postcss.config.mjs
- 依赖（bun）：@assistant-ui/react-markdown / @base-ui/react / class-variance-authority / clsx / tailwind-merge / lucide-react / tailwindcss@4 + @tailwindcss/postcss + postcss + tw-animate-css（dev）
- **组件形态（2026-10-14 二次改造后）**：直接用官方 `Thread`（`elements/thread.aui.tsx`，含官方 composer/message/reasoning 折叠/工具组/追问/markdown，action bar 复制/反馈/重试/导出）；引用走 data part（`{type:"data", name:"kb_refs"}`）+ `makeAssistantDataUI` 注册官方渲染器（Thread 官方实现对 source part 返回 null，故引用不走 source part）；正文 `<kb doc=.../>` 标签被官方 markdown 静默丢弃（无 rehype-raw），引用完整呈现在消息下方参考资料区（点击弹 antd Modal 看 chunk 原文）
- 已验证：tsc --noEmit ✓、next build ✓（/chat-next 路由注册）、后端 SSE 冒烟 ✓（真实 kb 提问，delta/context(stage)/done 全链路）、生成 CSS 确认含官方 utility（bg-background/text-muted-foreground 等）且无 preflight 混入；未验证：浏览器交互（本机浏览器工具 GLIBC 不可用）——线上需重建 web 镜像后登录打开
- 菜单三处同步（2026-10-14 方案J）：① 种子 KB_MENUS 新增 `chat_next`（新版问答 /chat-next，顶级 sort_num=2，kbs/agents/grp_ops/grp_config/system/grp_debug 顶级 +1 顺延）② 线上 modo_menu + modo_role_menu_rela 按 menu_id 精准 INSERT（勿 --reset-menus）③ 前端兜底 FALLBACK_MENUS 在 grp_ai 下加 chat_next（agents/websearch 顺延）——**兜底与种子结构本就漂移**（兜底 grp_ai 分组式、种子顶级页面式），各自保持内部一致即可
- 坑位：
  - 本地起 next start 验证须带 basePath：`/wiki/chat-next`；API 走 nginx 80（docker kb-api-server 未映射主机端口）
  - web/node_modules 存在 root 所有的 `@codemirror.bak` 残留 → npm install reify EACCES；已改名 `_rescued_codemirror_bak` 避让（不能 rm，root 所有；需 sudo 清理）
  - 3100 端口被旧的 next-server 占用（跑旧 build，/chat-next 404）；本地验证用 `next start -p 3211`

## 9. 开工检查单（新会话）

1. `cd /home/jenkins/chengkai/kb_compilation && .venv/bin/python -m pytest tests/ -q` → 应 127 passed
2. `git status` → 确认工作区状态
3. 读本文档「剩余工作」选一项继续
4. 改动后用「验证方式」整组跑
5. 完成后 `git add -A && git commit && git push`（用户习惯：功能完成直接提交推送）
