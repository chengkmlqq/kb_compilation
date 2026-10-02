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
  scope?: string;
  team_name?: string;
  owner_user_id?: string;
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

export function apiListKbs(page = 1, pageSize = 20, keyword = "", scope?: string) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (keyword) params.set("keyword", keyword);
  if (scope) params.set("scope", scope);
  return request<PageList<KbItem>>(`/api/v1/kbs?${params.toString()}`);
}

export function apiCreateKb(payload: {
  name: string;
  label?: string;
  description?: string;
  scope?: string;
  team_name?: string;
}) {
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
  in_links: { slug: string; title: string; page_type: string }[];
  created_at?: string | null;
  updated_at?: string | null;
}

export function apiWikiTree(kbId: string) {
  return request<WikiTree>(`/api/v1/kbs/${kbId}/wiki`);
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
export type ModelType = "chat" | "embedding";

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
  kind?: string;
  text?: string;
  dimension?: number;
  error?: string;
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

export function apiDeleteModel(id: string) {
  return request<{ deleted: boolean }>(`/api/v1/models/${id}`, { method: "DELETE" });
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

export function apiDebugModel(id: string, payload: { input: string; model?: string }) {
  return request<ModelDebugResult>(`/api/v1/models/${id}/debug`, {
    method: "POST",
    body: JSON.stringify(payload),
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
} = {}) {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.page_size) q.set("page_size", String(params.page_size));
  if (params.task_class) q.set("task_class", params.task_class);
  if (params.state) q.set("state", params.state);
  if (params.keyword) q.set("keyword", params.keyword);
  const qs = q.toString();
  return request<{ items: JobItem[]; total: number; page: number; page_size: number }>(
    `/api/v1/jobs${qs ? `?${qs}` : ""}`,
  );
}

export function apiGetJob(jobId: string) {
  return request<JobItem>(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
}

// ---- WeKnora 知识库代理（查看技能构建的 wiki 数据）----

export interface WeknoraKbItem {
  id: string;
  name: string;
  description?: string | null;
  knowledge_count?: number;
}

export interface WeknoraWikiStats {
  total_pages: number;
  total_links: number;
  pages_by_type?: Record<string, number>;
  recent_updates?: unknown[];
}

export interface WeknoraWikiPage {
  id: string;
  slug: string;
  title: string;
  page_type: string;
  status?: string;
  content?: string;
  summary?: string;
  in_links?: string[];
  out_links?: string[];
  category_path?: string[];
  wiki_path?: string;
  source_refs?: string[];
  created_at?: string;
  updated_at?: string;
}

export function apiWeknoraKbs() {
  return request<WeknoraKbItem[]>("/api/v1/weknora/kbs");
}

export function apiWeknoraStats(kbId: string) {
  return request<WeknoraWikiStats>(`/api/v1/weknora/kbs/${kbId}/stats`);
}

export function apiWeknoraPages(kbId: string, page: number, pageSize: number) {
  return request<{ pages: WeknoraWikiPage[] }>(
    `/api/v1/weknora/kbs/${kbId}/pages?page=${page}&page_size=${pageSize}`,
  );
}

export function apiWeknoraPage(kbId: string, slug: string) {
  return request<WeknoraWikiPage>(`/api/v1/weknora/kbs/${kbId}/pages/${slug}`);
}
