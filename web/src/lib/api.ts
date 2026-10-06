"use client";

/**
 * API client — same-origin fetch to the Next.js proxy route, which forwards
 * to the FastAPI backend. All calls are same-origin so the x-next-identity
 * cookie is included automatically (same contract as the source platform).
 */

export interface ApiEnvelope<T = unknown> {
  success: boolean;
  message?: string;
  data?: T;
}

export interface Identity {
  loginId: string;
  userId: string;
  userName: string;
  teamName: string;
  teamId: string;
  teamLabel: string;
}

async function request<T = unknown>(path: string, init?: RequestInit): Promise<ApiEnvelope<T>> {
  const res = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "content-type": "application/json" }),
      ...init?.headers,
    },
    cache: "no-store",
  });
  const text = await res.text();
  if (!text) {
    return { success: res.ok, message: `HTTP ${res.status}` };
  }
  try {
    return JSON.parse(text) as ApiEnvelope<T>;
  } catch {
    return { success: false, message: text.slice(0, 200) };
  }
}

export interface LoginResult {
  success: boolean;
  message?: string;
  data?: Identity | null;
  identity_cookie?: string | null;
}

/** Login: returns identity + the identity cookie to persist on the frontend host. */
export async function apiLogin(userId: string, pwd: string): Promise<LoginResult> {
  const res = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ userId, pwd }),
    cache: "no-store",
  });
  const text = await res.text();
  try {
    return JSON.parse(text) as LoginResult;
  } catch {
    return { success: false, message: text.slice(0, 200) };
  }
}

export async function apiMe(): Promise<ApiEnvelope<Identity>> {
  return request("/api/v1/me");
}

// ---- knowledge bases ----

export interface IndexingStrategy {
  vector_enabled?: boolean;
  keyword_enabled?: boolean;
  wiki_enabled?: boolean;
  graph_enabled?: boolean;
  [k: string]: unknown;
}

export interface FaqConfig {
  index_mode?: string;
  [k: string]: unknown;
}

export interface QuestionGenerationConfig {
  enabled?: boolean;
  question_count?: number;
  [k: string]: unknown;
}

export interface KbItem {
  id: string;
  name: string;
  label?: string | null;
  description?: string | null;
  scope?: string;
  team_name?: string;
  owner_user_id?: string;
  indexing_strategy?: IndexingStrategy;
  doc_count?: number;
  page_count?: number;
  created_at?: string | null;
  type?: "document" | "faq" | string;
  faq_config?: FaqConfig;
  question_generation_config?: QuestionGenerationConfig;
  custom_wiki_generation?: boolean;
  wiki_config?: {
    skill?: string;
    extraction_granularity?: string;
    synthesis_model_id?: string;
    [k: string]: unknown;
  };
  embedding_model_id?: string | null;
  summary_model_id?: string | null;
  extract_config?: { enabled?: boolean; [k: string]: unknown };
  vlm_config?: Record<string, unknown>;
  asr_config?: Record<string, unknown>;
  storage_provider_config?: Record<string, unknown>;
  storage_backend_id?: string;
  vector_store_id?: string;
}

export interface PageList<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface KbUpdatePayload {
  type?: "document" | "faq";
  indexing_strategy?: IndexingStrategy;
  custom_wiki_generation?: boolean;
  embedding_model_id?: string | null;
  summary_model_id?: string | null;
  configs?: Record<string, unknown>;
}

