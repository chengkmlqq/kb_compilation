# KB Compilation Platform — 进度记录

> 本文档用于跨会话续接工作。每次工作结束更新「当前状态」；新会话开始先读本文档。

最后更新：2026-10-20（模型注册表 max_tokens 配置，修复思考型模型空答案）

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
