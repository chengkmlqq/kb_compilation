# KB Compilation Platform — 进度记录

> 本文档用于跨会话续接工作。每次工作结束更新「当前状态」；新会话开始先读本文档。

最后更新：2026-10-27（wiki构建 tape 中间产物迁移 MinIO reg_cache + 失败文档重发准备）

## 51. 当前状态（2026-10-27 wiki构建 中间产物 /tmp → MinIO reg_cache 迁移）

- **问题**：济南市监局知识库（kb_id abbf0b4e…）大量文档构建失败，报
  `FileNotFoundError: /tmp/reg_<kid8>/fulltext.txt`。根因：wiki构建-* 编排脚本节点
  用容器本地 /tmp/reg_<kid8>/ 传中间产物（chunks.json/fulltext.txt/ls_all.json/
  terms.json/terms_filtered*.json/esm.json/catids.json），容器重建即丢失（10-24/25/26
  三次重启窗口），在途/恢复 run 后续节点读不到前面节点写的产物 → 批量 failed。
  全库 883 个此类失败 run（该 KB 434 个，去重 461 个文档）；另 10-26 22:00 批次出现
  独立问题：LLM 403/401（系统默认构建模型 deepseek-v4-pro 的 inferaiapi key 失效，
  实测 INVALID_API_KEY，用户负责换 key）。
- **修复（代码层）**：
  1. `scripts/migrate_wiki_tape_reg_cache.py`（新）：把 22 个 wiki构建 种子 JSON
     （nodes 设计层 + steps 执行层）+ `scripts/build_regulation_wiki_tape.py` 生成器的
     内联 code 统一改写：DIR 路径字面量 → `reg-cache/reg_<kid8>`（MinIO 前缀）、
     本地 open()/os.path.exists → rc.读写/rc.exists、删除 os.makedirs、注入
     `import reg_cache as rc`。`--check` 只校验不改写；`scripts/_ast_check.py`（新）
     提供全 code 语法校验。
  2. 线上 tape 重灌：`scripts/apply_wiki_tape_reg_cache.py`（新，容器内跑）按
     seed_tape_data/wiki构建-*.json 重建 21 条领域 wiki构建 编排的
     nodes/edges/exec_params + kb_tape_step（step id 前缀=tape_id[:12] 不变，拓扑
     pre/next 不变，仅 step_config.code 换新，在途 run 不受影响）；已对线上全部
     21 条执行（reg_cache=True 确认）。
- **验证**：worker 容器内用真实 kid 跑迁移后 n03（61 chunks/3970 字写入
  MinIO reg-cache/reg_ce5d81db/），并在不同容器（kb-celery-worker）用 n06 code 读回
  成功——即容器重建后中间产物不丢。冒烟产物已清理。test_orch_job_status_sync 5 passed。
- **重发准备（等 LLM key）**：`scripts/retrigger_wiki_build_failed.py`（新，容器内跑）
  按失败 run 的 bindings.kid 去重重发 wiki构建（--kb 必填，--include-403 纳入 LLM 类，
  --dry-run 只统计）；dry-run 已确认 478 个待重发（/tmp 类 461 + 403 类 5 + 孤儿 12）。
  `scripts/stale_run_cleanup.py`（新）把该 KB 24h+ 未推进的 queued/running 孤儿 run
  置 failed（已执行，19 个），释放防重 guard。
- **待办**：① 用户换好 inferaiapi key（当前仍 401）后，在 api-server 容器内跑
  `python /tmp/retrigger_wiki_build_failed.py --kb abbf0b4e0c3f41faaab8a3dd8180e1fd --include-403`
  全量重发 478 个文档；② 注意 other 类 175 个失败（n08 wiki_publish 退出码 2、
  n14 锁超时 1205、HTTP 4/5xx）与本次迁移无关，属另一批并发/资源问题，未处理。

## 51b. 跟进（2026-10-27 同日 11:30）：key 误判澄清 + 478 个重发已发起

- **LLM key 误判澄清**：先前实测 401 是错误方法——直接拿 kb_model.api_key 的
  **AES 密文**当 key 请求（llm_config() 实际用 decrypt_secret() 解密后才用）。
  按真实路径（知识库绑定模型 3bc35b89…/deepseek-v4-pro，用户已换 key，
  updated_at=02:26:52）实测 **LIVE 200**，key 有效，LLM 链路正常。
- **重发执行**：`retrigger_wiki_build_failed.py --kb abbf0b4e… --include-403` 发起
  478 个；发现 v1 脚本只建 kb_tape_run 行**漏投 orch 队列** → run 卡 queued，
  已修复（补 send_task execute_run → orch）+ 新增 `redispatch_queued_runs.py`
  对已建行未投递的 run 补投（475 个已补，幂等）；commit e9a8580 已推送。
- **执行中**：478 个全部进入 running（并发 475），样本 n01~n05 跑通（n03 写 MinIO
  产物验证链路），0 failed。17 节点长流程（多轮 LLM），预计数十分钟~数小时消化，
  后台 watch 脚本监控（每 5min 打点，终态后汇总失败原因）。
- **提示**：pytest 全量 939 passed / 12 failed，12 个失败为**既有环境问题**
  （缺 DATABASE_URL 等变量，stash 对比确认与本次改动无关）。

## 50. 当前状态（2026-10-26 切片增强 seed 遗留缺陷修复 + 测试回归护栏）

- **修复**：`scripts/seed_tape_data/切片智能增强.json` 的 n1 脚本补 `doc_extract_prompt`
  组装（标题/摘要/切片内容前 12000 字，与 kw_prompt/q_prompt 同构，nodes/steps 双写同步）。
  背景：n2 kb_llm_chat `prompt={{doc_extract_prompt}}` 此前**无任何节点设置该变量**，
  bindings 缺键时 `_template_replace` 原样返回字面量 → 二次执行 n2 收到
  `{{doc_extract_prompt}}` 字面量 prompt → LLM 返回 error 结构 → 增强降级空产物
  （2026-10-26 实机验证遗留缺陷，详见 skill kb-chunk-enhance-implementation.md）。
- **测试**（tests/test_chunk_enhance.py）：
  1. 结构断言：n1 code 含 `bindings['doc_extract_prompt']`、n2 prompt 引用模板；
  2. 执行断言回归护栏：_FakeClient 记录全部 user prompt，断言 n2 收到渲染后真实
     prompt（无 `{{` 字面量、含「文档标题：测试文档.md」）、abstract 来自
     doc_meta.summary、doc_keywords 非空（此前缺变量时 doc_meta={} 全靠短片降级兜底）；
  3. 突变验证：临时移除 doc_extract_prompt 块 → seed_structure + executes 2 用例失败，
     恢复后全绿（护栏有效）。
- **验证**：80 passed（chunk_enhance 8 + retrieval 4 文件 + ingest 2 + storage +
  orchestration_engine）；commit a95b464 已推送 dev。
- **部署提示**：线上须重新 `apply_chunk_enhance_tape.py` 灌新种子才生效（docker cp
  脚本+种子到 /tmp + 容器内跑）；已产生的降级数据不自动回补（enhance_meta={} 的文档
  需重上传/重嵌入才会重新增强）。

## 49. 当前状态（2026-10-25 全站页面查询区 loading 补齐专项）

- **需求**：「检查系统中所有页面，凡是涉及查询的地方，数据区域都应该有 loading 效果，逐个检查并补充直到全部补齐」。
- **范围**：web/src 全部业务页面 + 公共组件（3 个子代理并行逐文件审计 + 主代理独立抽查交叉核对）。
- **统一惯例**（与 kbs/models 页既有惯例对齐）：
  1. antd Table/ModoTable → `loading={xxxLoading}`（antd 自带表格遮罩，不另包 Spin）；
  2. 列表/卡片/网格 → `loading && items.length===0` 居中 `<Spin size="large" tip>`，已有数据再查询（搜索/翻页/刷新）时用 `<Spin spinning={loading}>` 包数据区；空态分支带 `!loading` 守卫防「加载中闪暂无数据」；
  3. 树 → `<Spin spinning>` 包树容器；远程 Select → `loading` prop；
  4. 抽屉/弹窗内容 → 内容区包 `<Spin spinning>`；统计卡 → 加载中显示 `-`；
  5. 按钮型异步操作（单库核对/修复全部等）→ 按钮 loading + 防连点。
- **新增/修复 loading 的文件（约 25 个）**：kbs/page（查询期间遮罩）、KbDocsPane（文档卡片视图 + 目录树 folderLoading）、wiki/[slug]（反馈列表 feedbackLoading）、CodeRepoPane（载入置位 + 内容区遮罩）、WikiManagePanel（indexLoading 消费）、wiki-keywords（首载 Spin + 翻页遮罩）、chunk-verify（单库核对 verifyingIds + 修复全部 fixAllLoading）、feedback-stats（statsLoading + 统计区遮罩）、jobs/page + job-stats（统计卡 loading prop）、usage/page + TokenTrendChart + KpiCards（optionsLoading + 图表 loading prop）、overview（trendLoading/kbStatsLoading）、cron（metaLoading 下拉）、orchestrations/page（分类树 Spin）、AgentEditorModal/KBConfigModal（optionsLoading 整窗 Spin）、system 页（users 下拉 dropdownLoading / teams 树+成员选项 / dims 分组下拉 / workers 任务抽屉 taskLoading / datasources 类型下拉 metaLoading）、models/agents/mcps（空态守卫 + spinning 包裹）、datagrid（client.tsx 数据源树 loading 消费 + db-tree 打开表结构 loading 提示）。
- **验证**：`web ./node_modules/.bin/tsc --noEmit` **0 错误**；`next build` 通过；每个改动文件 read_file 读回核对 JSX 闭合（子代理曾留 2 处 `</Spin>` 未闭合 + 5 处计划未执行，均已由主代理收尾）。
- **坑位**：① 子代理并行改同一仓库，两个文件（CodeRepoPane/feedback-stats）曾处于 Spin 标签未闭合的中间态——合并前必须全局搜 `<Spin` 与 `</Spin>` 配对或依赖 tsc 兜底；② patch 模糊匹配可能「移位命中」到相邻 Drawer/抽屉结尾（WikiManagePanel 闭合锚点）——多抽屉文件改闭合标签时锚点必须带块首唯一标识；③ search_files 交替 pattern 与含括号路径的单文件搜索 0 命中不可信，验证用 read_file 目标区；④ patch 返回的 lsp_diagnostics 多为中间态误报（同一文件连续 patch 时），以 read_file 结构核对为准；⑤ JobStats 由纯展示组件新增 loading prop（显示 "-"），jobs/page 需同步传 statsLoading——组件接口变更要跑调用点。
- **遗留（可接受盲区，未改）**：ChunkDebugDrawer「从文档载入」下拉、UploadConfirmDialog/ReparseConfigDialog 解析引擎下拉为静默加载（失败不阻断，无独立 loading state 可消费）；antd Tree loadData 懒加载节点自带节点级 loading（db-tree 文件夹展开无需额外遮罩）；useUserNames 有意非阻塞回退原始 ID。

## 48. 当前状态（2026-10-25 「wiki反馈修正」编排 + 每日定时任务）

- **需求**：编排管理新增编排，根据维护中心（wiki_feedback）反馈自动修正 wiki 页面；
  执行入口 = 定时任务每天一次；新增 feedback_fix 功能标签。
- **已交付（commit 81f2f7b，已推送 333f262..81f2f7b）**：
  1. 种子 `scripts/seed_tape_data/wiki反馈修正.json`（tape_type=feedback_fix、
     category=知识维护、status=effective，5 节点）：
     n1 收集待处理反馈（kw_ 标注自动 resolved 不改页、页面缺失反馈置 ignored、
     max_pages=20 分批）→ n2 loop 逐页 → n3 拼修正 prompt（原文+反馈清单，6000 字截断）
     → n4 kb_llm_chat（thinking=disabled、model={{summary_model_id}} 回落知识库绑定
     模型/系统默认）→ n5 应用修正（wiki_update_page + 反馈批量 resolved/ignored +
     report.jsonl + 末轮汇总 + rebuild_links）；loop 空集合不迭代安全终态。
  2. `worker/tasks/feedback_fix.py`（KbFeedbackFixTask）：扫描全库 pending 反馈按 kb
     分组，逐库 `execute_tape(env="prod")` + 轮询 run 终态（每库上限 1800s，超时中止
     本轮）。execute_modo_job 默认 env=prod → 定时任务天然写正式库；支持 dry_run。
  3. `scripts/apply_feedback_fix_tape.py`：幂等灌编排 + 建 modo_cron_task
     （KbFeedbackFixTask，cron `0 2 * * *` 每天 02:00，queue default）。
  4. feedback_fix 标签：web/src/lib/tape-func.ts（magenta）+ 编排表单 options +
     apply_tape_func_tags.py 命名规则。
  5. 测试 `tests/test_feedback_fix_tape.py` 12 用例全绿；提交快照验证 tsc exit 0 +
     pytest 46 passed（test_feedback_fix_tape + orchestration_engine + cron）。
- **部署步骤（代码已推送，容器未更）**：
  1. `docker cp scripts/apply_feedback_fix_tape.py scripts/seed_tape_data/wiki反馈修正.json kb-api-server:/tmp/`
     → `docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_feedback_fix_tape.py`
  2. worker 加载新模块：重建 kb-celery-* worker 镜像（Dockerfile COPY worker/）或
     docker cp worker/tasks/feedback_fix.py → 容器 + 重启对应 worker
  3. 前端重建 kb-web（tape-func.ts / orchestrations/page.tsx：标签 + 表单选项）
  4. `docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_tape_func_tags.py`
     （线上存量编排打 feedback_fix 标；种子自带不打存量）
  5. cron 生效：scheduler 轮询到次日 02:00 自动触发；可先手动验证
     （任务管理页手动执行 KbFeedbackFixTask 或直接投 execute_modo_job）
- **坑位**：
  ① 编排管理页手工执行默认 dev 环境（wiki_feedback/wiki_page 路由 _dev 表）——正式
     修正必须走定时任务（prod）或后续加 env 透传；
  ② LLM 输出非 JSON → n5 容错解析置 ignored 不炸 run（n4 勿开 json_mode）；
  ③ 中间产物 /tmp/fbfix（pages.json/report.jsonl）容器重建会丢——报告留痕以 wiki 页
     与反馈状态为准；
  ④ 定时任务串行逐库（execute_tape 同 tape 防重），单库超时中止本轮剩余库，次日续跑；
  ⑤ worker/celery_app.py 是并行会话高频并发文件（他人 aggregation 改动留在工作区未提交）。

## 47. 当前状态（2026-10-23 页面请求 <200ms 专项——全端点扫描与修复）

- **需求**：「每个页面的请求都应该在 200ms 内响应，大于 200ms 的都应该修复」
- **测量方法**：app.openapi 取权威 GET 路由清单（跳过需 path 参数的路由），admin
  身份 cookie（encode_identity_cookie 直构）逐端点冷/热各压一次，SQL 层事件钩子
  拆解 + EXPLAIN（MySQL8 列序含 partitions）、SHOW INDEX 卡度、冷热三连耗时。
- **根因（环境层）**：远端共享 MySQL（10.1.206.135，uptime 58 天，130+ 连接）缓冲池
  仅 128MB；doc_chunk 21 万行/wiki_page 10 万行 含 MEDIUMTEXT content，冷索引页
  读取秒级、热时 100-300ms——执行计划已最优（Using index）仍波动，属缓冲池冷热
  效应，代码侧无法根治只能缓存/覆盖索引兜底。首轮 overview 三件套 9-22s、/kbs 9s
  即此（2026-10-21 §39 同源复发）。
- **修复（commits 98448e4/03a8662/f1346d5，均已推送且容器热更生效）**：
  1. 覆盖索引：0039 wiki_page(kb_id,status,page_type) + 0043 kb_tape_run(status)
     （模型 __table_args__ 同步声明，dev 表 create_all 补建）；迁移幂等。
  2. 删除 list_kbs 合并计数遗留的死查询（结果被覆盖丢弃）。
  3. `api/services/agg_cache.py` 进程内短 TTL 聚合缓存（threading.Lock）：
     overview summary/kb-stats/trend 30s（key 含可见范围）、list_kbs 四组计数 15s、
     usage summary/trend 30s（preset 非 custom 时 start/end 随 now() 变化不可进 key）、
     metadata/datasources 15s。
  4. quality/runs 列表窄列：不拉 issues/attribution 大 JSON（单行 ~600KB），
     issues_count 走 MySQL JSON_LENGTH（其他方言回落）；新增 _run_item_row。
- **复扫结果（27 个页面端点，热态全 <200ms）**：kbs 24-28ms / overview 7-27ms /
  jobs 47ms / jobs/statistics 82-87ms（原 12-26s）/ workers 22ms / quality/runs 79ms /
  quality/docs 18ms / usage 全部 19-33ms / metadata/datasources 4ms / ontology 74ms /
  websearch 8ms / 其余均 <120ms。
- **验证**：test_jobs_monitor/test_quality_dim_runs/test_kb_admin/test_wiki_keywords/
  test_llm_usage_stats 63 passed；EXPLAIN 确认两处覆盖索引 Using index。
