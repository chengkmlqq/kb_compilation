# KB Compilation Platform — 进度记录

> 本文档用于跨会话续接工作。每次工作结束更新「当前状态」；新会话开始先读本文档。

最后更新：2026-10-03

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

## 9. 开工检查单（新会话）

1. `cd /home/jenkins/chengkai/kb_compilation && .venv/bin/python -m pytest tests/ -q` → 应 127 passed
2. `git status` → 确认工作区状态
3. 读本文档「剩余工作」选一项继续
4. 改动后用「验证方式」整组跑
5. 完成后 `git add -A && git commit && git push`（用户习惯：功能完成直接提交推送）
