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
  Segmented,
  Form,
  Input,
  Modal,
  Radio,
  Select,
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
  ShareAltOutlined,
  TagOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { WIKI_PAGE_TYPE_LABELS as TYPE_LABEL } from "@/lib/wikiTypes";
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
import {
  PRESET_LABELS,
  PRESET_OPTIONS,
  feedbackPresetLabel,
  feedbackTypeLabel,
} from "@/lib/wikiFeedback";

/** page_type → 中文标签（对齐 WeKnora getTypeLabel） */

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

/**
 * wiki 详情左侧两个 Tab —— 对齐 WeKnora WikiBrowser.wikiTabOptions：
 *   original_doc       原始文档 Wiki：按文档/本体抽出的页面
 *   clustered_knowledge 聚类知识 Wiki：跨文档聚合产物
 * 分类完全由 page_type 决定，不新增数据库字段。
 */
const ORIGINAL_DOC_TYPES = [
  "summary",
  "entity",
  "concept",
  "original_sentence",
  "frequent_keyword",
  "business_ontology",
  "rule_ontology",
];
const CLUSTERED_KNOWLEDGE_TYPES = [
  "synthesis",
  "comparison",
  "topic_cluster",
  "knowledge_graph_summary",
  "cross_document_insight",
];
type WikiTab = "original_doc" | "clustered_knowledge";
const WIKI_TABS: { key: WikiTab; label: string; types: string[] }[] = [
  { key: "original_doc", label: "原始文档 Wiki", types: ORIGINAL_DOC_TYPES },
  { key: "clustered_knowledge", label: "聚类知识 Wiki", types: CLUSTERED_KNOWLEDGE_TYPES },
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

/** source_refs → { id, name }；`<id>|knowledges.file_name` 取 id 为前缀，裸 id 则 id=name */
function parseSourceRefs(refs: string[]): { id: string; name: string }[] {
  return (refs || [])
    .map((ref) => {
      const raw = String(ref || "").trim();
      if (!raw) return null;
      const bar = raw.indexOf("|");
      if (bar >= 0) {
        const id = raw.slice(0, bar).trim();
        const name = raw.slice(bar + 1).trim();
        // 前缀为空（如 `|name`）或前缀明显不是文档 id（如纯标题）时仅当展示名用
        if (!id) return null;
        return { id, name: name || id };
      }
      return { id: raw, name: raw };
    })
    .filter(Boolean) as { id: string; name: string }[];
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
  | { kind: "directory"; key: string; folderId: string; label: string; count: number; pageCount: number; pageTotal: number; depth: number; hasChildren: boolean }
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
  /** 点击「来源文档」打开对应文档详情（对齐 WeKnora openSourceDoc；不传则仅展示） */
  onOpenDoc?: (docId: string) => void;
}

export default function WikiBrowseView({ kbId, focusSlug, onTreeChanged, onOpenGraph, onOpenDoc }: Props) {
  const router = useRouter();
  const { message } = App.useApp();

  // wiki 分类 Tab（原始文档 / 聚类知识）
  const [wikiTab, setWikiTab] = useState<WikiTab>("original_doc");

  // 已加载的分支：folderId（"" = 根）→ 子目录 + 子页面
  const [branches, setBranches] = useState<Record<string, BranchData>>({});
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string>(focusSlug || "");
  const [page, setPage] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);

  // 搜索（对齐 WeKnora searchQuery）；2026-10-15 列表视图切换已移除，仅保留树视图
  const [searchText, setSearchText] = useState("");
  const [searchResults, setSearchResults] = useState<WikiSearchItem[] | null>(null);

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
  const [feedbackPreset, setFeedbackPreset] = useState("");
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
      const tabTypes = WIKI_TABS.find((t) => t.key === wikiTab)?.types;
      const res = await apiWikiBranch(kbId, folderId, 1, 50, tabTypes);
      if (!res.success || !res.data) return { folders: [], pages: [] };
      const d = res.data;
      const folders = [...(d.folders || [])].sort((a, b) => a.name.localeCompare(b.name));
      const pages = [...(d.pages || [])].sort((a, b) => a.title.localeCompare(b.title));
      return { folders, pages };
    },
    [kbId, wikiTab],
  );

  /** 切换 Tab：清空树与选中，重新加载根级（原初始加载 effect 保持不变，深链仍走 focusSlug） */
  const switchWikiTab = (tab: WikiTab) => {
    if (tab === wikiTab) return;
    setWikiTab(tab);
    setBranches({});
    setExpandedKeys([]);
    setSelectedSlug("");
    selectedRef.current = "";
    setPage(null);
    setSearchText("");
    setSearchResults(null);
    setNavStack([]);
  };

  // Tab 切换后重新加载根级（跳过首次渲染；初始加载由下面的 effect 负责）
  const tabInitRef = useRef(false);
  useEffect(() => {
    if (!tabInitRef.current) {
      tabInitRef.current = true;
      return;
    }
    let cancelled = false;
    void (async () => {
      const rootBranch = await fetchBranch("");
      if (cancelled) return;
      setBranches({ "": rootBranch });
      const first = rootBranch.pages[0];
      if (first) {
        setSelectedSlug(first.slug);
        selectedRef.current = first.slug;
        void loadPage(first.slug);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wikiTab]);

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
          pageCount: f.page_count ?? 0,
          pageTotal: f.page_total ?? f.page_count ?? 0,
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
      const res = await apiWikiSubmitFeedback(kbId, page.slug, feedbackType, feedbackContent, feedbackPreset);
      if (res.success) {
        message.success("反馈已提交");
        setFeedbackContent("");
        setFeedbackPreset("");
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
  const sourceRefs = parseSourceRefs(page?.source_refs || []);
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
            <span className="kb-wiki-directory-count">{row.pageTotal}</span>
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
          <Segmented
            block
            size="small"
            value={wikiTab}
            onChange={(v) => switchWikiTab(v as WikiTab)}
            options={WIKI_TABS.map((t) => ({ label: t.label, value: t.key }))}
            style={{ marginBottom: 8 }}
          />
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
          ) : (
            // 2026-10-15：仅保留树视图（列表视图切换已移除）
            <div className="kb-wiki-tree-panel">
              <div className="kb-wiki-tree-list">{treeRows.map(renderSidebarRow)}</div>
            </div>
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
              {/* 2026-10-15：页面详情顶部返回按钮已移除（左树可直接导航） */}

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

              {/* 来源文档（可点击查看 — 对齐 WeKnora openSourceDoc：点击打开对应文档详情） */}
                            {sourceRefs.length > 0 && (
                              <div className="kb-wiki-reader-sources">
                                <span className="kb-wiki-link-label">来源</span>
                                {sourceRefs.map((ref, idx) => (
                                  <a
                                    key={`${ref.id}-${ref.name}-${idx}`}
                                    className="kb-wiki-source-ref"
                                    href="#"
                                    onClick={(e) => {
                                      e.preventDefault();
                                      if (onOpenDoc && ref.id) onOpenDoc(ref.id);
                                    }}
                                  >
                                    <FileOutlined style={{ fontSize: 14 }} /> {ref.name}
                                  </a>
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
              { value: "comment", label: "评论" },
              { value: "question", label: "提问" },
              { value: "helpful", label: "有帮助" },
            ]}
          />
          <Select
            allowClear
            style={{ width: "100%" }}
            placeholder="快捷标注（可选）"
            value={feedbackPreset || undefined}
            options={PRESET_OPTIONS}
            onChange={(v) => {
              setFeedbackPreset(v ?? "");
              // 选中预设且内容为空时自动填入对应文案（与后端 WIKI_FEEDBACK_PRESETS 一致）
              if (v && !feedbackContent) setFeedbackContent(PRESET_LABELS[v]);
            }}
          />
          <Input.TextArea
            rows={4}
            value={feedbackContent}
            onChange={(e) => setFeedbackContent(e.target.value)}
            placeholder={
              feedbackType === "question"
                ? "请输入你的问题"
                : feedbackType === "helpful"
                  ? "可补充说明（选填）"
                  : "请输入评论内容"
            }
          />
          {pageFeedback.length > 0 && (
            <div>
              <Space direction="vertical" size={4} style={{ width: "100%" }}>
                {pageFeedback.map((f) => (
                  <div key={f.id} style={{ fontSize: 12, color: "#888" }}>
                    [{feedbackTypeLabel(f.feedback_type)}
                    {f.preset ? ` / ${feedbackPresetLabel(f.preset)}` : ""}] {f.content}
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