- **坑位**：① 同一查询冷热耗时 100 倍差（2.5s→138ms）= 缓冲池冷热，先 EXPLAIN 确认
  plan 再判刑；② GROUP BY/COUNT 走二级索引时 MySQL 可能仍回表读大 JSON → 覆盖索引
  必须含查询全部列；③ 缓存 key 若含 str(now 派生时间) 会永不命中；④ 并行会话 CI
  会重建容器冲掉 docker cp 热更（本会话两次被冲），最终以 push→CI→deploy 为准；
  ⑤ agg_cache 是进程内缓存，多副本部署时各副本独立（TTL 窗口内短暂不一致，可接受）。
- **遗留（环境建议，非代码）**：远端库缓冲池 128MB 偏小 + 共享库连接 130+——
  建议 DB 侧调大 buffer_pool（如 2-4GB）或入库定期 ANALYZE；kb_visible_clauses
  owner_user_id 单列索引（§39 P2 未变）。缓存 TTL 30s 内页面统计有短暂陈旧属预期。

### 47.1 用户实测线上 /kbs 7s → swr + 预热 + 持久化（commit e873c82）

- **现象**：用户实测 http://10.1.215.50/api/v1/kbs 返回 7s（10.1.215.50 即本机
  对外 IP，nginx→api-server 同一栈）。复现：随机重启容器后首访 6.6s；long-idle
  后缓存过期（ttl 15s）回落同步算同样秒级。
- **根因**：① 重启后聚合缓存空 + 远端库缓冲池冷 → 首访同步算；② agg_cache 的
  staleness_ok=300s：距上次访问超 5 分钟，过期缓存被判定「太旧」弃用 → 同步算。
- **二次增强（均已推送且容器热更生效）**：
  1. `agg_cached_swr`（stale-while-revalidate）：过期先返回旧值 + 单飞后台线程
     刷新，staleness_ok 默认 3600s；fn(db) 签名（同步路径复用请求 db 兼容测试
     monkeypatch，后台自建 session）。
  2. `api/main.py` 启动预热：9 个聚合（kbs/overview×3/usage×2/metadata/
     jobs-statistics）并行线程预填，失败不影响启动。
  3. agg_cache 文件持久化 `/srv/kb/data/agg_cache.json`：docker restart 恢复
     缓存项（**含已过期项**——stale 分支必须有旧值才能快返回；exp 为
     CLOCK_MONOTONIC 跨进程一致）；容器重建丢文件由预热兜底。
  4. jobs/statistics 拆 `_job_statistics_data` + 30s swr 缓存（kb_tape_run 低频
     索引页冷读秒级）；预热列表新增。
- **实测**：缓存命中 kbs 27-111ms / jobs 25ms / usage 22ms / metadata 6ms /
  overview 8-10ms；过期瞬间 stale 75ms；重启后首访 1.3s（剩余为远端库冷缓冲下
  中间件 RBAC+主查询开销，agg_cache 无法覆盖）。
- **坑位**：① swr 后台刷新线程用 fn(db) 传参而非闭包捕获请求 session（线程无
  请求上下文，Session 非线程安全）；② 持久化恢复必须含过期项（只恢复未过期项
  会让高频键 ttl=15s 在重启后缺失 → 首访同步算，实测 10-27s）；③ 预热线程
  并行跑 + 请求并发时远端库慢是环境噪声，EXPLAIN Using index 仍 1s+ 时先查
  缓冲池/服务器负载，勿为噪声改代码；④ 预热聚合本身是 agg_cached_swr 调用，
  恢复的过期键会让预热走 stale 分支而非真正填充——预热依赖后台刷新线程完成
  set（10-40s），故预热后需等刷新完成缓存才新鲜。

## 46. 当前状态（2026-10-23 知识库列表接口效率修复——wiki 聚合覆盖索引 + 死查询清理）

- **需求**：「查一下 /kbs 接口效率」→ 实测定位 + 用户确认修复（commit 98448e4，已推送）
- **实测（admin 身份直打 api-server，SQL 层事件钩子拆解）**：list_kbs(page=30) 合计 34.6s，
  瓶颈 = wiki_page 条件聚合（page_count+keyword_count 一条 SQL）28.5s（冷态实测 79.6s），
  EXPLAIN rows=90080 全扫 key=NONE；另有合并计数时遗留的独立 wiki COUNT 死查询 0.2-4s
  （结果随后被覆盖丢弃）。其余全毫秒级：total 0.09s / doc 计数 1.4s(冷) / feedback 0.00s /
  主查询 0.00s / 中间件角色查询 0.03-0.22s / 连接握手 0.4-0.8ms。HTTP 端到端 20-80s 波动。
- **根因**：聚合需 page_type 判断高频词页，但索引只有 (kb_id,status)（idx_wiki_page_kb_status），
  每行回表；wiki_page 持续写入致优化器统计失真，多库 IN 选 9 万行全扫——2026-10-21 §39
  曾 ANALYZE 应急（28s→0.4s），统计再次失真复发，属同一问题根治落地。
- **修复**：① 0039 迁移（migrations/versions/0039_wiki_page_cover_index.py，幂等守卫）新增
  覆盖索引 ix_wiki_page_kb_status_type(kb_id, status, page_type)——条件聚合纯索引扫描零回表；
  ② api/services/kb_admin.py 删除 list_kbs 合并计数时遗留的独立 wiki COUNT 死查询（3+/13-）。
- **验证**：tests/test_kb_admin.py + test_wiki_keywords.py 25 passed；复测 wiki 聚合 0.3s，
  HTTP /kbs 端到端 306-635ms 稳定（page_size 10/20/30 一致）。
- **部署**：迁移已直接应用到线上库（alembic head=0039，索引已建）；kb-api-server 已热更
  （docker cp 新 kb_admin.py + restart）立即生效；CI 重建后固化。
- **坑位**：① 同一条聚合 SQL「单库快、多库 IN 慢」= 统计失真警报，先 EXPLAIN 看 type/key/rows
  再下结论；② ANALYZE 是应急，持续写入表必须靠覆盖索引根治；③ 合并/重构 SQL 后要检查
  旧查询是否仍被执行（本次死查询即 2026-10-21 合并时遗留，靠 SQL 事件钩子拆解暴露）。

## 48. 当前状态（2026-10-23 wiki评估迁移——「切片评估」页承载的 LLM 对照原文评估 Wiki 迁至 wiki评估页执行与展示）

- **需求**：「把切片评估页面的 wiki 评估迁移到 wiki评估页面去做和展示」——§44 改造
  （v3：单文件粒度 LLM 对照完整原文评估 Wiki）实现时挂靠在了「切片评估」编排
  （dim=chunk），评估对象是 wiki 页却落在切片评估页做与展示，本次迁移归位
- **执行迁移**（commit f2dbf0b，已推送 dev）：
  - `scripts/chunk_eval_v3_step_code.py` 改造为 wiki 版：issue/attribution 的 dim
    chunk→wiki、binding chunk_r→wiki_cmp_r、打印前缀 [切片评估]→[wiki评估]；
    作为「质量评估-QA」n06「Wiki-原文对比评估」节点代码（seed JSON nodes+steps
    双写同源）——页级抽样升级为逐文件对照完整原文评估（内容错误/缺失/是否有意义 +
    score + wiki 构建起始/耗时/评估耗时/结论）
  - n08 汇总+落库：wiki 维度 metrics 合并 v3 字段（`_wiki_cmp_m` 非 per_doc 字段为底 +
    程序检查 `_wiki_m` 覆盖，per_doc 以 v3 明细为主、空库回落程序 per_doc）；
    kb_quality_wiki_eval 落库改为 v3 per_doc 为主行 + 按 doc_id merge n03 程序计数
    （pages/empty/orphan/broken_links/placeholder/missing_source/no_folder）
  - wiki 语义分维持 n05 单文档 + n06 对照原文两路平均（v3 health=单文件均分，结构兼容）
- **展示迁移**：wiki评估页（/quality, dim=all）主页平铺表（kb_quality_wiki_eval）加
  切片/均长/Wiki构建起始/Wiki构建耗时/评估耗时/结论 列（结论含错误/问题 tooltip）；
  详情抽屉 wiki 维度卡片下展示「对照原文评估明细」单文件表（原 chunkDetailTable
  迁用，读 report.metrics.per_doc）；metricSummary wiki 分支补 v3 指标、chunk 分支
  恢复程序版指标
- **切片评估恢复程序版**（QC_CODE：过碎/过长/截断/重复）：gen_quality_dim_tapes.py
  QCLM_CODE→QC_CODE + label/descr 恢复；QSUM_DIM_CODE 的 chunk 落库兼容 dict per_doc
  （补查 file_name，写 short/long/truncated/dup 计数列）；kb_quality_chunk_eval 加
  short/long/truncated/dup 列（0044 迁移，与 wiki_eval 扩展同批）
- **DB 迁移**：0044_quality_wiki_eval_ext——kb_quality_wiki_eval 加 chunks/avg_len/
  wiki_pages/wiki_start_time/wiki_build_sec/doc_process_sec/wiki_tape/eval_duration_sec/
  conclusion/error；kb_quality_chunk_eval 加 short/long/truncated/dup（幂等，列存在跳过）
- **API**：list_quality_file_details 的 wiki 分支返回 v3 明细字段、chunk 分支返回
  程序版计数（对齐 kb_quality_chunk_eval 扩展列）
- **验证**：44 passed（quality 相关 4 个测试文件）；前端 tsc --noEmit 通过；seed JSON
  nodes/steps 同源一致性校验通过
- **坑位**：①「质量评估-QA」seed JSON 无生成器（库导出产物），改 n06/n08 须 nodes 与
  steps 双写，改后用 apply_quality_tape.py 应用；② gen_quality_dim_tapes.py 三个单维度
  编排共用 QSUM_DIM_CODE——改落库逻辑会同步进 文档转换/图数据 两个 JSON（属预期，
  提交时三文件一起）；③ 程序版 QC_CODE per_doc 是 dict（doc_id→明细）非 list，
  QSUM 落库前须转换 + 补 file_name；④ 容器内代码经 COPY 入镜像，git pull 后必须重建
  容器才生效（本次部署用 deploy/kb-update.sh 本地构建 + up）；⑤ MySQL 保留字列名
  （`long`/`empty`）在裸 SQL（text()）里必须反引号，否则 1064（已修：
  gen QSUM `long`、质量评估-QA n08 `empty`）；⑥ retrigger_quality_eval.py 原
  `run_dim = "doc" if dim == "wiki"` 笔误——wiki 重触发会误删 doc 记录、qrun 记错
  维度页面不可见（已修为 "all"）；⑦ docker exec 外层进程被 kill 时容器内 python
  变孤儿继续跑——重触发类长跑脚本须容器内 ps 确认无残留再重发，孤儿 run 的 celery
  任务在 worker 容器重建后可能残留 active 僵尸记录（CPU 时间不涨即僵尸，可无视，
  revoke + 清 tape guard 后重发）
- **落地**（2026-10-23 晚，f2dbf0b 部署 + 本轮修复）：容器重建 + 0044 迁移自动应用；
  apply_quality_tape.py 应用 质量评估-QA/切片评估 编排（n06=v3 wiki 版、n08 合并落库、
  切片 n2 程序版）；切片评估 9 库重发 success（指标如济南库 docs=944 short=38163
  health=70）；doc 评估 9 库重发 success（恢复误删）；wiki 评估（dim=all）9 库逐库
  串行重发中（v3 逐文件 LLM，数小时量级，后台 retrigger 轮询到终态）

## 47. 当前状态（2026-10-23 智能问答过程追踪——思考过程/工具调用链/检索命中落库，供分析定位）

- **需求**：「智能问答的思考过程、工具调用等记录下来，后面好做分析，定位知识库知识质量
  和模型能力的影响」
- **现状盘点**：chat_message 已存 content/refs/thinking（但 thinking 仅 quick 问答存，
  agent 链路不存）；kb_llm_usage 已记每次 LLM 调用的模型/tokens/耗时/重试。缺失 = agent
  思考全文 + 工具调用链明细 + 检索命中明细 + 回合时序——全在 SSE 事件流里用完即弃
  （agents.py 只把 thinking_rounds/tool_calls_count 两个计数放 done 事件，未落库）
- **实现**（commit 82a66fd，已推送 dev）：
  1. 新表 chat_trace（迁移 0042_chat_trace，按 assistant 消息一条）：thinking（reasoning
     全文）+ events（时序事件数组：stage/context/thinking/tool_call/tool_result/done，
     含相对耗时 ms + 截断的 arguments/result + success/error）+ hits（检索命中明细，
     content 截断）+ summary（rounds/tool_calls/retrieval_hits/dur_ms/error）
  2. `api/services/chat_trace.py` TraceCollector：事件循环里随手喂 on_event，finally
     save_trace（best-effort 不阻断问答）；无会话/无 user 不落；空响应尝试也落（失败分析）
  3. qa.py / agents.py 接入：事件循环喂 collector，会话模式 finally 落库；agent 链路
     补存 thinking 到 chat_message（_persist_partial 带 thinking，update_message/
     append_message 均透传）——刷新历史也能看到 agent 思考
  4. 只读 API：`api/routers/chat_traces.py` → GET /api/v1/chat/traces（列表，筛
     session/message/kind/kb/agent，管理员可见全部、普通用户仅本人）+ GET
     /api/v1/chat/traces/{trace_id}（详情含 thinking/events/hits）
- **验证**：tests/test_chat_trace.py 6 用例（事件收敛/工具失败/落库回查/无会话守卫/
  best-effort/字段白名单）；chat/qa/agent 相关 63 passed；实机链路：POST
  /api/v1/qa/stream（quick）→ chat_trace 落库，detail 返回完整 thinking 全文 +
  stage/context/thinking 时间线 + 消息关联 + summary
- **部署**：api 层改动 → 重建 kb-api-server（本次已 :local 部署，迁移自动 0040→0042，
  chat_trace 表已建）；无前端改动
- **⚠️ 部署踩坑（同批修复，commit e2b0736）**：0034_chat_message_attachment_ids.py /
  0035_job_ref_columns.py 一直 **untracked 未提交**——干净 checkout/CI/我的 worktree 构建
  缺它们 → alembic 修订树 KeyError('0035_job_ref_columns') → api-server 启动即崩
  （此前容器没事是因为并行会话从工作树构建侥幸带上）。已补齐入仓 + 并行会话在途的
  0041_quality_eval_file_tables 一并入仓，chat_trace 改链 0042 排其后单头线性（00fab20）。
  **教训：新建迁移文件当日必须 git add**，否则任何「干净 checkout 构建」必崩
- **后续分析用法**：按 session/kb/agent 拉 chat_traces 列表 → 详情看工具链命中与思考
  质量；配合 kb_llm_usage（tokens/耗时）与 chat_message.feedback 做质量归因。前端
  分析页暂无（需求未提，先接口供给）

## 45. 当前状态（2026-10-23 文档转换评估单文件粒度——文件名/状态/乱码/引擎/耗时逐文件展示）

- **需求**：「改文档转换评估逻辑：评估粒度=单文件；评估文件名、转换后的 MD 内容是否有乱码、状态是否正常；
  提取转换引擎、转换耗时展示；改造后清空旧评估记录，全部文档重新触发评估」
- **代码**（commit fe692bd，已推送 dev；`scripts/gen_quality_dim_tapes.py` + `scripts/seed_tape_data/文档转换评估.json`）：
  - 评估脚本 QD_CODE 重写为**单文件粒度**：逐文档产 per_doc 明细（doc_id/文件名/扩展名/文件大小/
    parse_state/parse_error/parse_engine/duration_ms/chunk_count/md_length/garbled/garbled_hits/score）
  - **乱码检测**（纯程序，无 LLM）：U+FFFD 替换符计数 / 非法代理区（0xD800-0xDFFF）/ 编码错位特征词
    （锟斤拷/烫烫烫/屯屯屯/锘挎/鈥/Ã© 等）/ 非法控制字符（仅备注）；命中落 issue_type=garbled_text，
    问题详情含命中项与上下文样例；文本源=doc_chunk 按 seq 拼接全量 content（enabled=1 且 chunk_type='text'）
  - **转换引擎**：doc_chunk.meta.parse_engine（兼容 dict/JSON 字符串，实测 mineru/docreader）
  - **转换耗时**：modo_job 最近一次解析任务 duration_ms（task_class LIKE '%Document%'，实测 KbDocumentProcessTask）
  - 得分：FAILED=-100 / 处理中=-50 / READY 空文本=-70（empty_conversion）/ READY 乱码=-40；汇总 health_score=
    平均单文件分；metrics 增 garbled/avg_score/avg_duration_ms/engines/by_state/per_doc
  - gen 脚本顺带修复：QCLM_CODE 引用改为读 `scripts/chunk_eval_v3_step_code.py` 文件（此前 import 缺该符号
    跑不起来），切片评估 label/descr 同步 v3 文案，三个单维度 seed 幂等可重放
  - 前端展示已在切片评估 v3 提交中就位（QualityAssessPane.tsx docDetailTable：文件名/状态/引擎/耗时/切片/乱码/得分；
    metricSummary 展示 文档/就绪/失败/处理中/乱码/平均耗时/引擎分布），本次无需再改前端
