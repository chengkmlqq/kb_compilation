"use client";

/**
 * Wiki 浏览视图 —— 像素级对齐 WeKnora WikiBrowser.vue（wiki-sidebar + wiki-reader）。
 *
 * 左栏（wiki-sidebar，280px / border-right）:
 *   - header：搜索行（搜索框 + 树/列表视图切换 + 新建根目录）
 *   - page-list：目录树（目录项 34px、缩进 14px/级、chevron、计数胶囊）
 *     与页面项（34px、page_type 彩色图标、标题 13px）或列表模式（项 98px：标题+摘要）
 *   - 空状态（图标 + 标题 + 描述）
 *
 * 右栏（wiki-reader，padding 16px 24px）:
 *   - 返回导航（页面栈）
 *   - header：标题 26px/600 + 反馈图标、meta 行（类型标签 + 更新时间 + 右侧图谱链接）
 *   - backlinks：被引用标签（顶部，border-bottom 分隔）
 *   - body：markdown（14px/lh1.6，h1 24px / h2 18px / h3 16px、blockquote 左边框、table fit-content）
 *   - sources：来源文档（底部，border-top 分隔）
 *
 * 目录树懒加载：初始只拉根级分支，展开目录时按 folder 取直接子项；
 * 深链 focusSlug 时沿父链逐级加载并自动展开；编辑/删除页面后全树按展开状态刷新。
 * 双链 [[slug]] 原地切换页面，不跳独立阅读页；编辑/反馈弹窗内嵌。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  App,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Radio,
  Space,
  Spin,
  Tag,
  Tooltip,
} from "antd";
import {
  ApartmentOutlined,
  AppstoreOutlined,
  BulbOutlined,
  BulbTwoTone,
  ClusterOutlined,
  DownOutlined,
  EditOutlined,
  FileOutlined,
  FileTextOutlined,
  FileUnknownOutlined,
  FolderAddOutlined,
  LinkOutlined,
  MessageOutlined,
  ProfileOutlined,
  ShareAltOutlined,
  TagOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  apiWikiBranch,
  apiWikiFolders,
  apiWikiListFeedback,
  apiWikiPage,
  apiWikiSearch,
  apiWikiSubmitFeedback,
  apiWikiUpdatePage,
  WikiFeedbackItem,
  WikiFolderNode,
  WikiPageDetail,
  WikiPageItem,
  WikiSearchItem,
} from "@/lib/api";

/** page_type → 中文标签（对齐 WeKnora getTypeLabel） */
const TYPE_LABEL: Record<string, string> = {
  entity: "实体",
  concept: "概念",
  synthesis: "综合",
  comparison: "对比",
  summary: "摘要",
  topic_cluster: "主题聚类",
  knowledge_graph_summary: "图谱摘要",
  cross_document_insight: "跨文档洞察",
};

/** page_type → antd Tag 颜色 */
const TYPE_COLOR: Record<string, string> = {
  entity: "purple",
  concept: "blue",
  summary: "gold",
  synthesis: "cyan",
  comparison: "magenta",
  topic_cluster: "geekblue",
  knowledge_graph_summary: "green",
  cross_document_insight: "orange",
};

const TYPE_OPTIONS = [
  { value: "entity", label: "实体" },
  { value: "concept", label: "概念" },
  { value: "summary", label: "摘要" },
];

/** page_type → 图标（对齐 WeKnora getPageIcon：entity=tag / concept=lightbulb / synthesis=relativity …） */
function pageIconOf(pageType: string) {
  switch (pageType) {
    case "entity":
      return <TagOutlined />;
    case "concept":
      return <BulbOutlined />;
    case "synthesis":
      return <ApartmentOutlined />;
    case "comparison":
      return <AppstoreOutlined />;
    case "summary":
      return <FileOutlined />;
    case "topic_cluster":
      return <ClusterOutlined />;
    case "knowledge_graph_summary":
      return <ShareAltOutlined />;
    case "cross_document_insight":
      return <BulbTwoTone />;
    default:
      return <FileTextOutlined />;
  }
}

