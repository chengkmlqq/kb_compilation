# 代码仓库知识库（Code Repo → Knowledge Base）开发方案

> 文档编号：KB-PLAN-2026-10-CODE-REPO
> 状态：待评审
> 适用仓库：kb_compilation（/home/jenkins/chengkai/kb_compilation）
> 目标：以 Git 代码仓库为知识源，创建知识库后自动拉取仓库代码存入 MinIO，按仓库目录结构建立知识库文档目录，代码文件进入既有解析与 Wiki 构建链路。

---

## 1. 需求概述

创建知识库时选择 Git 代码仓库作为知识来源，并在创建完成后自动完成以下动作：

1. 拉取仓库代码，二进制文件存入 MinIO 对象存储；
2. 将仓库目录结构镜像为知识库的文档目录树；
3. 仓库内文件逐个进入解析链路（分块、向量化）；
4. 基于解析结果执行 Wiki 构建（复用现有构建引擎编排）；
5. 支持手动/定时增量同步：按 commit 差异增删改文档与目录。

用户操作入口：知识库创建弹窗新增「代码仓库」来源配置；知识库详情页新增「代码仓库」页签展示拉取状态、触发重新拉取。

---

## 2. 现状基线（可复用能力盘点）

| 能力 | 现状位置 | 结论 |
|---|---|---|
| 知识库模型 | `api/models/knowledge.py::KbDatasource`（已有大量 JSON 配置列、build_tape_id、summary_strategy） | 新增代码仓库配置字段 |
| 文档模型 | `KbDocument`（source_type=file/table、folder_id 目录归属、storage_path、parse_state） | 新增 source_type=repo 来源 |
| 文档目录 | `DocFolder` 多级树（parent_id 邻接表，递归子树浏览） | 直接复用，仓库目录 → doc_folder |
| 对象存储 | `api/services/storage.py`（minio://bucket/key 双后端抽象，MinIO 已部署 kb-minio，bucket=kb-compilation） | 直接复用 put_bytes/get_bytes |
| 解析链 | worker `KbDocumentProcessTask` → `ingest_document`（docreader 解析 → 分块 → 向量化 → doc_chunk） | 代码文件需先扩展解析注册表 |
| Wiki 构建 | 解析 READY 后按 build_tape_id 走编排（kb_tape_run + KbOrchestrationRunTask） | 复用，文档级 bindings 不变 |
| 凭证加密 | `api/lib/crypto.py::aes_encrypt/aes_decrypt`（模型 api_key 同款 AES-128-CBC） | 复用存 git 拉取 key |
| 任务监控 | modo_job + TASK_CLASS_REGISTRY 分发 | 新增任务类注册 |
| 前端 | `web/src/app/(main)/kbs/page.tsx` 创建弹窗、KBConfigModal 配置弹窗 | 创建表单加来源配置、详情加页签 |

**关键约束**：上传白名单（`api/services/parser_registry.py::SUPPORTED_UPLOAD_TYPES`）与 docreader 注册表（`docreader/parser/registry.py` BUILTIN_ENGINE，20 种文档格式）目前不含代码扩展名（.py/.java/.js/.go 等）。代码仓库导入是内部落库路径（不经上传端点），但解析注册表必须认识代码格式，否则 worker 解析直接 FAILED。

---

## 3. 总体架构

```
前端（创建弹窗/详情页签）
   │  repo_config（仓库地址/分支/凭证类型/凭证）
   ▼
POST /api/v1/kbs（create_kb 增加代码仓库配置，凭证 AES 加密落库）
   │
   ▼ 创建成功即投递
Celery KbCodeRepoSyncTask（worker/tasks/code_repo.py）
   │  ① git clone（浅克隆 depth=1，凭证注入）
   │  ② 遍历工作树，跳过 .git/二进制/超大文件
   │  ③ 目录 → doc_folder 树（镜像仓库相对路径）
   │  ④ 文件 → MinIO（minio://kb-compilation/code_repos/{kb_id}/{commit}/{relpath}）
   │      + kb_document(source_type=repo, folder_id 按目录树)
   │  ⑤ 增量差异（对比上次 commit）：新增/变更 → 入队解析；删除 → 级联清理
   ▼
KbDocumentProcessTask（既有）→ ingest_document → 分块/向量化 → READY
   ▼
摘要编排 + 构建引擎编排（既有 build_tape_id 链路）→ wiki 页落库
```