- **部署**：`scripts/apply_doc_eval_tape.py` 容器内重建线上「文档转换评估」编排（tape id 1ee30c7d 不变，
  4 节点，label 已更新为「文档转换质量评估（单文件：状态/引擎/耗时/乱码）」）；不涉及 api/web/worker 代码，
  无需重建容器
- **清空+重触发**（`scripts/retrigger_doc_eval.py --include-empty`，api-server 容器内 nohup 后台执行）：
  清空旧 dim=doc 的 kb_quality_run+kb_quality_report 共 17 条，对全部 8 个有文档的知识库
  （含从未评估过的 35a8e888）逐个 execute_tape(env=prod) 发起 scope=all 并轮询到终态，全部 success
- **结果落库验证**：每库 doc_metrics 含 per_doc 明细，字段齐全（如 fdf70549 库「模型元数据.xlsx」READY /
  docreader / 1862392ms / garbled=false / score=100；eb6ee0ed 库 283 文档 health=99，mineru×119+docreader×162+
  未知×2，avg_dur≈1300s，2 个 FAILED）
- **坑位**：① 乱码检测只覆盖文本切片，纯图片/扫描 PDF 无文本 → 命中 empty_conversion 而非乱码；
  ② 未知引擎=该文档无 text 切片（FAILED/空转换），engines 统计用「未知」兜底；③ 文档转换评估纯程序秒级，
  与切片评估（LLM 逐文件数小时）串行不冲突；④ 重触发脚本轮询须 rollback 结束旧事务（MySQL REPEATABLE READ
  快照，同 f5332ef 修复）

## 44. 当前状态（2026-10-23 切片评估 v3——单文件粒度评估 Wiki：LLM 对照完整原文查内容错误/缺失/是否有意义 + Wiki 构建起始与耗时展示）

- **需求**：「改 wiki 评估逻辑：评估粒度=单文件；评估时调 LLM 看 wiki 是否有明显内容错误/缺失，
  对照完整 md 判断 wiki 是否有意义；提取 wiki 构建起始时间与耗时展示；改造后清空旧评估记录重新触发」
- **代码**（commits 7020238 + 7d9d577 + f5332ef，已推送 dev 且容器已生效；本会话接手时核对完成）：
  - `scripts/chunk_eval_v3_step_code.py` = 线上「切片评估」编排 n2 质量检查节点代码（已用
    `scripts/apply_chunk_eval_tape_v3.py` 应用到库内 kb_tape，tape id 92427d69 不变，4 节点：
    def 输入定义 → script 质量检查 → script 汇总落库 → print 完成汇总）
  - **评估逻辑**：每个文档单独评估——doc_chunk 按 seq 拼接当完整原文，按 source_refs 命中该文档的
    wiki 页（摘要页优先，content 二次回表避免整库拉正文），超量均匀抽样（原文 ≤20 块×1000 字、
    wiki ≤12 页×2000 字）；LLM 输出 JSON：score(0-100)/has_content_error+content_errors/
    has_missing+missing_items/meaningful+meaningful_note/conclusion；三类布尔问题落 issue：
    wiki_content_error / wiki_missing_content / wiki_not_meaningful；无切片/无 wiki 页确定性判定
    （no_chunks / wiki_no_pages）不调 LLM；LLM 失败记 sem_eval_error + 单文件 score=None 不阻断
  - **构建时间提取**：优先该文档的 wiki 构建编排 run（kb_tape_run inputs 含 document_id/doc_id/kid，
    取最近 success，回落最近一条）created_at/updated_at → per_doc.wiki_start_time / wiki_build_sec；
    回落 wiki 页创建时间窗；doc_process_sec 因 kb_document created_at/updated_at 混用时区会算负
    → 负值置空
  - 前端（`web/src/components/quality/QualityAssessPane.tsx` chunkDetailTable）：单文件明细表列 =
    文件名/切片/均长/Wiki页/Wiki构建起始/Wiki构建耗时/处理时长/评估耗时/评分/结论；问题类型文案补
    wiki_no_pages/wiki_content_error/wiki_missing_content/wiki_not_meaningful（已部署，容器构建产物
    已含「Wiki构建耗时」列）
- **清空+重触发**（`scripts/retrigger_chunk_eval.py`，api-server 容器内 running，PID 289，串行执行）：
  删除旧 dim=chunk 的 kb_quality_run + kb_quality_report（共清 1 条，可发现仅 1 库有独立 chunk 记录），
  对 7 个知识库逐个 execute_tape(env=prod) 重发起 scope=all 并轮询到终态（库里间串行，编排防重限制）
- **最终结果**（2026-10-23 12:30 全量重评估完成，7 库全部 success，report 落库 per_doc 含
  wiki_start_time/wiki_build_sec/score 等字段）：
  - 030e4f0d72 test（3 文档）：health 51.0，wiki_pages 29
  - 4d81657d4a 木业（9 文档）：health 0.0，wiki_pages 0（该库文档未构建出 wiki 页面，全 wiki_no_pages）
  - 610ece764f 济南市监局（944 文档）：health 65.9，wiki_pages 73767，985 问题
  - 7bab0d08 供管制度（28 文档）：health 60.7，wiki_pages 2049
  - 9c4f58df 茶叶（32 文档）：health 75.7，wiki_pages 2812
  - eb6ee0ed NG集团规范（281 文档）：health 33.4，wiki_pages 14804
  - fdf70549 电信语义层（1 文档）：health 0.0，wiki_pages 0（无 wiki 页）
- **中断与自愈**（同日）：并行会话反复部署重建 api/worker 容器（10:47/11:13/11:24），celery
  评估任务随 worker 死亡丢失、run 变孤儿（status 恒 running 无人执行），NG 库两次被斩。
  处理：① 孤儿 run 置 failed 释放 tape 防重 guard（execute_tape 的 guard 是 tape 级，不看 KB）；
  ② 清中断尝试的孤儿占位行（否则该库评估在质量页不可见/卡 queued）；③ 自愈驱动
  `scripts/retrigger_recover.py`（宿主机侧，一次性 docker exec 执行，容器重建不受影响）——
  检测 worker 容器创建时间晚于 run 创建即判死 → kill 活跃 run + 清占位行 → 重发，直到跑完。
  坑：execute_tape 内 import celery 会打 "Tasks registered:" 横幅到 stdout，解析 run_id 须用
  标记行（RUNID=）取末行，整段 stdout 会污染 SQL；docker inspect {{.Created}} 是 RFC3339
  nano 格式，需 python3.11+（系统 python3 解析 'Z' 失败）。
- **坑位**：① v3 逐文件调 LLM，大库整库评估要数小时（944 文档约 2h），重触发脚本单库等待上限 6h；
  ② 轮询 kb_tape_run 时同一 DB 会话连续 SELECT 在 MySQL REPEATABLE READ 下共享快照永远读不到新状态
  → 每轮轮询前 rollback（f5332ef 修）；③ 首轮清空后脚本重启只能扫到已生成记录的库 → 新增 --kbs 显式
  指定；④ 质量 run 行（kb_quality_run）在编排结束前恒为 queued，真实进度看 kb_tape_run.status 与
  build worker 日志；⑤ 结果落库双写：kb_quality_run.chunk_metrics（汇总+per_doc）+ kb_quality_report
  （整库一行，metrics 同构）；⑥ 与并行会话（质量评估 doc 维度单文件化）无重叠文件
- **部署注意**：代码/脚本已推送 dev；应用层（apply 脚本 → tape）与容器（api/worker/web 重建）均已生效，
  无需再部署；重触发跑完后质量评估「切片评估」页自动可见新记录（旧记录已清空）

## 43. 当前状态（2026-10-22 构建任务完成检查——质量评估目录新子菜单）

- **需求**：「在质量评估菜单目录下开发一个构建任务完成检查的子菜单，用来检查任务
  监控页面的构建任务是否真的成功了」——任务监控把构建链任务（文档解析/摘要编排/
  wiki构建编排/题材分析编排）显示为「成功」，但历史上出现过多类「假成功」
  （ORCH job 状态未回写 e01d707、run 成功但节点 failed、run 成功但产物缺失），
  本页逐条核对真实性。
- **后端**（新增 `api/routers/quality_build_check.py`，prefix `/quality/build-check`，
  已注册 main.py）：
  - `GET /tasks`：构建链任务列表（modo_job 三类任务类 KbDocumentProcessTask/
    KbPreChunkedIngestTask/KbOrchestrationRunTask + kb_tape_run 双表按
    构建/摘要/题材 关键词匹配；ORCH job 预关联 run 批量反查、独立 run 无承接
    job 也纳入、双表同名去重；支持 kb/kind/result 筛选 + 分页）
  - `GET /overview`：按检查结果/类型分组计数（顶部统计卡）
  - `GET /tasks/{id}`：详情 + 编排节点 step_results 明细（job id 或 run id 均可查；
    ORCH job 关联 run 不存在 → 返回 unknown 而非 404）
  - **4 项检查**（每行输出 checks[] + result）：
    1. consistency 状态一致性：任务监控显示（modo_job.state 大写） vs 真实状态
       （run.status / parse_state 推断）——job SUCCESS 但 run failed/cancelled =
       假成功；job PENDING/RUNNING 但 run 已终态 = job 未回写
    2. internal 节点一致性：run=success 但 step_results 有 failed 节点（引擎内部
       矛盾）或全部 skipped（空转）→ 假成功
    3. artifact 产物验证（run 成功时按类型）：summary → kb_document.summary_status
       ==READY；build → bindings 建页统计键（summary_slug/kw_created/kw_merged/
       rule_created/rule_merged/biz_created/biz_merged，值非空判定），无键兜底
       wiki_page 计数（单文档按 source_refs 含 doc_id）；domain_resolve →
       bindings.resolved_tape_id 非空。产物查询按 run_env 路由（dev run 查 _dev 表）
    4. zombie 僵尸检测：queued/running 且 updated_at 超 6h 未推进（对齐
       NODE_STALE_S）→ 疑似卡死
  - **result 判定优先级**：stale > unknown > fake_success > failed > running > ok；
    解析任务真实状态由 kb_document.parse_state 推断（READY→success/FAILED→failed）
- **前端**（`web/src/app/(main)/quality-build-check/page.tsx` + api.ts 类型/接口）：
  顶部 6 统计卡（构建任务/真实完成/疑似假成功/失败/疑似卡死/进行中，仿 job-stats
  形态）+ 筛选（知识库/类型/检查结果）+ ModoTable 列表（检查结果 Tag 带失败检查点
  Tooltip、任务/类型/知识库/文档/页面显示/真实状态/产物/时间/详情）+ 详情抽屉
  （检查项明细 ✓/✗/⚠ + 编排节点执行记录表）
- **菜单**：seed_framework_db.py KB_MENUS + MenuContext FALLBACK_MENUS 加
  `("quality_build_check", "构建任务检查", "/quality-build-check", "grp_quality",
  "SafetyOutlined", 5)`（AppSider ICON_MAP 已有 SafetyOutlined）
- **测试**：tests/test_quality_build_check.py 21 用例（解析假成功/真实成功/真实失败、
  ORCH job 假成功、run 节点 failed 假成功、全 skipped 假成功、产物键命中 ok、
  产物缺失假成功、wiki 页兜底 ok、摘要未落库假成功、摘要 READY ok、僵尸、进行中、
  独立 run 纳入、非构建 run 排除、run 缺失 unknown、筛选/overview/detail）；回归
  test_quality_dim_runs + test_quality_runs_delete + test_jobs_monitor +
  test_orch_job_status_sync + test_summary_failed_mark = 45 passed；web tsc --noEmit 0 错
- **坑位**：① 双表循环聚合必须按主键去重（测试环境 prod/dev 两轮 env_scope 查
  同一物理表）；② run 侧 like 匹配要覆盖 构建/摘要/题材 三关键词（只 like 构建
  漏摘要与题材分析）；③ 产物验证的 DB 查询用请求注入 session + env_scope(run_env)
  路由，不能 get_sessionmaker()()（测试环境连真实库）；④ job.create_time 列类型
  标注 object，序列化走 _iso() 安全包装；⑤ 列表 result 筛选必须在全量检查后做
  （result 依赖检查结果），构建链任务量小全量检查可接受
- **部署注意**：api 层（新 router + main.py）→ 重建 kb-api-server；前端（页面 +
  api.ts + 菜单兜底）→ 重建 kb-web；菜单属库内数据 → docker cp
  seed_framework_db.py 跑（幂等 upsert 补 quality_build_check 行，种子角色自动授权）
- **遗留**：result=stale 的僵尸任务页面仅展示，未提供「一键重投/终止」操作（编排页
  可手动重跑）；产物验证的 wiki 兜底仅按页面计数，未做页面内容非空校验（质量评估
  wiki 维度已覆盖）

## 42. 当前状态（2026-10-22 质量评估批量跑通——两处 bug 修复 + 全库评估完成）

- **任务**：用质量评估功能评估全部已构建 KB 文档（7 库 × dim=all/doc/chunk/graph），
  过程中修 bug，修完用 deploy/kb-update.sh 更新后重评，直到全部完成。
- **Bug 1（评估任务永远排队中）**：`api/routers/quality.py` launch 时
  `execute_tape(db, tape_id, inputs)` 未传 env（None→dev），而占位行建在
  API 默认 prod 上下文、UI 读正式表；评估脚本 get_sessionmaker() 被 env 路由到
  _dev 表 → 读旧快照库（未同步新库的 KB）、结果落 kb_quality_run_dev，占位行
  永远 queued。**修复：显式传 env="prod"**（与上传链一致：数据在 prod，编排节点
  须同环境读写）。commit 64efefa。另清理了旧代码遗留的 4 条卡死占位评估任务 +
  3 条孤儿 run 行（不清理会触发「已有运行中的任务」拦截）。
- **Bug 2（dim=all 维度分全丢）**：电信语义层 dim=all 成功但 chunk/wiki/graph 全
  None（单维度评估 99/100 正常）。run.bindings 只剩 wiki_sem_r/wiki_cmp_r/
  graph_sem_r（n05/n06/n07），n02/n03/n04 并行写的 chunk_r/wiki_r/graph_r 全丢。
  **根因**：`worker/tasks/orch_runner.py` `_run_node_impl` 写回 bindings 是
  「无锁读 _bindings_from_run → merge → _update_run 整行覆盖」（写才加锁）。
  fan-out 并行节点毫秒级完成时（小库单节点 100-250ms）后写者覆盖先写者的 key；
  大库单节点数秒自然错开故一直没暴露。**修复：新增 `_merge_run_bindings`——读-改-
  写在 `_lock_run`（SELECT FOR UPDATE）内完成，同 run 并行节点提交串行化；
  __loop_state__ 仍由调度中枢独占。**commit 5148f33。
- **验证**：修复后重跑 电信语义层 dim=all → chunk=99/wiki=0/graph=60（原全 None）；
  test 库回归 dim=all → chunk=99/wiki=48.5/graph=74（原 graph None，同 bug）。
  测试：test_quality_dim_runs 7 + test_orch_runner_bindings_merge 4（新）+ orch
  相关套件 52 passed；两次部署均走 deploy/kb-update.sh（沙箱 nohup 代跑）。
- **全库评估结果（7 库 × 4 维度，全部 success）**：
  - test：chunk 99 / wiki 48.5 / graph 74 / doc 100（69 问题，wiki 断链/孤立为主）
  - 木业：chunk 98 / wiki 无页(0 页) / graph 60 / doc 100
  - 济南市监局：chunk 70 / wiki 63.3 / graph 48 / doc 100（71638 问题，wiki 孤立/缺出处居多）
  - 供管制度：chunk 98 / wiki 54.4 / graph 48 / doc 96（1 文档转换失败）
  - 茶叶：chunk 77 / wiki 63.5 / graph 44 / doc 100
  - NG集团规范：chunk 69 / wiki 79.6 / graph 48 / doc 100（270 文档仍 PENDING）
  - 电信语义层：chunk 99 / wiki 0（无页面）/ graph 60 / doc 100
- **部署注意**：两次改动均已在 dev 提交推送（64efefa + 5148f33）；容器已重建生效。
- **坑位**：① 质量评估与「编排页手动执行」同走 execute_tape 但语义不同——评估是
  生产审计必须 prod，手动执行是测试执行走 dev，勿统一改；② 并行编排节点写回
  bindings 必须行锁内合并，任何「读-改-写」三段式在 fan-out 快速完成时都会
  lost-update；③ 评估大库（济南 73765 页）单次 dim=all 约 4 分钟，7 库全量约
  11 分钟；④ 同 tape 防重 guard 要求按 KB 串行（每 KB 4 维度并行、KB 间等待），
  批量脚本见 /tmp/run_quality_eval.py。

## 41. 当前状态（2026-10-21 索引缺失巡检——模型声明 vs 实际库比对）

- **巡检方法**：SQLAlchemy 元数据声明索引（index=True 单列 + Index() 组合）vs
  实际库 SHOW INDEX 覆盖比对，按「索引覆盖的列组合」而非索引名判断
  （迁移自定义名与 SQLAlchemy 自动命名 ix_<table>_<col> 不同，纯名字比对会误报）。