export function apiUpdateKb(kbId: string, payload: KbUpdatePayload) {
  return request(`/api/v1/kbs/${encodeURIComponent(kbId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function apiGetKb(kbId: string) {
  return request<KbItem>(`/api/v1/kbs/${encodeURIComponent(kbId)}`);
}

export function apiListKbs(page = 1, pageSize = 20, keyword = "", scope?: string) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (keyword) params.set("keyword", keyword);
  if (scope) params.set("scope", scope);
  return request<PageList<KbItem>>(`/api/v1/kbs?${params.toString()}`);
}

export interface KbCreatePayload {
  name: string;
  label?: string;
  description?: string;
  scope?: string;
  team_name?: string;
  type?: "document" | "faq" | string;
  indexing_strategy?: IndexingStrategy;
  custom_wiki_generation?: boolean;
  embedding_model_id?: string | null;
  summary_model_id?: string | null;
  vector_store_id?: string | null;
  configs?: Record<string, unknown>;
  ontology_schema_name?: string;
}

export function apiCreateKb(payload: KbCreatePayload) {
  return request<{ id: string; scope?: string }>("/api/v1/kbs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiDeleteKb(kbId: string) {
  return request(`/api/v1/kbs/${kbId}`, { method: "DELETE" });
}

// ---- documents ----

export interface DocItem {
  id: string;
  kb_id: string;
  file_name: string;
  file_ext?: string | null;
  file_size?: number | null;
  parse_state: string;
  parse_error?: string | null;
  chunk_count: number;
  created_at?: string | null;
}

export function apiListDocuments(
  kbId: string,
  page = 1,
  pageSize = 20,
  opts: { keyword?: string; fileType?: string; parseStatus?: string } = {}
) {
  const params = new URLSearchParams({
    page: String(page),
    pageSize: String(pageSize),
  });
  if (opts.keyword) params.set("keyword", opts.keyword);
  if (opts.fileType) params.set("file_type", opts.fileType);
  if (opts.parseStatus) params.set("parse_status", opts.parseStatus);
  return request<PageList<DocItem>>(`/api/v1/kbs/${kbId}/documents?${params.toString()}`);
}

export function apiUploadDocument(kbId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<DocItem>(`/api/v1/kbs/${kbId}/documents/upload`, {
    method: "POST",
    body: form,
  });
}

export function apiUploadDocumentByUrl(kbId: string, url: string, fileName?: string) {
  const params = new URLSearchParams({ url });
  if (fileName) params.set("file_name", fileName);
  return request<DocItem>(`/api/v1/kbs/${kbId}/documents/upload?${params.toString()}`, {
    method: "POST",
  });
}

export function apiDeleteDocument(kbId: string, docId: string) {
  return request(`/api/v1/kbs/${kbId}/documents/${docId}`, { method: "DELETE" });
}

export function apiReparseDocument(kbId: string, docId: string) {
  return request(`/api/v1/kbs/${kbId}/documents/${docId}/reparse`, { method: "POST" });
}

export async function apiDownloadDocument(kbId: string, docId: string, fileName: string) {
  const res = await fetch(`/api/v1/kbs/${kbId}/documents/${docId}/download`);
  if (!res.ok) throw new Error(`下载失败 (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName || "document.bin";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---- wiki ----

export interface WikiFolderItem {
  id: string;
  name: string;
  parent_id: string;
  page_count: number;
}

export interface WikiPageItem {
  id: string;
  slug: string;
  title: string;
  page_type: string;
  folder_id: string;
  summary?: string | null;
}

export interface WikiTree {
  kb_id: string;
  folders: WikiFolderItem[];
  pages: WikiPageItem[];
}

export interface WikiPageDetail {
  id: string;
  slug: string;
  title: string;
  page_type: string;
  content: string;
  summary?: string | null;
  source_refs: string[];
  folder_id: string;
  links: { slug: string; title: string; page_type: string }[];
  in_links: { slug: string; title: string; page_type: string }[];
  created_at?: string | null;
  updated_at?: string | null;
}

export function apiWikiTree(kbId: string) {
  return request<WikiTree>(`/api/v1/kbs/${kbId}/wiki`);
}

export interface WikiFolderNode {
  id: string;
  name: string;
  parent_id: string;
  child_count?: number;
  page_count?: number;
}

export interface WikiBranchData {
  kb_id: string;
  folder_id: string;
  folders: WikiFolderNode[];
  pages: WikiPageItem[];
  page: number;
  page_size: number;
  has_more: boolean;
  total_pages: number;
  total_folders: number;
}

/** 懒加载目录分支（对齐 WeKnora 侧栏按需展开）；folderId='' 表示根级 */
export function apiWikiBranch(kbId: string, folderId = "", page = 1, pageSize = 50) {
  const params = new URLSearchParams({
    folder_id: folderId,
    page: String(page),
    page_size: String(pageSize),
  });
  return request<WikiBranchData>(`/api/v1/kbs/${encodeURIComponent(kbId)}/wiki/branch?${params.toString()}`);
}

/** 全量目录元数据（轻量，深链定位父链用） */
export function apiWikiFolders(kbId: string) {
  return request<WikiFolderNode[]>(`/api/v1/kbs/${encodeURIComponent(kbId)}/wiki/folders`);
}

export interface WikiGraphNode {
  slug: string;
  title: string;
  page_type: string;
  link_count: number;
  summary?: string | null;
}

export interface WikiGraphEdge {
  source: string;
  target: string;
}

export interface WikiGraphData {
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
  meta: { mode: string; total: number; returned: number; truncated: boolean };
}

export function apiWikiGraph(
  kbId: string,
  params: { mode?: "overview" | "ego"; center?: string; depth?: number; limit?: number; types?: string[] } = {},
) {
  const q = new URLSearchParams();
  if (params.mode) q.set("mode", params.mode);
  if (params.center) q.set("center", params.center);
  if (params.depth !== undefined) q.set("depth", String(params.depth));
  if (params.limit !== undefined) q.set("limit", String(params.limit));
  if (params.types && params.types.length > 0) q.set("types", params.types.join(","));
  const qs = q.toString();
  return request<WikiGraphData>(`/api/v1/kbs/${kbId}/wiki/graph${qs ? `?${qs}` : ""}`);
}

// ---- wiki 管理（页面/目录 CRUD + 统计/检查/重建链接）----

export interface WikiStatsData {
  kb_id: string;
  total_pages: number;
  total_folders: number;
  total_links: number;
  pages_by_type: Record<string, number>;
  orphan_count: number;
}

export interface WikiLintIssue {
  slug: string;
  title: string;
  issue_type: string;
  detail: string;
}

export interface WikiLintData {
  kb_id: string;
  total_issues: number;
  issues: WikiLintIssue[];
  broken_link_count: number;
}

export function apiWikiStats(kbId: string) {
  return request<WikiStatsData>(`/api/v1/kbs/${kbId}/wiki/stats`);
}

export function apiWikiLint(kbId: string) {
  return request<WikiLintData>(`/api/v1/kbs/${kbId}/wiki/lint`);
}

export interface WikiLogItem {
  id: string;
  action: string;
  slug: string;
  title: string;
  detail: string;
  operator: string;
  created_at: string | null;
}

export function apiWikiLogs(kbId: string) {
  return request<{ total: number; items: WikiLogItem[] }>(`/api/v1/kbs/${kbId}/wiki/logs`);
}

export interface WikiIndexData {
  folder_tree: { id: string; name: string; parent_id: string }[];
  pages_by_type: Record<string, number>;
  recent_pages: {
    slug: string;
    title: string;
    page_type: string;
    folder_id: string;
    updated_at: string | null;
  }[];
  total_pages: number;
  total_folders: number;
}

export function apiWikiIndex(kbId: string) {
  return request<WikiIndexData>(`/api/v1/kbs/${kbId}/wiki/index`);
}

export interface WikiFeedbackItem {
  id: string;
  slug: string;
  user_id: string;
  feedback_type: string;
  content: string;
  status: string;
  created_at: string | null;
}

export function apiWikiSubmitFeedback(kbId: string, slug: string, feedbackType: string, content: string) {
  return request<{ id: string; feedback_type: string; status: string }>(
    `/api/v1/kbs/${kbId}/wiki/pages/${encodeURIComponent(slug)}/feedback`,
    { method: "POST", body: JSON.stringify({ feedback_type: feedbackType, content }) }
  );
}

export function apiWikiListFeedback(kbId: string, slug: string) {
  return request<{ total: number; items: WikiFeedbackItem[] }>(
    `/api/v1/kbs/${kbId}/wiki/pages/${encodeURIComponent(slug)}/feedback`
  );
}

export function apiWikiListAllFeedback(kbId: string, status?: string) {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<{ total: number; items: WikiFeedbackItem[] }>(
    `/api/v1/kbs/${kbId}/wiki/feedback${qs}`
  );
}

export function apiWikiUpdateFeedbackStatus(kbId: string, feedbackId: string, status: string) {
  return request<{ id: string; status: string }>(
    `/api/v1/kbs/${kbId}/wiki/feedback/${feedbackId}/status`,
    { method: "PUT", body: JSON.stringify({ status }) }
  );
}

export interface WikiSearchItem {
  slug: string;
  title: string;
  page_type: string;
  summary?: string | null;
  content_head?: string;
}

export interface WikiSearchData {
  query: string;
  total: number;
  items: WikiSearchItem[];
}

export function apiWikiSearch(kbId: string, q: string, limit = 20) {
  return request<WikiSearchData>(`/api/v1/kbs/${kbId}/wiki/search?q=${encodeURIComponent(q)}&limit=${limit}`);
}

export function apiWikiRebuildLinks(kbId: string) {
  return request<{ added_links: number }>(`/api/v1/kbs/${kbId}/wiki/rebuild-links`, { method: "POST" });
}

export function apiWikiCreatePage(
  kbId: string,
  data: { title: string; slug?: string; page_type?: string; content?: string; summary?: string; folder_id?: string },
) {
  return request<{ id: string; slug: string; title: string }>(`/api/v1/kbs/${kbId}/wiki/pages`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function apiWikiUpdatePage(
  kbId: string,
  slug: string,
  data: { title?: string; page_type?: string; content?: string; summary?: string; folder_id?: string; status?: string },
) {
  return request<{ id: string; slug: string }>(`/api/v1/kbs/${kbId}/wiki/pages/${encodeURIComponent(slug)}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function apiWikiDeletePage(kbId: string, slug: string) {
  return request(`/api/v1/kbs/${kbId}/wiki/pages/${encodeURIComponent(slug)}`, { method: "DELETE" });
}

export function apiWikiCreateFolder(kbId: string, data: { name: string; parent_id?: string }) {
  return request<{ id: string; name: string }>(`/api/v1/kbs/${kbId}/wiki/folders`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function apiWikiDeleteFolder(kbId: string, folderId: string) {
  return request(`/api/v1/kbs/${kbId}/wiki/folders/${folderId}`, { method: "DELETE" });
}

export function apiWikiPage(kbId: string, slug: string) {
  // Next 16 客户端导航(router.push)时 useParams 返回未解码的编码串，
  // 硬导航时返回解码中文 —— 先 decode 再 encode 幂等兼容两种输入
  // （纯中文无 % 序列时 decodeURIComponent 原样返回，不抛错）。
  let s = slug;
  try {
    s = decodeURIComponent(slug);
  } catch {
    // 仅当含非法 % 序列时进入（中文 slug 不会），保持原样
  }
  return request<WikiPageDetail>(`/api/v1/kbs/${kbId}/wiki/pages/${encodeURIComponent(s)}`);
}

// ---- search ----

export interface SearchHit {
  chunk_id: string;
  content: string;
  document_id: string;
  kb_id: string;
  score: number;
}

export function apiSearch(kbId: string, query: string, topK = 5) {
  return request<{ items: SearchHit[]; total: number }>(`/api/v1/kbs/${kbId}/search`, {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK, threshold: 0.2, embed_query: true }),
  });
}

// ---- agents ----

export interface AgentItem {
  id: string;
  name: string;
  description?: string | null;
  avatar?: string | null;
  is_builtin?: boolean;
  config?: Record<string, unknown>;
  created_at?: string | null;
}

export function apiListAgents(page = 1, pageSize = 20) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  return request<PageList<AgentItem>>(`/api/v1/agents?${params.toString()}`);
}

export function apiCreateAgent(payload: { name: string; description?: string; config?: Record<string, unknown> }) {
  return request<{ id: string }>("/api/v1/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateAgent(agentId: string, payload: { name?: string; description?: string; config?: Record<string, unknown> }) {
  return request(`/api/v1/agents/${encodeURIComponent(agentId)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function apiDeleteAgent(agentId: string) {
  return request(`/api/v1/agents/${agentId}`, { method: "DELETE" });
}

export function apiCopyAgent(agentId: string) {
  return request<{ id: string; name: string }>(`/api/v1/agents/${agentId}/copy`, {
    method: "POST",
  });
}

// ---- datasources (framework page) ----

export interface DatasourceItem {
  id?: string;
  dsName?: string;
  dsLabel?: string;
  dsType?: string;
  dsVersion?: string;
  dsCategory?: string;
  state?: string;
}

export function apiListDatasources(
  page = 1,
  pageSize = 10,
  opts: { keyword?: string; name?: string; label?: string; dsType?: string } = {},
) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (opts.keyword) params.set("keyword", opts.keyword);
  if (opts.name) params.set("name", opts.name);
  if (opts.label) params.set("label", opts.label);
  if (opts.dsType) params.set("dsType", opts.dsType);
  return request<{ items?: DatasourceItem[]; total?: number }>(
    `/api/v1/open/datasources?${params.toString()}`,
  );
}

// ---- 数据源管理（迁移 data-synth 完整功能） ----

export interface DsCategoryItem {
  id: string;
  categoryName: string;
  categoryLabel: string;
  sorted: number;
}

export interface DsTypeItem {
  id: string;
  dsType: string;
  dsTypeLabel: string;
  dsCategory: string;
  img?: string | null;
  sorted: number;
  isSupport?: string | null;
}

export interface DsVersionItem {
  id: string;
  dsType: string;
  versionName: string;
  versionValue: string;
  sorted: number;
}

export interface DsFormFieldItem {
  id: string;
  dsType: string;
  name: string;
  label: string;
  widget?: string | null;
  sorted: number;
  defaultValue?: string | null;
  invisible?: number | null;
  isConf?: number | null;
  options?: string | null;
  placeHold?: string | null;
  regex?: string | null;
  required?: number | null;
  tooltip?: string | null;
  validInfo?: string | null;
  dsVersion?: string | null;
}

export interface DsSavePayload {
  id?: string;
  name: string;
  label?: string | null;
  dsType: string;
  dsAcct?: string | null;
  dsAuth?: string | null;
  dsCategory?: string | null;
  dsVersion?: string | null;
  url?: string | null;
  dsConf?: string | null;
  state?: string | null;
}

export interface TeamDsMapItem {
  id: string;
  dsName: string;
  schemaName?: string | null;
  teamName: string;
  isProd?: string | null;
}

export function apiListDsCategories() {
  return request<DsCategoryItem[]>("/api/v1/open/datasources/categories");
}

export function apiListDsTypes(dsCategory?: string, search?: string) {
  const params = new URLSearchParams();
  if (dsCategory) params.set("dsCategory", dsCategory);
  if (search) params.set("search", search);
  const qs = params.toString();
  return request<DsTypeItem[]>(`/api/v1/open/datasources/types${qs ? `?${qs}` : ""}`);
}

export function apiGetDsTypeDetail(dsType: string) {
  return request<DsTypeItem>(`/api/v1/open/datasources/types/${encodeURIComponent(dsType)}`);
}

export function apiListDsVersions(dsType?: string) {
  const qs = dsType ? `?dsType=${encodeURIComponent(dsType)}` : "";
  return request<DsVersionItem[]>(`/api/v1/open/datasources/versions${qs}`);
}

export function apiListDsFormFields(dsType: string, dsVersion?: string) {
  const params = new URLSearchParams({ dsType });
  if (dsVersion) params.set("dsVersion", dsVersion);
  return request<DsFormFieldItem[]>(`/api/v1/open/datasources/form-fields?${params.toString()}`);
}

export function apiCreateDatasource(payload: DsSavePayload) {
  return request<{ id?: string; created?: boolean }>("/api/v1/open/datasources", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateDatasource(id: string, payload: DsSavePayload) {
  return request<{ id?: string; updated?: boolean }>(
    `/api/v1/open/datasources/${encodeURIComponent(id)}`,
    { method: "PUT", body: JSON.stringify(payload) },
  );
}

export function apiDeleteDatasource(id: string) {
  return request<{ id?: string; deleted?: boolean }>(
    `/api/v1/open/datasources/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

/** 数据源详情（编辑时载入已有配置；敏感字段由后端脱敏返回）。 */
export function apiGetDatasource(id: string) {
  return request<{
    id: string;
    name: string;
    label?: string | null;
    dsType?: string | null;
    dsCategory?: string | null;
    dsVersion?: string | null;
    dsAcct?: string | null;
    dsAuth?: string | null;
    url?: string | null;
    dsConf?: string | null;
    state?: string | null;
  }>(`/api/v1/open/datasources/${encodeURIComponent(id)}`);
}

/** 测试数据源连接（dsId 或原始参数）。 */
export function apiTestDatasource(payload: Record<string, unknown>) {
  return request<{ success?: boolean; message?: string }>("/api/v1/open/datasources/test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---- Neo4j 知识图谱（后端 api/routers/graph.py） ----

export interface GraphNode {
  name: string;
  entity_type?: string;
  /** 度（邻接数），后端 ego/overview 可能返回 */
  degree?: number;
  /** 描述（部分实体带摘要） */
  description?: string;
  /** 关联 chunk（来源 chunk 列表） */
  chunks?: string[];
  properties?: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  /** 关系类型（后端 overview/ego 返回） */
  type?: string;
  relation?: string;
  properties?: Record<string, unknown>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphHealth {
  enabled: boolean;
  available?: boolean;
  [k: string]: unknown;
}

export interface GraphEntityTypeStat {
  type: string;
  count?: number;
}

export interface GraphStats {
  nodes: number;
  edges: number;
  entity_types?: GraphEntityTypeStat[];
  relation_types?: GraphEntityTypeStat[];
  [k: string]: unknown;
}

export function apiGraphHealth() {
  return request<GraphHealth>("/api/v1/graph/health");
}

export function apiKbGraph(kbId: string, params?: { limit?: number }) {
  const qs = params?.limit ? `?limit=${params.limit}` : "";
  return request<GraphData>(`/api/v1/kbs/${kbId}/graph${qs}`);
}

export function apiKbGraphStats(kbId: string) {
  return request<GraphStats>(`/api/v1/kbs/${kbId}/graph/stats`);
}

export function apiKbGraphEgo(kbId: string, center: string, depth = 1, limit = 200) {
  const qs = `?center=${encodeURIComponent(center)}&depth=${depth}&limit=${limit}`;
  return request<GraphData>(`/api/v1/kbs/${kbId}/graph/ego${qs}`);
}

export function apiKbGraphSearch(kbId: string, q: string, limit = 100) {
  return request<GraphData>(`/api/v1/kbs/${kbId}/graph/search`, {
    method: "POST",
    body: JSON.stringify({ q, limit }),
  });
}

// ---- 数据查询（DataGrid，迁移 data-synth） ----

export interface DatagridDsItem {
  dsName: string;
  dsLabel?: string;
  dsType?: string;
  dsId?: string;
  schema?: string;
}

export interface DatagridColumn {
  name?: string;
  label?: string;
  data_type?: string;
  is_nullable?: string | number | boolean;
  default_value?: string | null;
  comment?: string | null;
  type?: string;
}

export interface DatagridExecuteResult {
  success: boolean;
  msg?: string;
  resultType?: "select" | "execute";
  columns?: string[];
  rows?: Array<Record<string, unknown>>;
  rowcount?: number | null;
  truncated?: boolean;
}

export function apiDatagridDatasources() {
  return request<DatagridDsItem[]>("/api/v1/datagrid/datasources");
}

export function apiDatagridExecute(dsName: string, sql: string, schemaName?: string) {
  return request<DatagridExecuteResult[]>("/api/v1/datagrid/execute", {
    method: "POST",
    body: JSON.stringify({ dsName, sql, schemaName }),
  });
}

export interface DatagridMetaResult {
  success: boolean;
  msg?: string;
  columns?: string[];
  rows?: Array<Record<string, unknown>>;
  total?: number;
  ddl?: string;
}

// 注意：/tables /columns /ddl /table-info 返回裸业务体（顶层 rows/ddl，不包 data），
// 与 /datasources、/execute（包 data）不同；返回类型按真实结构声明。
export function apiDatagridTables(dsName: string, schemaName?: string, searchName?: string): Promise<DatagridMetaResult> {
  return request<DatagridMetaResult>("/api/v1/datagrid/tables", {
    method: "POST",
    body: JSON.stringify({ dsName, schemaName, searchName }),
  }) as unknown as Promise<DatagridMetaResult>;
}

export function apiDatagridColumns(dsName: string, tableName: string, schemaName?: string): Promise<DatagridMetaResult & { rows?: DatagridColumn[] }> {
  return request<{ success: boolean; msg?: string; rows?: DatagridColumn[] }>(
    "/api/v1/datagrid/columns",
    { method: "POST", body: JSON.stringify({ dsName, tableName, schemaName }) },
  ) as unknown as Promise<DatagridMetaResult & { rows?: DatagridColumn[] }>;
}

export function apiDatagridDdl(dsName: string, tableName: string, schemaName?: string): Promise<DatagridMetaResult> {
  return request<{ success: boolean; ddl?: string; msg?: string }>("/api/v1/datagrid/ddl", {
    method: "POST",
    body: JSON.stringify({ dsName, tableName, schemaName }),
  }) as unknown as Promise<DatagridMetaResult>;
}

export function apiDatagridTableInfo(dsName: string, tableName: string, schemaName?: string): Promise<DatagridMetaResult> {
  return request<{ success: boolean; rows?: Array<Record<string, unknown>>; msg?: string }>(
    "/api/v1/datagrid/table-info",
    { method: "POST", body: JSON.stringify({ dsName, tableName, schemaName }) },
  ) as unknown as Promise<DatagridMetaResult>;
}

export function apiDatagridViews(dsName: string, schemaName?: string): Promise<DatagridMetaResult> {
  return request<{ success: boolean; rows?: Array<Record<string, unknown>> }>("/api/v1/datagrid/views", {
    method: "POST",
    body: JSON.stringify({ dsName, schemaName }),
  }) as unknown as Promise<DatagridMetaResult>;
}

export function apiListTeamDsAuth(teamName: string) {
  return request<TeamDsMapItem[]>(
    `/api/v1/open/datasources/team-auth/${encodeURIComponent(teamName)}`,
  );
}

export function apiSaveTeamDsAuth(teamName: string, items: TeamDsMapItem[]) {
  return request<{ added?: number; removed?: number; total?: number }>(
    `/api/v1/open/datasources/team-auth/${encodeURIComponent(teamName)}`,
    { method: "PUT", body: JSON.stringify(items) },
  );
}

// ---- system admin (read-only) ----

export interface SysUserItem {
  id?: string;
  user_id?: string;
  user_name?: string;
  email?: string | null;
  phone?: string | null;
  default_team?: string | null;
  state?: string | null;
  create_dt?: string | null;
}

export interface SysRoleItem {
  role_id?: string;
  role_name?: string;
  role_descr?: string | null;
  role_type?: string;
  state?: string;
}

export interface SysTeamItem {
  team_id?: string;
  team_name?: string;
  label?: string | null;
  descr?: string | null;
  parent_team_name?: string | null;
  state?: string | null;
}

export interface SysMenuItem {
  menu_id?: string;
  menu_name?: string;
  menu_label?: string | null;
  menu_type?: string | null;
  menu_icon?: string | null;
  route?: string | null;
  parent_id?: string | null;
  sort_num?: number | null;
  state?: string | null;
  menu_descr?: string | null;
}

export interface SysLogItem {
  id?: string;
  user_name?: string | null;
  user_id?: string | null;
  team_name?: string | null;
  oper_type?: string | null;
  oper_content?: string | null;
  oper_url?: string | null;
  oper_time?: string | null;
}

export function apiListUsers(page = 1, pageSize = 20, keyword = "") {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (keyword) params.set("keyword", keyword);
  return request<PageList<SysUserItem>>(`/api/v1/system/users?${params.toString()}`);
}

export function apiListRoles(page = 1, pageSize = 20) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  return request<PageList<SysRoleItem>>(`/api/v1/system/roles?${params.toString()}`);
}

export function apiListTeams(page = 1, pageSize = 20) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  return request<PageList<SysTeamItem>>(`/api/v1/system/teams?${params.toString()}`);
}

export function apiListMenus() {
  return request<{ items: SysMenuItem[]; total: number }>("/api/v1/system/menus");
}

export function apiListOperationLogs(page = 1, pageSize = 20, keyword = "") {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (keyword) params.set("keyword", keyword);
  return request<PageList<SysLogItem>>(`/api/v1/system/operation-logs?${params.toString()}`);
}

// ---------------------------------------------------------------------------
// 系统管理写接口：角色 CRUD / 角色菜单授权 / 角色用户配置 / 菜单 CRUD / my-menus
// ---------------------------------------------------------------------------

export interface SysRoleWrite {
  role_id?: string;
  role_name: string;
  role_type: string; // plat-mgr | team-role
  role_descr?: string;
  state?: string;
}

export interface SysMenuWrite {
  menu_id?: string;
  menu_name: string;
  menu_label: string;
  parent_id?: string | null;
  sort_num?: number;
  menu_icon?: string | null;
  state?: string;
  menu_type?: string;
  route?: string | null;
  menu_descr?: string | null;
  menu_ext_conf?: Record<string, unknown> | string | null;
}

/** 当前用户可见菜单（Sider 渲染用；plat-mgr / AUTH_ADMIN_USERS 全量） */
export function apiMyMenus() {
  return request<{ items: SysMenuItem[]; total: number }>("/api/v1/system/my-menus");
}

export function apiCreateRole(payload: SysRoleWrite) {
  return request<{ success: boolean; message?: string }>("/api/v1/system/roles", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateRole(roleId: string, payload: SysRoleWrite) {
  return request<{ success: boolean; message?: string }>(
    `/api/v1/system/roles/${encodeURIComponent(roleId)}`,
    { method: "PUT", body: JSON.stringify(payload) },
  );
}

export function apiDeleteRole(roleId: string) {
  return request<{ success: boolean; message?: string }>(
    `/api/v1/system/roles/${encodeURIComponent(roleId)}`,
    { method: "DELETE" },
  );
}

export function apiGetRoleMenus(roleId: string) {
  return request<{ menuIds: string[] }>(
    `/api/v1/system/roles/${encodeURIComponent(roleId)}/menus`,
  );
}

export function apiSaveRoleMenus(roleId: string, menuIds: string[]) {
  return request<{ success: boolean; message?: string }>(
    `/api/v1/system/roles/${encodeURIComponent(roleId)}/menus`,
    { method: "PUT", body: JSON.stringify({ menuIds }) },
  );
}

export function apiGetRoleUsers(roleId: string) {
  return request<{ userIds: string[] }>(
    `/api/v1/system/roles/${encodeURIComponent(roleId)}/users`,
  );
}

export function apiSaveRoleUsers(roleId: string, userIds: string[]) {
  return request<{ success: boolean; message?: string }>(
    `/api/v1/system/roles/${encodeURIComponent(roleId)}/users`,
    { method: "PUT", body: JSON.stringify({ userIds }) },
  );
}

export function apiCreateMenu(payload: SysMenuWrite) {
  return request<{ success: boolean; message?: string }>("/api/v1/system/menus", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateMenu(menuId: string, payload: SysMenuWrite) {
  return request<{ success: boolean; message?: string }>(
    `/api/v1/system/menus/${encodeURIComponent(menuId)}`,
    { method: "PUT", body: JSON.stringify(payload) },
  );
}

export function apiDeleteMenu(menuId: string) {
  return request<{ success: boolean; message?: string }>(
    `/api/v1/system/menus/${encodeURIComponent(menuId)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// 模型配置 (system/model-config)
// ---------------------------------------------------------------------------

export interface ModelConfigItem {
  code: string;
  label: string;
  description: string;
  value: string;
  sensitive: boolean;
  source: "db" | "env";
}

export function apiGetModelConfig() {
  return request<{ items: ModelConfigItem[] }>("/api/v1/system/model-config");
}

export function apiSaveModelConfig(items: { code: string; value: string | null }[]) {
  return request<{ saved: string[]; count: number }>("/api/v1/system/model-config", {
    method: "PUT",
    body: JSON.stringify({ items }),
  });
}

export function apiTestModelEndpoint(payload: { base_url: string; api_key: string; model: string }) {
  return request<{ ok: boolean; kind?: string; models?: string[]; model_matches?: boolean; error?: string }>(
    "/api/v1/system/model-config/test",
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export interface ModelTestResult {
  ok?: boolean;
  kind?: string;
  models?: string[];
  model_matches?: boolean;
  error?: string;
}

// -----------------------------------------------------------------------------
// 分级模型注册表 (/models) —— personal / team / system 三级
// -----------------------------------------------------------------------------

export type ModelScope = "personal" | "team" | "system";
export type ModelType = "chat" | "embedding" | "rerank" | "vllm" | "asr";

export interface ModelItem {
  id: string;
  scope: ModelScope;
  name: string;
  display_name: string;
  type: ModelType;
  source: string;
  provider: string;
  description: string;
  base_url: string;
  interface_type: string;
  dimension: number | null;
  supports_vision: boolean;
  custom_headers: Record<string, string>;
  owner_user_id: string;
  owner_team_name: string;
  is_default: boolean;
  max_concurrency: number | null;
  thinking_control: string | null;
  status: string;
  api_key_masked: string;
  api_key_configured: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ModelProvider {
  value: string;
  label: string;
  modelTypes: string[];
  defaultUrls: Record<string, string>;
}

export interface ModelDebugResult {
  ok?: boolean;
  elapsed_ms?: number;
  request?: Record<string, unknown>;
  raw_response?: unknown;
  observations?: Record<string, unknown>;
  error?: string;
}

export interface ModelDebugOptions {
  system_prompt?: string;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  thinking?: boolean;
}

export interface ModelDebugPayload {
  input?: string;
  documents?: string[];
  options?: ModelDebugOptions;
  file?: File;
}

export function apiListModels(params?: { type?: string; scope?: string }) {
  const qs = new URLSearchParams();
  if (params?.type) qs.set("type", params.type);
  if (params?.scope) qs.set("scope", params.scope);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<{ items: ModelItem[]; is_admin: boolean }>(`/api/v1/models${suffix}`);
}

export function apiListModelProviders() {
  return request<{ items: ModelProvider[] }>("/api/v1/models/providers");
}

export function apiGetModel(id: string) {
  return request<{ item: ModelItem }>(`/api/v1/models/${id}`);
}

export interface ModelPayload {
  scope: ModelScope;
  name: string;
  display_name?: string | null;
  type: ModelType;
  source?: string;
  provider?: string | null;
  description?: string | null;
  base_url?: string | null;
  api_key?: string | null;
  interface_type?: string | null;
  dimension?: number | null;
  supports_vision?: boolean;
  custom_headers?: Record<string, string> | null;
  owner_team_name?: string | null;
  is_default?: boolean;
  max_concurrency?: number | null;
  thinking_control?: string | null;
}

export function apiCreateModel(payload: ModelPayload) {
  return request<{ item: ModelItem }>("/api/v1/models", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateModel(id: string, payload: Partial<ModelPayload>) {
  return request<{ item: ModelItem }>(`/api/v1/models/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export interface ModelExportItem {
  scope: string;
  name: string;
  display_name?: string;
  type: string;
  source?: string;
  provider?: string;
  description?: string;
  base_url?: string;
  api_key?: string;
  interface_type?: string;
  dimension?: number;
  supports_vision?: boolean;
  custom_headers?: Record<string, string>;
  is_default?: boolean;
  max_concurrency?: number;
  thinking_control?: string;
}

export function apiModelExport() {
  return request<{ models: ModelExportItem[]; count: number }>("/api/v1/models/export");
}

export function apiModelImport(models: ModelExportItem[], mode = "upsert") {
  return request<{ created: number; updated: number; errors: Array<{ index: number; name: string; error: string }> }>(
    "/api/v1/models/import",
    {
      method: "POST",
      body: JSON.stringify({ mode, models }),
    },
  );
}

export function apiDeleteModel(id: string) {
  return request<{ deleted: boolean }>(`/api/v1/models/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// 本体 Schema（抽取分类结构配置）
// ---------------------------------------------------------------------------
export interface OntologyCategory {
  id: string;
  schema_name: string;
  schema_label: string;
  schema_desc: string | null;
  dimension: "business" | "rule";
  cat_no: number;
  cat_name: string;
  cat_label: string;
  prompt_hint: string | null;
  neo4j_edge: string | null;
  state: string;
  sort: number;
  created_at: string | null;
}

export interface OntologySchemaGroup {
  schema_name: string;
  schema_label: string;
  schema_desc: string | null;
  business: OntologyCategory[];
  rule: OntologyCategory[];
  total: number;
}

export interface OntologyKbBinding {
  kb_id: string;
  schema_name: string | null;
  schema: OntologySchemaGroup | null;
}

export interface OntologyCategoryPayload {
  schema_name: string;
  schema_label?: string;
  schema_desc?: string | null;
  dimension: "business" | "rule";
  cat_no: number;
  cat_name: string;
  cat_label?: string;
  prompt_hint?: string | null;
  neo4j_edge?: string | null;
  state?: string;
}

export function apiListOntologySchemas(schemaName?: string, dimension?: string) {
  const q = new URLSearchParams();
  if (schemaName) q.set("schema_name", schemaName);
  if (dimension) q.set("dimension", dimension);
  const qs = q.toString();
  return request<{ schemas: OntologySchemaGroup[] }>(`/api/v1/ontology-schemas${qs ? `?${qs}` : ""}`);
}

export function apiCreateOntologyCategory(payload: OntologyCategoryPayload) {
  return request<{ item: OntologyCategory }>("/api/v1/ontology-schemas", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateOntologyCategory(id: string, payload: OntologyCategoryPayload) {
  return request<{ item: OntologyCategory }>(`/api/v1/ontology-schemas/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function apiDeleteOntologyCategory(id: string) {
  return request<{ deleted: string }>(`/api/v1/ontology-schemas/${id}`, { method: "DELETE" });
}

export function apiCopyOntologySchema(sourceSchema: string, newSchemaName: string, newSchemaLabel = "") {
  return request<{ copied: number; schema_name: string }>("/api/v1/ontology-schemas/copy", {
    method: "POST",
    body: JSON.stringify({ source_schema: sourceSchema, new_schema_name: newSchemaName, new_schema_label: newSchemaLabel }),
  });
}

export function apiGetKbOntologySchema(kbId: string) {
  return request<OntologyKbBinding>(`/api/v1/kbs/${kbId}/ontology-schema`);
}

export function apiBindKbOntologySchema(kbId: string, schemaName: string | null) {
  return request<{ kb_id: string; schema_name: string | null }>(`/api/v1/kbs/${kbId}/ontology-schema`, {
    method: "PUT",
    body: JSON.stringify({ schema_name: schemaName }),
  });
}

export function apiCopyModel(id: string) {
  return request<{ item: ModelItem }>(`/api/v1/models/${id}/copy`, { method: "POST" });
}

export function apiSetModelDefault(id: string) {
  return request<{ item: ModelItem }>(`/api/v1/models/${id}/default`, { method: "POST" });
}

export function apiTestModel(payload: { base_url: string; api_key: string; model: string }) {
  return request<ModelTestResult>("/api/v1/models/test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiDebugModel(id: string, payload: ModelDebugPayload) {
  const fd = new FormData();
  if (payload.input) fd.append("input", payload.input);
  if (payload.documents?.length) fd.append("documents", JSON.stringify(payload.documents));
  if (payload.options) fd.append("options", JSON.stringify(payload.options));
  if (payload.file) fd.append("file", payload.file);
  return request<ModelDebugResult>(`/api/v1/models/${id}/debug`, {
    method: "POST",
    body: fd,
  });
}

// ---------------------------------------------------------------------------
// MCP 服务器管理 (system/mcp-servers, 代理 agent-gateway)
// ---------------------------------------------------------------------------

export interface McpServerItem {
  name: string;
  type?: string;
  url?: string;
  headers?: Record<string, string>;
  [k: string]: unknown;
}

export function apiListMcpServers() {
  return request<{ data: McpServerItem[]; source?: string; count?: number }>("/api/v1/system/mcp-servers");
}

export function apiSaveMcpServers(servers: McpServerItem[], verify = false, force = false) {
  return request(`/api/v1/system/mcp-servers${verify ? "?verify=true" : ""}${force ? (verify ? "&force=true" : "?force=true") : ""}`, {
    method: "PUT",
    body: JSON.stringify({ servers, verify, force }),
  });
}

export function apiTestMcpServer(item: McpServerItem) {
  return request<{ ok?: boolean; tool_count?: number; name?: string; error?: string }>("/api/v1/system/mcp-servers/test", {
    method: "POST",
    body: JSON.stringify(item),
  });
}

export function apiDeleteMcpServer(name: string) {
  return request(`/api/v1/system/mcp-servers/${encodeURIComponent(name)}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// 技能管理 (system/skills, 代理 agent-gateway)
// ---------------------------------------------------------------------------

export interface SkillItem {
  name: string;
  description?: string;
  scripts?: string[];
  [k: string]: unknown;
}

export function apiListSkills() {
  return request<{ data: SkillItem[] }>("/api/v1/system/skills");
}

export function apiInstallSkill(file: File, name?: string) {
  const form = new FormData();
  form.append("file", file);
  if (name) form.append("name", name);
  return request<{ name?: string; installed?: boolean; skill_count_after?: number; scripts?: string[]; error?: string }>(
    "/api/v1/system/skills/install",
    { method: "POST", body: form },
  );
}

export function apiGetSkillDetail(name: string) {
  return request<{ name?: string; description?: string; content?: string; files?: string[] }>(
    `/api/v1/system/skills/${encodeURIComponent(name)}`,
  );
}

export function apiDeleteSkill(name: string) {
  return request(`/api/v1/system/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
}

// -----------------------------------------------------------------------------
// 分级 MCP / 技能注册表 (/mcps, /skills) —— personal / team / system 三级
// -----------------------------------------------------------------------------

export interface McpRegistryItem {
  id: string;
  scope: ModelScope;
  name: string;
  type: string;
  url: string;
  headers: Record<string, string>;
  command: string;
  args: string[];
  env: Record<string, string>;
  owner_user_id: string;
  owner_team_name: string;
  enabled: boolean;
  state: string;
}

export interface McpToolInfo {
  name: string;
  description: string;
  params: unknown[];
}

export interface McpTestResult {
  ok: boolean;
  name?: string;
  transport?: string;
  tool_count?: number;
  tools?: McpToolInfo[];
  error?: string;
  elapsed_ms?: number;
}

export function apiTestMcp(item: McpRegistryItem) {
  return request<McpTestResult>("/api/v1/mcps/test", {
    method: "POST",
    body: JSON.stringify({
      server: {
        name: item.name,
        type: item.type || "streamable_http",
        url: item.url || "",
        headers: item.headers || {},
        command: item.command || "",
        args: item.args || [],
        env: item.env || {},
      },
    }),
  });
}

export interface SkillRegistryItem {
  id: string;
  scope: ModelScope;
  name: string;
  description: string;
  version: string;
  package_size: number;
  owner_user_id: string;
  owner_team_name: string;
  state: string;
  created_at: string | null;
  updated_at: string | null;
}

export function apiListMcps(scope?: string) {
  const qs = scope ? `?scope=${scope}` : "";
  return request<{ items: McpRegistryItem[]; is_admin: boolean }>(`/api/v1/mcps${qs}`);
}

export function apiCreateMcp(payload: Record<string, unknown>) {
  return request<{ item: McpRegistryItem }>("/api/v1/mcps", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateMcp(id: string, payload: Record<string, unknown>) {
  return request<{ item: McpRegistryItem }>(`/api/v1/mcps/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function apiDeleteMcp(id: string) {
  return request<{ deleted: boolean }>(`/api/v1/mcps/${id}`, { method: "DELETE" });
}

// -----------------------------------------------------------------------------
// 联网搜索提供方注册表 (/websearch) —— personal / team / system 三级
// -----------------------------------------------------------------------------

export type WebSearchProviderType = "tavily" | "serper" | "bing" | "exa" | "generic";

export interface WebSearchProviderItem {
  id: string;
  scope: ModelScope;
  name: string;
  description: string;
  provider_type: WebSearchProviderType;
  /** 读接口为脱敏值（****），保存时回传脱敏值表示保持原 Key */
  api_key: string;
  base_url: string;
  extra_config: Record<string, unknown>;
  owner_user_id: string;
  owner_team_name: string;
  enabled: boolean;
  state: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface WebSearchResult {
  title: string;
  url: string;
  snippet: string;
}

export function apiListWebsearchProviders(scope?: string) {
  const qs = scope ? `?scope=${scope}` : "";
  return request<{ items: WebSearchProviderItem[]; is_admin: boolean }>(`/api/v1/websearch${qs}`);
}

export function apiCreateWebsearchProvider(payload: Record<string, unknown>) {
  return request<{ item: WebSearchProviderItem }>("/api/v1/websearch", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateWebsearchProvider(id: string, payload: Record<string, unknown>) {
  return request<{ item: WebSearchProviderItem }>(`/api/v1/websearch/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function apiDeleteWebsearchProvider(id: string) {
  return request<{ deleted: boolean }>(`/api/v1/websearch/${id}`, { method: "DELETE" });
}

export function apiWebsearchSearch(id: string, query: string, maxResults = 5) {
  return request<{ results: WebSearchResult[] }>(`/api/v1/websearch/${id}/search`, {
    method: "POST",
    body: JSON.stringify({ query, max_results: maxResults }),
  });
}

export function apiListSkillsRegistry(scope?: string) {
  const qs = scope ? `?scope=${scope}` : "";
  return request<{ items: SkillRegistryItem[]; is_admin: boolean }>(`/api/v1/skills${qs}`);
}

export function apiInstallSkillRegistry(file: File, scope: string) {
  const form = new FormData();
  form.append("file", file);
  form.append("scope", scope);
  return request<{ item: SkillRegistryItem }>("/api/v1/skills/install", {
    method: "POST",
    body: form,
  });
}

export function apiDeleteSkillRegistry(id: string) {
  return request<{ deleted: boolean }>(`/api/v1/skills/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// 会话管理 (sessions)
// ---------------------------------------------------------------------------

export interface ChatSessionItem {
  id: string;
  kb_id?: string | null;
  agent_id?: string | null;
  title: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

export interface ChatRefItem {
  chunk_id: string;
  score: number;
  content?: string;
  document_id?: string;
  kb_id?: string;
  meta?: Record<string, string>;
}

export interface ChatMessageItem {
  id: string;
  role: "user" | "assistant";
  content: string;
  refs?: ChatRefItem[];
  thinking?: string | null;
  created_at: string;
}

export interface QaStreamEvent {
  type: string;
  stream_id?: string;
  hits?: ChatRefItem[];
  text?: string;
  message?: string;
}

export function apiCreateSession(kb_id?: string | null, title?: string) {
  return request<ChatSessionItem>("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ kb_id: kb_id || null, title: title || null }),
  });
}

export function apiListSessions(page = 1, pageSize = 50) {
  return request<{ items: ChatSessionItem[]; total: number }>(
    `/api/v1/sessions?page=${page}&page_size=${pageSize}`,
  );
}

export function apiUpdateSession(
  sessionId: string,
  patch: { title?: string; kb_id?: string | null; pinned?: boolean },
) {
  return request<ChatSessionItem>(`/api/v1/sessions/${sessionId}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

export function apiDeleteSession(sessionId: string) {
  return request(`/api/v1/sessions/${sessionId}`, { method: "DELETE" });
}

export function apiBatchDeleteSessions(sessionIds: string[]) {
  return request<{ deleted: number }>("/api/v1/sessions/batch-delete", {
    method: "POST",
    body: JSON.stringify({ session_ids: sessionIds }),
  });
}

export function apiClearSessionMessages(sessionId: string) {
  return request(`/api/v1/sessions/${sessionId}/messages`, { method: "DELETE" });
}

export function apiGenerateTitle(sessionId: string) {
  return request<{ title: string }>(`/api/v1/sessions/${sessionId}/generate-title`, { method: "POST" });
}

export function apiRecommendQuestions(kbId?: string | null, count = 3) {
  return request<{ questions: string[] }>("/api/v1/sessions/recommendations", {
    method: "POST",
    body: JSON.stringify({ kb_id: kbId || null, count }),
  });
}

export function apiFollowUp(sessionId: string, lastAnswer: string, count = 3) {
  return request<{ questions: string[] }>("/api/v1/sessions/follow-up", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, last_answer: lastAnswer, count }),
  });
}

export function apiLoadSessionMessages(sessionId: string) {
  return request<{ items: ChatMessageItem[] }>(`/api/v1/sessions/${sessionId}/messages`);
}

export function apiDeleteSessionMessage(sessionId: string, messageId: string) {
  return request(`/api/v1/sessions/${sessionId}/messages/${messageId}`, { method: "DELETE" });
}

export function apiSearchMessages(keyword: string, limit = 20) {
  return request<{ items: (ChatMessageItem & { session_id: string; session_title: string })[] }>(
    "/api/v1/sessions/search",
    { method: "POST", body: JSON.stringify({ keyword, limit }) },
  );
}

export interface ChatAttachmentItem {
  id: string;
  session_id: string;
  file_name: string;
  file_ext: string;
  media_type?: string;
  file_size: number;
  created_at: string;
}

export function apiListAttachments(sessionId: string) {
  return request<{ items: ChatAttachmentItem[] }>(`/api/v1/sessions/${sessionId}/attachments`);
}

export function apiUploadAttachment(sessionId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<ChatAttachmentItem>(`/api/v1/sessions/${sessionId}/attachments`, {
    method: "POST",
    body: form,
  });
}

export function apiDeleteAttachment(sessionId: string, attachmentId: string) {
  return request(`/api/v1/sessions/${sessionId}/attachments/${attachmentId}`, { method: "DELETE" });
}

export function apiCreateAgentSession(agentId: string, kbId?: string | null, title?: string) {
  return request<ChatSessionItem>("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, kb_id: kbId || null, title: title || null }),
  });
}

// ---- task monitor (任务监控) ----

export interface JobItem {
  id: string;
  task_id?: string | null;
  task_class?: string | null;
  queue_name?: string | null;
  trigger_type?: string | null;
  state?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  duration_ms?: number | null;
  error_message?: string | null;
  log_path?: string | null;
  create_time?: string | null;
  params?: Record<string, unknown>;
}

export function apiListJobs(params: {
  page?: number;
  page_size?: number;
  task_class?: string;
  state?: string;
  keyword?: string;
  queue_name?: string;
} = {}) {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.page_size) q.set("page_size", String(params.page_size));
  if (params.task_class) q.set("task_class", params.task_class);
  if (params.state) q.set("state", params.state);
  if (params.keyword) q.set("keyword", params.keyword);
  if (params.queue_name) q.set("queue_name", params.queue_name);
  const qs = q.toString();
  return request<{ items: JobItem[]; total: number; page: number; page_size: number }>(
    `/api/v1/jobs${qs ? `?${qs}` : ""}`,
  );
}

export function apiGetJob(jobId: string) {
  return request<JobItem>(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
}

export interface AgentTraceSpan {
  trace_id?: string;
  span_id?: string;
  name?: string;
  type?: string;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number;
  span_data?: Record<string, unknown>;
}

export interface AgentTraceData {
  spans: AgentTraceSpan[];
  has_trace: boolean;
  summary?: { span_count: number; duration_ms: number; llm_calls: number; tools: string[] };
}

export function apiGetJobTrace(jobId: string) {
  return request<AgentTraceData>(`/api/v1/jobs/${encodeURIComponent(jobId)}/trace`);
}

// ---- 我的团队 / 切换团队 / 默认团队（对齐 data-synth team-actions） ----

export interface UserTeamItem {
  teamName: string;
  teamId: string;
  teamLabel: string;
}

export function apiMyTeams() {
  return request<UserTeamItem[]>("/api/v1/system/my-teams");
}

export function apiSwitchTeam(teamName: string) {
  return request<{ identity_cookie?: string } & Identity>(
    "/api/v1/system/switch-team",
    { method: "POST", body: JSON.stringify({ teamName }) },
  );
}

export function apiGetDefaultTeam() {
  return request<string>("/api/v1/system/default-team");
}

export function apiSetDefaultTeam(teamName: string) {
  return request<{ success: boolean; message?: string }>(
    "/api/v1/system/default-team",
    { method: "POST", body: JSON.stringify({ teamName }) },
  );
}

// ---- 通知（系统消息，modo_system_message） ----

export interface NotificationItem {
  id: string;
  title: string;
  content: string;
  type?: string;
  is_read?: string;
  priority?: string;
  link_url?: string | null;
  sender_id?: string | null;
  create_date?: string;
}

export function apiNotificationUnreadCount() {
  return request<{ unread_count: number }>("/api/v1/system/notifications/unread-count");
}

export function apiListNotifications(params: { tab?: "unread" | "all"; page?: number; page_size?: number } = {}) {
  const q = new URLSearchParams();
  q.set("tab", params.tab || "unread");
  if (params.page) q.set("page", String(params.page));
  if (params.page_size) q.set("page_size", String(params.page_size));
  return request<{ items: NotificationItem[]; total: number }>(
    `/api/v1/system/notifications?${q.toString()}`,
  );
}

export function apiMarkNotificationRead(messageId: string) {
  return request<{ success: boolean; message?: string }>(
    `/api/v1/system/notifications/${encodeURIComponent(messageId)}/read`,
    { method: "POST" },
  );
}

export function apiMarkAllNotificationsRead() {
  return request<{ success: boolean; message?: string }>(
    "/api/v1/system/notifications/read-all",
    { method: "POST" },
  );
}

// ---- 菜单图标目录 ----

export function apiListMenuIcons() {
  return request<string[]>("/api/v1/system/icons");
}

// ---- 登录页运行时认证配置（对齐 data-synth /api/open/auth-mode） ----

export interface AuthModeConfig {
  authMode: "local" | "sso";
  ssoEnabled: boolean;
  appLogo?: string;
  defaultRedirectPath?: string;
}

export function apiAuthMode() {
  return request<AuthModeConfig>("/api/v1/auth/auth-mode");
}

// ---- 任务监控统计 / 队列 / 停止 / 删除（对齐 data-synth JobStatistics） ----

export interface JobStatistics {
  total: number;
  running: number;
  success: number;
  failed: number;
  stopped: number;
  queued: number;
}

export function apiJobStatistics() {
  return request<JobStatistics>("/api/v1/jobs/statistics");
}

export interface JobQueueOption {
  queueName: string;
  queueLabel?: string | null;
}

export function apiJobQueues() {
  return request<JobQueueOption[]>("/api/v1/jobs/queues");
}

export function apiStopJob(jobId: string) {
  return request<{ id: string; state: string }>(
    `/api/v1/jobs/${encodeURIComponent(jobId)}/stop`,
    { method: "POST" },
  );
}

export function apiDeleteJob(jobId: string) {
  return request<{ id: string; deleted: boolean }>(
    `/api/v1/jobs/${encodeURIComponent(jobId)}`,
    { method: "DELETE" },
  );
}

// ---- 任务管理（定时任务 modo_cron_task，对齐 data-synth cron） ----

export interface CronTaskItem {
  id: string;
  name: string | null;
  label: string | null;
  cronExpression: string | null;
  taskClass: string | null;
  state: string | null;
  fireParams: string | null;
  queueName: string | null;
  nextFireTime: string | null;
}

export interface CronTaskSavePayload {
  name: string;
  label: string;
  cronExpression: string;
  taskClass: string;
  state: string;
  fireParams?: string | null;
  queueName?: string | null;
}

export function apiListCronTasks(params: {
  pageNum?: number;
  pageSize?: number;
  keyWord?: string;
} = {}) {
  const q = new URLSearchParams();
  if (params.pageNum) q.set("pageNum", String(params.pageNum));
  if (params.pageSize) q.set("pageSize", String(params.pageSize));
  if (params.keyWord) q.set("keyWord", params.keyWord);
  return request<{
    content: CronTaskItem[];
    totalElements: number;
    pageNum: number;
    pageSize: number;
  }>(`/api/v1/cron?${q.toString()}`);
}

export function apiCreateCronTask(payload: CronTaskSavePayload) {
  return request<CronTaskItem>("/api/v1/cron", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function apiUpdateCronTask(id: string, payload: CronTaskSavePayload) {
  return request<CronTaskItem>(`/api/v1/cron/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function apiDeleteCronTask(id: string) {
  return request<null>(`/api/v1/cron/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function apiToggleCronTask(id: string, state: "0" | "1") {
  return request<CronTaskItem>(
    `/api/v1/cron/${encodeURIComponent(id)}/toggle?state=${state}`,
    { method: "POST" },
  );
}

export function apiCronQueues() {
  return request<Array<{ queueName: string; queueLabel: string | null }>>(
    "/api/v1/cron/queues",
  );
}

export function apiCronRegisteredTasks() {
  return request<Array<{ taskClass: string; name: string }>>(
    "/api/v1/cron/registered-tasks",
  );
}

// ---- Worker 监控（Flower，对齐 data-synth system/workers） ----

export interface FlowerWorkerOverview {
  workerName: string;
  status: "ONLINE" | "OFFLINE";
  activeQueueNames: string[];
  registeredTaskCount: number;
  registeredTaskNames: string[];
  processedTaskCount: number;
  concurrency: number;
  prefetchCount: number;
  activeTaskCount: number;
  reservedTaskCount: number;
  scheduledTaskCount: number;
  lastHeartbeatAt?: string | null;
}

export interface FlowerWorkersOverview {
  workers: FlowerWorkerOverview[];
  summary: { total: number; online: number; offline: number };
  collectedAt: string;
}

export function apiFlowerWorkers(forceRefresh = false) {
  return request<FlowerWorkersOverview>(
    `/api/v1/workers?force_refresh=${forceRefresh ? "true" : "false"}`,
  );
}

export interface FlowerWorkerTaskItem {
  taskId: string;
  taskName: string;
  state: string;
  queueName?: string | null;
  argsText?: string;
  kwargsText?: string;
  receivedAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  runtimeSeconds?: number | null;
  resultText?: string | null;
  exceptionText?: string | null;
}

export interface FlowerWorkerTaskOverview {
  workerName: string;
  activeTasks: FlowerWorkerTaskItem[];
  reservedTasks: FlowerWorkerTaskItem[];
  scheduledTasks: FlowerWorkerTaskItem[];
  recentTasks: FlowerWorkerTaskItem[];
  summary: { active: number; reserved: number; scheduled: number; recent: number };
  collectedAt: string;
}

export function apiFlowerWorkerTasks(workerName: string, recentLimit = 50) {
  return request<FlowerWorkerTaskOverview>(
    `/api/v1/workers/${encodeURIComponent(workerName)}/tasks?recent_limit=${recentLimit}`,
  );
}

export interface WorkerRegisteredTaskCronConfig {
  id: string;
  name?: string | null;
  label?: string | null;
  cronExpression?: string | null;
  queueName?: string | null;
  state?: string | null;
  nextFireTime?: string | null;
}

export interface WorkerRegisteredTaskConfigItem {
  taskName: string;
  taskClass: string;
  cronConfigs: WorkerRegisteredTaskCronConfig[];
}

export interface WorkerRegisteredTaskConfigOverview {
  workerName: string;
  tasks: WorkerRegisteredTaskConfigItem[];
  summary: {
    taskCount: number;
    configuredTaskCount: number;
    cronConfigCount: number;
  };
}

export function apiWorkerRegisteredTasks(workerName: string) {
  return request<WorkerRegisteredTaskConfigOverview>(
    `/api/v1/workers/${encodeURIComponent(workerName)}/registered-tasks`,
  );
}

// ---- 文件管理（对齐 data-synth system/files） ----

export interface SysFileItem {
  id: string;
  file_name: string;
  file_extension?: string | null;
  file_size?: number | null;
  mime_type?: string | null;
  storage_type: string;
  ds_name?: string | null;
  bucket_name?: string | null;
  storage_path: string;
  team_id: string;
  business_module: string;
  created_by?: string | null;
  create_date?: string | null;
  update_date?: string | null;
  state?: string | null;
  // explorer 附加字段
  name?: string;
  isFolder?: boolean;
  type?: string;
  sourcePath?: string;
}

export interface FileExplorerResponse {
  list: SysFileItem[];
  total: number;
  page: number;
  pageSize: number;
}

export function apiFileExplore(params: {
  current_path?: string;
  search?: string;
  page?: number;
  page_size?: number;
}) {
  const q = new URLSearchParams();
  if (params.current_path) q.set("current_path", params.current_path);
  if (params.search) q.set("search", params.search);
  if (params.page) q.set("page", String(params.page));
  if (params.page_size) q.set("page_size", String(params.page_size));
  const qs = q.toString();
  return request<FileExplorerResponse>(`/api/v1/files/explore${qs ? `?${qs}` : ""}`);
}

export function apiFileUpload(file: File, module = "default") {
  const form = new FormData();
  form.append("file", file);
  form.append("module", module);
  return fetch("/api/v1/files/upload", {
    method: "POST",
    body: form,
    credentials: "same-origin",
  }).then((r) => r.json() as Promise<ApiEnvelope<SysFileItem>>);
}

export function apiFileDownloadUrl(fileId: string) {
  return `/api/v1/files/${encodeURIComponent(fileId)}/download`;
}

export function apiFileDelete(fileId: string) {
  return request<{ id: string; deleted: boolean }>(
    `/api/v1/files/${encodeURIComponent(fileId)}`,
    { method: "DELETE" },
  );
}

export function apiFileRmdir(path: string) {
  return request<{ total: number }>(
    `/api/v1/files/rmdir?path=${encodeURIComponent(path)}`,
    { method: "POST" },
  );
}

export function apiFileZipUrl(path: string) {
  return `/api/v1/files/zip?path=${encodeURIComponent(path)}`;
}

export interface ParserEngineRule {
  file_types: string[];
  engine: string;
}

export interface ChunkingConfig {
  chunk_size: number;
  chunk_overlap: number;
  separators: string[];
  enable_parent_child: boolean;
  parent_chunk_size: number;
  child_chunk_size: number;
  strategy: string;
  token_limit: number;
  languages: string[];
  parser_engine_rules: ParserEngineRule[];
}

export interface ChunkingDefaults {
  chunk_size: number;
  chunk_overlap: number;
  strategy: string;
  parent_chunk_size: number;
  child_chunk_size: number;
}

export interface ChunkingConfigData {
  chunking: Partial<ChunkingConfig>;
  defaults: ChunkingDefaults;
}

export function apiGetChunkingConfig(kbId: string) {
  return request<ChunkingConfigData>(`/api/v1/kbs/${kbId}/chunking-config`);
}

export function apiPutChunkingConfig(kbId: string, chunking: Partial<ChunkingConfig>) {
  return request<{ chunking: ChunkingConfig }>(`/api/v1/kbs/${kbId}/chunking-config`, {
    method: "PUT",
    body: JSON.stringify(chunking),
  });
}

export interface PreviewChunk {
  seq: number;
  content: string;
  chars: number;
  tokens: number;
  is_parent: boolean;
  parent_seq: number | null;
}

export interface PreviewProfile {
  total_chars: number;
  total_lines: number;
  avg_line_len: number;
  md_heading_total: number;
  heading_density: number;
  dominant_heading_level: number;
  has_tables: boolean;
  has_code: boolean;
  detected_langs: string[];
}

export interface PreviewDiagnostics {
  selected_tier: string;
  tier_chain: string[];
  rejected: { tier: string; reason: string }[];
  profile: PreviewProfile | null;
}

export interface ChunkPreviewData {
  chunks: PreviewChunk[];
  parents?: PreviewChunk[];
  diagnostics: PreviewDiagnostics;
}

export function apiPreviewChunk(text: string, config?: Partial<ChunkingConfig>) {
  return request<ChunkPreviewData>("/api/v1/kbs/chunk-preview", {
    method: "POST",
    body: JSON.stringify({ text, config }),
  });
}

export interface ParserEngineItem {
  name: string;
  display_name: string;
  description?: string;
  available: boolean;
  reason?: string | null;
  file_types: string[];
}

export function apiListParserEngines() {
  return request<ParserEngineItem[]>("/api/v1/parsers/engines");
}

// 文档解析分块（分块调试抽屉「使用文档内容」用）——后端已有该端点
export interface DocChunkItem {
  chunk_id: string;
  seq: number;
  content: string;
  meta?: Record<string, unknown> | null;
  enabled?: boolean;
}

export function apiGetDocumentChunks(kbId: string, docId: string) {
  return request<{ total: number; items: DocChunkItem[] }>(
    `/api/v1/kbs/${kbId}/documents/${docId}/chunks`,
  );
}

// ---- datagrid 导出（对齐 ds exportExcel / exportSql） ----

export interface DatagridExportResult {
  success: boolean;
  data?: string;   // base64(xlsx) 或 INSERT 语句文本
  filename?: string;
  rows?: number;
  msg?: string;
}

export function apiDatagridExportExcel(dsName: string, sql: string): Promise<DatagridExportResult> {
  // 后端返回裸业务体（success/data/filename 平级），不走 ApiEnvelope
  return request<DatagridExportResult>("/api/v1/datagrid/export-excel", {
    method: "POST",
    body: JSON.stringify({ dsName, sql }),
  }) as unknown as Promise<DatagridExportResult>;
}

export function apiDatagridExportSql(dsName: string, sql: string, tableName = "export_table"): Promise<DatagridExportResult> {
  return request<DatagridExportResult>("/api/v1/datagrid/export-sql", {
    method: "POST",
    body: JSON.stringify({ dsName, sql, tableName }),
  }) as unknown as Promise<DatagridExportResult>;
}

// ---- 通知管理端（对齐 ds sendSystemMessageAction 等） ----

export interface NotifyUserItem {
  user_id?: string;
  user_name?: string;
  email?: string | null;
}

export function apiListNotifyUsers(keyWord = "") {
  return request<{ items: NotifyUserItem[]; total: number }>(
    `/api/v1/system/notifications/users?keyWord=${encodeURIComponent(keyWord)}`,
  );
}

export interface SendMessagePayload {
  userIds?: string[];
  title: string;
  content: string;
  type?: "INFO" | "WARNING" | "ERROR" | "SUCCESS";
  priority?: "LOW" | "NORMAL" | "HIGH";
  linkUrl?: string;
}

export function apiSendSystemMessage(payload: SendMessagePayload) {
  return request<{ sent: number }>("/api/v1/system/notifications/send", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface TestAlertApiPayload {
  endpoint: string;
  method: "POST" | "PUT" | "PATCH";
  id?: string;
  alertLevel?: string;
  alertTitle: string;
  alertContent?: string;
  alertTime?: string;
}

export function apiTestExternalAlertApi(payload: TestAlertApiPayload) {
  return request<{ status_code: number; body: string }>(
    "/api/v1/system/notifications/test-alert-api",
    { method: "POST", body: JSON.stringify(payload) },
  );
}