核心原则：**代码仓库 = 新的文档来源（source_type=repo），全链路复用现有「文档 → 解析 → Wiki」管道**，不另起炉灶。

---

## 4. 数据模型设计

### 4.1 kb_datasource 新增代码仓库配置（迁移 0028_code_repo_config）

| 列 | 类型 | 说明 |
|---|---|---|
| `repo_type` | String(16), default="" | 空=普通文档库；"git"=代码仓库知识库 |
| `repo_url` | String(512), default="" | 仓库地址（http/https/ssh/git@） |
| `repo_branch` | String(128), default="" | 拉取分支（空=仓库默认分支） |
| `repo_auth_type` | String(16), default="none" | none / token / ssh_key / user_pass |
| `repo_auth_secret` | Text, default="" | AES 加密的凭证（token / SSH 私钥 / 用户名+密码 JSON） |
| `repo_commit_sha` | String(64), default="" | 最近一次成功拉取的 commit（增量同步基准） |
| `repo_pull_status` | String(16), default="" | idle / pulling / success / failed |
| `repo_pull_error` | Text, default="" | 最近一次拉取失败原因 |
| `repo_last_pull_at` | DateTime, nullable | 最近一次拉取时间 |
| `repo_file_count` | Integer, default=0 | 最近一次拉取的文件数（状态展示用） |

- 凭证加密：`aes_encrypt(json.dumps({...}))`；序列化输出（`_kb_dict`）**只回显 auth_type 与 URL 脱敏尾段，不回显凭证**。
- 迁移走现有 alembic 链（`migrations/versions/0028_*`），revision id ≤32 字符，`api/services/migrations.py` 自愈护栏自动覆盖。

### 4.2 kb_document

- `source_type` 新增取值 `"repo"`（现已有 file/table，索引已建）。
- 复用字段：`folder_id`（目录归属）、`storage_path`（MinIO 路径）、`file_name`（保留仓库相对路径，如 `src/main/java/A.java`）、`parse_state`。
- 追加一列 `repo_commit_sha`（String(64)）标记该文件来自哪个 commit，供增量 diff 与「变更即重解析」判定。

### 4.3 doc_folder

- 不新增列。仓库根目录 = 知识库根层级（顶层所选仓库名不单独建目录，与 2026-10-17 目录上传语义一致）；仓库子目录逐级建 doc_folder（同名同级复用）。

---

## 5. 后端 API 设计（api/routers/kbs.py）

| 端点 | 方法 | 说明 |
|---|---|---|
| `POST /api/v1/kbs` | create_kb 扩展 | body 增加 `repo_type/repo_url/repo_branch/repo_auth_type/repo_auth_secret`；校验仓库 URL 格式与凭证非空；创建成功且 repo_type=git 时投递首次同步任务 |
| `GET /api/v1/kbs/{kb_id}` | 详情 | `_kb_dict` 增加 repo_* 字段（凭证脱敏） |
| `PUT /api/v1/kbs/{kb_id}` | update_kb 扩展 | 修改仓库配置（改凭证需重传明文，AES 重加密；不改则保持） |
| `POST /api/v1/kbs/{kb_id}/repo/pull` | 新增 | 手动触发重新拉取（幂等：pulling 中拒绝）；投递 KbCodeRepoSyncTask，返回 job_id |
| `GET /api/v1/kbs/{kb_id}/repo` | 新增 | 拉取状态/commit/文件数/最近错误（前端页签轮询） |
| `DELETE /api/v1/kbs/{kb_id}` | delete_kb 扩展 | 级联清理代码仓库 MinIO 前缀 `code_repos/{kb_id}/` 与凭证列 |

权限：沿用现有 `_require_kb_caller` / `_kb_manage`（可管理才能改仓库配置与触发拉取）。

---

## 6. Worker：代码仓库同步任务（新增 worker/tasks/code_repo.py）

### 6.1 任务类

```
TASK_CLASS_REGISTRY["KbCodeRepoSyncTask"] = handler(job_id, task_params)
task_params: {kbId, cookie}
```

注册进 `worker/celery_app.py`；任务监控页展示为「代码仓库同步」类任务（前端 TASK_CLASS_LABEL 补映射，与 2026-10-18 编排任务同款）。

### 6.2 拉取实现（git）

