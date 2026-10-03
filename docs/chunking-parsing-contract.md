# 切片/解析引擎 对齐契约(WeKnora → kb_compilation)

## 1. KB 级切片配置(存 kb_datasource.indexing_strategy JSON 的 `chunking` 键)

```json
{
  "chunking": {
    "chunk_size": 800,
    "chunk_overlap": 80,
    "separators": ["\n## ", "\n\n", "\n"],
    "enable_parent_child": false,
    "parent_chunk_size": 4096,
    "child_chunk_size": 384,
    "strategy": "auto",
    "token_limit": 0,
    "languages": [],
    "parser_engine_rules": [{ "file_types": ["pdf"], "engine": "mineru" }]
  }
}
```

strategy ∈ `"" | auto | heading | heuristic | recursive | legacy`
engine ∈ `docreader | mineru | mineru_cloud | paddleocr_vl | paddleocr_vl_cloud`

## 2. HTTP API

### `GET /api/v1/kbs/{kb_id}/chunking-config`
200 `{success, data: {chunking: <上面那份>, defaults: {chunk_size:800, chunk_overlap:80, strategy:"auto", parent_chunk_size:4096, child_chunk_size:384}}}`

### `PUT /api/v1/kbs/{kb_id}/chunking-config`
body = 上面那份 chunking 对象(partial 合并即可)。200 `{success:true,data:{chunking:...}}`

### `POST /api/v1/kbs/chunk-preview`
body `{text: string, config?: <chunking 对象>}` → 200
```json
{ "success": true, "data": {
  "chunks": [{"seq":0,"content":"...","chars":812,"tokens":271,"is_parent":false,"parent_seq":null}],
  "diagnostics": {
    "selected_tier": "heading",
    "tier_chain": ["heading","heuristic","recursive","legacy"],
    "rejected": [{"tier":"heading","reason":"..."}],
    "profile": {"total_chars":8120,"total_lines":140,"avg_line_len":58.0,
                "md_heading_total":12,"heading_density":0.086,"dominant_heading_level":2,
                "has_tables":true,"has_code":false,"detected_langs":["zh"]}
  }
}}
```

### `GET /api/v1/parsers/engines`
200 `{success, data: [{name, display_name, description, available, reason, file_types}]}`
不可用时 `available:false` + `reason:"MinerU endpoint 未配置"`。

## 3. 前端

### KB 设置弹窗(知识库编辑/设置)新增「切片配置」区
- 策略下拉(选项:legacy / auto / heading / heuristic / recursive,label 用中文,附说明文字)
  - 右侧「测试」按钮 → 打开分块调试抽屉
- 分隔符:可创建多选(creatable),预设 `["\n## ", "\n\n", "\n", "。", ". ", " "]`
- 父子分块:Switch;开启后父块滑杆 512–8192(默认4096)、子块 64–2048(默认384)
- Token 上限 InputNumber(0 = 按字符)
- 语言 Select 多选(en/de/zh)
- 解析引擎规则:列表,每行 = 文件类型多选 + 引擎下拉(引擎来自 `GET /parsers/engines`,不可用的置灰并带 reason tooltip)

### 分块调试抽屉
- 顶部:粘贴文本 TextArea(可从「使用文档内容」按钮拉取某文档的解析文本)
- 展示:选中档位 chunks 列表(序号/字符数/token数/内容折叠)、Diagnostics 面板(选中档位 Tag + 档位链 + 被拒原因 + profile 关键指标)

## 4. 解析 worker

三个独立 celery 队列(队列名即引擎名):`docreader` / `mineru` / `paddleocr`
- `worker/tasks/parsers/registry.py`:`resolve_engine(file_ext, rules, available_map)` → 引擎名;不可用回退 docreader 并记日志
- `worker/tasks/parsers/docreader_parser.py`(已有逻辑迁移/包装)
- `worker/tasks/parsers/mineru_parser.py`:自建 MinerU API(`/file_parse`)+ 云端(api_key)
- `worker/tasks/parsers/paddle_parser.py`:PaddleOCR-VL 自建 + 云端
- 路由:按 `parser_engine_rules` 命中文件类型 → 投对应队列;任务名 `parse_document_job`,`task_routes` 按队列分发

## 5. doc_chunk

新增列 `parent_chunk_id String(64) NULL` + `parent_seq Integer NULL`;检索命中子块时返回 parent 内容(与 WeKnora 一致)。