/** page_type → 图标颜色（对齐 WeKnora .wiki-page-file-icon--{type}） */
const PAGE_ICON_COLOR: Record<string, string> = {
  entity: "#8b5cf6",
  concept: "#3b82f6",
  synthesis: "#06b6d4",
  comparison: "#d946ef",
  summary: "#f59e0b",
  topic_cluster: "#0ea5e9",
  knowledge_graph_summary: "#10b981",
  cross_document_insight: "#f97316",
};

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** source_refs 归一化（对齐 WeKnora normalizeSourceRefs）：
 *  裸 id / `<id>|knowledges.file_name` → 展示名取 `|` 之后，否则回落 id */
function sourceLabel(ref: string): string {
  const raw = String(ref || "").trim();
  if (!raw) return "";
  const bar = raw.indexOf("|");
  if (bar >= 0) {
    const name = raw.slice(bar + 1).trim();
    if (name) return name;
  }
  return raw;
}

// [[slug]] / [[slug|别名]] 双链 → markdown 链接（href 规整为 /kbs/{id}/wiki/{slug}，组件内拦截原地切换）
function renderWikiLinks(content: string, kbId: string): string {
  if (!content) return content;
  const wikiLinkRe = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;
  return content.replace(wikiLinkRe, (_m, slug: string, alias?: string) => {
    const clean = (slug || "").trim();
    if (!clean) return _m;
    const label = (alias || "").trim() || clean;
    const href = `/kbs/${encodeURIComponent(kbId)}/wiki/${encodeURIComponent(clean)}`;
    return `[${label}](${href})`;
  });
}

/** 扁平化的树行（目录 / 页面），带深度用于缩进 */
type TreeRow =
  | { kind: "directory"; key: string; folderId: string; label: string; count: number; depth: number; hasChildren: boolean }
  | { kind: "page"; key: string; page: WikiPageItem; depth: number };

interface BranchData {
  folders: WikiFolderNode[];
  pages: WikiPageItem[];
}

interface Props {
  kbId: string;
  focusSlug?: string;
  onTreeChanged?: () => void;
  onOpenGraph?: (slug: string) => void;
}