- **首选系统 git CLI**：`git clone --depth 1 --branch <branch> <url> <tmpdir>`（浅克隆，省带宽与磁盘）。凭证注入：
  - token：URL 内嵌 `https://<token>@host/...`（仅进程内拼接，不落日志）；
  - ssh_key：`GIT_SSH_COMMAND="ssh -i <临时私钥文件> -o StrictHostKeyChecking=no"`；
  - user_pass：`GIT_ASKPASS` 脚本或 `-c credential.helper=` + 环境注入。
- Dockerfile.api 增加 `git` 依赖（`apt-get install -y git`，含 openssh-client）。
- 拉取目录用 worker 挂载的临时盘（`/tmp/code-repo/{kb_id}-{task_id}`），任务结束清理；超大仓库设超时（默认 10 分钟，env `GIT_CLONE_TIMEOUT` 可调）与 `--filter=blob:none`（可选开关）。
- 失败处理：捕获 stderr，写 `repo_pull_status=failed` + `repo_pull_error`，任务返回失败（任务监控可见）。

### 6.3 文件分拣与落库

遍历工作树（跳过 `.git/`、隐藏文件、`node_modules/`、`target/`、`dist/` 等目录与 `.DS_Store`，规则常量 `REPO_SKIP_DIRS/REPO_SKIP_FILES`，可随 KB repo_config 扩展）：
- 二进制/超大文件（> `MAX_REPO_FILE_BYTES`，默认 10MB）：跳过并计数（不建文档，避免解析 FAILED 噪声）；
- 文本文件：读字节 → `storage.put_bytes(minio://{bucket}/code_repos/{kb_id}/{commit}/{relpath})` → `create_document(source_type="repo", file_name=relpath, folder_id=对应目录, storage_path=..., repo_commit_sha=commit)`；
- **增量 diff**：若 `repo_commit_sha` 非空，`git diff --name-status <old_commit>..<new_commit>`（或遍历对比相对路径集合）：
  - 新增/变更（M/A）→ 重建 storage_path 对应 commit 前缀并重新入队解析（沿用 `_enqueue_document_process`，重解析走既有 reparse 逻辑）；
  - 删除（D）→ 复用 `delete_document` 级联清理（chunk/向量/MinIO 对象/wiki 关联）。
- 全部完成后：更新 `repo_commit_sha/repo_last_pull_at/repo_file_count/repo_pull_status=success`。

### 6.4 首次同步

创建知识库时投递：clone → 全量建目录/文档 → 逐个入队解析（串行入队避免打满队列，与目录上传同策略）。解析成功后自动进入既有摘要编排 + 构建引擎编排链路（未绑定构建引擎时走「题材分析-领域判定」自动路由，见 §8），**无需任何额外编排改造**——编排 bindings 只需 kb_id/document_id，代码文件与文档文件等价。

---

## 7. 代码文件解析扩展（docreader + 白名单）

### 7.1 新增 CodeParser

- 新建 `docreader/parser/code_parser.py`：继承 `BaseParser`，按行读取源码文本，保留行结构，meta 记录 `language`（按扩展名映射：py/java/js/ts/go/rust/c/cpp/sql/yaml/json/xml/html/css/shell 等）与 `source_path`。
- 注册进 `docreader/parser/registry.py` 的 BUILTIN_ENGINE：将代码扩展名映射到 CodeParser（`"py": CodeParser, "java": CodeParser, ...`）。
- 分块策略沿用现有 chunking 管线（800/80 默认），代码按行切块天然适合递归分隔符；后续可加「按函数/类边界切块」的 code 策略（二期）。

### 7.2 白名单同步

- `api/services/parser_registry.py::SUPPORTED_UPLOAD_TYPES` 同步加入代码扩展名（与 docreader 注册表逐项一致）。
- `tests/test_upload_file_types.py::test_supported_types_match_docreader_builtin_registry` 护栏自动覆盖（两边漂移会红）。
- 用户侧语义：代码文件因此也可手动上传（上传确认弹窗 accept 自动包含新格式），但主场景是仓库拉取。

### 7.3 格式门禁兼容

上传端点门禁逻辑不变（repo 拉取是内部路径，不走 upload 端点，不受 MAX_UPLOAD_BYTES 单文件上限约束——仓库内大文件由 MAX_REPO_FILE_BYTES 单独控制）。

---

## 8. Wiki 构建衔接（设计约束：代码知识构建必须走服务编排）

