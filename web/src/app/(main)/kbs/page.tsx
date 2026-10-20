"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  App,
  Button,
  Col,
  Dropdown,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import {
  BookOutlined,
  CodeOutlined,
  CommentOutlined,
  DeleteOutlined,
  ProfileOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  apiCreateKb,
  apiCreateKbCategory,
  apiDeleteKb,
  apiDeleteKbCategory,
  apiGetDocument,
  apiListAgents,
  apiListBuildEngines,
  apiListKbCategories,
  apiListKbs,
  apiListModels,
  apiListSkillsRegistry,
  apiRenameKbCategory,
  apiUpdateKb,
  AgentItem,
  BuildEngineItem,
  DocItem,
  KbCategoryItem,
  KbCreatePayload,
  KbItem,
  ModelItem,
} from "@/lib/api";
import { groupBuildEngineOptions } from "@/lib/tape-func";
import { ModoTabs } from "@/components/biz/modo-tabs";
import KBConfigModal from "@/components/KBConfigModal";
import KbDocsPane from "@/components/kb/KbDocsPane";
import KbWikiPane from "@/components/kb/KbWikiPane";
import KbGraphPane from "@/components/kb/KbGraphPane";
import CodeRepoPane from "@/components/kb/CodeRepoPane";
import WikiGraphView from "@/components/WikiGraphView";
import DocDetailPanel from "@/components/DocDetailPanel";

// 2026-10-16 对齐 data_supply_base corpus_info（KnowledgeSetOntology）页：图片资源从
// data_supply_base/data-supply-front/src/pages/KnowledgeSetOntology/images/ 原样复制，
// 部署前缀 /wiki（next.config basePath）。
const KBS_ASSET_BASE = "/wiki/kbs";
const headerTitleIcon = `${KBS_ASSET_BASE}/header-title-icon.png`;
const cardTitleIcon = `${KBS_ASSET_BASE}/card-title-icon.png`;
const cardOperateIcon = `${KBS_ASSET_BASE}/card-operate-icon.png`;

/** 彩色放大镜（对齐 modo-design IconSearchColor：viewBox 12x12，圆圈 opacity .25 + 实心头） */
const SearchColorIcon = ({ size = 14 }: { size?: number }) => (
  <svg
    viewBox="0 0 12 12"
    fill="none"
    stroke="none"
    width={size}
    height={size}
    style={{ verticalAlign: "-2px" }}
    aria-hidden
  >
    <path
      fillRule="evenodd"
      d="M5 8a3 3 0 1 0-3-3 3 3 0 0 0 3 3Zm0 1a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z"
      opacity=".25"
      fill="currentColor"
    />
    <path
      d="M7.64 8.005a4.018 4.018 0 0 0 .365-.364l.35.097a.5.5 0 0 1 .219.129L11 10.293l-.707.707-2.426-2.426a.5.5 0 0 1-.129-.22Z"
      fill="currentColor"
    />
  </svg>
);

/** 彩色加号（对齐 modo-design IconAddColor：圆圈 opacity .25 + 实心加号） */
const AddColorIcon = ({ size = 14 }: { size?: number }) => (
  <svg
    viewBox="0 0 12 12"
    fill="none"
    stroke="none"
    width={size}
    height={size}
    style={{ verticalAlign: "-2px" }}
    aria-hidden
  >
    <path
      fillRule="evenodd"
      d="M6 11a5 5 0 1 0-5-5 5 5 0 0 0 5 5Zm.5-4.5v2h-1v-2h-2v-1h2v-2h1v2h2v1Z"
      opacity=".25"
      fill="currentColor"
    />
    <path d="M5.5 8.5h1v-2h2v-1h-2v-2h-1v2h-2v1h2Z" fill="currentColor" />
  </svg>
);

/** arco IconSearch（Input.Search 内置后缀图标：viewBox 48 / 描边 4），14px */
const SearchLineIcon = () => (
  <svg
    viewBox="0 0 48 48"
    fill="none"
    stroke="currentColor"
    strokeWidth={4}
    width={14}
    height={14}
    aria-hidden
  >
    <path d="M33.072 33.071c6.248-6.248 6.248-16.379 0-22.627-6.249-6.249-16.38-6.249-22.628 0-6.248 6.248-6.248 16.379 0 22.627 6.248 6.248 16.38 6.248 22.628 0Zm0 0 8.485 8.485" />
  </svg>
);

/** arco Empty 默认插画（viewBox 48 / 描边 4）；容器 font-size 148px → 插画 148px */
const EmptyIllustration = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path d="M24 5v6m7 1 4-4m-18 4-4-4m28.5 22H28s-1 3-4 3-4-3-4-3H6.5M40 41H8a2 2 0 0 1-2-2v-8.46a2 2 0 0 1 .272-1.007l6.15-10.54A2 2 0 0 1 14.148 18H33.85a2 2 0 0 1 1.728.992l6.149 10.541A2 2 0 0 1 42 30.541V39a2 2 0 0 1-2 2Z" />
  </svg>
);

/** arco IconLeft / IconRight（分页上一页 / 下一页：viewBox 48 / 描边 4） */
const ChevronLeftIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path d="M32 8.4 16.444 23.956 32 39.513" />
  </svg>
);

const ChevronRightIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path d="m16 39.513 15.556-15.557L16 8.4" />
  </svg>
);

/** DSB 文档库（knowledge_lib_neo）左树目录节点图标——原样内联官方
    `KnowledgeLibNeo/images/folderIcon.svg`（14x14 蓝色文件夹：#517ED8 外壳 + 白色内页 +
    #97B9EB→#739CE2 渐变扉页），非自绘。 */
