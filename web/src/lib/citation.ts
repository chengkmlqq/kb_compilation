/**
 * 问答引用标签预处理 —— 像素级对齐 WeKnora `frontend/src/utils/citationMarkdown.ts`。
 *
 * WeKnora 做法：模型输出 `<kb doc="文件名" chunk_id="..." kb_id="..."/>` 标签，
 * 前端把它转成可点击的胶囊（显示文档名，hover/点击看片段内容）。
 * kb 现状：后端 SYSTEM_PROMPT 让模型输出裸 `[chunk_id]`，MarkdownViewer 不识别，
 * 于是页面上就是一串裸 ID —— 本模块负责把三种来源的引用标记统一解析成胶囊：
 *
 *   1. `<kb doc="x.docx" chunk_id="..." kb_id="..."/>`   新格式（WeKnora 同款，对齐目标）
 *   2. `[chunk_id: 314e5022...]`                       kb 现行格式（模型把提示词里的 [chunk_id] 展开成字面量）
 *   3. 裸 32 位 hex id（如 ea9f387f611741a0a2bd3a10354301f3）  模型偶尔直接吐 id
 *
 * 解析结果用哨兵 token（@@KB_CITATION_n@@）替换，避免被 Markdown 解析器吞掉；
 * MarkdownViewer 的 inline() 再把这些 token 换回胶囊 ReactNode。
 */

/** 命中片段的引用信息（对应 chat SSE 的 context 事件 hits）。 */
export interface CitationRef {
  chunk_id: string;
  document_id?: string;
  kb_id?: string;
  content?: string;
  score?: number;
  /** 文档文件名（后端补全，用于胶囊显示名） */
  document_title?: string;
}

const PLACEHOLDER_RE = /@@KB_CITATION_(\d+)@@/g;

/** WeKnora 同款：文件名过长时中间省略。 */
export function truncateMiddle(text: string, maxLength = 13): string {
  if (!text) return "";
  if (text.length <= maxLength) return text;
  const half = Math.floor((maxLength - 3) / 2);
  const start = text.slice(0, half + ((maxLength - 3) % 2));
  const end = text.slice(-half);
  return `${start}...${end}`;
}

/** 引用胶囊显示名：优先文档名 → 内容首行摘要 → 短 id。 */
export function citationDisplayName(
  doc: string | undefined,
  ref: CitationRef | undefined,
): string {
  if (doc) return truncateMiddle(doc);
  if (ref?.document_title) return truncateMiddle(ref.document_title);
  const content = (ref?.content || "").trim().split("\n")[0] || "";
  if (content) return truncateMiddle(content);
  const id = ref?.chunk_id || "";
  return id ? `片段 ${id.slice(0, 8)}` : "来源";
}

/** 按 chunk_id 找引用（refs 里可能没有该 id，模型编造的情况返回 undefined）。 */
export function findCitationRef(
  chunkId: string,
  refs: CitationRef[] | null | undefined,
): CitationRef | undefined {
  if (!chunkId || !refs?.length) return undefined;
  return refs.find((r) => r && r.chunk_id === chunkId);
}

const ATTR_RE = /([\w-]+)\s*=\s*"([^"]*)"/g;

function parseAttrs(attrString: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  if (!attrString) return attrs;
  ATTR_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = ATTR_RE.exec(attrString)) !== null) attrs[m[1]] = m[2];
  return attrs;
}

const KB_TAG_RE = /<kb\b([^>]*?)\s*\/?>/gi;
/** `[chunk_id: xxx]` / `[chunkid:xxx]`（模型把 SYSTEM_PROMPT 的 [chunk_id] 展开成字面量） */
const CHUNK_BRACKET_RE = /\[chunk[_-]?id\s*[::]\s*([0-9a-zA-Z_-]{6,})\]/gi;
/** 裸 32 位 hex id（排除已经在 <kb/> 或 [chunk_id:] 里的，由调用顺序保证） */
const BARE_ID_RE = /\b([0-9a-f]{32})\b/gi;

function buildToken(
  chunkId: string,
  doc: string,
  kbId: string,
  refs: CitationRef[] | null | undefined,
  store: string[],
): string {
  const ref = findCitationRef(chunkId, refs);
  const display = citationDisplayName(doc, ref);
  const payload = JSON.stringify({
    chunkId,
    doc: doc || ref?.document_title || "",
    kbId: kbId || ref?.kb_id || "",
    display,
  });
  const idx = store.length;
  store.push(payload);
  return `@@KB_CITATION_${idx}@@`;
}

/**
 * 把答案文本里的引用标记替换成哨兵 token。
 * @param text 模型回答原文
 * @param refs 本次检索命中的片段（SSE context 事件），用于补显示名与跳转信息
 * @returns 处理后文本 + token payload 数组（顺序与 token 序号一一对应）
 */
export function preprocessCitations(
  text: string,
  refs?: CitationRef[] | null,
): { content: string; snippets: string[] } {
  if (!text) return { content: text, snippets: [] };
  const snippets: string[] = [];
  // 快速短路：没有任何可能的引用标记时原样返回

  // 1) <kb doc="..." chunk_id="..." kb_id="..."/> —— WeKnora 同款
  let out = text.replace(KB_TAG_RE, (_m, attrString: string) => {
    const attrs = parseAttrs(attrString);
    const chunkId = attrs.chunk_id || attrs.chunkId || "";
    const doc = attrs.doc || "";
    const kbId = attrs.kb_id || attrs.kbId || "";
    if (!chunkId) return "";
    return buildToken(chunkId, doc, kbId, refs, snippets);
  });

  // 2) [chunk_id: xxx] —— kb 现行格式
  out = out.replace(CHUNK_BRACKET_RE, (_m, chunkId: string) => {
    const ref = findCitationRef(chunkId, refs);
    return buildToken(
      chunkId,
      "",
      ref?.kb_id || "",
      refs,
      snippets,
    );
  });

  // 3) 裸 32 位 hex —— 模型直接吐文档/片段 id（表格单元格里的那种）
  out = out.replace(BARE_ID_RE, (_m, id: string) => {
    const ref = findCitationRef(id, refs);
    // 有对应命中片段 → 片段引用；否则当文档 id（无 chunk 内容，只给名字）
    return buildToken(id, "", ref?.kb_id || "", refs, snippets);
  });

  return { content: out, snippets };
}

/** 从预处理后的文本里取出 token 对应的 payload（MarkdownViewer 调用）。 */
export function collectCitationSnippets(text: string): string[] {
  const found: string[] = [];
  let m: RegExpExecArray | null;
  PLACEHOLDER_RE.lastIndex = 0;
  while ((m = PLACEHOLDER_RE.exec(text)) !== null) {
    found.push(m[1]);
  }
  return found;
}

/** token payload 的结构。 */
export interface CitationPayload {
  chunkId: string;
  doc: string;
  kbId: string;
  display: string;
}

export function parsePayload(json: string): CitationPayload | null {
  try {
    const p = JSON.parse(json) as CitationPayload;
    if (p && p.chunkId) return p;
  } catch {
    // ignore
  }
  return null;
}