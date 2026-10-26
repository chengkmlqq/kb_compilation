// Agent 工具调用「表述」层（2026-10-21 对齐 WeKnora AgentStreamDisplay /
// agent-tool-display.ts / agent-tool-icons.ts）：
// - 工具名 → 中文名映射（TOOL_NAME_KEYS 对齐）
// - 工具图标选择（getToolIconName 对齐）
// - 卡片标题（pending/成功/失败三态，搜索类带 query，对齐 getToolTitle/getToolDescription）
// - 结果摘要行（搜索结果/关键词命中/wiki 读页等，对齐 getSearchResultsSummary 等）
// 纯函数、无 React 依赖，方便单测与复用。

// ── 工具名 → 中文名（对齐 WeKnora TOOL_NAME_KEYS + kb 目录补全）──
const TOOL_NAME_ZH: Record<string, string> = {
  // 推理
  thinking: "思考",
  todo_write: "计划管理",
  // RAG
  knowledge_search: "知识库检索",
  kb_search: "知识库检索",
  grep_chunks: "搜索关键词",
  list_knowledge_chunks: "查看知识分块",
  get_document_info: "获取文档信息",
  database_query: "数据库查询",
  // 图谱
  query_knowledge_graph: "知识图谱查询",
  // Wiki
  wiki_search: "Wiki 检索",
  wiki_read_page: "读取 Wiki 页面",
  wiki_read_source_doc: "精读源文档",
  wiki_flag_issue: "登记 Wiki 问题",
  wiki_read_issue: "查看 Wiki 问题",
  wiki_update_issue: "更新 Wiki 问题",
  // Wiki 写
  wiki_write_page: "写入 Wiki 页面",
  wiki_replace_text: "替换 Wiki 文本",
  wiki_rename_page: "重命名 Wiki 页面",
  wiki_delete_page: "删除 Wiki 页面",
  // 数据分析
  data_schema: "数据结构",
  data_analysis: "数据分析",
  // 联网
  web_search: "网络搜索",
  web_fetch: "网页抓取",
  // 技能
  run_skill_script: "执行技能脚本",
  read_skill: "读取技能",
  // 多模态
  image_analysis: "查看图片内容",
  attachment_parsing: "解析附件",
  query_understand: "理解问题",
  // 兜底
  get_related_documents: "查找相关文档",
};

/** 未命中的工具（如 MCP 工具 mcp_xxx）返回 raw 名。 */
export function zhToolName(toolName?: string | null): string {
  if (!toolName) return "工具";
  return TOOL_NAME_ZH[toolName] ?? toolName;
}

// ── 图标（对齐 WeKnora getAgentToolIconName 语义；antd 图标名）──
export function toolIconKey(toolName?: string | null): string {
  if (!toolName) return "tool";
  if (toolName === "thinking") return "bulb";
  if (toolName === "knowledge_search" || toolName === "kb_search") return "search";
  if (toolName === "wiki_search" || toolName === "grep_chunks") return "search";
  if (toolName === "web_search" || toolName === "web_fetch") return "global";
  if (toolName === "get_document_info" || toolName === "list_knowledge_chunks") return "fileSearch";
  if (toolName === "wiki_read_page" || toolName === "wiki_read_source_doc") return "fileSearch";
  if (toolName === "todo_write") return "orderedList";
  if (toolName === "image_analysis" || toolName === "query_understand") return "bulb";
  if (toolName === "attachment_parsing") return "paperClip";
  if (toolName === "database_query" || toolName === "data_analysis" || toolName === "data_schema")
    return "database";
  if (toolName === "query_knowledge_graph") return "shareAlt";
  if (toolName === "run_skill_script" || toolName === "read_skill") return "code";
  return "tool";
}

// ── 参数提取 ├─ 2026-10-21 对齐 WeKnora agent-tool-display getQueryText ──
function collectQueryStrings(value: unknown): string[] {
  if (value == null) return [];
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return [];
    if (trimmed.startsWith("[")) {
      try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) {
          return parsed.filter((q): q is string => typeof q === "string" && Boolean(q.trim()));
        }
      } catch {
        // fall through to treat as a single query string
      }
    }
    return [trimmed];
  }
  if (Array.isArray(value)) {
    return value.filter((q): q is string => typeof q === "string" && Boolean(q.trim()));
  }
  return [];
}