const FolderIcon = () => (
  <svg width={14} height={14} viewBox="0 0 14 14" fill="none" aria-hidden>
    <path
      d="M5.22522 1.53125C5.4198 1.53125 5.60889 1.59615 5.76248 1.71561L6.30936 2.14093C6.46294 2.26037 6.65205 2.32529 6.84662 2.32529H12.25C12.7332 2.32529 13.125 2.71704 13.125 3.20029V11.545C13.125 12.0283 12.7332 12.42 12.25 12.42H1.75C1.26675 12.42 0.875 12.0283 0.875 11.545V2.40625C0.875 1.923 1.26675 1.53125 1.75 1.53125H5.22522Z"
      fill="#517ED8"
    />
    <path
      d="M11.5615 3.34619C11.9239 3.34624 12.2177 3.64003 12.2177 4.00244V9.49554C12.2177 9.85793 11.9239 10.1517 11.5615 10.1518H2.43872C2.0763 10.1518 1.7825 9.85796 1.78247 9.49554V4.00244C1.78247 3.64 2.07628 3.34619 2.43872 3.34619H11.5615Z"
      fill="#fff"
    />
    <path
      d="M12.4688 4.47998C12.8312 4.47998 13.125 4.77379 13.125 5.13623V11.5447C13.125 12.028 12.7332 12.4197 12.25 12.4197H1.75C1.26675 12.4197 0.875 12.028 0.875 11.5447V5.13623C0.875 4.77379 1.16881 4.47998 1.53125 4.47998H12.4688Z"
      fill="url(#kbDocFolderGradient)"
    />
    <path
      d="M11.9229 5.68994C12.0438 5.68994 12.1417 5.78788 12.1417 5.90869V5.92493C12.1417 6.04572 12.0437 6.14368 11.9229 6.14368H10.5457C10.4249 6.14368 10.327 6.04572 10.327 5.92493V5.90869C10.327 5.78788 10.4249 5.68994 10.5457 5.68994H11.9229Z"
      fill="#517ED8"
    />
    <defs>
      <linearGradient
        id="kbDocFolderGradient"
        x1="7"
        y1="4.47998"
        x2="7"
        y2="12.4197"
        gradientUnits="userSpaceOnUse"
      >
        <stop stopColor="#97B9EB" />
        <stop offset="1" stopColor="#739CE2" />
      </linearGradient>
    </defs>
  </svg>
);

/** arco IconCaretDown / IconCaretRight（DSB 文档库左树 hover 时替换文件夹图标的展开箭头） */
const CaretDownIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path
      fill="currentColor"
      stroke="none"
      d="M24.938 34.829a1.2 1.2 0 0 1-1.875 0L9.56 17.949c-.628-.785-.069-1.949.937-1.949h27.007c1.006 0 1.565 1.164.937 1.95L24.937 34.829Z"
    />
  </svg>
);

const CaretRightIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path
      fill="currentColor"
      stroke="none"
      d="M34.829 23.063c.6.48.6 1.394 0 1.874L17.949 38.44c-.785.629-1.949.07-1.949-.937V10.497c0-1.007 1.164-1.566 1.95-.937l16.879 13.503Z"
    />
  </svg>
);

/**
 * 删除前预校验：拉取全量启用智能体（分页循环取完），返回显式绑定该知识库的
 * 智能体列表。判定口径与后端 delete_kb 的 AGENT_BOUND 守卫一致：
 * kb_selection_mode=selected 且库 id 在 knowledge_bases / knowledge_ids 中。
 */
async function fetchAgentsBoundToKb(kbId: string): Promise<AgentItem[]> {
  const bound: AgentItem[] = [];
  const pageSize = 100; // 后端 list_agents page_size 上限
  for (let page = 1; ; page += 1) {
    const res = await apiListAgents(page, pageSize);
    if (!res.success || !res.data) break;
    const { items, total } = res.data;
    for (const ag of items) {
      const cfg = (ag.config ?? {}) as {
        kb_selection_mode?: string;
        knowledge_bases?: string[];
        knowledge_ids?: string[];
      };
      if (cfg.kb_selection_mode !== "selected") continue;
      const kbIds = new Set([
        ...(cfg.knowledge_bases ?? []),
        ...(cfg.knowledge_ids ?? []),
      ]);
      if (kbIds.has(kbId)) bound.push(ag);
    }
    if (items.length < pageSize || page * pageSize >= total) break;
  }
  return bound;
}

/** 新建知识库弹窗分区导航（对齐智能体编辑器 AgentEditorModal 与配置弹窗 KBConfigModal） */
const CREATE_SECTIONS = [
  { key: "basic", label: "基本信息", icon: <ProfileOutlined /> },
  { key: "source", label: "知识来源", icon: <CodeOutlined /> },
  { key: "wiki", label: "Wiki 构建", icon: <BookOutlined /> },
  { key: "retrieval", label: "检索配置", icon: <SearchOutlined /> },
];

/** 知识库详情分段：文档 / Wiki / 知识图谱(Neo4j) / wiki 关系图 / 代码仓库(2026-10)，各自成为顶层动态选项卡 */
type KbSection = "docs" | "wiki" | "graph" | "wiki-graph" | "repo";
/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务分段 */
type KbSectionTab = {
  key: string;
  title: string;
  kbId: string;
  section: KbSection;
  /** wiki 关系图 ego 聚焦页（从 wiki 页「在图谱中查看」传入） */
  focusSlug?: string;
};

const SECTION_LABEL: Record<KbSection, string> = {
  docs: "文档",
  wiki: "Wiki",
  graph: "知识图谱",
  "wiki-graph": "wiki 关系图",
  repo: "代码仓库",
};

/** 文档详情选项卡（点击文件打开，可关闭）：key = doc-<docId>，内容为 DocDetailPanel */
type KbDocTab = {
  key: string;
  kbId: string;
  doc: DocItem;
};