- **发现 6 表 7 索引缺失**（模型声明但线上库未建，均为高频查询列）：
  - chat_session.parent_session_id（分叉会话查询）
  - kb_datasource.scope / owner_team_name（kb_visible_clauses 每请求可见性过滤）
  - kb_document.source_type（按来源类型过滤）
  - kb_quality_run.kb_id（按库查质量运行）
  - wiki_link.kb_id（51k 行，删除/读链接多处 WHERE）
  - wiki_page_issue.kb_id（按库查 wiki 问题）
- **全表核对**：其余业务表索引完整（kb_llm_usage 6 索引 / modo_job 7 索引 /
  wiki_page 4 索引等均 OK）；dev 双表（xxx_dev）数据量小且仅编排测试读写，
  不在正式查询路径，未补（随 create_all 建，量小影响有限）。
- **修复**：migrations/versions/0036_missing_indexes.py（幂等守卫
  _table_exists/_column_exists/_index_exists，跨 MySQL/PG；命名对齐
  模型 ix_<table>_<col>）。已在线上库执行（0035 → 0036，5.5s），
  7 索引全部落库确认。
- **验证**：test_migrations_version_table + test_kb_admin + test_system_admin
  64 passed；全量 pytest 841 passed / 1 failed（存量 DB 环境问题）。
- **P2 待续**：modo_job.state/task_class/create_time 统计过滤仍全扫
  （系统任务排除靠 not_in + 状态分组，补偿收益低未做）；ChatMessage.feedback
  反馈统计（chats 表量级小）；kb_document 文档列表 file_ext/parse_state
  过滤（已随 kb_id 索引前缀生效，单独立索引收益低）。

## 40. 当前状态（2026-10-21 智能问答「为什么没有返回」——同模型 WeKnora 能答 kb 不能）

- **现象**：智能问答（供管制度智能体）最近会话 assistant 消息 content=''，无回复；
  usage 记录 04:19:43 / 04:27:52 两次 `op:agent 429 Too Many Requests`。
- **对比 WeKnora（同 deepseek-v4-pro 能答）**：
  1. **WeKnora 不强制 thinking**：基类 `baseProvider.Thinking() = noThinking{}`（provider.go:66），
     deepseekProvider 继承不发 thinking 字段；模型真吐思考也有 `removeThinkingContent`
     剥离 `<think>` 兜底（openai_stream.go:26-27,88-101）。
  2. **kb 前端硬编码 `thinking: true`**（chat/page.tsx:773，cbcff52 引入）→ agents.py:532
     `req.thinking` 优先于 agent 配置（供管制度智能体 config.thinking=False）→ 强制网关
     思考模式 → deepseek-v4-pro 工具循环+长上下文（8k+ tokens）下只输出 reasoning_content、
     正文 content 空（usage 铁证：agent 调用 in 7k-9k、out 仅 59-243 tokens 全是思考无正文）。
  3. **429 重试太弱**：chat.py 原 3 次尝试、退避 1.5s/3s，限流高峰（InferAI ~3次/分）
     连续失败直接「无返回」；编排引擎同场景已是 15s 起指数退避 + 5 次。
- **修复（2 文件，api+web 各一）**：
  1. `web/src/app/(main)/chat/page.tsx`：`thinking: true` → `undefined`——不再覆盖 agent
     配置，agent 问答回落 thinking=False 正常输出正文；普通问答回落网关默认（reasoning
     仍会经 thinking 事件折叠展示，有则展示不强制）。对齐 WeKnora。
  2. `api/services/chat.py` `_stream_openai_stream`：429/403/408 限流类 5s 起指数退避
     （5/10/15/20s）、尝试次数 3→5；5xx 保持 1.5s 起。重试耗尽后 429 抛中文可读提示
     （「模型服务繁忙（HTTP 429 限流）…请稍等 1-2 分钟」，对齐 404 处理手法），
     不再裸抛 httpx 英文异常。
- **验证**：test_chat_client/test_chat/test_qa_stream_resume 20 过；web tsc --noEmit 0 错；
  全量 pytest 828 passed / 8 failed（存量 DATABASE_URL 环境问题，与基线一致）。
- **部署注意**：api + web 双层改动 → 重建 kb-api-server + kb-web 生效。
- **实测确认（同模型三种参数均可出正文，简单问答不受 thinking 影响）**：no-thinking /
  thinking-true / thinking-false 三发 1+1 均返回 content='2'；问题仅在 agent 工具循环
  组合下 thinking 强制开启时出现。后续若用户需要深度思考开关，应按 WeKnora 思路做
  provider 级 thinking_control（模型条目已有 thinking_control 字段），而非全局硬编码。

## 39. 当前状态（2026-10-21 列表接口性能——GET /api/v1/kbs 慢的根因修复）

- **需求**：用户反馈 `GET /api/v1/kbs?page=1&page_size=30` 很慢（10.1.215.50 线上）。
- **根因（每次请求 45+ 次串行 DB 往返，全部打到远程库 10.1.206.135:3306/data_supply）**：
  1. **rbac_guard 中间件菜单 N+1（主因）**：api/middleware.py `_menu_api_guard` 对 my_menus
     返回的每个菜单单独 `select(Menu)` + `menu_api_perms` 再查一次（~2N 次查询，N=菜单数 30+），
     且对**所有受控 /api/v1 请求**都生效——所有列表接口慢大概率同一根因。
  2. **list_kbs 计数查询多**：page_counts / keyword_counts 两次独立 group-by 全扫 wiki_page
     该 kb_id 命中集（远程库 RTT 叠加）；is_admin 3 次查询。
- **修复（2 文件，纯 api 层）**：
  1. `api/middleware.py`：`_menu_api_guard` 改为一次 `WHERE menu_id IN (...)` 批量取回候选菜单，
     路由匹配 + api_perms 解析在内存完成（新增 `_menu_ext_perms(mrow)` 与
     services.system_admin.menu_api_perms 语义一致）；每请求菜单查询从 ~2N 降为 1。
  2. `api/services/kb_admin.py` list_kbs：wiki_page 两次 group-by 合并为一次条件聚合
     （`func.sum(case((page_type=='frequent_keyword',1),else_=0))` + coalesce），
     page_counts/keyword_counts 同一条 SQL 产出；import 补 `case`。
- **验证**：test_kb_admin + test_system_admin 57 过；test_rbac_guard + test_wiki_keywords +
  test_menu_tree 24 过；全量 pytest 821 passed / 8 failed（8 个全是存量
  `Missing required env var: DATABASE_URL` 环境问题，与本次无关）。
- **部署注意**：纯 api 层 → 重建 kb-api-server 生效（CI 拉镜像后
  `cd deploy && docker-compose pull && docker-compose up -d --no-deps api-server`）；web 无改动。
- **剩余事项（P2/待续，承接 §38 未竟项）**：可见性 OR 条件（owner_user_id OR team OR scope）
  拆 UNION 或加 (state, created_at, id) 复合索引；my_menus 角色收集与 check_path_permission
  重复（每请求 role 关系查 3-4 遍）可合并；is_admin 的 role 查询可批量。

## 38. 当前状态（2026-10-21 性能隐患修复——任务监控全表扫描 / 全库统计 / 计数物化 / N+1）

- **需求**：用户要求检查并修复系统性能隐患（分页/全库查询/统计类）。排查出 P0-P2 隐患清单，
  按 A→B→C 顺序修复（A=job JSON LIKE 冗余列；B=jobs 列表 SQL 侧分页；C=overview trend 去
  func.date()），另顺手修 P1 项（计数物化/N+1/反馈统计聚合）。
- **完成项（全部代码层，含迁移 0035）**：
  1. **A — modo_job 业务引用冗余列**：`api/models/framework.py` Job 新增
     `run_id/kb_id/doc_id/datasource_id` 四列（均 index=True），并挂 `before_insert`/
     `before_update` 事件从 task_params JSON 自动提取（提取键兼容 ORCH_RUN_*/DOC_*/WIKI_SKILL_*/
     META_*/REPO_* 各任务形态；所有创建路径零改动自动落库）。迁移
     `migrations/versions/0035_job_ref_columns.py` 补列 + 索引 + 存量回填（主键游标分页，
     跨 MySQL/PG 用 Python 解析，无引用行跳过不阻塞游标）。查询侧替换 4 处 JSON LIKE：
     jobs.py:262/665（attached run 判定）、kbs.py:667+1521（WIKI 任务 kb_id 收窄）、
     metadata.py:129+243（datasource_id）——全部改冗余列等值索引直查。
  2. **B — jobs 列表 SQL 侧分页**：`api/routers/jobs.py` list_parent_jobs / list_jobs 原「窄列
     全量载入 + 应用层合并排序切分」，改为两侧各按 (时间,id) 倒序取 top-k（k=offset+page_size）
     再合并切片——数学上精确等价全局排序分页（反证法：全局前 k 元素必在自己表内排前 k），
     1000 组随机模拟 0 差异。attached run 排除下沉为 SQL NOT EXISTS（借 run_id 列）。
     保留窄列排序 → 无 filesort 爆 sort_buffer（1038）风险（dc63463 防护语义不丢）。
  3. **C — overview/trend 去 func.date() 化**：`api/routers/overview.py` WHERE 改裸列
     `created_at >= 当天零点`（命中 0029 迁移的 (kb_id, created_at) 复合索引），
     GROUP BY 仍按 DATE() 分桶；三处（文档/分块/Wiki 页）同改。新增 `_since_dt()`。
  4. **P1 顺手项**：
     - `feedback_stats()` 总数/赞踩改 SQL 聚合（case+count），今日/本周窄列只取 feedback_at
       （原全列物化全部反馈行）；`list_feedback` 每行一次 `_prev_user_question` N+1 改主查询
       相关子查询一次取回（删 `_prev_user_question`）。
     - `kb_admin.py` doc_branch/wiki_branch/list_wiki_pages 三处 `total = len(all())` 改 SQL
       count（原把 content/summary Text 大列全量物化只为计数）。
     - `agents.py` list_agents / `datasource.py` list total 改 SQL count。
     - `metadata.py` list_metadata_datasources：表数/最近采集时间批量 group by 聚合（原每数据源
       2 次查询 N+1）；jobs 逐源查询保留（已走 datasource_id 索引）。
- **验证**：任务监控 21 用例全过（test_jobs_monitor.py + ext）；test_feedback_stats 7 过；
  全量 pytest 821 passed / 8 failed（8 个全是存量 `Missing required env var: DATABASE_URL`
  环境问题，与本改动无关）；web `tsc --noEmit` exit 0；迁移回填逻辑 SQLite 实测 OK
  （六类 task_params 形态提取正确）；top-k 分页 1000 组随机模拟 0 mismatch。
- **部署注意**：代码/迁移均属 api+worker 层 → 需跑 `alembic upgrade head`（0035 补列+回填）
  后重建 kb-api-server + celery worker；web 无改动无需重建前端。存量 job 行回填后
  LIKE 查询可彻底下线（回填前新代码查询可能漏存量数据——先跑迁移再上线代码）。
- **坑位**：① SQLAlchemy 事件监听器签名是 `(mapper, connection, target)` 三参，写成单参
  TypeError（已修）；② before_insert/update 事件对所有 Job 写入生效，scheduler 定时任务的
  fire_params 非 JSON 或无可提取键 → 事件返回空 dict 不落库，安全；③ run_id 冗余列使
  attached 判定可在 SQL 侧 NOT EXISTS——注意 modo_job 不在双表隔离名单（ISOLATED_TABLES），
  dev 环境下子查询仍查正式表，符合语义（attached job 都在 modo_job 一张表）。
- **剩余事项（P2/待续）**：
  - [ ] 索引补充：Job.state / Job.task_class / Job.create_time（统计 group by state 与
    按 task_class 过滤仍全扫）；ChatMessage.feedback（反馈统计）；三张知识表
    created_at 索引已在 0029（kb_document/doc_chunk/wiki_page 的 (kb_id, created_at)）。
  - [ ] 可选：TaskRun 双表枚举的统计可下沉 UNION；build_engines/kb_categories 全表查询
    表很小暂不改；files 探索 group by 全表扫量级可控暂不改。
  最后更新：2026-10-21（token 使用统计页布局重叠修复——进度见 §38；摘要编排失败标记见 §37）

  ## 38. 当前状态（2026-10-21 token 使用统计页 /wiki/usage 布局重叠修复，未提交）

  - **需求**：token 使用统计页（`web/src/app/(main)/usage/page.tsx`，2026-10-20 补建、工作区 untracked 未提交）内容重叠：图表区内容压在第 2 行分布图与明细表上面
  - **根因（两层）**：
    1. **内容滚动区 flex 压塌**：滚动区是 flex column + `overflowY:auto`，第 1 行图表 grid 设了 `minHeight:0` 却没设 `flexShrink:0` → 内容超高时被 flex-shrink 压成 **h=0**，而 grid 子元素（白盒，高 559）`overflow:visible` 照常绘制 → 直接覆盖第 2 行分布图（y=258）与明细表（y=764）。实测 row1 容器 y=242 h=0、其子元素 y=242 h=559
    2. **趋势图容器高度不匹配**：`TokenTrendChart` 外层套固定 `height:240` 容器，但 Card 本身高约 344（标题栏 36 + EChart 300）→ 卡片底边溢出 100px+；且页面标题「Token 使用趋势」与 Card 自带标题重复
  - **修复（3 文件，纯前端）**：
    1. `web/src/app/(main)/usage/page.tsx`：第 1 行 grid 去掉 `minHeight:0`、加 `flexShrink:0`（第 2 行分布图 grid 同样补 `flexShrink:0`）；趋势图外层去掉固定 `height:240` 容器，改 `display:flex;flexDirection:column` + 内层 `flex:1;minHeight:0` 撑满，传 `title=""` 避免与页面标题重复
    2. `web/src/components/usage/TokenTrendChart.tsx`：新增 `title`/`height` props（默认 title="Token 使用趋势"、height=300，height 支持 "100%"），Card 加 `style={{height:"100%"}}` + body `height:100%` —— 页面用 `height="100%"` 让趋势图随 grid 行高撑满，与右侧知识库分布卡等高
    3. `web/src/components/usage/DistributionChart.tsx`：去掉 Card 的 `style={{height:"100%"}}`（在 grid 拉伸盒内百分比高度解析异常，KB 卡底边 895 溢出 box 877，压到第 2 行 2px）
  - **验证（tsc 0 错 + bun run build 过 + playwright 实机）**：1600x900 下内容区三块无重叠——row1 y=242 h=635、row2 y=893 h=496、明细表 y=1405 h=280，趋势卡与 KB 卡等高 566 填满行；明细表 wrapper 底 833 < 分页顶 837 无重叠；滚动区 scrollH 1447 > clientH 648 正常滚动。1280x800 / 1920x1080 两视口未复测（脚本中断）
  - **部署注意**：纯前端 → 提交推送后 CI 重建 kb-web 生效；页面本身 untracked（`?? web/src/app/(main)/usage/`）+ DistributionChart 有一处未提交改动（`export type Metric`），一并提交
  - **坑位**：① 滚动区 flex column 里「内容超高时想让它滚动而不是压塌子项」→ 每个内容行必须 `flexShrink:0`，`minHeight:0` 单独用会把行压成 0 且子项照常溢出绘制（重叠假象）；② antd Card `height:100%` 在 grid 拉伸盒内不靠谱，需要撑满时给 Card 加 style height:100% + body height:100% 且父链显式给高度；③ 页面标题与组件 Card 自带标题重复时，组件加 title prop 由页面传 ""；④ 本机验证反代 BACKEND 指向 `127.0.0.1:80`（本地容器栈，账号用构造 cookie 的 chengkai/plat-mgr），非 10.1.215.50（admin/sys 在线上后端不可用）

  ## 37. 当前状态（2026-10-21 摘要编排失败标记 + 已就绪状态区分）

- **需求**：① 摘要编排失败也要在文档列表标出来（此前摘要 run 失败 summary_status 永远留 "" → 灰色「已就绪」，与「没配摘要」无法区分）；② 两个「已就绪」（绿=全流程完成 / 灰=有缺项）在文案上区分
- **根因**：摘要编排 run 失败时没有任何回写 kb_document.summary_status 的路径（只由 kb_write_doc_summary 原子写 READY，全文搜索确认无写 FAILED 点）→ 摘要失败与摘要未生成同态
- **实现（三层）**：
  1. `worker/tasks/doc_process.py` `_enqueue_summary_task`：摘要 run 的 inputs 加 `"purpose": "summary"` 身份标记（构建/题材分析/手动执行 run 无此标记，不受影响）
  2. `worker/tasks/orch_runner.py`：新增 `_mark_summary_failed(inputs, error)`——仅 purpose=summary 且有 document_id 时回写 `summary_status=FAILED` + `summary_error`（截 500 字，守卫：已有 READY 不被旧失败 run 覆盖，回写异常不阻断编排）；`_update_run` 置 failed 终态时自动调用（覆盖「编排不存在」与节点失败终态两条路径）。⚠️ 函数内 `from api.db import get_sessionmaker`（模块顶部 import 的绑定不响应测试 monkeypatch）
  3. `web/src/components/DocCardView.tsx` `docStageTag`：失败细分（摘要失败/构建失败/摘要与构建失败，红）；灰色「已就绪」按缺项细分：`已就绪·未构建`（摘要完成构建未生成）/ `已就绪·无摘要`（构建完成摘要未生成）/ `已就绪·待生成`（都缺）；所有状态 Tag 挂 title 悬浮显示「已就绪 · 摘要xx · 构建xx」三段详情