**原则**：代码仓库文件的 Wiki 构建与文档构建完全同一条编排链路——`KbCodeRepoSyncTask` 只负责「拉取 → 落库 → 入队解析」，**解析 READY 之后的一切构建动作全部由服务编排承载**（kb_tape_run + KbOrchestrationRunTask），不内置任何直连 LLM / 技能兜底，与现有文档链路（§3 架构图右侧）零改造复用。

- **触发路径**：kb_document 解析 READY（parse_state=READY）后，`worker/tasks/doc_process.py::_handle_process_document` 的下游逻辑对 repo 来源文档天然生效：
  1. **摘要编排**：按 KB `summary_strategy`（默认 auto）触发标准摘要编排（Stuff/Map-Reduce/Refine，产物写 kb_document.summary）；
  2. **构建编排**：KB 级 `build_tape_id`（创建代码仓库知识库时下拉选择，复用 `GET /api/v1/kbs/build-engines`，只列已发布编排）→ 直接投递该编排；**未绑定** → 走「题材分析-领域判定」自动路由（等摘要 run 终态 → 题材分析编排判定领域 → 投递对应领域 wiki 构建编排）；
  3. **文档级覆盖**：repo 来源文档同样支持文档级 build_tape_id（一期同步任务按 KB 默认绑定，不留文档级入口，如需可二期放开）。
- **编排适配**：现有领域编排（20 个 wiki构建-*）prompt 面向文档内容生成摘要/实体页，代码文件（模块说明/类/函数/依赖关系）内容同样可生成实体页，**一期直接复用即可跑通**。
- **代码专属编排（建议随一期一起落地）**：新增种子编排「wiki构建-代码仓库」并纳入 `seed_tape_data/` 导出-固化流程——LLM 节点 prompt 面向代码结构（模块职责 / 关键类与函数 / 依赖关系 / 用法示例），使代码知识库产出更贴近研发场景（架构说明页、模块索引页）；该编排发布（effective）后即可在创建代码仓库知识库时被选为构建引擎，或由题材分析路由（在题材分析 n1 的领域清单条件中纳入代码类编排）。
- **任务可见性**：编排 run 走既有 kb_tape_run，任务监控页「编排执行」标签、文档卡片 wiki 构建状态标签均自动呈现，无需前端改造。
- **删除级联**：`delete_document` 现有级联清理 wiki 关联的逻辑对 repo 来源文档同样生效（无需改动）。

---

## 9. 前端设计

### 9.1 创建知识库弹窗（kbs/page.tsx）

- 表单新增「知识来源」分区（置于首个分区，对齐 AgentEditorModal 分区导航模式）：
  - 来源类型 Radio：`文档上传（默认）/ Git 代码仓库`；
  - 选代码仓库时展开配置区：仓库地址（必填）、分支（选填，默认仓库默认分支）、凭证类型 Select（无凭证/Token/SSH 私钥/用户名+密码）、凭证输入（TextArea/Password，按类型切换）；
  - 校验：地址必填、凭证类型非 none 时凭证必填；
- 创建 payload 增加 repo_* 字段；创建成功后若为代码仓库，详情页自动显示同步状态。

### 9.2 知识库详情「代码仓库」页签（新增 web/src/components/kb/CodeRepoPane.tsx）

- 展示：仓库地址（脱敏）、分支、凭证类型、最近 commit（前 8 位）、文件数、拉取状态 Tag（进行中 Spin / 成功 / 失败红字 + 错误信息）、最近拉取时间；
- 操作：「立即同步」按钮（POST /repo/pull，防重复点击）、「修改配置」（复用配置表单）；
- 同步进度：任务监控页可见（复用既有 modo_job 体系），页签内轮询 GET /repo 更新状态；
- 文档列表/目录树：复用现有 KbDocsPane（source_type=repo 的文档自然出现在文档页签，目录树即仓库结构）。

### 9.3 KBConfigModal 编辑弹窗

- 配置分区追加「代码仓库」区（可改地址/分支/凭证；凭证留空=保持不变）。

---

## 10. 安全设计

| 项 | 方案 |
|---|---|
| 凭证存储 | AES-128-CBC 加密落库（复用 api/lib/crypto.py，AES_SECRET_KEY env），任何序列化接口不回显 |
| 凭证传输 | 前端仅创建/修改时提交，读取接口不返回 secret |
| 日志 | clone 命令 stderr 捕获写入 repo_pull_error（截断 500 字符）；凭证不落 modo_job.error_message 与日志 |
| 临时文件 | 私钥写 worker 临时目录（600 权限），任务结束清理；clone 目录同清理 |
| 网络 | 仅 worker 出网到仓库地址；clone 超时限制防 SSRF 拖死 worker |
| 越权 | 仓库配置修改/拉取触发走 `_kb_manage`（可管理人才行） |

