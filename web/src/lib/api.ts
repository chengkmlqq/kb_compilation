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

export interface KbItem {
  id: string;
  name: string;
  label?: string | null;
  description?: string | null;
  indexing_strategy?: Record<string, boolean>;
  doc_count?: number;
  page_count?: number;
  created_at?: string | null;
}

export interface PageList<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export function apiListKbs(page = 1, pageSize = 20, keyword = "") {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (keyword) params.set("keyword", keyword);
  return request<PageList<KbItem>>(`/api/v1/kbs?${params.toString()}`);
}

export function apiCreateKb(payload: { name: string; label?: string; description?: string }) {
  return request<{ id: string }>("/api/v1/kbs", {
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

export function apiListDocuments(kbId: string, page = 1, pageSize = 20) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
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

export function apiDeleteDocument(kbId: string, docId: string) {
  return request(`/api/v1/kbs/${kbId}/documents/${docId}`, { method: "DELETE" });
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
  created_at?: string | null;
  updated_at?: string | null;
}

export function apiWikiTree(kbId: string) {
  return request<WikiTree>(`/api/v1/kbs/${kbId}/wiki`);
}

export function apiWikiPage(kbId: string, slug: string) {
  return request<WikiPageDetail>(`/api/v1/kbs/${kbId}/wiki/pages/${encodeURIComponent(slug)}`);
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

export function apiDeleteAgent(agentId: string) {
  return request(`/api/v1/agents/${agentId}`, { method: "DELETE" });
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

export function apiListDatasources(page = 1, pageSize = 10, keyword = "") {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (keyword) params.set("keyword", keyword);
  return request<{ items?: DatasourceItem[]; total?: number }>(
    `/api/v1/open/datasources?${params.toString()}`,
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
  route?: string | null;
  parent_id?: string | null;
  sort_num?: number | null;
  state?: string | null;
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

export interface ChatMessageItem {
  id: string;
  role: "user" | "assistant";
  content: string;
  refs?: { chunk_id: string; score: number }[];
  created_at: string;
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