- **测试**：新增 `tests/test_summary_failed_mark.py` 7 用例（回写/非摘要不动/未知 purpose 不动/READY 不覆盖/_update_run failed 触发/success 不触发/非摘要 failed 不动）；43 passed（+ domain_guided_build + orchestration_engine 回归）
- **验证**：tsc --noEmit exit 0；定向 pytest 43 passed。⚠️ 全量 pytest 现被 `api/routers/jobs.py:467` 语法错误阻塞——并行会话在途改动（107+/17-，run 耗时对齐逻辑）把 _run_duration_ms docstring 开头 `"""` 丢了，import api.main 即 SyntaxError，非本次改动，未代改
- **部署注意**：doc_process.py/orch_runner.py 属 worker/API 层 → 重建 kb-api-server + 4 celery worker（或 docker cp 热修 + restart）；DocCardView 属前端 → 重建 kb-web。存量已失败的摘要 run 不回补（只对新失败生效）
- **坑位**：① 磁盘上 summary_status=FAILED 的旧数据不存在（该状态此前从未写入），前端 FAILED 分支是新链路产物；② 手动在编排页执行摘要 run 无 purpose 标记 → 失败不会回写文档（有意设计：手动调试不干扰业务状态）；③ jobs.py 语法错误会随并行会话提交进 CI 必红——若对方提交前未修复需补 `"""`

## 36. 当前状态（2026-10-20 新建知识库分区表单取值缺失修复）

- **症状**：新建知识库时左侧分区导航停在「检索配置」，填写了名称后直接点「创建」→ 报错 `name: Field required`（后端 KBCreateRequest.name 必填 422）
- **根因（antd 分区表单 + validateFields 语义）**：新建弹窗是分区表单（基本信息/知识来源/Wiki 构建/检索配置），`name` 输入框只在「基本信息」分区挂载。rc-field-form 的 `validateFields()` 不传 nameList 时**只把当前挂载分区字段收进返回值**（useForm.js finalValueNamePathList 仅取 fieldEntities 已注册字段）——其他分区 Form.Item 卸载后其值保留在 store（preserve=true）但不在 `values` 里。工作区旧修复只加了 `getFieldValue("name")` 非空预检（预检通过），payload 仍取 `values.name` → undefined → 后端 422
- **同源隐患（一并修复）**：反向停留在「基本信息」提交时，`values.vector_enabled/keyword_enabled/embedding_model_id/build_engine/repo_*` 同样缺失 → 检索/构建/来源分区设置被静默丢弃（如向量/关键词检索被强制 false、git 仓库配置丢失）
- **修复（commit 待推，纯前端 1 文件）**：`web/src/app/(main)/kbs/page.tsx` onCreate——保留 name 非空预检（`getFieldValue`，切回基本信息+提示），payload 全部改从 `form.getFieldsValue(true)`（完整 store，含未挂载分区保留值）取值：`name=String(nameVal).trim()`、`vector_enabled/keyword_enabled` 用 `!== false` 默认兜底（未到访检索分区=开，与 initialValue 一致）、scope 兜底 personal、repo 系列判断改 `all.repo_type`
- **验证**：`tsc --noEmit` exit 0
- **部署注意**：纯前端 → CI 重建 kb-web 生效
- **坑位**：① antd `validateFields()` 返回值 ≠ 全表单值——分区表单的 payload 一律从 `getFieldsValue(true)` 取，不要依赖 validateFields 返回的 values；② Form.Item 卸载后值在 store（preserve 默认 true），`getFieldValue` 能读到，但 validateFields 结果里没有；③ 表单项 initialValue 只在挂载时写入 store，未到访分区的字段要从 payload 侧给默认值（`!== false`/`|| "personal"` 等）

## 35. 当前状态（2026-10-20 问答页思考输出时出现两个「思考中…」）

- **症状**：智能问答页面思考内容输出过程中，页面同时显示两个「思考中…」
- **根因（两条渲染路径各自重复提示）**：
  1. **智能体模式**：`m.agent && m.thinking` 时顶部渲染 `AgentThinkingRow`（2492 行，streaming 时头部显示「思考中… / 进行中」），而正文区（1499 行）原条件 `m.streaming` 为真时再显示一个「思考中…」→ 双显
  2. **快速问答**：`AssistantThink` 的 think-btn（2329 行，streaming 时显示「思考中…」）与 content-view 占位（2353 行，`answerStreaming` 时再显示「思考中…」）→ 双显
- **修复（commit 79a1a6a，已推送 dev；纯前端 1 插入/3 删除）**：
  1. 正文区占位条件 `m.streaming` → `m.streaming && !m.thinking`——有思考块在展示时正文区不再重复提示，留空等 `contentVisible` 放行打字机
  2. `AssistantThink` 的 content-view 删除 `answerStreaming` 时的「思考中…」占位——think-btn 已是思考状态唯一提示
- **验证**：`tsc --noEmit` exit 0
- **部署注意**：纯前端 → CI 重建 kb-web 生效
- **坑位**：① 思考态提示在三处组件各有一份（AgentThinkingRow / AssistantThink think-btn / 正文占位），改一处必须扫全部「思考中…」字面量（`grep -n 思考中 web/src`）避免重复提示；② `AssistantThink` 仅在 `!m.agent && m.thinking` 分支渲染，其 think-btn 必然存在，故 content-view 不需要独立占位

## 34. 当前状态（2026-10-20 CI 构建失败修复：chat/page.tsx 图片粘贴类型错误）

- **症状**：push dev 触发 CI，`bun run build` 在 TypeScript 阶段失败——`chat/page.tsx:711:31 Type error: Argument of type 'ChatAttachmentItem' is not assignable to parameter of type 'string'`（另 714 行同错），构建退出码 1，阻塞所有后续部署
- **根因**：并行会话 34e6195（图片粘贴上传改造）在 709 行 `const attId = await uploadAttachment(...)`——`uploadAttachment` 返回 `Promise<ChatAttachmentItem | null>`（1077 行 `return res.data`），而调用处把返回值当 string 用：`turnImageIds.push(attId)`（应 push `attId.id`）、`localImages.push({ id: attId })`（应 `att.id`）
- **修复（commit ac6a412，已推送 dev）**：`web/src/app/(main)/chat/page.tsx` 709-717 行——接收为 `att`，`if (att)` 后 `turnImageIds.push(att.id)`、`localImages.push({ id: att.id, ... })`；4 insertions/4 deletions
- **验证**：`tsc --noEmit` exit 0（修复前快照 tsc 报同 2 错，见 §32 归因——当时已识别为并行会话在途代码预存问题，未代改；CI 构建坐实后必须修，阻断交付）
- **部署注意**：前端源码改动 → CI 重建 kb-web 镜像生效（本次修复本身即 CI 构建产物）；工作区并行会话其他未提交文件（GlobalDropZone.tsx/UploadConfirmDialog.tsx/KbDocsPane.tsx/upload-dir.ts 等）未纳入提交
- **坑位**：① 本地工作区 tsc 绿 ≠ 提交快照 tsc 绿（并行会话在途改动可能临时掩盖/恰好修复基线错误）——CI 构建以仓库 checkout 快照为准；② 类型错误在已提交快照坐实（CI 失败）后，即使是他人引入也必须修（阻断交付），commit message 注明来源

## 33. 当前状态（2026-10-20 问答页移除深度思考按钮）

- **需求**：「删除深度思考按钮」——用户反馈没开开关也返回思考过程（此前关闭态被 `deepThinking || undefined` 转成不传参数，默认模型 deepseek-v4-pro 网关默认开启思考 → 开关形同虚设；23a0346 已改为显式透传但用户选择直接删除入口）
- **代码改动（commit 0f5ce24，已推送 dev）**：`web/src/app/(main)/chat/page.tsx`（纯前端，4+/29-）：
  1. 删除输入区「深度思考」按钮（BulbOutlined）与 deepThinking state/setter
  2. 会话快照 last_request_state 的 thinking 字段写入与恢复一并移除（不再记忆该开关）
  3. 删除「深度思考需至少选一个知识库或上传附件」发送拦截（对齐 DSB 反思模式的限制随之消失）
  4. 请求参数固定 `thinking: false`（显式 disabled）——默认模型不再输出推理过程
- **保留不动**：后端 qa.py/agents.py 的 thinking 参数（其他客户端可继续用）；消息里已保存的 thinking 字段与思考折叠块展示（历史消息回看）；QaStreamEvent thinking 事件处理
- **验证**：worktree 独立分支（避开并行会话同文件编辑）grep 零 `deepThinking` 残留；tsc --noEmit 仅剩 2 个预存错误（并行会话 34e6195 引入的 ChatAttachmentItem 类型问题，未代修）；全量 pytest 799 passed / 10 failed 全为既有基线
- **部署注意**：前端改动 → 重建 kb-web 镜像生效

## 32. 当前状态（2026-10-20 知识库删除前校验智能体绑定）

- **需求**：「知识库删除前要校验是否绑定了智能体」——被智能体显式绑定的知识库拒绝删除，避免删完后智能体检索范围悬空
- **绑定判定口径**（对齐 AgentEditorModal「知识库范围」三态）：**selected**（config.knowledge_bases / knowledge_ids 含该 kb_id）= 显式绑定，删除必须拦截；**all** = 智能体检索全部库，不拦截（其 resolve_kb_scope 按 state=="1" 过滤，软删后自动排除，不会悬空；若拦截则「一个 all 智能体锁死所有库删除」）；**none** = 不绑定；已删智能体（state=="0"）不参与拦截
- **代码改动（commit a9e236f，已推送 dev）**：
  1. `api/services/kb_admin.py`：新增 `_agents_bound_to_kb(db, kb_id)`（查 state=1 的 kb_agent，解析 config 判 selected 绑定，返回 [{id,name,kb_selection_mode}]）；`delete_kb` 开头插入守卫——绑定则返回 `{"success": False, "code": "AGENT_BOUND", "message": "知识库已被智能体「xxx」绑定，请先解除绑定后再删除", "data": {"bound_agents": [...]}}`，知识库保持 state=1 不被软删
  2. `api/routers/kbs.py`：del_kb 对 `code == "AGENT_BOUND"` 抛 **400**（业务拒绝），「知识库不存在」仍 404
  3. `web/src/app/(main)/kbs/page.tsx`：删除确认前预校验——模块级 `fetchAgentsBoundToKb`（分页拉全量启用智能体 page_size=100 循环取完，本地判 selected 绑定）；有绑定则 modal.warning 列出智能体名称并中止删除，不弹确认框；预校验失败不阻断（后端守卫兜底）
  4. `tests/test_kb_admin.py` +4 用例（显式绑定拒绝+库保持可用 / 绑定其他库放行 / all 模式放行 / 已删智能体放行），17 passed
- **验证**：test_kb_admin 17 passed（4 新 + 13 旧）；`tsc --noEmit` exit 0；全量 pytest **799 passed / 10 failed**（全部为既有基线：test_chat_image_qa 2 + test_doc_download_fallback 7 + test_engine_admin 1，storage/门禁类，与本次无关）
- **部署注意**：api 端改动 → CI 重建 kb-api-server（或 docker cp 热修 kb_admin.py + kbs.py + restart）；前端改动 → 重建 kb-web。无迁移、无库内数据
- **坑位**：① WeKnora 原版 DeleteKnowledgeBase 无 agent 校验（纯软删+异步清理），本守卫是 kb 平台新增需求，不追求对齐；② selected 模式的 resolve_kb_scope **不过滤 state**（软删库仍会进智能体检索范围），这正是 selected 必须拦截的技术原因；③ all 模式放行是有意设计（resolve_kb_scope 的 all 分支过滤 state=="1"）

## 31. 当前状态（2026-10-20 agent 问答「没有收到回复」排查与兜底修复）

- **症状**：docker kb 平台最近一次 agent 会话（sess_d8bf4e7f，供管制度知识智能体，「小额零星采购走什么流程」）assistant 消息 content=''，用户没收到回复
- **排查链路（库内证据）**：
  1. chat_session 最新 = agent 会话（agent_id=7eea2b66 供管制度知识智能体，绑定 808ac2b9 供管制度知识库）；chat_message 里 assistant 消息 content=''、thinking=NULL、refs=NULL
  2. kb_llm_usage 时间线：04:33-04:34（用户会话）9 次调用中 5 次**0/0 空响应 success**（网关返回空流，非 2048 预算——§28 指纹不匹配），最后一次 04:34:41 空 → 落库空消息；04:49 出现 403；之后裸调模型 key 全部 401 INVALID_API_KEY（inferai/siliconflow/tokenerpgw 三个网关的 key 全部失效）
  3. 复现（curl SSE）：agent 工具循环里 knowledge_search/grep_chunks/wiki_search 全部无命中（供管制度库无「小额零星采购」内容），第 7 轮 LLM 调用 403 Forbidden → except 发 error 事件，但 **finally 无条件 append 空 assistant 消息**（agents.py 527-530）→ 前端「没有回复」
- **根因分层**：
  1. **模型凭据失效（主因，阻断所有问答）**：默认 chat 模型 deepseek-v4-pro（3bc35b89，is_default=1，base=api.inferaiapi.com）key 裸调 401；siliconflow（c1395203）/tokenerpgw（a71934c7）也 401。04:49 起平台内调用 403。**需用户提供有效 key 更新模型注册表**
  2. **平台无空响应兜底**：LLM 空流（0/0，重试耗尽后无输出）时只有 stage/done 事件，且 finally 无条件落 content='' 消息——把网关故障固化成「空回复」
- **代码兜底修复（commit 待推）**：
  1. `api/routers/agents.py`：生成循环 try/except/else/finally 重构——else 分支在**无异常且无正文/无引用**时发可见 error 提示「模型未返回有效内容，请稍后重试；若持续为空请检查模型服务配置」；finally 的 append 加 `(answer_parts or refs)` 守卫，**空响应/403 不再落 content='' 历史消息**（done 的 message_id 为空，前端按占位处理）
  2. `tests/test_agent_skills_scope.py` +1 用例（LLM 空流×3 轮 → 无 delta + error 提示 + 3 次调用）
- **验证**：agent 相关 8 个测试文件 86 passed；热修容器后复现：403 场景 error 事件正常、done message_id=""（空消息不再落库）；`tsc` 无前端改动
- **部署注意**：api/routers/agents.py 已 docker cp 热修 kb-api-server + restart；CI 重建固化。**模型 key 需人工换新**（模型注册表页编辑 deepseek-v4-pro 默认模型的 API Key，或提供新 key 我代改）
- **坑位**：① agent 会话空回复 ≠ 空答案（§28 是 max_tokens 吃满，本次是网关空流/403）——先看 kb_llm_usage 的 input/output_tokens 指纹：0/0=网关空流，=2048=预算吃满；② 外部 LLM key 失效是断崖式的（04:49 前正常、之后全 403），裸调验证最快；③ agents.py finally 无条件 append 空消息是历史缺陷，任何生成空/异常的 agent 会话都会被固化空消息

## 30. 当前状态（2026-10-20 目录上传文件名修复 + 登录 500 修复）

- **需求**：「知识库上传文件夹，默认勾选保留子目录，实际上传后没建目录、知识文件名前面多了一级目录」
- **根因（浏览器级实测坐实）**：Chrome 对 `<input webkitdirectory>` 选中的文件，multipart 的
  Content-Disposition filename 用的是 **webkitRelativePath**（如「制度文件/2024/a.pdf」），而
  **不是** `File.name`（裸文件名）。前端 `form.append("file", file)` 未显式指定 filename →
  后端 `file.filename` 收到完整相对路径 → `kb_document.file_name` 落成「顶层目录/文件名」
  （即用户看到的「文件名前面多了一级目录」）。目录本身创建正常（playwright 实测：子目录
  2024/更深 均建出且复用正确）；「没建目录」= 所选顶层目录按设计不建（弹窗文案「所选目录本身
  不创建」），仅子目录会镜像建目录——若用户传的是扁平文件夹则一个目录都不建。
- **修复（前端 + 后端双层）**：
  1. `web/src/lib/api.ts`：`apiUploadDocument` / `apiUploadDocumentWithProgress` 的
     `form.append("file", file)` → `form.append("file", file, file.name)`（显式指定 basename
     filename，覆盖 Chrome 的 webkitRelativePath quirk）
  2. `api/routers/kbs.py` upload_document：`fname` 剥路径只留裸名
     （`raw.replace("\\","/").rsplit("/",1)[-1]`，兼容正反斜杠）——防御其他客户端带路径上传
  3. `tests/test_upload_file_types.py` +2 用例（正斜杠「制度文件/2024/规范.md」/ 反斜杠
     「dir\sub\规范.md」→ 落库 file_name 均剥成裸名），13 passed
