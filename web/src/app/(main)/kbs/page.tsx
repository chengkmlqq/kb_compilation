"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
  Tooltip,
  Typography,
} from "antd";
import {
  BookOutlined,
  DeleteOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  apiCreateKb,
  apiDeleteKb,
  apiGetDocument,
  apiListBuildEngines,
  apiListDatasources,
  apiListKbs,
  apiListModels,
  apiListSkillsRegistry,
  BuildEngineItem,
  DatasourceItem,
  DocItem,
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
import WikiGraphView from "@/components/WikiGraphView";
import DocDetailPanel from "@/components/DocDetailPanel";

// 2026-10-16 对齐 data_supply_base corpus_info（KnowledgeSetOntology）页：图片资源从
// data_supply_base/data-supply-front/src/pages/KnowledgeSetOntology/images/ 原样复制，
// 部署前缀 /wiki（next.config basePath）。
const KBS_ASSET_BASE = "/wiki/kbs";
const headerTitleIcon = `${KBS_ASSET_BASE}/header-title-icon.png`;
const cardTitleIcon = `${KBS_ASSET_BASE}/card-title-icon.png`;
const cardOperateIcon = `${KBS_ASSET_BASE}/card-operate-icon.png`;
const cardLockIcon = `${KBS_ASSET_BASE}/card-lock-icon.png`;

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

/** arco IconCaretDown（Tree 切换器三角：viewBox 48 / 实心 path）；容器字号 9px → 1em = 9px */
const CaretDownIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" aria-hidden>
    <path
      fill="currentColor"
      stroke="none"
      d="M24.938 34.829a1.2 1.2 0 0 1-1.875 0L9.56 17.949c-.628-.785-.069-1.949.937-1.949h27.007c1.006 0 1.565 1.164.937 1.95L24.937 34.829Z"
    />
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

// 新建知识库弹窗分区导航（对齐智能体编辑器 AgentEditorModal 与配置弹窗 KBConfigModal）
const CREATE_SECTIONS = [
  { key: "wiki", label: "Wiki 构建", icon: <BookOutlined /> },
  { key: "retrieval", label: "检索配置", icon: <SearchOutlined /> },
];

/** 知识库详情分段：文档 / Wiki / 知识图谱(Neo4j) / wiki 关系图，各自成为顶层动态选项卡 */
type KbSection = "docs" | "wiki" | "graph" | "wiki-graph";
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
};

/** 文档详情选项卡（点击文件打开，可关闭）：key = doc-<docId>，内容为 DocDetailPanel */
type KbDocTab = {
  key: string;
  kbId: string;
  doc: DocItem;
};