export default function WikiBrowseView({ kbId, focusSlug, onTreeChanged, onOpenGraph }: Props) {
  const router = useRouter();
  const { message } = App.useApp();

  // 已加载的分支：folderId（"" = 根）→ 子目录 + 子页面
  const [branches, setBranches] = useState<Record<string, BranchData>>({});
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string>(focusSlug || "");
  const [page, setPage] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);

  // 视图模式（树 / 列表）与搜索（对齐 WeKnora sidebarViewMode + searchQuery）
  const [viewMode, setViewMode] = useState<"tree" | "list">("tree");
  const [searchText, setSearchText] = useState("");
  const [searchResults, setSearchResults] = useState<WikiSearchItem[] | null>(null);
  const [flatPages, setFlatPages] = useState<WikiPageItem[] | null>(null);

  // 页面栈（返回导航）
  const [navStack, setNavStack] = useState<string[]>([]);

  const expandedRef = useRef<string[]>([]);
  const selectedRef = useRef<string>(focusSlug || "");

  // 编辑
  const [editOpen, setEditOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm] = Form.useForm();
  // 反馈
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackType, setFeedbackType] = useState("helpful");
  const [feedbackContent, setFeedbackContent] = useState("");
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [pageFeedback, setPageFeedback] = useState<WikiFeedbackItem[]>([]);

  const loadPage = useCallback(
    async (slug: string, opts?: { pushNav?: boolean }) => {
      setLoading(true);
      setSelectedSlug(slug);
      selectedRef.current = slug;
      if (opts?.pushNav) setNavStack((prev) => [...prev, slug]);
      const res = await apiWikiPage(kbId, slug);
      if (res.success && res.data) {
        setPage(res.data);
        setNotFound(false);
      } else {
        setPage(null);
        setNotFound(true);
      }
      setLoading(false);
    },
    [kbId],
  );

  const fetchBranch = useCallback(
    async (folderId: string): Promise<BranchData> => {
      const res = await apiWikiBranch(kbId, folderId);
      if (!res.success || !res.data) return { folders: [], pages: [] };
      const d = res.data;
      const folders = [...(d.folders || [])].sort((a, b) => a.name.localeCompare(b.name));
      const pages = [...(d.pages || [])].sort((a, b) => a.title.localeCompare(b.title));
      return { folders, pages };
    },
    [kbId],
  );

  // 初始加载：根级分支 + focusSlug 深链定位（沿父链逐级加载并展开）
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const rootBranch = await fetchBranch("");
      if (cancelled) return;
      const next: Record<string, BranchData> = { "": rootBranch };
      const nextExpanded: string[] = [];

      if (focusSlug) {
        const pageRes = await apiWikiPage(kbId, focusSlug);
        if (!cancelled && pageRes.success && pageRes.data) {
          const folderId = pageRes.data.folder_id || "";
          if (folderId) {
            const fRes = await apiWikiFolders(kbId);
            const folders = fRes.success ? fRes.data || [] : [];
            const chain: string[] = [];
            let cur = folderId;
            let guard = 0;
            while (cur && guard < 20) {
              chain.unshift(cur);
              const f = folders.find((x) => x.id === cur);
              cur = f?.parent_id || "";
              guard += 1;
            }
            for (const fid of chain) {
              if (cancelled) return;
              next[fid] = await fetchBranch(fid);
              nextExpanded.push(fid);
            }
          }
          setSelectedSlug(focusSlug);
          selectedRef.current = focusSlug;
          void loadPage(focusSlug);
        }
      } else {
        // 默认选中第一个根级页面
        const firstRootPage = rootBranch.pages[0];
        if (firstRootPage) {
          setSelectedSlug(firstRootPage.slug);
          selectedRef.current = firstRootPage.slug;
          void loadPage(firstRootPage.slug);
        }
      }
      if (!cancelled) {
        setBranches(next);
        setExpandedKeys(nextExpanded);
        expandedRef.current = nextExpanded;
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kbId, focusSlug]);

  /** 扁平化渲染行：目录（及其递归子项）在前，页面在后（对齐 antd Tree 与 WeKnora 目录优先） */
  const treeRows = useMemo<TreeRow[]>(() => {
    const expanded = new Set(expandedKeys);
    const rows: TreeRow[] = [];
    const walk = (parentId: string, depth: number) => {
      const branch = branches[parentId];
      if (!branch) return;
      for (const f of branch.folders) {
        rows.push({
          kind: "directory",
          key: `folder:${f.id}`,
          folderId: f.id,
          label: f.name,
          count: f.child_count ?? 0,
          depth,
          hasChildren: (f.child_count ?? 0) > 0,
        });
        if (expanded.has(f.id)) walk(f.id, depth + 1);
      }
      for (const p of branch.pages) {
        rows.push({ kind: "page", key: `page:${p.slug}`, page: p, depth });
      }
    };
    walk("", 0);
    return rows;
  }, [branches, expandedKeys]);

  /** 列表模式：递归收集所有已加载分支的页面（并按需补拉未加载目录） */
  const loadFlatPages = useCallback(async () => {
    const collected: WikiPageItem[] = [];
    const visited = new Set<string>();
    const walk = async (folderId: string) => {
      if (visited.has(folderId)) return;
      visited.add(folderId);
      let branch = branches[folderId];
      if (!branch) {
        branch = await fetchBranch(folderId);
        setBranches((prev) => ({ ...prev, [folderId]: branch as BranchData }));
      }
      collected.push(...branch.pages);
      for (const f of branch.folders) await walk(f.id);
    };
    await walk("");
    setFlatPages(collected);
  }, [branches, fetchBranch]);

  const onToggleDirectory = useCallback((folderId: string) => {
    setExpandedKeys((prev) => {
      const next = prev.includes(folderId) ? prev.filter((k) => k !== folderId) : [...prev, folderId];
      expandedRef.current = next;
      return next;
    });
  }, []);

  /** 展开目录时懒加载该目录的直接子项（对齐 WeKnora） */
  const onExpandDirectory = useCallback(
    async (folderId: string) => {
      if (branches[folderId]) {
        onToggleDirectory(folderId);
        return;
      }
      const branch = await fetchBranch(folderId);
      setBranches((prev) => ({ ...prev, [folderId]: branch }));
      onToggleDirectory(folderId);
    },
    [branches, fetchBranch, onToggleDirectory],
  );

  // 编辑/删除等树外变更后：按当前展开状态刷新已加载分支
  const reloadTree = useCallback(async () => {
    const next: Record<string, BranchData> = { "": await fetchBranch("") };
    for (const fid of expandedRef.current) {
      next[fid] = await fetchBranch(fid);
    }
    setBranches(next);
    const cur = selectedRef.current;
    if (cur) void loadPage(cur);
  }, [fetchBranch, loadPage]);

  const runSearch = useCallback(async () => {
    const q = searchText.trim();
    if (!q) {
      setSearchResults(null);
      return;
    }
    const res = await apiWikiSearch(kbId, q, 50);
    setSearchResults(res.success ? (res.data?.items || []) : []);
  }, [kbId, searchText]);

  const loadFeedback = useCallback(async () => {
    if (!page) return;
    const res = await apiWikiListFeedback(kbId, page.slug);
    if (res.success && res.data) setPageFeedback(res.data.items);
  }, [kbId, page]);
  useEffect(() => {
    if (feedbackOpen) void loadFeedback();
  }, [feedbackOpen, loadFeedback]);

  const openEdit = useCallback(() => {
    if (!page) return;
    editForm.setFieldsValue({
      title: page.title,
      page_type: page.page_type,
      summary: page.summary || "",
      content: page.content || "",
    });
    setEditOpen(true);
  }, [page, editForm]);

  const submitEdit = async () => {
    if (!page) return;
    const values = await editForm.validateFields();
    setSaving(true);
    try {
      const res = await apiWikiUpdatePage(kbId, page.slug, {
        title: values.title,
        page_type: values.page_type,
        summary: values.summary || undefined,
        content: values.content || "",
      });
      if (res.success) {
        message.success("页面已保存");
        setEditOpen(false);
        void loadPage(page.slug);
        onTreeChanged?.();
        void reloadTree();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const submitFeedback = async () => {
    if (!page) return;
    setFeedbackSaving(true);
    try {
      const res = await apiWikiSubmitFeedback(kbId, page.slug, feedbackType, feedbackContent);
      if (res.success) {
        message.success("反馈已提交");
        setFeedbackContent("");
        void loadFeedback();
      } else {
        message.error(res.message || "提交失败");
      }
    } finally {
      setFeedbackSaving(false);
    }
  };

  const handleSelectPage = (slug: string) => {
    if (slug === selectedRef.current) return;
    void loadPage(slug, { pushNav: true });
  };

  /** 返回上一页（页面栈） */
  const goBack = () => {
    setNavStack((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      const back = next.pop() as string;
      void loadPage(back);
      return next;
    });
  };

  const onLocalLink = (e: React.MouseEvent<HTMLAnchorElement>) => {
    const href = e.currentTarget.getAttribute("href") || "";
    const m = href.match(/\/wiki\/([^/]+)$/);
    if (m) {
      e.preventDefault();
      void loadPage(decodeURIComponent(m[1]), { pushNav: true });
    }
  };

  const openGraph = () => {
    if (!page) return;
    if (onOpenGraph) onOpenGraph(page.slug);
    else router.push(`/kbs/${kbId}?wiki=graph&focus=${encodeURIComponent(page.slug)}`);
  };

  const hasContentPages = treeRows.length > 0;
  const sourceRefs = (page?.source_refs || []).map(sourceLabel).filter(Boolean);
  const searchMode = searchResults !== null;

  // ── 左栏行渲染 ──
  const renderSidebarRow = (row: TreeRow) => {
    if (row.kind === "directory") {
      const expanded = expandedKeys.includes(row.folderId);
      return (
        <div
          key={row.key}
          className="kb-wiki-directory-item"
          style={{ "--kb-wiki-depth": row.depth } as React.CSSProperties}
          onClick={() => void onExpandDirectory(row.folderId)}
        >
          <DownOutlined className={`kb-wiki-directory-toggle${expanded ? " kb-wiki-directory-toggle--expanded" : ""}`} />
          <span className="kb-wiki-directory-title">{row.label}</span>
          <div className="kb-wiki-tree-trailing">
            <span className="kb-wiki-directory-count">{row.count}</span>
          </div>
        </div>
      );
    }
    const p = row.page;
    const active = p.slug === selectedSlug;
    return (
      <Tooltip key={row.key} title={p.title} placement="top" mouseEnterDelay={0.6}>
        <div
          className={`kb-wiki-page-item kb-wiki-page-item--tree${active ? " active" : ""}`}
          style={{ "--kb-wiki-depth": row.depth } as React.CSSProperties}
          onClick={() => handleSelectPage(p.slug)}
        >
          <span
            className="kb-wiki-page-file-icon"
            style={{ color: PAGE_ICON_COLOR[p.page_type] || undefined }}
          >
            {pageIconOf(p.page_type)}
          </span>
          <span className="kb-wiki-page-item-title">{p.title}</span>
        </div>
      </Tooltip>
    );
  };

  const renderListItem = (p: WikiPageItem | WikiSearchItem) => {
    const active = p.slug === selectedSlug;
    return (
      <div
        key={("id" in p && p.id) || p.slug}
        className={`kb-wiki-page-item kb-wiki-page-item--list${active ? " active" : ""}`}
        onClick={() => handleSelectPage(p.slug)}
      >
        <div className="kb-wiki-page-item-title">{p.title}</div>
        {p.summary && <div className="kb-wiki-page-item-summary">{p.summary}</div>}
      </div>
    );
  };

  return (
    <div className="kb-wiki-root">
      {/* ── 左栏：wiki-sidebar ── */}
      <aside className="kb-wiki-sidebar">
        <div className="kb-wiki-sidebar-header">
          <div className="kb-wiki-search-row">
            <Input
              className="kb-wiki-search-input"
              placeholder="搜索 wiki 页面"
              allowClear
              prefix={<FileUnknownOutlined />}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              onPressEnter={() => void runSearch()}
              onClear={() => {
                setSearchText("");
                setSearchResults(null);
              }}
            />
            <div className="kb-wiki-view-toggle" role="group">
              <Tooltip title="树视图" placement="top">
                <button
                  type="button"
                  className={`kb-wiki-view-toggle-btn${viewMode === "tree" ? " active" : ""}`}
                  aria-pressed={viewMode === "tree"}
                  onClick={() => {
                    setViewMode("tree");
                    setFlatPages(null);
                  }}
                >
                  <ProfileOutlined />
                </button>
              </Tooltip>
              <Tooltip title="列表视图" placement="top">
                <button
                  type="button"
                  className={`kb-wiki-view-toggle-btn${viewMode === "list" ? " active" : ""}`}
                  aria-pressed={viewMode === "list"}
                  onClick={() => {
                    setViewMode("list");
                    void loadFlatPages();
                  }}
                >
                  <UnorderedListOutlined />
                </button>
              </Tooltip>
            </div>
          </div>
        </div>

        <div className="kb-wiki-page-list">
          {/* 搜索模式：扁平结果列表，无分组外壳 */}
          {searchMode ? (
            <>
              {searchResults!.map((p) => renderListItem(p))}
              {searchResults!.length === 0 && (
                <div className="kb-wiki-empty-state">
                  <p className="kb-wiki-empty-desc">没有找到匹配的页面</p>
                </div>
              )}
            </>
          ) : viewMode === "tree" ? (
            <div className="kb-wiki-tree-panel">
              <div className="kb-wiki-tree-list">{treeRows.map(renderSidebarRow)}</div>
            </div>
          ) : flatPages === null ? (
            <div className="kb-wiki-group-loading">
              <Spin size="small" />
            </div>
          ) : (
            flatPages.map((p) => renderListItem(p))
          )}

          {/* 空状态 */}
          {!searchMode && !loading && !hasContentPages && (
            <div className="kb-wiki-empty-state">
              <div className="kb-wiki-empty-icon">
                <FileUnknownOutlined style={{ fontSize: 36 }} />
              </div>
              <p className="kb-wiki-empty-title">暂无 wiki 页面</p>
              <p className="kb-wiki-empty-desc">构建完成后，wiki 页面会显示在这里</p>
            </div>
          )}
        </div>
      </aside>

      {/* ── 右栏：wiki-content / wiki-reader ── */}
      <div className="kb-wiki-content">
        <div className="kb-wiki-reader">
          {loading && !page ? (
            <div className="kb-wiki-reader-empty">
              <Spin />
            </div>
          ) : notFound || !page ? (
            <div className="kb-wiki-reader-empty">
              <Empty description={notFound ? `Wiki 页面不存在: ${selectedSlug}` : "从左侧选择页面"} />
            </div>
          ) : (
            <>
              {/* 返回导航 */}
              {navStack.length > 0 && (
                <div className="kb-wiki-nav-bar">
                  <a className="kb-wiki-nav-back" onClick={goBack}>
                    <DownOutlined style={{ transform: "rotate(90deg)", fontSize: 14 }} />
                    <span>返回</span>
                  </a>
                </div>
              )}

              {/* 页面 header */}
              <div className="kb-wiki-reader-header">
                <h2 className="kb-wiki-reader-title">
                  <span className="kb-wiki-reader-title-text">{page.title}</span>
                  <Tooltip title="页面反馈" placement="bottom">
                    <button
                      type="button"
                      className="kb-wiki-feedback-trigger"
                      aria-label="页面反馈"
                      onClick={() => setFeedbackOpen(true)}
                    >
                      <MessageOutlined style={{ fontSize: 16 }} />
                    </button>
                  </Tooltip>
                </h2>
                <div className="kb-wiki-reader-meta">
                  <Tag color={TYPE_COLOR[page.page_type] || "default"}>
                    {TYPE_LABEL[page.page_type] || page.page_type}
                  </Tag>
                  <span className="kb-wiki-reader-meta-text">更新于 {formatDate(page.updated_at)}</span>
                  <span className="kb-wiki-reader-actions">
                    <Button size="small" type="text" icon={<EditOutlined />} onClick={openEdit}>
                      编辑
                    </Button>
                    <a className="kb-wiki-reader-graph-link" onClick={openGraph}>
                      <ShareAltOutlined /> 在图谱中查看
                    </a>
                  </span>
                </div>
              </div>

              {/* 被引用（in_links） */}
              {page.in_links.length > 0 && (
                <div className="kb-wiki-reader-backlinks">
                  <span className="kb-wiki-backlink-label">
                    <LinkOutlined style={{ fontSize: 14 }} /> 被引用
                  </span>
                  {page.in_links.map((link) => (
                    <a
                      key={link.slug}
                      className="kb-wiki-backlink-tag"
                      onClick={() => void loadPage(link.slug, { pushNav: true })}
                    >
                      {link.title}
                    </a>
                  ))}
                </div>
              )}

              {/* 正文 */}
              <div className="kb-wiki-reader-body">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{ a: (props) => <a {...props} onClick={onLocalLink} /> }}
                >
                  {renderWikiLinks(page.content || "", kbId)}
                </ReactMarkdown>
              </div>

              {/* 出链（本页引用） */}
              {page.links.length > 0 && (
                <div className="kb-wiki-reader-sources">
                  <span className="kb-wiki-link-label">引用</span>
                  {page.links.map((link) => (
                    <a
                      key={link.slug}
                      className="kb-wiki-source-ref"
                      onClick={() => void loadPage(link.slug, { pushNav: true })}
                    >
                      <LinkOutlined style={{ fontSize: 14 }} /> {link.title}
                    </a>
                  ))}
                </div>
              )}

              {/* 来源文档 */}
              {sourceRefs.length > 0 && (
                <div className="kb-wiki-reader-sources">
                  <span className="kb-wiki-link-label">来源</span>
                  {sourceRefs.map((name, idx) => (
                    <span key={`${name}-${idx}`} className="kb-wiki-source-ref">
                      <FileOutlined style={{ fontSize: 14 }} /> {name}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* 编辑弹窗 */}
      <Modal
        title="编辑页面"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => void submitEdit()}
        confirmLoading={saving}
        width={720}
      >
        <Form form={editForm} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: "请输入标题" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="page_type" label="类型">
            <Radio.Group options={TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="summary" label="摘要">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="content" label="内容（支持 Markdown + [[slug]] 双链）">
            <Input.TextArea rows={14} />
          </Form.Item>
        </Form>
      </Modal>
      {/* 反馈弹窗 */}
      <Modal
        title="页面反馈"
        open={feedbackOpen}
        onCancel={() => setFeedbackOpen(false)}
        onOk={() => void submitFeedback()}
        confirmLoading={feedbackSaving}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Radio.Group
            value={feedbackType}
            onChange={(e) => setFeedbackType(e.target.value)}
            options={[
              { value: "helpful", label: "有帮助" },
              { value: "wrong", label: "内容有误" },
              { value: "missing", label: "内容缺失" },
            ]}
          />
          <Input.TextArea
            rows={4}
            value={feedbackContent}
            onChange={(e) => setFeedbackContent(e.target.value)}
            placeholder="请描述问题或建议"
          />
          {pageFeedback.length > 0 && (
            <div>
              <Space direction="vertical" size={4} style={{ width: "100%" }}>
                {pageFeedback.map((f) => (
                  <div key={f.id} style={{ fontSize: 12, color: "#888" }}>
                    [{f.feedback_type}] {f.content}
                  </div>
                ))}
              </Space>
            </div>
          )}
        </Space>
      </Modal>
    </div>
  );
}