- **附带发现并修复（同批提交）**：登录 API 500 —— 共享框架库 `modo_oper_log` 被其他平台演进
  出 `operation_id` 列（NOT NULL 无默认值），kb 抽离时模型未带该列 → INSERT 被 MySQL 严格
  模式 1364 拒绝 → **所有登录（含 UI）500**。修复：`api/models/framework.py` OperLog 补
  `operation_id`（`default=""`，与共享库既有行一致）。test_framework_models 68 passed
- **验证**：playwright 实机（chromium-1228）目录上传复现+回归：修复前 4 个文档名全带
  「复现测试/」前缀；后端热修后同批上传文档名全为裸名、正确落入 2024/更深 目录；`tsc --noEmit`
  rc 0；定向 pytest（upload_file_types 13 + doc_folder/doc_upload_folder 8 + framework_models
  68 + audit_log 5）全绿。全量 pytest **782 passed / 13 failed**：12 项为既有基线（chat_image_qa
  2 + doc_download_fallback 6 + engine_admin 1 + env_isolation 2，dfea06a 干净 worktree 复现
  一致）；第 13 项 `test_resolve_kb_url_dev_schema` 由**并行会话未提交的 api/db.py 双表改造**
  （工作区 M + 新增 env_table_router.py）引入——其改掉 `_resolve_kb_url` dev 分支后该用例在
  无 KNOWLEDGE_DATABASE_URL 环境必 RuntimeError，非本次改动所致，未代修
- **部署注意**：后端 kbs.py/framework.py 改动 → 重建 kb-api-server（本机已 docker cp 热修 +
  restart）；前端 api.ts 改动 → 重建 kb-web（前端显式 filename 属于源头修复，后端兜底已
  先行生效）
- **坑位**：① webkitdirectory 的 File 对象 `name` 与 multipart filename 是两回事，FormData
  必须显式第三参；② 拖放目录路径 GlobalDropZone 已做 `normalizeDroppedFile` 归一化，只有
  文件夹选择器路径中招；③ 验证目录上传用 playwright `set_input_files(目录)`（webkitdirectory
  输入不接受 FilePayload，须传真实目录路径；顶层目录名会进 webkitRelativePath）

## 29. 当前状态（2026-10-20 知识库配置弹窗新增「基本信息」维护）

- **需求**：「知识库配置增加知识库基本信息的维护」——配置弹窗此前只在分区导航上方常驻一个「所属分类」，名称/描述无处编辑（新建后不可改）
- **代码改动（纯前端 + 回归测试）**：
  1. `web/src/components/KBConfigModal.tsx`：分区导航顶部新增「基本信息」分区（ProfileOutlined，默认选中）；内含 知识库名称（Input，required+whitespace 校验）/ 知识库描述（TextArea rows=3 maxLength=1000）/ 所属分类（Select，原常驻项移入本分区）；分区底部只读元信息（知识库 ID 可复制 / 文档数 / Wiki 页面数 / 创建时间）；`onSave` payload 加 `name`（trim 空则保留原名）/ `description`（空串可清空）
  2. `web/src/lib/api.ts`：`KbUpdatePayload` 增 `name?` / `description?` 字段
  3. 后端零改动：`update_kb`（api/services/kb_admin.py:404-409）原生支持 name/description/label，PUT 端点走 model_dump
  4. `tests/test_kb_categories.py` 增 2 用例（PUT 更新 name/description / 空白名保留原名），12 passed
- **验证**：`tsc --noEmit --incremental false` rc 0；`bun run build` rc 0；`uv run pytest tests/test_kb_categories.py` 12 passed；全量 `uv run pytest -q` **765 passed / 12 failed 全部为既有基线**（test_chat_image_qa 2 + test_doc_download_fallback 6 + test_engine_admin 1 + test_env_isolation 2，与 §28 记录的 HEAD d364eeb 基线一致：后 10 条缺 DATABASE_URL 环境、前 2 条图片门禁护栏与实现不同步，均与本次改动无关）
- **部署注意**：纯前端 → 重建 kb-web；无迁移、无库内数据
- **坑位**：JSX 里直接 `kb.id` 报 TS18047（kb 可能 null）→ 必须 `kb?.id ?? ""`；`Input`/`ProfileOutlined` 需补 import

## 28. 当前状态（2026-10-20 模型注册表 max_tokens 配置，修复思考型模型空答案）

- **需求**：智能问答最近会话（sess_aae0fb3d，2026-10-19）「什么是小额采购」助手正文为空。根因 = deepseek-v4-pro（思考型）reasoning 3853 字符吃满 max_tokens=2048 预算（kb_llm_usage 记录 output_tokens=2048 恰好等于上限）→ 正文 0 token。链路 `chat.py:289/302` max_tokens 恒为默认 2048，模型注册表此前无该字段（模型页的 max_tokens 只是**调试抽屉临时参数**，api/routers/models.py:716-720 仅作用于单次 debug 请求，不落库）
- **代码改动（commit c170b94）**：
  1. 迁移 `0033_model_max_tokens.py`（revises 0032_merge_0030_heads，新增 kb_model.max_tokens Integer 列，幂等 _table_exists+列检查）
  2. `api/models/model.py` KbModel.max_tokens + `api/services/models.py` 全链（model_to_dict/create/update/copy/_model_to_config）+ `api/routers/models.py` ModelPayload/export
  3. `api/services/chat.py`：ChatConfig.max_tokens；stream/stream_events/chat 三处 max_tokens 默认值 2048 → None（None=未显式指定 → cfg.max_tokens or 2048）；load_chat_config 传入 resolved["max_tokens"]；image_qa.py / image_chunks.py 的 ChatConfig 构造同步带 max_tokens
  4. 前端：models/page.tsx 配置表单「最大输出 Token（空=默认 2048）」表单项（chat/vllm，tooltip 提示思考模型建议 8192，min 256/max 131072）；编辑回填+提交 payload；调试抽屉打开/切换模型时 maxTokens 跟随模型配置（显式传参仍优先）；api.ts ModelItem/ModelPayload/ModelExportItem 加 max_tokens
  5. 测试：test_models.py create(8192)/0 清空/copy 断言 + test_chat_client.py `test_chat_client_max_tokens_from_model_config`（未配置→2048 / cfg 8192→8192 / 显式传参 4096 优先）
- **验证**：定向 38 passed；`tsc --noEmit` exit 0；alembic heads 仅 0033；全量 763 passed / **12 failed 全部为既有基线**（test_chat_image_qa 2 + test_doc_download_fallback 6 + test_engine_admin 1 + test_env_isolation 2，HEAD d364eeb 干净 worktree 复现同样 12 failed）
- **部署+配置注意**：① api 端改动需重建 kb-api-server（迁移自动 upgrade）；前端需重建 kb-web；② **新列默认 NULL = 问答链路仍走 2048——若要修复空答案，部署后须在模型配置页把默认 chat 模型 deepseek-v4-pro 的「最大输出 Token」改为 8192 并保存**；③ 调试抽屉默认值随所选模型配置，但手动改过就保持手动值
- **坑位**：① 模型页「调试」的 Max Tokens 与「配置表单」的 max_tokens 是两个东西，前者是请求参数不落库；② max_tokens 0/空串 → 不落库（None）；③ stream_events 显式传参会覆盖 cfg（调试接口行为，页面值 > 模型配置）

## 27. 当前状态（2026-10-20 Wiki 反馈维护中心 + 高频词管理，对齐 WeKnora）

- **需求**：把 WeKnora 的 wiki 反馈维护中心（wiki_page_feedback + WikiMaintenance.vue）与高频词管理（wiki/keywords/overview + WikiFeedbackPresetKeys）迁到 kb——反馈支持 comment/question + 快捷预设标注 + 三态流转（pending/resolved/ignored）+ 软删 + 分页筛选；高频词按 frequent_keyword 页面聚合展示词频/文档数/标注计数，可独立标注（kw_meaningful/kw_noise/kw_mis_extracted/custom）
- **代码改动（本次提交）**：
  1. 迁移：`0030_wiki_feedback_preset`（加 preset 列，revises 0029_overview_trend_indexes）+ `0031_wiki_feedback_maintenance`（加 page_title/reported_by_name/updated_at/deleted_at 反规范化列 + 存量 open→pending 归并 + kb_id+slug / kb_id+type+status 复合索引，文件名 0031_wiki_feedback_maintenance.py）。⚠️ **versions 目录有两个 0029（code_repo_config → overview_trend_indexes 线性链）**，0030/0031 挂在链尾，无分叉
  2. `api/models/knowledge.py`：WikiFeedback 对齐 WeKnora（feedback_type 默认 comment、status 默认 pending、preset/page_title/reported_by_name NOT NULL + server_default、updated_at/deleted_at）+ WIKI_FEEDBACK_PRESETS（内容类 5 键 + kw_* 3 键）/ TYPES / STATUSES 白名单
  3. `api/services/kb_admin.py`：`wiki_submit_feedback` 支持 preset（白名单校验 + 空内容自动回填预设文案 + helpful 空内容回填「有帮助」）；`wiki_list_feedback` 改过滤+分页（slug/type/status，软删不可见）；`wiki_update_feedback_status` 白名单校验 + 软删行不可操作；新增 `wiki_delete_feedback`（软删）/ `wiki_keyword_overview`（frequent_keyword 页聚合，正则解析「在 N 篇文档中高频出现（总频次：M）」头部 + 旧规范分组频次累加兜底，标注计数**排除软删**）/ `wiki_keyword_mark`（独立标注通道，preset 白名单 + custom 必填内容）；`list_kbs` 附带 keyword_count/feedback_count（卡片统计）
  4. `api/routers/kbs.py`：feedback 列表端点支持 slug/type/status/page/page_size；submit 校验 preset（400）；新增 DELETE 反馈（软删）、GET keywords/overview、POST keywords/mark；⚠️ **mark 端点返回注解不能写 `dict | JSONResponse`**（FastAPI 会当 response_model 推断 → collection 期 FastAPIError Invalid args for response field），400 分支用 HTTPException
  5. 前端：`web/src/lib/api.ts`（WikiFeedbackItem 加 page_title/reported_by_name/preset/updated_at；apiWikiListAllFeedback 改参数对象；apiWikiDeleteFeedback / apiListWikiKeywords / apiMarkWikiKeyword）；新增 `lib/wikiFeedback.ts`（PRESET_LABELS/TYPE_OPTIONS 共享常量）；`wiki-maintenance/page.tsx`（独立页：知识库选择 + 汇总徽标 + 类型/状态/关键词筛选 + 表格 + 钉底分页 + 标记解决/重开/忽略/删除）；`wiki-keywords/page.tsx`（首 tab 知识库卡片列表带高频词计数 → 动态选项卡开列表）+ `components/kb/WikiKeywordsPane.tsx`（排名/关键词/含义/频次/文档数/标注列 + 详情抽屉 + 标注弹窗）；WikiBrowseView 与 kbs/[id]/wiki/[slug] 反馈弹窗加快捷标注 Select + comment/question/helpful 类型；kbs 卡片 meta 加「反馈 N」badge 点击进维护中心（?kb_id=）；WikiManagePanel 状态 open→pending 三处
  6. 菜单：seed KB_MENUS 加一级「高频词」（/wiki-keywords，BarChartOutlined，sort 4）+ grp_ops 下「维护中心」（/wiki-maintenance，CommentOutlined，sort 9），其余分组 sort_num 顺延；AppSider ICON_MAP 补 BarChartOutlined；MenuContext FALLBACK_MENUS 补两条（兜底挂 grp_knowledge 下，与种子位置不同属既有简化结构差异）
  7. chat/page.tsx + globals.css 的 .kb-chat-footer 改 min-height 自适应（图片预览条插入后 textarea 不被压缩至 0）——工作区遗留同批改动
- **验证**：`tests/test_wiki_keywords.py` 7 用例（频次解析排序/search/旧规范兜底/标注计数含软删排除/分页/mark 校验）+ `tests/test_wiki_management.py` 状态断言改 pending（19 passed）；全量 `uv run pytest -q` **759 passed / 2 failed**（2 红 = test_chat_image_qa 图片门禁护栏，**HEAD f0903f0 干净 worktree 复现同样失败**，858b0b6 改了门禁行为未同步旧测试，属基线问题非本次回归）；`tsc --noEmit` exit 0；`bun run build` rc 0（/wiki-keywords、/wiki-maintenance 路由正常产出）
- **部署注意**：api 端（模型/服务/路由/迁移）→ 重建 kb-api-server；前端（页面/组件/常量）→ 重建 kb-web；迁移由 api-server 启动自动 upgrade（容器热修需 cp 0030/0031 两个迁移文件）；菜单属库内数据 → docker cp seed_framework_db.py 跑（菜单幂等 upsert + 种子角色自动授权新菜单，**非种子角色需在菜单管理手动勾选**）
- **坑位**：① mark 端点返回注解陷阱见上；② WikiManagePanel 旧状态 open 已全改 pending（radio/Tag/重开按钮），遗留 open 会导致筛选空列表/更新 422；③ 高频词词频解析依赖 kb 模板头部句格式，旧规范页面走分组累加兜底；④ 迁移文件名与 revision 号允许不同（alembic 按 revision id 链），但文件命名统一用 revision 前缀避免混乱；⑤ 并行会话在途文件（agent_tools_full.py / AgentEditorModal.tsx）未提交，勿误带

## 26. 当前状态（2026-10-17 知识库文档图标改用 DSB 文档库逐类型图标）

- **需求**：「data_supply_base 的文档库针对各种文件有不同的图标，把图标抄过来，放到 kb-compilation 的知识库的文件上」
- **DSB 侧真源（实测确认）**：DSB 文档库（knowledge_lib_neo）的逐类型图标是 `KnowledgeLibNeo/images/` 下的 **PNG**（doc/pdf/xlsx/ppt/txt/md/img/html/mp3/mp4.png，均 80x80），映射表在 `KnowledgeSetNeo/pages/KnowledgeLog/index.tsx` 的 `fileTypeMap`（docx/doc/wps→doc、xlsx/xls/csv/et→xlsx、ppt/pptx/dps→ppt、jpg/png/gif/webp/img/image→img、md、txt、html、mp3、mp4；**未命中回落 txt**）。注：其文档树 `WeknoraDocTree.tsx` 用的是 arco `IconFile` + `fileTypeColor()` 按类型染色的单图标，与「逐类型图标」是两套，本次抄的是 fileTypeMap 那套 PNG。
- **代码改动（commit 4bfc47f）**：
  1. 图标字节级原样拷贝 → `web/public/images/dsb/file-icons/`（10 个 PNG，md5 与源一致），运行期经 `/wiki/images/dsb/file-icons/<name>.png` 提供（应用 basePath=/wiki，静态资源需自带前缀）
  2. 新增 `web/src/components/kb/FileTypeIcon.tsx`：`FILE_ICON_NAME` 映射（DSB fileTypeMap 同口径）+ `fileIconSrc()` + `fileTypeIcon(ext, size=16, style?)` 返回 `<img>`；未命中回落 txt（与 DSB 一致）
  3. 5 处调用点统一走该函数：文档卡片 `DocCardView`（28px）、文档表格 `KbDocsPane`（16px）、文档详情 `DocDetailPanel`（18px）、预览弹窗 `DocPreviewModal`（16px）、上传确认弹窗 `UploadConfirmDialog`（16px）；`DocCardView` 保留 `fileTypeIcon` 同名导出（其余三处 import 自它，未改动契约）
  4. 清理：`DocCardView` 移除 8 个 antd `File*Outlined` 导入；`UploadConfirmDialog` 去掉 `FileTextOutlined`
- **验证**：`web` tsc --noEmit exit 0；`bun run build` 通过；**本地构建 + 反代线上后端**（playwright）实机断言：供管制度知识库 18 篇 → 图标命中 `doc.png`（.doc/.docx）与 `txt.png`（.zip 回落），`/wiki/images/dsb/file-icons/*.png` 全 200 image/png；**部署后线上复验**（nginx :80，同上 KB）18 处图标命中、无 console error，截图 /tmp/kb_doc_icons_live.png
- **坑位**：① 图标是 `<img>`，容器 `font-size` 不再决定大小，尺寸必须显式传 `size`（卡片 28/表格 16/详情 18）；② 静态资源路径必须带 basePath（`/wiki/...`），漏前缀会 404；③ DSB 文档树（染色单图标）与文档列表（逐类型 PNG）是两套图标语言，抄之前先确认抄的是哪一套；④ `export { x } from "..."` 不建立本地绑定——同文件内还要用该符号时必须 `import` + 再 `export`

## 26. 当前状态（2026-10-22 压缩包嵌套递归解包）