---

## 11. 部署与迁移

1. **迁移**：`migrations/versions/0028_code_repo_config.py`（kb_datasource + 9 列、kb_document + 1 列），api-server 启动自动 `alembic upgrade head`（幂等，含 ensure_version_table_width 自愈）。
2. **镜像**：Dockerfile.api 加 `git`/`openssh-client`；api/worker 代码改动 → CI 重建 kb-api-server/kb-worker（及 orch/build 等 worker 镜像同镜像多命令，一次重建全覆盖）。
3. **前端**：创建弹窗/详情页签/KBConfigModal 改动 → CI 重建 kb-web。
4. **env**：deploy/.env 追加 `GIT_CLONE_TIMEOUT`（默认 600）、`MAX_REPO_FILE_BYTES`（默认 10485760）可选覆盖。
5. **存量库兼容**：现有知识库 repo_type=""，创建/更新接口对旧库零影响；`_kb_dict` 用 getattr 兜底空值。

---

## 12. 测试计划（tests/）

| 文件 | 覆盖 |
|---|---|
| `tests/test_code_repo_config.py` | create_kb 带 repo 配置落库/加密、序列化脱敏、update 改凭证重加密、get 详情回显、delete 级联清 MinIO 前缀 |
| `tests/test_code_repo_sync.py` | 同步任务：clone（mock git 命令）→ 目录树镜像（doc_folder 层级断言）→ 文件落库（storage_path 前缀断言）→ 增量 diff（新增/变更重解析入队、删除级联）→ 失败状态写入；mock git 输出与 storage.put_bytes |
| `tests/test_repo_pull_endpoint.py` | POST /repo/pull 幂等（pulling 拒绝）、权限（不可管理 403）、无 repo_type 400 |
| `tests/test_upload_file_types.py` | 扩展：白名单与 docreader 注册表同步断言自动覆盖代码扩展名 |
| `tests/test_code_parser.py` | CodeParser 按行提取、language 推断、空文件/大文件边界 |

前端验证门：`cd web && ./node_modules/.bin/tsc --noEmit` + `bun run build`；后端全量 `uv run pytest -q`。

---

## 13. 实施分期

### 一期（MVP，本方案主体）

1. 迁移 0028 + 模型扩展；
2. docreader CodeParser + 白名单同步；
3. worker KbCodeRepoSyncTask（clone/分拣/目录树/落库/首拉）；
4. API（create 扩展 + /repo/pull + /repo 详情）；
5. 前端（创建弹窗来源配置 + 详情代码仓库页签）；
6. **代码专属编排「wiki构建-代码仓库」**（seed_tape_data 固化 + 发布为构建引擎可选，走既有题材分析路由清单）；
7. 手动同步（POST /repo/pull）跑通全链路：建库 → 拉取 → 解析 → 摘要编排 → 构建引擎编排 → wiki 生成（小样本仓库验证）；
8. 测试 + 部署。

### 二期（增量完善）

- 增量同步的 diff 驱动重解析（一期先全量对比路径集合，二期按 commit 树精确 diff）；
- 定时同步（复用 cron 体系，按 KB 配置间隔）；
- 代码专属编排增强（架构图/依赖关系图/函数索引页）；
- 仓库文件忽略规则 UI 化、代码分块策略（函数边界）。

---

## 14. 风险与待确认项

| 项 | 说明 |
|---|---|
| 仓库规模 | 浅克隆 + blob:none + 文件数/大小上限兜底；超大型仓库首次拉取耗时需实测 |
| git 依赖 | 需要容器可访问 apt 源装 git（部署环境需确认网络） |
| 凭证方式 | 一期支持 token/ssh_key/user_pass 三种，均需实测目标仓库类型（内网 GitLab/Gitea/GitHub） |
| 编排产出质量 | 代码文件走通用编排的 wiki 质量需小样本实测（用户测试偏好：小样本验证） |
| 二进制仓库文件 | 图片类（png/jpg 等）在白名单内可解析，其余二进制跳过（计数回报） |

---

*文档结束。评审通过后按一期顺序开工，代码改动按「提交推送 → CI 重建 → pull 部署」惯例落地。*