export default function KbsPage() {
  const { message, modal } = App.useApp();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [skills, setSkills] = useState<{ name: string; description?: string }[]>([]);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [vectorStores, setVectorStores] = useState<DatasourceItem[]>([]);
  const [buildEngines, setBuildEngines] = useState<BuildEngineItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [configKb, setConfigKb] = useState<KbItem | null>(null);
  const [form] = Form.useForm();
  const [createSection, setCreateSection] = useState("wiki");
  // 2026-10-16 DSB 知识集页对齐：左侧分类树（按 label 分组筛选）
  const [selectedLabel, setSelectedLabel] = useState<string>("all");
  // 2026-10-16 DSB corpus_info 对齐：分类树顶部「搜索分类」框
  const [categoryKeyword, setCategoryKeyword] = useState("");
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

  // DSB 对齐：左侧分类树 = 「全部」+ 各标签（DSB 分类树不展示计数，按卡片数倒序）
  const labels = useMemo(() => {
    const map = new Map<string, number>();
    for (const k of kbs) {
      const l = k.label || "无标签";
      map.set(l, (map.get(l) || 0) + 1);
    }
    return Array.from(map.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([label]) => label);
  }, [kbs]);

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
  // 向量库可选资源：数据源管理中 Elasticsearch / PostgreSQL 类型的资源
  useEffect(() => {
    void (async () => {
      try {
        const res = await apiListDatasources(1, 200);
        const items =
          res.success && res.data?.items
            ? res.data.items.filter(
                (d) =>
                  d.dsType === "elasticsearch" ||
                  d.dsType === "postgresql" ||
                  d.dsType === "pg"
              )
            : [];
        setVectorStores(items);
      } catch {
        // 向量库资源加载失败不阻断创建（留空走系统默认向量库）
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

  const openCreate = () => {
    form.resetFields();
    setCreateSection("wiki"); // 对齐智能体编辑器：每次打开回到首个分区
    setCreateOpen(true);
  };

  const onCreate = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    const payload: KbCreatePayload = {
      name: values.name,
      label: values.label,
      description: values.description,
      scope: values.scope || "personal",
      type: values.type || "document",
      indexing_strategy: {
        vector_enabled: !!values.vector_enabled,
        keyword_enabled: !!values.keyword_enabled,
        // 「Wiki 构建」开关表单已移除（2026-10-15）：新建不再需要该开关——
        // 配置了构建引擎（build_engine）即自动触发构建任务
        wiki_enabled: true,
        // 知识图谱开关表单已移除（2026-10-15）：新建默认关闭（对齐后端新库默认）
        graph_enabled: false,
      },
      embedding_model_id: values.embedding_model_id || undefined,
      summary_model_id: values.summary_model_id || undefined,
      vector_store_id: values.vector_store_id || undefined,
      build_tape_id: values.build_engine || undefined,
      // 摘要策略固定 auto：系统按文档规模自动路由（小文档全文直塞 / 大文档分块合并），不再开放给用户选择
      summary_strategy: "auto",
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

  const onDelete = (kb: KbItem) => {
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
              value={categoryKeyword}
              onChange={(e) => setCategoryKeyword(e.target.value)}
              suffix={<SearchLineIcon />}
            />
          </div>
          <div className="kb-kbs-category-tree-main">
            {/* 根节点「全部」（对齐 arco Tree aria-level=0：切换器 12x12 底片 + 9px 三角） */}
            <div
              className="kb-kbs-tree-node kb-kbs-tree-node--root"
              onClick={() => setSelectedLabel("all")}
            >
              <span className="kb-kbs-tree-indent" aria-hidden="true" />
              <span
                className="kb-kbs-tree-switcher"
                onClick={(e) => {
                  e.stopPropagation();
                  setTreeExpanded((v) => !v);
                }}
              >
                <span className={`kb-kbs-tree-switcher-icon${treeExpanded ? " expanded" : ""}`}>
                  <CaretDownIcon />
                </span>
              </span>
              <span
                className={`kb-kbs-tree-title${selectedLabel === "all" ? " active" : ""}`}
                title="全部"
              >
                全部
              </span>
            </div>
            {treeExpanded &&
              labels.map((l) => (
                <div key={l} className="kb-kbs-tree-node" onClick={() => setSelectedLabel(l)}>
                  <span className="kb-kbs-tree-indent" aria-hidden="true">
                    <span className="kb-kbs-tree-indent-block" />
                  </span>
                  <span className="kb-kbs-tree-switcher" />
                  <span
                    className={`kb-kbs-tree-title${selectedLabel === l ? " active" : ""}`}
                    title={l}
                  >
                    {l}
                  </span>
                </div>
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
                  <div className="kb-kbs-card" onClick={() => openKbSection(kb, "docs")}>
                    <div className="kb-kbs-card-header">
                      <div className="kb-kbs-card-icon">
                        <img src={cardTitleIcon} alt="" />
                      </div>
                      <div className="kb-kbs-card-detail">
                        <div className="kb-kbs-card-title-row">
                          <div className="kb-kbs-card-title-name" title={kb.name}>
                            {kb.name}
                          </div>
                          {/* 私有知识库标记（对齐 DSB：card-lock-icon.png 14px + 「私有」气泡） */}
                          {(kb.scope || "personal") === "personal" && (
                            <Tooltip title="私有">
                              <img className="kb-kbs-card-title-flag" src={cardLockIcon} alt="" />
                            </Tooltip>
                          )}
                        </div>
                        <div className="kb-kbs-card-meta">
                          {kb.doc_count ?? 0}个文件 · 更新于
                          {kb.created_at ? kb.created_at.slice(5, 10).replace("-", "/") : "-"}
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
          <Form form={form} layout="vertical" preserve={false}>
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item
                  name="name"
                  label="知识库名称"
                  rules={[{ required: true, message: "请输入知识库名称" }]}
                >
                  <Input placeholder="如：监管制度知识库" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="label" label="标签（可选）">
                  <Input placeholder="如：制度 / 技术" />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item name="description" label="描述（可选）">
              <Input.TextArea rows={2} placeholder="知识库用途说明" />
            </Form.Item>
            <Form.Item
              name="scope"
              label="归属"
              initialValue="personal"
              extra="个人知识库仅自己可见，团队知识库团队成员可见，系统知识库所有用户可见（仅管理员可建）"
            >
              <Select
                options={[
                  { label: "个人", value: "personal" },
                  { label: "团队", value: "team" },
                  { label: "系统", value: "system" },
                ]}
              />
            </Form.Item>

            <div style={{ display: "flex", gap: 16, minHeight: 300 }}>
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
              <div style={{ flex: 1, overflowY: "auto", maxHeight: 480 }}>
                {createSection === "wiki" && (
                  <>
                    <Form.Item
                      name="build_engine"
                      label="构建引擎"
                      extra="选择服务编排中已发布的编排程序，文档解析完成后自动按该编排构建 Wiki（录入需先到「服务编排」设计并发布）；留空 = 使用内置构建"
                    >
                      <Select
                        allowClear
                        placeholder={buildEngines.length ? "选择构建引擎" : "暂无已发布的编排，请先到服务编排发布"}
                        options={groupBuildEngineOptions(buildEngines)}
                      />
                    </Form.Item>
                    <Form.Item
                      name="summary_model_id"
                      label="大语言模型（LLM）"
                      extra="Wiki 合成/文档理解用；留空 = 系统默认"
                    >
                      <Select allowClear options={chatOptions} placeholder="选择大语言模型" />
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
                      <Select allowClear options={embeddingOptions} placeholder="选择向量模型" />
                    </Form.Item>
                    <Form.Item
                      name="vector_store_id"
                      label="向量数据库"
                      initialValue=""
                      extra="选择该知识库使用的向量库来源（数据源管理中配置的 ES / PostgreSQL 资源）；创建后不可切换，留空 = 系统默认（pgvector）"
                    >
                      <Select
                        options={[
                          { label: "默认（系统配置 pgvector）", value: "" },
                          ...vectorStores.map((s) => ({
                            label: `${s.dsLabel || s.dsName || s.id}（${s.dsType}）`,
                            value: s.id as string,
                          })),
                        ]}
                        placeholder="选择向量数据库"
                      />
                    </Form.Item>
                  </>
                )}
              </div>
            </div>
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
            <Input.Search
              className="kb-kbs-search"
              placeholder="输入并搜索..."
              prefix={<SearchColorIcon size={14} />}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onSearch={(v) => setSearch(v.trim())}
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