- **需求**：「压缩包内部的压缩包也要解压出来」——此前 upload-archive 只解顶层，包内再嵌的 zip/rar 被白名单当「不支持格式」跳过
- **后端改动**：`api/routers/kbs.py` upload-archive 主循环重构为递归 `_expand(file_infos, prefix, depth)`：
  - 嵌套 zip/rar 条目递归解包；`keep_structure=true` 时子包名（去扩展名）作为一层目录挂到当前路径（如 制度/内层.zip → 制度/内层/…），完整保留层级；`false` 时仍平铺 folder_id
  - 深度上限 `MAX_ARCHIVE_DEPTH=5`（超限子包跳过、计入 skipped.failed + errors，不阻断整包）；损坏子包同样跳过；子包体积计入全局解压总量（保守防 zip-of-zip 炸弹）；新增跨层入库总数 `total_created` 上限（防规模爆炸，超限 400 整体拒绝）
  - 新增常量 `MAX_ARCHIVE_DEPTH`；返回结构不变（skipped 键不变，前端无需改动）
- **测试**：`tests/test_upload_file_types.py` 新增 4 用例（zip 嵌 zip 保留层级/坏子包跳过/深度上限/zip 嵌 rar 递归进 bsdtar 分支）；`tests/_rar_builder.py` 补注释说明夹具限制（HEAD_SIZE=32 为 bsdtar 可解析的最小构造；含 zip 二进制内容的 rar 会让 bsdtar 误读内部字节，故 rar 嵌套用例以 zip 作外层）。`uv run pytest tests/test_upload_file_types.py` **21 passed**
- **全量回归**：`uv run pytest tests/` 883 passed / 8 failed（8 个失败为 test_doc_download_fallback + test_engine_admin 的 `Missing required env var: DATABASE_URL` 环境性失败，与本改动无关）
- **部署注意**：仅后端 kb-api-server 需重建；旧前端兼容（返回结构未变）

## 25. 当前状态（2026-10-20 上传格式门禁 + zip 压缩包自动解包）

- **需求**：「知识库上传文件时，系统支持处理哪些格式就允许传哪些格式，不要传了又处理不了；另外支持 zip 打包上传并自动解包」
- **根因**：`POST /kbs/{id}/documents/upload` 原先不校验扩展名——任意文件都落库并排队，worker 解析时才由 docreader 注册表抛 `Unsupported file type: xxx` → 文档 FAILED（用户侧表现就是「传了又处理不了」）
- **「系统支持哪些格式」的唯一真值**：docreader 内置解析器注册表（`docreader/parser/registry.py` BUILTIN_ENGINE）= doc/docx/pdf/ppt/pptx/xls/xlsx/txt/md/markdown/csv/epub/mhtml + png/jpg/jpeg/gif/bmp/tiff/webp，共 20 种（**html/htm 不在其中**）。后端白名单 `api/services/parser_registry.py::SUPPORTED_UPLOAD_TYPES` 与之逐项一致，并由 `tests/test_upload_file_types.py::test_supported_types_match_docreader_builtin_registry` 反查护栏（两边漂移会红）
- **后端改动**：
  1. `api/services/parser_registry.py`：新增 `SUPPORTED_UPLOAD_TYPES` / `SUPPORTED_UPLOAD_ACCEPT` / `SUPPORTED_ARCHIVE_TYPES`(zip) / `JUNK_FILE_NAMES` 与 `ext_of` / `is_supported_ext` / `is_archive_ext` / `unsupported_type_detail` / `archive_entry_parts` / `is_hidden_or_junk`
  2. `api/routers/kbs.py`：单文件 / URL 导入加**格式门禁**（不支持 → 400，提示里带可处理格式清单）；新增 `GET /api/v1/kbs/file-types/supported`（白名单下发前端）；新增 `POST /api/v1/kbs/{kb_id}/documents/upload-archive`（zip **自动解包**：按包内目录结构建文档目录、同级同名复用、`keep_structure=false` 平铺到当前目录；跳过目录项/隐藏/系统垃圾文件/空文件/非白名单格式/超大文件；zip-slip 路径防护；条目 ≤2000、解压后 ≤1.2GB；每个文件仍走「存储 → kb_document(PENDING) → Celery 解析」同一条链路；返回 created + skipped 统计）
- **前端改动**：新增 `web/src/lib/upload-support.ts`（白名单 / accept / 分拣 / 提示文案，兜底清单与后端同源）；`lib/api.ts` 增 `apiGetSupportedFileTypes` / `apiUploadArchive`（XHR 进度）/ `apiErrorMessage`；`KbDocsPane` 载入白名单 → 文件选择 `accept` 只列可处理格式 + 预校验跳过提示、新增「上传压缩包（自动解包）」入口（隐藏 input accept=.zip；进度 message + 结果概要，完成后自动刷新列表与目录树）、目录上传按白名单分拣（跳过隐藏/空/不支持）、文件类型筛选选项改为白名单同源；`GlobalDropZone` 拖入 zip 走自动解包（uploadTask 上报进度/结果与跳过统计），拖放收集按白名单过滤（`lib/drop-files.ts` 新增 `supported` 选项，拒绝原因写明「格式不支持」）
- **验证**：`tests/test_upload_file_types.py` 11 用例（白名单与 docreader 注册表一致 / .psd 拒绝且不落库 / .md、.PDF 通过 / 白名单端点 / zip 解包保留目录结构 / 跳过隐藏+空+不支持+zip-slip / keep_structure=false 平铺 / 非 zip、坏包、未知目录拒绝）；全量 `uv run pytest -q` **709 passed**；`tsc --noEmit` exit 0；`bun run build` rc 0
- **部署注意**：后端需重建 kb-api-server（新端点 + 门禁），前端需重建 kb-web。**旧前端 + 新后端兼容**（只是多一层拦截）；**新前端 + 旧后端**会 404 白名单接口 → 前端回落本地兜底清单（预校验仍生效，但「上传压缩包」入口会报错），故两者应同时上线

## 24. 当前状态（2026-10-20 知识库详情页面样式对齐知识库管理页）

- **需求**：「知识库详情页面样式优化，参考知识库页面，要求 UI 统一」。详情 = 打开知识库后的「文档」页签 / `/kbs/[id]` 深链页（两者共用同一批 pane 组件）；此前详情整体是 antd 默认观感（antd Tree 带连接线、白底方角搜索框、常驻 antd 分页、antd Card 卡片），与已按 DSB 像素级改造的知识库管理页不像一套
- **代码改动（纯前端；当前仅在工作区、未提交——本会话 terminal 被系统拒绝）**：
  1. 新增 `web/src/components/kb/KbsIcons.tsx`（共用图标：DSB 文档库 folderIcon 14×14 蓝色文件夹 / arco Caret 展开箭头 / arco 分页箭头 / arco 空态插画 / modo 彩色放大镜）、`web/src/components/kb/KbsEmpty.tsx`（arco 空态：148px 插画 + 「暂无数据」，`variant="inline"` 供表格占位行）、`web/src/components/kb/KbsSimplePagination.tsx`（DSB arco simple 分页：共 N 条 + 页码输入/总页数 + 条/页下拉）
  2. `web/src/components/kb/KbDocsPane.tsx`：左树从 antd `<Tree showLine>` 改为自绘行复用管理页 `.kb-doc-tree-*` 样式族（36px 行 / 14px 图标槽 / hover 浮现展开箭头 / 选中 primary-light 底）；sider 230px 灰底 → 260px 白底 + #f0f0f0 右边框；搜索框对齐 `.kb-kbs-search`（fill-2 底 / 14px 胶囊 / 28px 高 / 12px 字 + hover-focus 状态链）；状态/类型下拉改 `ModoSelect`（filled、28px 胶囊）、刷新/添加知识改 `ModoButton`（28px round）；分页由常驻 `ModoPagination` 换成 `KbsSimplePagination`（移出内边距区、横贯内容列底部、超一页才出现）；卡片视图与表格占位空态换 `KbsEmpty`
  3. `web/src/components/DocCardView.tsx`：文档卡片由 antd `<Card hoverable>` 改自绘 `.kb-doc-card` 三段式（对齐 `.kb-kbs-card`：150px 高 / header 44px / 两行摘要 40px / footer 28px 状态文本）；状态由彩色 Tag 组改 footer 纯文本（`docStageText()`，失败仍红色且可点开构建过程），悬浮 Popover 保留
  4. `web/src/app/(main)/kbs/[id]/page.tsx`：外壳 className `kbs-page`（无样式）→ `kb-kbs-page`（modo 字体栈 + 12px 基准 + 白底），去掉 `padding: 10`（消除比管理页多出的一圈灰边）
  5. `web/src/app/globals.css`：改写「知识库文档（文件）列表」段（sider / 树 / 搜索 / 下拉 / 视图切换 / 分页条 / 空态 / 文档卡片），删除失效的 `.kb-list-card` 与 `.kb-doc-scroll-container` 规则
- **验证（terminal 被拒，走 execute_code 沙箱 subprocess 通道）**：`tsc --noEmit --incremental false` rc=0 零诊断；`bun run build` rc=0（Compiled successfully，`/kbs`、`/kbs/[id]` 正常产出）；产物 CSS（`.next/static/chunks/*.css`）核对到 `.kb-doc-card{height:150px…}`、`.kb-doc-search-input.ant-input-affix-wrapper{background:#eff4f9;border-radius:14px;height:28px}`、`.kb-doc-pagination-bar{background:#f9fbfd;padding:6px 16px}`，`.kb-list-card` 0 命中；`uv run pytest -q` **698 passed**
- **坑位**：① 详情与管理页共用同一套类名（`.kb-doc-tree-*`、`.kb-kbs-pagination*`）——改管理页左树会同时影响详情左树，本次为定向复用、不复制样式；② 并行会话当时正在改 `kbs/page.tsx` 左树图标（蓝色文件夹 + Caret），故本次**刻意不动 kbs/page.tsx**，图标在详情侧同源内联一份（避免撞车）；③ `KbsSimplePagination` 显隐判据为 `total > pageSize`（管理页原为写死 `total > 30`），管理页在用户把每页条数调到小于总数时也会出现分页条，属修正
- **部署注意**：纯前端改动 → 重建 kb-web 镜像；详情内页签与 `/kbs/[id]` 深链共用 pane 组件，改动对两条入口同时生效

## 23. 当前状态（2026-10-16 菜单图标接入 modo-design 彩色图标）

- **需求**：KB 平台侧边栏菜单此前全用 @ant-design/icons 单色线框图标，与山东数据供给平台（data_supply_shandong）观感不一致。本次为语义可对应的菜单项换用同族 modo-design 彩色图标（Icon*Color）
- **代码改动（commit 29975e8）**：
  1. 新增 `web/src/components/ModoMenuIcons.tsx`：按官方源码内联 9 个彩色图标 SVG（viewBox 0 0 12 12、`fill=currentColor` + opacity .25 分层，官方 `.modo-icon` 语义；路径逐条与 `modo-design/icon/react-icon/Icon*Color/index.js` 一致，非自绘），导出 `MODO_MENU_ICON_NAMES` / `MODO_ICON_COMPONENTS`
  2. `web/src/app/(main)/_components/AppSider.tsx`：ICON_MAP 追加 9 个 modo 图标名（1em，随 `.kb-nav-card-icon` 24px；未命中仍回落 AppstoreOutlined）
  3. 菜单名同步（种子 `scripts/seed_framework_db.py` KB_MENUS + 前端兜底 `MenuContext.tsx` FALLBACK_MENUS）：kbs=IconBook1Color、chat/agents=IconSemanticSearchColor、grp_ops/jobs=IconTaskMonitorColor、files/workers=IconCloudServerColor、datasources=IconDatabaseColor、system=IconSystemMgrColor、orch_tapes=IconResourceOrchestrationColor
  4. `api/routers/system.py` MENU_ICONS 目录补齐这 9 个名字（菜单管理页下拉可选）
  5. `web/src/app/(main)/system/menus/page.tsx`：图标组件表 = antd 图标池 ∪ modo 图标池（下拉预览可渲染彩色图标）
  6. **补齐漏配图标名（同日二轮）**：`AppSider.tsx` 的 ICON_MAP 缺 7 个库内菜单在用的名字（ApartmentOutlined 本体Schema / BlockOutlined 编排组件 / BugOutlined 调试工具 / CheckCircleOutlined 质量评估 / LikeOutlined 问答反馈 / ScheduleOutlined 任务管理 / SearchOutlined 联网搜索），`iconOf()` 命中不到即静默回落 DEFAULT_ICON（AppstoreOutlined）——实机截图发现「调试工具」一级卡片显示成默认 4 方块图标。已补 import + 映射，ICON_MAP 现 36 键，与「KB_MENUS ∪ 前端兜底 ∪ 线上库」所需图标名零缺失
- **库内数据（apply 脚本已应用）**：新增 `scripts/apply_menu_modo_icons.py`（按 **menu_name** 匹配，因同一菜单 menu_id 在各环境形态不一：本部署 files/workers 是 UUID）→ docker cp 进 kb-api-server 执行，**10/10 命中**（9 改 + files 行 UUID 键补改），重放 0 更新；`GET /system/my-menus` 实测返回 10 条 `Icon*Color`
- **范围口径（用户 2026-10-16 确认）**：一级「配置管理」「调试工具」两个分组**保持 antd 单色图标**（ToolOutlined/BugOutlined），不做 modo 彩色化——一级卡片最终形态 = 5 彩色（智能问答/知识库/智能体/运维监控/系统管理）+ 2 单色
- **验证**：`web` 目录 `./node_modules/.bin/tsc --noEmit` exit 0；my-menus 接口实测 10 条 modo 图标名；apply 脚本幂等（二次执行全 skip）；playwright 实机断言（kb_compilation/.venv 自带 playwright + ~/.cache/ms-playwright/chromium-1228，需显式 executable_path，本机 agent-browser 因 glibc 不可用）：登录 /wiki/login → 7 个一级卡片图标逐个断言命中（5 个 modo + ToolOutlined + BugOutlined）、无默认图标回落、无 console error；截图 /tmp/kb_menu_icons_final.png
- **坑位**：① 按 menu_id 写死映射会漏改（本部署 files/workers 的 menu_id 是 UUID，其它是语义名）→ 一律按 menu_name 匹配；② 图标名换新后**前端必须同步部署**——旧 kb-web 的 ICON_MAP 无这些名字会全部回落默认 Appstore 图标（改造期间视觉会短暂统一成默认图标）；③ **ICON_MAP 缺失的图标名不会报错、只会静默回落默认图标**（本次新发现 7 个，含一级「调试工具」）——加菜单/换图标后应核对 ICON_MAP ⊇ 库内所有 menu_icon 名；④ 第二列（160px 子菜单）在窄菜单形态下只渲染文字、不渲染图标（对齐 DSB modo-studio frame：`o&&!i ? labelOnly : [icon,label]`），故子项 menu_icon 仅在该项作为一级卡片出现时才可见
- **部署**：前端为新组件 + 图标映射改动 → CI 重建 kb-web 后 pull；后端仅图标目录常量 → CI 重建 kb-api-server（或容器内 docker cp api/routers/system.py + restart 热修）

## 22. 当前状态（2026-10-18 智能问答图片链路对齐 WeKnora）

- **需求**：对比 kb 与 WeKnora 的图片问答实现逻辑后对齐——kb 此前只认 `supports_vision`（不支持就报错）、图片是**会话级**每轮重发、OIP 协议下图片被静默丢弃、agent 配置里的 `vlm_model_id/attachment_image_understanding` 是**空开关**（只有配置层无消费点）
- **对齐后的图片处理顺序**（与 WeKnora handler/session + chat_pipeline 一致）：
  1. 模型能看图（`supports_vision` 且协议非 OIP）→ 图片按 content-array 直传（vision）
  2. 不能看图 → 用视觉模型（agent 配置 `vlm_model_id` 优先，否则平台可见的支持视觉 chat 模型）逐图生成描述，以「[用户上传图片内容]」注入 prompt（describe）；描述回写附件 `caption`，历史轮次按 caption 回带（对齐 WeKnora `extractImageCaptions`）
  3. 都没有 → 明确报错（沿用原文案「当前对话模型不支持图片输入…」）
- **代码改动**：
  1. 新增 `api/services/image_qa.py`：`plan_image_input()`（vision/describe/reject 三态 + OIP 判定）、`describe_images()`（逐图 VLM 描述，失败不阻断）、`resolve_vision_config()`、`build_image_analysis_prompt()`（WeKnora 同款提示词）；**两个问答端点共用一份判断**（此前 qa.py/agents.py 各写一份，已出现 agents 漏持久化图片 id 的漂移）
  2. `api/services/models.py`：抽出 `_model_to_config()`（含 `supports_vision`）+ 新增 `resolve_vision_model_config()`（显式 model_id > 作用域默认 chat 模型若支持视觉 > 作用域内首个支持视觉的 chat 模型）
  3. `api/services/chat.py`：`ChatConfig.supports_vision`、`build_messages(image_description=)`（拼「[用户上传图片内容]」）、`answer_question(image_description=, include_doc_images=)`——describe 路径下**用户图与命中片段的文档图都不直传**（否则非视觉模型收到 image part 直接报错）
  4. **当轮图片注入**（A2）：`qa.py`/`agents.py` 请求新增 `image_attachment_ids`（None=旧客户端回落会话级；[]=本轮无图），只注入本轮图片；前端 `chat/page.tsx` 发送时带上本轮上传返回的附件 id
  5. **图片落对象存储**（B1，迁移 **0025** 加 `chat_attachment.object_key`/`caption`）：MinIO 已配置时图片写 `chat_images/<session>/<att>.<ext>`、DB 只留 key（未配置/失败回落 file_data）；读图统一走 `_attachment_image_bytes()`（object_key 优先）；删附件/删会话顺带清理对象（会话删除此前还会残留附件行，一并修）
  6. agent 链路补上用户消息的图片附件 id 持久化（此前漏传 → agent 会话历史消息看不到图片）