/** 从 arguments（JSON 字符串或对象）提取 query/queries 去重拼接（对齐 getQueryText）。 */
export function queryTextOf(args?: unknown): string {
  if (!args) return "";
  let parsed: unknown = args;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return "";
    }
  }
  if (!parsed || typeof parsed !== "object") return "";
  const record = parsed as Record<string, unknown>;
  const queries: string[] = [];
  queries.push(...collectQueryStrings(record.query));
  queries.push(...collectQueryStrings(record.queries));
  return Array.from(new Set(queries)).join("，");
}

/** 从 arguments 提取 slug/slugs 去重拼接。 */
export function slugTextOf(args?: unknown): string {
  if (!args) return "";
  let parsed: unknown = args;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return "";
    }
  }
  if (!parsed || typeof parsed !== "object") return "";
  const record = parsed as Record<string, unknown>;
  const slugs: string[] = [];
  slugs.push(...collectQueryStrings(record.slug));
  slugs.push(...collectQueryStrings(record.slugs));
  return Array.from(new Set(slugs)).join("、");
}

// ── 卡片标题（对齐 WeKnora getToolTitle / getToolDescription）──
export interface ToolCallState {
  name: string;
  pending?: boolean;
  success?: boolean;
  arguments?: string;
  /** 工具执行结果文本（用于摘要/详情） */
  result?: string;
}

const SEARCH_TOOLS = new Set(["knowledge_search", "kb_search", "wiki_search", "grep_chunks", "web_search", "web_fetch"]);

/** 卡片标题：pending 时「执行中…」+ 工具名；完成时搜索类带 query；失败带「失败」。 */
export function toolTitle(ev: ToolCallState): string {
  const zh = zhToolName(ev.name);
  if (ev.pending) return `正在${zh}…`;
  // 搜索/读取类工具：标题带上 query/slug（WeKnora「检索知识库：「query」」形态）
  if (SEARCH_TOOLS.has(ev.name)) {
    const q = queryTextOf(ev.arguments);
    if (q) return `${zh}：「${q}」`;
  }
  if (ev.name === "wiki_read_page" || ev.name === "wiki_read_source_doc") {
    const slug = slugTextOf(ev.arguments);
    if (slug) return `${zh}：「${slug}」`;
  }
  const ok = ev.success !== false;
  return ok ? zh : `${zh}失败`;
}

/** 状态文案（对齐 WeKnora toolStatus）：执行中/完成/失败 + 耗时。 */
export function toolStatusText(ev: ToolCallState & { duration?: number }): string {
  if (ev.pending) return "执行中";
  if (ev.success === false) return "失败";
  if (ev.duration != null) return `完成 ${Math.round(ev.duration * 10) / 10}s`;
  return "完成";
}

// ── 结果摘要行（对齐 WeKnora getSearchResultsSummary / getGrepResultsSummary）──
function countHitDocs(result: string): number {
  // 检索结果行形态：「[1] 文档标题 (score=0.1234)\n内容…」（agent_tools_full _chunk_hits_to_text）
  const set = new Set<string>();
  const re = /^\[\d+\]\s+([^\n(]+)/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(result))) {
    const title = m[1].trim();
    if (title) set.add(title);
  }
  return set.size;
}

/** 工具完成后的摘要行（搜索结果数/关键词命中/wiki 读页结果等）；无摘要返回空串。 */
export function toolSummaryText(ev: ToolCallState): string {
  if (ev.pending || ev.success === false || !ev.result) return "";
  const r = ev.result.trim();
  if (!r) return "";
  // 检索无命中的固定表述
  if (/^（检索无命中）/.test(r)) return "未找到匹配的内容";
  if (r === "（wiki 无命中）" || r === "（无分块）" || r === "（无文档）" || r === "（无问题）") {
    return "未找到匹配的内容";
  }
  if (ev.name === "knowledge_search" || ev.name === "kb_search" || ev.name === "grep_chunks") {
    const docCount = countHitDocs(r);
    if (docCount > 0) return `找到 ${docCount} 个相关片段`;
  }
  if (ev.name === "wiki_read_page" || ev.name === "wiki_read_source_doc") {
    // 页面正文成功读取：「# 标题\n正文…」取首行标题
    const firstLine = r.split("\n").find((l) => l.trim().length > 0) ?? "";
    const title = firstLine.replace(/^#+\s*/, "").trim();
    if (title) return title;
  }
  // 兜底：取第一行（工具结果摘要，最多 60 字）
  const firstLine = r.split("\n").find((l) => l.trim().length > 0) ?? "";
  if (firstLine.trim()) return firstLine.trim().slice(0, 80);
  return "";
}

/** 详情区标题（对齐 WeKnora「结果」区块）：失败时展示错误。 */
export function toolDetailLabel(ev: ToolCallState): string {
  if (ev.success === false) return "失败原因";
  return "结果";
}