export default function KbsPage() {
  const { message, modal } = App.useApp();
  const router = useRouter();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [skills, setSkills] = useState<{ name: string; description?: string }[]>([]);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [buildEngines, setBuildEngines] = useState<BuildEngineItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [configKb, setConfigKb] = useState<KbItem | null>(null);
  // 卡片拖拽改分类（2026-10-20）：dragKbId = 正在拖拽的知识库；dropTargetLabel = 当前悬停的分类目标（高亮）
  const [dragKbId, setDragKbId] = useState<string | null>(null);
  const [dropTargetLabel, setDropTargetLabel] = useState<string | null>(null);
  const [form] = Form.useForm();
    const [createSection, setCreateSection] = useState("basic");
    // 代码仓库知识库（2026-10）：条件渲染依赖表单值，用 useWatch 订阅触发重渲染
    const repoType = Form.useWatch("repo_type", form);
    const repoAuthType = Form.useWatch("repo_auth_type", form);
  // 2026-10-16 DSB 知识集页对齐：左侧分类树（按 label 分组筛选）
  const [selectedLabel, setSelectedLabel] = useState<string>("all");
  // 2026-10-16 DSB corpus_info 对齐：分类树顶部「搜索分类」框
  const [categoryKeyword, setCategoryKeyword] = useState("");
  // 2026-10-19 分类树 CRUD：数据来自 /api/v1/kb-categories（kb_category 行 ∪ 可见 KB label）
  const [categories, setCategories] = useState<KbCategoryItem[]>([]);
  const [catModal, setCatModal] = useState<
    | { mode: "create" }
    | { mode: "rename"; name: string }
    | null
  >(null);
  const [catName, setCatName] = useState("");
  const [catSubmitting, setCatSubmitting] = useState(false);
  // DSB 对齐：分类树展开态（DSB 默认展开全部）+ 分页页码输入框文本
  const [treeExpanded, setTreeExpanded] = useState(true);
  const [jumpText, setJumpText] = useState("1");

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「知识库管理」= 页面标题 + 列表，不可关闭；后续把知识库的
  // 「文档 / Wiki / 知识图谱」各自作为一个可关闭选项卡打开（扁平拆分）。
  const [tabs, setTabs] = useState<KbSectionTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  // 文档详情动态选项卡：点击文件打开（可关闭），不再是抽屉
  const [docTabs, setDocTabs] = useState<KbDocTab[]>([]);
  // 各知识库的文档列表刷新信号（详情页签内删除/重解析后 +1）
  const [refreshTicks, setRefreshTicks] = useState<Record<string, number>>({});

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    // 关闭「文档」分段页签时，一并关闭其下打开的文档详情页签
    const m = /^kb-(.+)-docs$/.exec(key);
    if (m) setDocTabs((prev) => prev.filter((t) => t.kbId !== m[1]));
    if (activeTab === key) setActiveTab("home");
  };

  /** 该知识库「文档」分段页签的 key；若未打开则回落到首页 */
  const docsTabKey = (kbId: string) => {
    const key = `kb-${kbId}-docs`;
    return tabs.some((t) => t.key === key) ? key : "home";
  };

  /** 打开文档详情页签：已存在则仅更新 doc 数据并激活 */
  const openDoc = (kbId: string, doc: DocItem) => {
    const key = `doc-${doc.id}`;
    setDocTabs((prev) => {
      const exists = prev.find((t) => t.key === key);
      if (exists) return prev.map((t) => (t.key === key ? { ...t, doc } : t));
      return [...prev, { key, kbId, doc }];
    });
    setActiveTab(key);
  };

  /** 关闭文档详情页签：若当前激活则回落到该库「文档」页签 */
  const closeDocTab = (key: string) => {
    const tab = docTabs.find((t) => t.key === key);
    setDocTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab(tab ? docsTabKey(tab.kbId) : "home");
  };

  /** 文档列表变化时同步已打开详情页签的 doc 数据（如自动摘要回填） */
  const syncDocs = (kbId: string, list: DocItem[]) => {
    setDocTabs((prev) => {
      let changed = false;
      const next = prev.map((t) => {
        if (t.kbId !== kbId) return t;
        const found = list.find((x) => x.id === t.doc.id);
        if (found && found !== t.doc) {
          changed = true;
          return { ...t, doc: found };
        }
        return t;
      });
      return changed ? next : prev;
    });
  };

  /** 详情页签内删除成功：关闭页签并刷新该库文档列表 */
  const removeDoc = (kbId: string, docId: string) => {
    const key = `doc-${docId}`;
    setDocTabs((prev) => prev.filter((t) => t.key !== key));
    setRefreshTicks((prev) => ({ ...prev, [kbId]: (prev[kbId] || 0) + 1 }));
    if (activeTab === key) setActiveTab(docsTabKey(kbId));
  };

  /** 详情页签内重解析成功：刷新该库文档列表 */
  const reparseDoc = (kbId: string) => {
    setRefreshTicks((prev) => ({ ...prev, [kbId]: (prev[kbId] || 0) + 1 }));
  };

  /** 打开某知识库的某个分段选项卡：已存在则仅激活，否则新增后激活 */
  const openKbSection = (kb: KbItem, section: KbSection, focusSlug?: string) => {
    const key = `kb-${kb.id}-${section}`;
    setTabs((prev) => {
      // 「知识图谱」pane 承载 PopotoGraphView：popoto 是全局单例且用固定 DOM id
      // （#popoto-graph/#popoto-taxonomy 等）渲染。多个 KB 的图谱页签并存时，
      // 后挂载实例的 resetPopotoContainers 会清掉先开页签的图、start() 又只渲染
      // 进 getElementById 命中的第一个容器，且各实例的 IntersectionObserver/
      // cleanup 竞态操作同一个 d3 force simulation → rAF 循环叠加 → CPU 打满
      // 页面卡死（2026-10-15 实测「图谱按钮有时点开页面卡死」根因）。故同一时间
      // 只保留一个图谱页签：打开新 KB 图谱时自动关掉其他 KB 的图谱页签。
      // （wiki-graph 分段用自绘 WikiGraphView，不依赖 popoto 单例，不受影响）
      const base =
        section === "graph"
          ? prev.filter((t) => !(t.section === "graph" && t.kbId !== kb.id))
          : prev;
      const exists = base.find((t) => t.key === key);
      if (exists) {
        // 已存在：仅更新 focusSlug（wiki 关系图 ego 聚焦），不重复开
        if (exists.focusSlug === focusSlug) return base;
        return base.map((t) => (t.key === key ? { ...t, focusSlug } : t));
      }
      return [
        ...base,
        {
          key,
          title: `${SECTION_LABEL[section]} · ${kb.name}`,
          kbId: kb.id,
          section,
          focusSlug,
        },
      ];
    });
    setActiveTab(key);
  };

  /** 按分段类型渲染对应 pane（渲染时构造 children，避免把 ReactNode 塞进 state） */
  const renderKbSection = (t: KbSectionTab) => {
    if (t.section === "wiki") {
      return (
        <KbWikiPane
          kbId={t.kbId}
          onOpenGraph={(slug?: string) => {
            const kb = kbs.find((k) => k.id === t.kbId);
            if (kb) openKbSection(kb, "wiki-graph", slug);
          }}
          onOpenDoc={(docId) => {
            void (async () => {
              const res = await apiGetDocument(t.kbId, docId);
              if (res.success && res.data) openDoc(t.kbId, res.data);
            })();
          }}
        />
      );
    }
    if (t.section === "graph") return <KbGraphPane kbId={t.kbId} />;
    if (t.section === "wiki-graph") {
      return <WikiGraphView kbId={t.kbId} focusSlug={t.focusSlug} />;
    }
    if (t.section === "repo") return <CodeRepoPane kbId={t.kbId} />;
    return (
      <KbDocsPane
        kbId={t.kbId}
        refreshTick={refreshTicks[t.kbId] || 0}
        onOpenDoc={(doc) => openDoc(t.kbId, doc)}
        onDocsChange={(list) => syncDocs(t.kbId, list)}
        onDocRemoved={(docId) => removeDoc(t.kbId, docId)}
      />
    );
  };

  const visibleKbs = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = kbs;
    if (selectedLabel !== "all") {
      list = list.filter((k) => (k.label || "无标签") === selectedLabel);
    }
    if (q) {
      list = list.filter(
        (k) =>
          k.name.toLowerCase().includes(q) ||
          (k.label || "").toLowerCase().includes(q)
      );
    }
    return list;
  }, [kbs, search, selectedLabel]);

  // 2026-10-19 分类树数据源：后端合并 kb_category 行与可见知识库的 label（含计数，倒序）
  const loadCategories = useCallback(async () => {
    try {
      const res = await apiListKbCategories();
      if (res.success) setCategories(res.data?.items ?? []);
    } catch {
      // 分类加载失败不阻断页面（树回退为空）
    }
  }, []);

  useEffect(() => {
    void loadCategories();
  }, [loadCategories]);

  const openCatCreate = () => {
    setCatName("");
    setCatModal({ mode: "create" });
  };

  const openCatRename = (name: string) => {
    setCatName(name);
    setCatModal({ mode: "rename", name });
  };

  const submitCatModal = async () => {
    const name = catName.trim();
    if (!name) {
      message.warning("请输入分类名称");
      return;
    }
    if (catModal?.mode === "rename" && name === catModal.name) {
      setCatModal(null);
      return;
    }
    setCatSubmitting(true);
    try {
      const res =
        catModal?.mode === "rename"
          ? await apiRenameKbCategory(catModal.name, name)
          : await apiCreateKbCategory(name);
      if (res.success) {
        message.success(catModal?.mode === "rename" ? "已重命名" : "分类已创建");
        setCatModal(null);
        void loadCategories();
        void load(); // 重命名/删除会影响知识库 label 展示
      } else {
        message.error(res.message || "操作失败");
      }
    } catch (e) {
      message.error(`操作失败: ${String(e)}`);
    } finally {
      setCatSubmitting(false);
    }
  };

  const doDeleteCategory = (name: string, count: number) => {
    modal.confirm({
      title: `删除分类「${name}」？`,
      content:
        count > 0
          ? `该分类下 ${count} 个知识库将回到「未分类」。`
          : "删除后不可恢复。",
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const res = await apiDeleteKbCategory(name);
          if (res.success) {
            message.success("已删除");
            void loadCategories();
            void load();
          } else {
            message.error(res.message || "删除失败");
          }
        } catch (e) {
          message.error(`删除失败: ${String(e)}`);
        }
      },
    });
  };

  // 分页（DSB 同款：total > 页容量时才出现分页条，页码输入框可跳页）
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const commitJump = () => {
    const n = Number.parseInt(jumpText, 10);
    const next = Number.isNaN(n) ? page : Math.min(Math.max(n, 1), pageCount);
    setJumpText(String(next));
    if (next !== page) setPage(next);
  };

  useEffect(() => {
    setJumpText(String(page));
  }, [page]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListKbs(page, pageSize);
      if (res.success) {
        setKbs(res.data?.items || []);
        setTotal(res.data?.total ?? 0);
      } else {
        message.error(res.message || "加载失败");
      }
    } finally {
      setLoading(false);
    }
  }, [message, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  // 技能下拉数据源：kb_skill 表（/api/v1/skills），正是 wiki 构建 agent 实际加载的技能
  useEffect(() => {
    void (async () => {
      try {
        const res = await apiListSkillsRegistry();
        if (res.success && res.data) setSkills(res.data.items);
      } catch {
        // 技能列表加载失败不阻断创建（技能下拉为空即用默认技能）
      }
    })();
  }, []);

  // 模型下拉数据源：Embedding（向量化）+ chat（大语言模型/合成），对齐 WeKnora 创建表单模型配置
  useEffect(() => {
    void (async () => {
      try {
        const res = await apiListModels();
        if (res.success && res.data) setModels(res.data.items || []);
      } catch {
        // 模型列表加载失败不阻断创建
      }
    })();
  }, []);

  // 构建引擎下拉数据源：服务编排中已发布的编排程序（status=effective）
  useEffect(() => {
    void (async () => {
      try {
        const res = await apiListBuildEngines();
        setBuildEngines(res.data?.items || []);
      } catch {
        // 列表加载失败不阻断创建（下拉为空时留空走内置构建）
      }
    })();
  }, []);

  const embeddingOptions = useMemo(
    () =>
      models
        .filter((m) => m.type === "embedding")
        .map((m) => ({ label: m.name, value: m.id })),
    [models]
  );
  const chatOptions = useMemo(
    () => models.filter((m) => m.type === "chat").map((m) => ({ label: m.name, value: m.id })),
    [models]
  );

  // 可选模型唯一时默认选上（省去手动选择；仅在字段为空时填充，不覆盖用户已选）
  useEffect(() => {
    if (!createOpen) return;
    if (chatOptions.length === 1 && !form.getFieldValue("summary_model_id")) {
      form.setFieldValue("summary_model_id", chatOptions[0].value);
    }
    if (embeddingOptions.length === 1 && !form.getFieldValue("embedding_model_id")) {
      form.setFieldValue("embedding_model_id", embeddingOptions[0].value);
    }
  }, [createOpen, chatOptions, embeddingOptions, form]);

  const openCreate = () => {
    form.resetFields();
    setCreateSection("basic"); // 对齐智能体编辑器：每次打开回到首个分区
    setCreateOpen(true);
  };

  const onCreate = async () => {
    // 分区表单校验/取值坑（2026-10-20 二次修复）：antd validateFields() 不传
    // nameList 时只返回「当前挂载分区」字段的值——其他分区的 Form.Item 卸载后，
    // 值虽保留在 store（preserve=true）但不在 validateFields 返回值里。
    // ① name 输入框只在「基本信息」分区挂载，故先用 getFieldValue 单独校验非空：
    //    未填则切回基本信息分区并提示（切回后已填值仍在）；
    // ② payload 必须从 getFieldsValue(true)（整个 store）取值——否则停留在
    //    「检索配置」时提交 name 缺失（后端报 name: Field required），停留在
    //    「基本信息」时提交则检索/构建/来源分区的设置被静默丢弃。
    const nameVal = form.getFieldValue("name");
    if (!nameVal || !String(nameVal).trim()) {
      if (createSection !== "basic") setCreateSection("basic");
      form.validateFields(["name"]).catch(() => null);
      message.warning("请先填写知识库名称");
      return;
    }
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    // 全分区已填值（含未挂载分区保留在 store 的值）；未到访过分区/未设置的字段
    // 不在 store 中，以下各取值均带与表单项 initialValue 一致的默认兜底
    const all = form.getFieldsValue(true);
    const isRepo = all.repo_type === "git";
    const payload: KbCreatePayload = {
      name: String(nameVal).trim(),
      label: all.label || undefined,
      description: all.description,
      scope: all.scope || "personal",
      type: all.type || "document",
      indexing_strategy: {
        vector_enabled: all.vector_enabled !== false,
        keyword_enabled: all.keyword_enabled !== false,
        // 「Wiki 构建」开关表单已移除（2026-10-15）：新建不再需要该开关——
        // 配置了构建引擎（build_engine）即自动触发构建任务
        wiki_enabled: true,
        // 知识图谱开关表单已移除（2026-10-15）：新建默认关闭（对齐后端新库默认）
        graph_enabled: false,
      },
      embedding_model_id: all.embedding_model_id || undefined,
      summary_model_id: all.summary_model_id || undefined,
      build_tape_id: all.build_engine || undefined,
      // 摘要策略固定 auto：系统按文档规模自动路由（小文档全文直塞 / 大文档分块合并），不再开放给用户选择
      summary_strategy: "auto",
      // 代码仓库知识库（2026-10）
      repo_type: isRepo ? "git" : "",
      repo_url: isRepo ? all.repo_url : "",
      repo_branch: isRepo ? all.repo_branch || "" : "",
      repo_auth_type: isRepo ? all.repo_auth_type || "none" : "none",
      repo_auth_secret: isRepo ? all.repo_auth_secret || "" : "",
    };
    const res = await apiCreateKb(payload);
    if (res.success) {
      message.success("知识库已创建");
      setCreateOpen(false);
      form.resetFields();
      setPage(1); // 新库按创建时间倒序在第 1 页
      void load();
    } else {
      message.error(res.message || "创建失败");
    }
  };

  const onDelete = async (kb: KbItem) => {
    // 删除前预校验：该库是否被智能体显式绑定（kb_selection_mode=selected 且
    // 库 id 在 knowledge_bases/knowledge_ids 中）。有绑定则提示并中止，
    // 避免删完后智能体检索范围悬空（后端同样拦截，双保险）。
    let boundAgents: AgentItem[] = [];
    try {
      boundAgents = await fetchAgentsBoundToKb(kb.id);
    } catch {
      // 预校验失败不回退：后端 delete 接口自身也有 AGENT_BOUND 守卫
    }
    if (boundAgents.length > 0) {
      modal.warning({
        title: `无法删除知识库「${kb.name}」`,
        content: `该知识库已被 ${boundAgents.length} 个智能体绑定（${boundAgents.map((a) => a.name).join("、")}），请先在智能体配置中解除绑定后再删除。`,
      });
      return;
    }
    const docN = kb.doc_count ?? 0;
    const wikiN = kb.page_count ?? 0;
    const content =
      docN > 0 || wikiN > 0
        ? `将一并删除 ${docN} 个文档、${wikiN} 个 Wiki 页及图谱数据，删除后不可恢复。`
        : "删除后不可恢复。";
    modal.confirm({
      title: `删除知识库「${kb.name}」？`,
      content,
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiDeleteKb(kb.id);
        if (res.success) {
          message.success("已删除");
          // 删除当前页最后一条且不在第 1 页时回退一页，避免空页
          if (kbs.length === 1 && page > 1) setPage(page - 1);
          else void load();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  // ---- 卡片拖拽改分类（2026-10-20）----
  // 拖拽卡片到左侧分类树：拖到分类条目 = 归入该分类；拖到「全部」= 移出分类（未分类）。
  // 后端 PUT /kbs/{id} 的 exclude_none=True 会丢弃 null，清空分类必须传空字符串 ""。
  const onDropToCategory = async (kb: KbItem, label: string) => {
    const target = label || "";
    if ((kb.label || "") === target) return;
    const res = await apiUpdateKb(kb.id, { label: target });
    if (res.success) {
      message.success(target ? `已移至分类「${target}」` : "已移出分类（未分类）");
      void load();
      void loadCategories();
    } else {
      message.error(res.message || "修改分类失败");
    }
  };

  // 列表区（首个选项卡内容）：筛选 + 卡片视图 + 钉底分页 + 新建弹窗/配置弹窗
  // 根容器用 height:100% 对齐模型管理页——外层 .ant-tabs-content 非 flex 容器，
  // flex:1 对 block 父无效会退回内容高度，导致卡片区撑不开、分页不钉底（贴在卡片下方而非页面底部）
  const listPane = (
    <div className="kb-kbs-page">
      {/* DSB 知识库页对齐 main layout：左分类树 sider 260px 白底 + 右卡片网格 */}
      <div className="kb-kbs-main-layout">
        <div className="kb-kbs-category-sider">
          <div className="kb-kbs-category-search">
            <Input
              placeholder="搜索分类"
              allowClear
              value={categoryKeyword}
              onChange={(e) => setCategoryKeyword(e.target.value)}
              suffix={<SearchLineIcon />}
            />
          </div>
          <div className="kb-kbs-category-tree-main">
            {/* 根节点「全部」= 一级目录节点（对齐 DSB 文档库 knowledge_lib_neo 左树 .arco-tree-node）；
                右键 = 新建分类（2026-10-19 移除工具栏 + 按钮，入口只留这里） */}
            <Dropdown
              trigger={["contextMenu"]}
              menu={{
                items: [{ key: "create", label: "新建分类", onClick: openCatCreate }],
              }}
            >
              <div
                className={`kb-doc-tree-row kb-doc-tree-row--root${
                  selectedLabel === "all" ? " active" : ""
                }${dropTargetLabel === "" ? " kb-doc-tree-row--drop-target" : ""}`}
                style={{ paddingLeft: 8 }}
                onClick={() => setSelectedLabel("all")}
                onDragOver={(e) => {
                  if (!dragKbId) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  setDropTargetLabel("");
                }}
                onDragLeave={() => setDropTargetLabel((v) => (v === "" ? null : v))}
                onDrop={(e) => {
                  e.preventDefault();
                  const kb = kbs.find((k) => k.id === dragKbId);
                  setDropTargetLabel(null);
                  if (kb) void onDropToCategory(kb, "");
                }}
                title="全部"
              >
                {/* 图标槽（DSB .icon-container 14px）：默认文件夹图标，hover 时让位给展开箭头 */}
                <span className="kb-doc-tree-icon">
                  <span className="kb-doc-tree-node-icon">
                    <FolderIcon />
                  </span>
                  <span
                    className="kb-doc-tree-expand-icon"
                    onClick={(e) => {
                      e.stopPropagation();
                      setTreeExpanded((v) => !v);
                    }}
                  >
                    {treeExpanded ? <CaretDownIcon /> : <CaretRightIcon />}
                  </span>
                </span>
                <span className="kb-doc-tree-title">全部</span>
                {total > 0 && <span className="kb-doc-tree-count">{total}</span>}
              </div>
            </Dropdown>
            {treeExpanded &&
              categories.map((c) => (
                <Dropdown
                  key={c.name}
                  trigger={["contextMenu"]}
                  menu={{
                    items: [
                      { key: "rename", label: "重命名", onClick: () => openCatRename(c.name) },
                      {
                        key: "delete",
                        label: "删除",
                        danger: true,
                        onClick: () => doDeleteCategory(c.name, c.count),
                      },
                    ],
                  }}
                >
                  <div
                    className={`kb-doc-tree-row${
                      selectedLabel === c.name ? " active" : ""
                    }${dropTargetLabel === c.name ? " kb-doc-tree-row--drop-target" : ""}`}
                    style={{ paddingLeft: 20 }}
                    onClick={() => setSelectedLabel(c.name)}
                    onDragOver={(e) => {
                      if (!dragKbId) return;
                      e.preventDefault();
                      e.dataTransfer.dropEffect = "move";
                      setDropTargetLabel(c.name);
                    }}
                    onDragLeave={() =>
                      setDropTargetLabel((v) => (v === c.name ? null : v))
                    }
                    onDrop={(e) => {
                      e.preventDefault();
                      const kb = kbs.find((k) => k.id === dragKbId);
                      setDropTargetLabel(null);
                      if (kb) void onDropToCategory(kb, c.name);
                    }}
                    title={c.name}
                  >
                    <span className="kb-doc-tree-icon">
                      {/* 二级分类为叶子节点：图标常显（DSB 叶子节点 .node-icon.visible） */}
                      <span className="kb-doc-tree-node-icon kb-doc-tree-node-icon--visible">
                        <FolderIcon />
                      </span>
                    </span>
                    <span className="kb-doc-tree-title">{c.name}</span>
                    {c.count > 0 && <span className="kb-doc-tree-count">{c.count}</span>}
                  </div>
                </Dropdown>
              ))}
          </div>
        </div>

        <div className="kb-kbs-list-panel">
          <div className="kb-kbs-content">
          {visibleKbs.length === 0 ? (
            /* 空态（对齐 arco Empty：148px 插画 + 「暂无数据」） */
            <div className="kb-kbs-empty">
              <div className="kb-kbs-empty-wrapper">
                <div className="kb-kbs-empty-image">
                  <EmptyIllustration />
                </div>
                <div className="kb-kbs-empty-description">暂无数据</div>
              </div>
            </div>
          ) : (
            <Row
              className={`kb-kbs-rows${total > 30 ? " kb-kbs-rows--paged" : ""}`}
              gutter={[16, 16]}
            >
              {visibleKbs.map((kb) => (
                <Col key={kb.id} span={6}>
                  <div
                    className={`kb-kbs-card${dragKbId === kb.id ? " kb-kbs-card--dragging" : ""}`}
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData("text/plain", kb.id);
                      e.dataTransfer.effectAllowed = "move";
                      setDragKbId(kb.id);
                    }}
                    onDragEnd={() => {
                      setDragKbId(null);
                      setDropTargetLabel(null);
                    }}
                    onClick={() => openKbSection(kb, "docs")}
                  >
                    <div className="kb-kbs-card-header">
                      <div className="kb-kbs-card-icon">
                        <img src={cardTitleIcon} alt="" />
                      </div>
                      <div className="kb-kbs-card-detail">
                        <div className="kb-kbs-card-title-row">
                          <div className="kb-kbs-card-title-name" title={kb.name}>
                            {kb.name}
                          </div>
                        </div>
                        <div className="kb-kbs-card-meta">
                          {kb.doc_count ?? 0}个文件 · {kb.page_count ?? 0}个Wiki
                          {(kb.feedback_count ?? 0) > 0 && (
                            <span
                              className="kb-kbs-card-feedback-badge"
                              title="查看维护中心反馈"
                              onClick={(e) => {
                                e.stopPropagation();
                                router.push(`/wiki-maintenance?kb_id=${encodeURIComponent(kb.id)}`);
                              }}
                            >
                              <CommentOutlined style={{ fontSize: 12 }} />
                              反馈 {kb.feedback_count}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="kb-kbs-card-description">{kb.description}</div>
                    <div className="kb-kbs-card-footer">
                      <div className="kb-kbs-card-footer-left" title={kb.label || "未分类"}>
                        {kb.label || "未分类"}
                      </div>
                      <div
                        className="kb-kbs-card-footer-right"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Dropdown
                          menu={{
                            items: [
                              { key: "open", label: "打开知识库" },
                              { key: "wiki", label: "Wiki" },
                              { key: "graph", label: "知识图谱" },
                              { key: "wiki-graph", label: "wiki 关系图" },
                              ...(kb.repo_type === "git"
                                ? [{ key: "repo", label: "代码仓库" }]
                                : []),
                              { key: "config", label: "知识库配置" },
                              { type: "divider" },
                              { key: "delete", label: "删除", danger: true },
                            ],
                            onClick: ({ key, domEvent }) => {
                              domEvent.stopPropagation();
                              if (key === "open") openKbSection(kb, "docs");
                              else if (key === "wiki") openKbSection(kb, "wiki");
                              else if (key === "graph") openKbSection(kb, "graph");
                              else if (key === "wiki-graph") openKbSection(kb, "wiki-graph");
                              else if (key === "repo") openKbSection(kb, "repo");
                              else if (key === "config") setConfigKb(kb);
                              else if (key === "delete") onDelete(kb);
                            },
                          }}
                        >
                          <img src={cardOperateIcon} alt="" />
                        </Dropdown>
                      </div>
                    </div>
                  </div>
                </Col>
              ))}
            </Row>
          )}
          </div>

          {/* 分页条（对齐 .knowledge-set-pagniation + arco Pagination simple：total > 30 才出现） */}
          {total > 30 && (
            <div className="kb-kbs-pagination-bar">
              <div className="kb-kbs-pagination">
                <div className="kb-kbs-pagination-total">共 {total} 条</div>
                <ul className="kb-kbs-pagination-list">
                  <li
                    className={`kb-kbs-pagination-item kb-kbs-pagination-item-prev${
                      page <= 1 ? " kb-kbs-pagination-item-disabled" : ""
                    }`}
                    onClick={() => {
                      if (page > 1) setPage(page - 1);
                    }}
                  >
                    <ChevronLeftIcon />
                  </li>
                  <li className="kb-kbs-pagination-item-simple-pager">
                    <Input
                      className="kb-kbs-pagination-jumper-input"
                      value={jumpText}
                      onChange={(e) => setJumpText(e.target.value)}
                      onBlur={commitJump}
                      onPressEnter={commitJump}
                      disabled={pageCount <= 1}
                    />
                    <span className="kb-kbs-pagination-separator">/</span>
                    <span>{pageCount}</span>
                  </li>
                  <li
                    className={`kb-kbs-pagination-item kb-kbs-pagination-item-next${
                      page >= pageCount ? " kb-kbs-pagination-item-disabled" : ""
                    }`}
                    onClick={() => {
                      if (page < pageCount) setPage(page + 1);
                    }}
                  >
                    <ChevronRightIcon />
                  </li>
                </ul>
                <div className="kb-kbs-pagination-option">
                  <Select
                    value={pageSize}
                    style={{ width: 96 }}
                    options={[10, 20, 30, 40, 50].map((n) => ({
                      label: `${n} 条/页`,
                      value: n,
                    }))}
                    onChange={(v) => {
                      setPageSize(v);
                      setPage(1);
                    }}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

        {/* 新建知识库弹窗（对齐智能体编辑器 AgentEditorModal：Modal + 左侧分区导航 + 底部确定/取消） */}
        <Modal
          title="新建知识库"
          open={createOpen}
          onOk={() => void onCreate()}
          onCancel={() => setCreateOpen(false)}
          okText="创建"
          cancelText="取消"
          width={860}
          destroyOnClose
        >
          {/* 2026-10-20 修复：preserve={false} 会在分区切换（Form.Item 卸载）时清空
              已填字段——去掉，改用打开弹窗时 resetFields 清空（openCreate 已有） */}
          <Form form={form} layout="vertical">
            <div style={{ display: "flex", gap: 16, minHeight: 440 }}>
              {/* 左侧分区导航（对齐智能体编辑器 AgentEditorModal） */}
              <div
                style={{
                  width: 150,
                  borderRight: "1px solid #f0f0f0",
                  paddingRight: 12,
                  flexShrink: 0,
                }}
              >
                {CREATE_SECTIONS.map((s) => (
                  <div
                    key={s.key}
                    onClick={() => setCreateSection(s.key)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "8px 12px",
                      borderRadius: 6,
                      cursor: "pointer",
                      marginBottom: 4,
                      background: createSection === s.key ? "rgba(22,119,255,0.1)" : undefined,
                      color: createSection === s.key ? "#1677ff" : undefined,
                      fontWeight: createSection === s.key ? 600 : 400,
                    }}
                  >
                    {s.icon}
                    {s.label}
                  </div>
                ))}
              </div>

              {/* 右侧分区内容 */}
              <div style={{ flex: 1, overflowY: "auto", maxHeight: 560 }}>
                {createSection === "basic" && (
                  <>
                    <Form.Item
                      name="name"
                      label="知识库名称"
                      rules={[{ required: true, whitespace: true, message: "请输入知识库名称" }]}
                    >
                      <Input placeholder="如：监管制度知识库" maxLength={255} />
                    </Form.Item>
                    <Form.Item name="description" label="描述（可选）">
                      <Input.TextArea rows={3} placeholder="知识库用途说明" maxLength={1000} showCount />
                    </Form.Item>
                    <Form.Item
                      name="scope"
                      label="归属"
                      initialValue="personal"
                      extra="个人知识库仅自己可见，团队知识库团队成员可见，系统知识库所有用户可见（仅管理员可建）"
                    >
                      <Select
                        showSearch
                        optionFilterProp="label"
                        options={[
                          { label: "个人", value: "personal" },
                          { label: "团队", value: "team" },
                          { label: "系统", value: "system" },
                        ]}
                      />
                    </Form.Item>
                    <Form.Item
                      name="label"
                      label="分类（可选）"
                      extra="选择左侧分类树中的已有分类；留空 = 未分类（可在知识库配置中或拖拽卡片调整）"
                    >
                      <Select
                        allowClear
                        showSearch
                        optionFilterProp="label"
                        placeholder="选择分类"
                        options={categories.map((c) => ({ label: c.name, value: c.name }))}
                      />
                    </Form.Item>
                  </>
                )}
                {createSection === "source" && (
                  <>
                    <Form.Item
                      name="repo_type"
                      label="知识来源"
                      initialValue="file"
                      extra="文档上传 = 手动上传文件/目录构建知识库；Git 代码仓库 = 创建后自动拉取仓库代码并按目录结构建立知识库"
                    >
                      <Select
                        showSearch
                        optionFilterProp="label"
                        options={[
                          { label: "文档上传（手动）", value: "file" },
                          { label: "Git 代码仓库（自动拉取）", value: "git" },
                        ]}
                      />
                    </Form.Item>
                    {repoType === "git" && (
                      <>
                        <Form.Item
                          name="repo_url"
                          label="仓库地址"
                          rules={[{ required: true, message: "请输入 Git 仓库地址" }]}
                          extra="支持 http/https/ssh/git@ 地址，如 https://git.example.com/team/repo.git"
                        >
                          <Input placeholder="https://git.example.com/team/repo.git" />
                        </Form.Item>
                        <Form.Item name="repo_branch" label="分支（可选）">
                          <Input placeholder="留空 = 仓库默认分支" />
                        </Form.Item>
                        <Form.Item
                          name="repo_auth_type"
                          label="拉取凭证"
                          initialValue="none"
                        >
                          <Select
                            showSearch
                            optionFilterProp="label"
                            options={[
                              { label: "无凭证（公开仓库）", value: "none" },
                              { label: "Token（访问令牌）", value: "token" },
                              { label: "SSH 私钥", value: "ssh_key" },
                              { label: "用户名 + 密码", value: "user_pass" },
                            ]}
                          />
                        </Form.Item>
                        {(repoAuthType ?? "none") !== "none" && (
                          <Form.Item
                            name="repo_auth_secret"
                            label={repoAuthType === "ssh_key" ? "SSH 私钥内容" : repoAuthType === "user_pass" ? "用户名:密码（冒号分隔）" : "Token"}
                            rules={[{ required: true, message: "请输入凭证内容" }]}
                          >
                            <Input.TextArea
                              rows={repoAuthType === "ssh_key" ? 6 : 2}
                              placeholder={
                                repoAuthType === "ssh_key"
                                  ? "-----BEGIN OPENSSH PRIVATE KEY----- ..."
                                  : repoAuthType === "user_pass"
                                    ? "username:password"
                                    : "ghp_xxxxxxxxxxxx"
                              }
                            />
                          </Form.Item>
                        )}
                        <div style={{ padding: 8, background: "#f5f7fa", borderRadius: 6, color: "#4d5e7d", fontSize: 12 }}>
                          创建后将自动拉取仓库代码存入对象存储，仓库目录会镜像为知识库文档目录，文件进入解析与 Wiki 构建编排链路。
                        </div>
                      </>
                    )}
                  </>
                )}
                {createSection === "wiki" && (
                  <>
                    <Form.Item
                      name="build_engine"
                      label="构建引擎"
                      extra="选择服务编排中已发布的编排程序，文档解析完成后自动按该编排构建 Wiki（录入需先到「服务编排」设计并发布）；留空 = 使用内置构建"
                    >
                      <Select
                        allowClear
                        showSearch
                        optionFilterProp="label"
                        placeholder={buildEngines.length ? "选择构建引擎" : "暂无已发布的编排，请先到服务编排发布"}
                        options={groupBuildEngineOptions(buildEngines)}
                      />
                    </Form.Item>
                    <Form.Item
                      name="summary_model_id"
                      label="大语言模型（LLM）"
                      extra="Wiki 合成/文档理解用；留空 = 系统默认"
                    >
                      <Select allowClear showSearch optionFilterProp="label" options={chatOptions} placeholder="选择大语言模型" />
                    </Form.Item>
                  </>
                )}

                {createSection === "retrieval" && (
                  <>
                    <Form.Item
                      name="vector_enabled"
                      label="向量检索"
                      valuePropName="checked"
                      initialValue={true}
                      extra="基于语义相似度召回，需配置 Embedding 模型"
                    >
                      <Switch />
                    </Form.Item>
                    <Form.Item
                      name="keyword_enabled"
                      label="关键词检索"
                      valuePropName="checked"
                      initialValue={true}
                      extra="基于关键词子串匹配召回（LIKE 检索）"
                    >
                      <Switch />
                    </Form.Item>
                    <Form.Item
                      name="embedding_model_id"
                      label="向量模型（Embedding）"
                      extra="知识向量化用；留空 = 系统默认"
                    >
                      <Select allowClear showSearch optionFilterProp="label" options={embeddingOptions} placeholder="选择向量模型" />
                    </Form.Item>
                  </>
                )}
              </div>
            </div>
          </Form>
        </Modal>

        {/* 新建 / 重命名分类弹窗（2026-10-19 分类树 CRUD） */}
        <Modal
          title={catModal?.mode === "rename" ? "重命名分类" : "新建分类"}
          open={catModal !== null}
          onOk={() => void submitCatModal()}
          onCancel={() => setCatModal(null)}
          okText="保存"
          cancelText="取消"
          confirmLoading={catSubmitting}
          destroyOnClose
        >
          <Form layout="vertical" style={{ marginTop: 8 }}>
            <Form.Item label="分类名称" required>
              <Input
                value={catName}
                onChange={(e) => setCatName(e.target.value)}
                placeholder="如：制度 / 技术"
                maxLength={64}
                onPressEnter={() => void submitCatModal()}
              />
            </Form.Item>
          </Form>
        </Modal>

        <KBConfigModal
          kb={configKb}
          open={configKb !== null}
          onClose={(changed) => {
            setConfigKb(null);
            if (changed) void load();
          }}
        />
    </div>
  );

  return (
    // 2026-10-08: 对齐用户管理页范式——外层定高不滚动，卡片内「筛选固定 / 卡片视图滚动 / 分页钉底」
    <div
      className="kbs-page"
      style={{
        height: "calc(100vh - var(--app-header-h, 48px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
        background: "#fff",
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action !== "remove") return;
          const k = String(key);
          if (k.startsWith("doc-")) closeDocTab(k);
          else closeTab(k);
        }}
        tabBarExtraContent={
          <div className="kb-kbs-tab-actions">
            <Input
              className="kb-kbs-search"
              placeholder="输入并搜索..."
              prefix={<SearchColorIcon size={14} />}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onPressEnter={() => setSearch(search.trim())}
            />
            <Button
              className="kb-kbs-new-btn"
              type="primary"
              icon={<AddColorIcon size={12} />}
              onClick={openCreate}
            >
              新建
            </Button>
          </div>
        }
        items={[
          {
            key: "home",
            // 页面标题：DSB header-title-icon.png（16px）置于标题左侧
            label: (
              <span className="kb-kbs-tab-title">
                <img className="kb-kbs-tab-title-icon" src={headerTitleIcon} alt="" />
                知识库管理
              </span>
            ),
            children: listPane,
          },
          ...tabs.map((t) => ({
            key: t.key,
            label: t.title,
            closable: true,
            children: renderKbSection(t),
          })),
          // 文档详情：点击文件打开的动态选项卡（可关闭），承载 DocDetailPanel
          ...docTabs.map((t) => ({
            key: t.key,
            label: t.doc.file_name,
            closable: true,
            children: (
              <DocDetailPanel
                kbId={t.kbId}
                doc={t.doc}
                onDocReparsed={() => reparseDoc(t.kbId)}
              />
            ),
          })),
        ]}
      />
    </div>
  );
}