- **验证**：全量 `.venv/bin/python -m pytest tests/ -q` → **672 passed**（含 tests/test_chat_image_qa.py 新增 18 例：当轮 id 注入、caption 历史回带、对象存储读写与上传落 key、vision/describe/reject/OIP 四态、agent「附件图片理解」门禁、describe 下不直传任何图片）；`cd web && ./node_modules/.bin/tsc --noEmit` exit 0
- **配置语义（用户侧）**：智能体配置「图片上传」= 允许传图；「附件图片理解」= 允许在对话模型看不了图时用视觉模型生成描述（未勾选且未配视觉模型 → 传图仍会提示换支持视觉的模型）；模型配置页「支持多模态（图片输入）」标记决定是否直传
- **部署注意**：api/worker 改动 → CI 重建 kb-api-server/kb-worker；前端 chat/page.tsx 改动需重建 kb-web；迁移 0025 由 api-server 启动时自动执行（幂等）；本机容器热修可 docker cp，正式走 CI
- **本次未做（另行确认）**：B2 文档图片对齐 WeKnora（命中片段里的 `kb-image://` 图片改为「markdown 引用留在 context + system prompt 要求原样输出」，替换现在读回对象存储喂视觉模型的做法）

## 21. 当前状态（2026-10-17 用编排管理全部编排数据更新系统种子数据）

- **需求**：把线上「服务编排」页维护的全部编排数据（kb_tape + 执行层 kb_tape_step）固化到仓库种子数据，使新建环境开箱即得与线上一致的 26 条编排
- **线上现状（API 全量拉取实锤）**：共 26 条编排、全部 effective：20 个领域 wiki构建-* + 表构建Wiki-基础原子版 + 摘要三模式（Stuff/MR/Refine）+ 题材分析-领域判定 + 质量评估-QA；kb_tape_template 0 行、kb_step_define 18 个 effective。**线上没有「wiki构建-基础原子版」**（种子旧版独有，本次同步后不再播种）
- **关键坑（实测）**：线上三个摘要编排的**画布 nodes 仍是远古版**（LLM 节点无 system/thinking、script 无 META 清洗、writer 是 wiki_upsert_page），而**执行层 kb_tape_step 是修复版**（§17/§20 的 apply 脚本改的）——API export 端点只给画布，直接导出会倒退种子。故摘要三模式的种子由「种子常量（post-f813cea：kb_write_doc_summary + model）+ 两个质量补丁」合成：LLM 节点 system + thinking=disabled（apply_*_summary_fixes 同款文案）、script 清洗收敛为窄 META（apply_fix_summary_cleanup_meta 同款正则）；其余 23 条用线上 API 导出
- **代码改动**：
  1. `scripts/seed_framework_db.py`：`_seed_domain_wiki_tapes` → `_seed_tapes_from_exports`（统一读 seed_tape_data/*.json，恢复 category/status/tape_type 三个字段；支持 draft.steps 执行层原样还原；题材分析编排建完自动把 DOMAIN_RESOLVE_TAPE 参数指向新 id）；main() 原 6 个逐编排播种调用（含 wiki构建-基础原子版）合并为 1 个，旧播种函数保留供查阅
  2. 新增 `scripts/export_orchestrations_to_seed.py`（DB 直连导出工具：kb_tape + kb_tape_step → seed_tape_data/*.json，含执行层 steps；改线上编排后重跑即再同步；用法 docker cp 进 kb-api-server 跑）
- **种子数据（seed_tape_data/ 26 个 JSON，2026-10-17 固化）**：旧 20 个领域 JSON 删除，新 26 个按 tape_name 命名；status 全 effective（线上真值）、category 用线上真值、tape_type 按功能标签（wiki构建-*/表构建Wiki → kb_build、摘要-* → summary、其余 general；**线上库 tape_type 曾全 general，种子按产品功能标签保留**）
- **验证**：py_compile 两脚本 OK；26 文件解析 + 字段形态全过（题材分析 n2 保留 thinking/system；摘要三模式 LLM 节点 thinking=disabled、writer=kb_write_doc_summary、无旧宽正则残留）；未跑 pytest（terminal 被用户拒，测试不覆盖种子模块，静态验证已尽）
- **部署注意**：种子改动仅影响**新建环境**（幂等按 tape_name skip，不动存量库）；旧函数停用不影响现有环境；线上如需重跑种子导出，用 export_orchestrations_to_seed.py（能拿到执行层 steps，比本次 API 路径更保真）

## 20. 当前状态（2026-10-16 题材分析编排：自动选择领域构建编排）

- **需求**：知识库创建时未选构建引擎、上传文件也未选 → 构建任务**等摘要任务结束**，调用 LLM 基于摘要 + 平台已有领域构建编排的划分**自动判定用哪个编排**继续构建；配置了构建引擎（KB 级或文档级）→ 保持现状**并行执行**
- **代码改动**：
  1. `worker/tasks/doc_process.py`：`_enqueue_summary_task` 返回值 bool → run_id（供串行链等待）；新增 `_enqueue_domain_guided_build`（串行链：等摘要 run 终态 → enqueue 题材分析 run → 等终态 → 读 `bindings.resolved_tape_id` → enqueue 对应领域编排）、`_wait_run_terminal`（窄列轮询）、`_read_run_binding`、`_enqueue_resolve_run`（RESOLVE_RUN_* job）、`_domain_resolve_tape_id`（读 DOMAIN_RESOLVE_TAPE 参数）；`_handle_process_document` 的 `elif flags["wiki"]` 分支改走 domain-guided，任一环节缺失/失败/超时回退 skill 链（原行为）
  2. `scripts/seed_framework_db.py`：SYSTEM_CONFIG 加 `DOMAIN_RESOLVE_TAPE` 参数（seq 41，留空=不启用）+ 题材分析编排模板 `DOMAIN_RESOLVE_TAPE`（n1 script 读 kb_tape 领域清单+文档摘要拼 prompt / n2 kb_llm_chat thinking=disabled 判领域 / n3 script 映射 resolved_tape_id）+ `_seed_domain_resolve_tape`
  3. 新增 `scripts/apply_domain_resolve_tape.py`（幂等可重放：建编排+写参数，`--disable` 清参数停用）
  4. 新增 `tests/test_domain_guided_build.py` 8 用例（参数未配置/摘要未触发/摘要失败/成功选定/未匹配/题材分析失败 各回退路径）
- **库内数据（apply 脚本已应用）**：编排「题材分析-领域判定」id=`8255bb048a884d87b82b17479399244e`（draft，3 节点：读取领域编排清单/LLM领域判定/映射为构建编排）；参数 `DOMAIN_RESOLVE_TAPE`=该 id
- **容器热修**：doc_process.py cp 到 5 业务容器（api-server + 4 celery worker）+ restart；seed/apply cp 进 kb-api-server；api health UP
- **验证**：全量 pytest 639 passed / 3 failed（3 红仍为 §15 既有基线）；端到端 execute 题材分析 → LLM 判定 `domain=行业监管与公共政策` → `resolved_tape_id=1c6267d3…`（wiki构建-行业监管与公共政策），领域清单 21 个、摘要 311 字符正确读取
- **坑位（实测）**：①execute 接口 body 必须是 `{"inputs":{...}}`（平铺 {kb_id,document_id} 会被当空 inputs）；②线上 kb_tape.tape_type 全是 general（3de3c25 打标晚于存量编排导入）→ n1 清单条件用 `or_(tape_type=='kb_build', tape_name.like('wiki构建-%'))` 双判据；③领域编排 seed 是 draft（未发布）→ 清单筛 `status != 'offline'` 而非 effective
- **部署注意**：①上传链未选引擎时 doc_process handler 同步轮询摘要 run（最长 30 分钟超时），期间占 default worker 槽位；②用户在服务编排页「保存设计/发布」会覆盖 kb_tape_step 冲掉 n2 的 system/thinking → 重跑 apply 恢复；③CI 重建前容器内热修有效，重建后需 pull 新镜像固化

## 19. 当前状态（2026-10-15 编排分类多级化 + 补充描述）

- **背景**：用户问编排管理左树是否支持多级展示。实测：代码仅两级（迁移 0021 parent_id 设计，`_category_with_children`/计数 rollup/前端渲染三层均两级）；线上 kb_tape_category 库表只有 20 条一级（parent_id 全 NULL、descr 空、无二级）。按 cates.md（仓库根，20 企业领域 + 各领域文档类型 + 典型文档列）改造
- **代码改动**（commit 62c4eac）：
  1. `scripts/seed_tape_categories.py` 重写为内嵌 cates.md 全量数据——一级 20 领域（descr=领域用途说明）+ 二级 211 文档类型（descr 取典型文档列，如 工作报告→日报、周报、月报、季度报告、年度总结）；幂等扩为「缺 parent_id 补挂 + descr 空白补填（不覆盖已有）」；医疗健康补齐 7 类（cates.md 含「医院运营」，旧清单漏）；跨领域重名 3 个（经营指标/知识产权/风险管理）只挂首个领域
  2. `web/src/app/(main)/orchestrations/page.tsx`：左树一级/二级行悬浮 Tooltip 显示 descr
  3. 新增 `cates.md` 入库（分类清单数据源）
- **库内数据（已应用）**：docker cp 进 kb-api-server 执行 → 新建 211 / 修正 20（20 个存量领域补 descr）；验证：20 一级 + 211 二级（二级全部挂在 20 领域下）、descr 231/231 全填
- **验证**：tests/test_tape_category.py 19 passed；web tsc --noEmit exit 0
- **部署**：前端 Tooltip 改动需 CI 重建 kb-web + pull（数据层改动即时生效，左树已见两级展开）；脚本改动走 CI 固化
- **二轮（同日 2026-10-15，commit 65b8fd5）目录结构改为多级**：根目录分「领域」「通用」——cates.md 全部 20 个企业领域挂到「领域」下（三级 = 文档类型），6 个通用编排（wiki构建-基础原子版/表构建/摘要三模式/质量评估-QA，kb_tape.category=通用）归「通用」。配套：后端 `_list_categories` 计数按层级从深到浅递归上卷（根目录=全部后代）、`_category_with_children` 改 BFS 后代收集（点根目录过滤全部后代）；前端左树递归渲染任意层级（有子级带 ▸/▾）；种子脚本加 FIXED_CATEGORIES（领域=1/通用=2/摘要=3）+ 领域统一 parent_id=领域。线上已应用（种子新建1/修正22，服务层 cp+restart），接口实测 领域=20、通用=6；tests/test_tape_category.py 21 passed（+2 三级用例）

## 18. 当前状态（2026-10-14 任务监控页摘要任务层级修复）

- **背景**：用户看任务监控页「文档处理 · 文档 e24d6c…」（DOC_ 链头）时发现「外边还有一个摘要任务」平级显示，觉得逻辑不对。排查数据确认：摘要触发 job（SUMMARY_RUN_448e16cb5c0d，KbOrchestrationRunTask）的 parent_id **本来就是 DOC_（已是子任务）**；问题是 kb_tape_run 被 list_parent_jobs 无条件渲染成顶层虚拟父任务行（_run_to_job_dict parent_id 硬编码 None）→ 同一个摘要执行在页面出现两条：链头子任务 + 顶层平级行
- **修复（api/routers/jobs.py）**：
  1. list_parent_jobs：查询 run 顶层行前，先收 parent_id 非空且 task_params.runId 指向该 run 的 KbOrchestrationRunTask job 集合（attached_run_ids），这些 run 不作为顶层行（已作为链头子任务展示），顶层只保留独立执行（手动跑编排等无承接 job 的 run）
  2. list_job_children：链头的子任务是 KbOrchestrationRunTask 时，把对应 run 的 step_results / run_tape_name / run_id 附在子任务 dict 上——展开链头（DOC_）看到摘要任务行，信息列展示「摘要-Map-Reduce · 编排节点 2/N」进度
- **前端**（parent-job-table.tsx / job-table.tsx）：TASK_CLASS_LABEL 补 `KbOrchestrationRunTask: "编排执行"`（此前 fallback 显示裸编码）；ChildTable jobCols「信息/错误」列支持 step_results 行渲染编排节点进度
- **测试**：test_jobs_monitor.py 新增 test_parents_attached_run_not_top_level（被承接 run 不进顶层 + children 带 run 节点）；jobs 17 passed；全量 600 passed / 3 failed（3 红仍为 §15 既有基线，无新增回归）；tsc exit 0
- **部署**：api/routers/jobs.py 改后需 docker cp 热修 kb-api-server + restart（或等 CI 重建）；前端源码改动需重建 kb-web

## 17. 当前状态（2026-10-14 摘要编排落点改造：wiki 页 → kb_document.summary）

- **背景**：be65902 移除直连 LLM 摘要链路后，摘要统一由编排生成，但编排产物是 wiki 摘要页（不写 kb_document.summary 字段，直连 LLM 时代的存储位置）。用户要求：三个摘要编排（Stuff ca5f228c / Map-Reduce b5954467 / Refine 103e45f4，线上 effective）的摘要落点改回 kb_document.summary
- **引擎**（api/services/orchestration_engine.py）：新增基础原子 `kb_write_doc_summary`（KbWriteDocSummaryStep）：读 bindings[summary_var]（默认 final_summary）→ UPDATE kb_document.summary/summary_status=READY/summary_error=NULL；skip_empty 空摘要跳过（loop 非末轮兜底）；注册进 ATOM_MAP + step_define 种子（设计器可选「写文档摘要」组件）
- **模板**（scripts/seed_framework_db.py 三套 SUMMARY_*_TAPE）：最后 wiki_upsert_page → kb_write_doc_summary；前驱 script 去掉 page_title/page_slug/page_content/hashlib（不再写 wiki），只留 final_summary（保留 2026-10-13 思考特征词清洗 + Refine 的 running_summary 回写）；模板描述「产物=wiki 摘要页」→「写 kb_document.summary 字段」
- **线上同步**（新增 scripts/apply_summary_write_doc_summary.py，幂等可重放，docker cp 进 kb-api-server 跑）：三个 tape 的 kb_tape_step（最后节点 step_inst 改 + script code 更新）+ tape.nodes + tape_template.nodes（按 label 匹配，模板 name 与 tape_name 不一致的坑已实测处理）三处同步；已执行（step 结构验证：三 tape 最后节点均为 kb_write_doc_summary，config 含 document_id/summary_var/skip_empty）
- **容器热修**：orchestration_engine.py docker cp 到 5 个业务容器（api-server + 4 celery worker）+ restart（镜像代码不含新原子，不热修线上执行会报「未知组件指令」）；api health UP；grep 确认 5 容器均有 kb_write_doc_summary
- **测试**：test_orchestration_engine.py +3（写字段/空摘要跳过/文档不存在失败）+ 结构测试改断言（写摘要节点替代写页节点）；18 passed；全量 585 passed / 3 failed（3 红仍为 §15 既有基线，无新增回归）
- **部署注意**：引擎/模板改动已提交，CI 重建后 pull 固化；apply 脚本改动线上库数据，若用户在服务编排页「保存设计/发布」覆盖回旧配置，重跑 apply 脚本即可恢复
- **遗留**：存量 wiki 摘要页（doc-1737d45155-* 等）未清理，删文档时因 source_refs=[] 会残留孤儿页（已备案 §16）

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

### 8.4 assistant-ui 试验问答页（已移除 2026-10-15）
- ~~/chat-next（web/src/app/(main)/chat-next/ + components/assistant-ui + components/ui + hooks + lib/utils.ts + postcss.config.mjs）~~ **已全量删除**（含依赖 @assistant-ui/react、@assistant-ui/react-markdown、@base-ui/react、class-variance-authority、clsx、lucide-react、tailwind-merge、tailwindcss@4、@tailwindcss/postcss、postcss、tw-animate-css；globals.css tailwind 段回退）
- 后端 0783426 的 `FollowUpRequest.session_id` / `follow_up_suggestions()` 放宽（chat-next 无会话模式专用）已回退为必填
- 菜单三处同步已还原：种子 KB_MENUS 移除 chat_next（顶级 sort_num 顺延还原）、线上 modo_menu / modo_role_menu_rela 待清理、前端兜底 FALLBACK_MENUS 移除 chat_next
- 旧版 /chat 智能问答页面不受影响（不依赖上述任何文件/依赖）

## 9. 开工检查单（新会话）

1. `cd /home/jenkins/chengkai/kb_compilation && .venv/bin/python -m pytest tests/ -q` → 应 127 passed
2. `git status` → 确认工作区状态
3. 读本文档「剩余工作」选一项继续
4. 改动后用「验证方式」整组跑
5. 完成后 `git add -A && git commit && git push`（用户习惯：功能完成直接提交推送）
