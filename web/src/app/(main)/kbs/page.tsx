"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Drawer,
  Dropdown,
  Empty,
  Form,
  Input,
  Row,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  ApartmentOutlined,
  BookOutlined,
  DeleteOutlined,
  FileTextOutlined,
  MessageOutlined,
  MoreOutlined,
  PlusOutlined,
  SlidersOutlined,
} from "@ant-design/icons";
import {
  apiCreateKb,
  apiDeleteKb,
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
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import KBConfigModal from "@/components/KBConfigModal";
import KbDocsPane from "@/components/kb/KbDocsPane";
import KbWikiPane from "@/components/kb/KbWikiPane";
import KbGraphPane from "@/components/kb/KbGraphPane";
import WikiGraphView from "@/components/WikiGraphView";
import DocDetailPanel from "@/components/DocDetailPanel";

const PIPELINE_OPTIONS = [
  { label: "向量检索", value: "vector", default: true },
  { label: "关键词检索", value: "keyword", default: true },
  { label: "Wiki 构建", value: "wiki", default: false },
  { label: "知识图谱", value: "graph", default: false },
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
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [configKb, setConfigKb] = useState<KbItem | null>(null);
  const [form] = Form.useForm();
  const [searchForm] = Form.useForm<{ keyword: string }>();

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
      const exists = prev.find((t) => t.key === key);
      if (exists) {
        // 已存在：仅更新 focusSlug（wiki 关系图 ego 聚焦），不重复开
        if (exists.focusSlug === focusSlug) return prev;
        return prev.map((t) => (t.key === key ? { ...t, focusSlug } : t));
      }
      return [
        ...prev,
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
    return q
      ? kbs.filter(
          (k) =>
            k.name.toLowerCase().includes(q) ||
            (k.label || "").toLowerCase().includes(q)
        )
      : kbs;
  }, [kbs, search]);

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
    setCreateOpen(true);
  };

  const onCreate = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    const picked: string[] = values.pipelines || [];
    const payload: KbCreatePayload = {
      name: values.name,
      label: values.label,
      description: values.description,
      scope: values.scope || "personal",
      type: values.type || "document",
      indexing_strategy: {
        vector_enabled: picked.includes("vector"),
        keyword_enabled: picked.includes("keyword"),
        wiki_enabled: picked.includes("wiki"),
        graph_enabled: picked.includes("graph"),
      },
      embedding_model_id: values.embedding_model_id || undefined,
      summary_model_id: values.summary_model_id || undefined,
      vector_store_id: values.vector_store_id || undefined,
      build_tape_id: values.build_engine || undefined,
      summary_strategy: values.summary_strategy || "auto",
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

  // 筛选（对齐用户管理页：查询/重置 显式触发；本地过滤当前页数据）
  const doSearch = (vals: { keyword?: string }) => {
    setSearch(vals.keyword?.trim() || "");
  };

  const doReset = () => {
    searchForm.resetFields();
    setSearch("");
  };

  // 列表区（首个选项卡内容）：筛选 + 卡片视图 + 钉底分页 + 新建抽屉/配置弹窗
  const listPane = (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        padding: "12px 16px 0",
      }}
    >
        {/* 筛选表单（对齐用户管理页：查询/重置 显式触发） */}
        <Form
          form={searchForm}
          layout="inline"
          style={{ marginBottom: 12, flexShrink: 0 }}
          onFinish={doSearch}
          initialValues={{ keyword: "" }}
        >
          <Form.Item name="keyword" label="知识库">
            <Input allowClear placeholder="搜索名称/标签" style={{ width: 220 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button
                onClick={() => {
                  searchForm.resetFields();
                  doReset();
                }}
              >
                重置
              </Button>
            </Space>
          </Form.Item>
        </Form>

        {/* 卡片视图（保留）——flex:1 占满剩余高度，内容超出时内部滚动。
            横向隐藏：antd Row 的 gutter 会给行加 -8px 左右外边距，宽出容器 16px，
            双轴 overflow:auto 会因此常驻横向滚动条（卡片内容本身并未超出）。 */}
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden" }}>
          {visibleKbs.length === 0 ? (
            <Empty description="暂无知识库，点击右上角「新建知识库」创建">
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                新建知识库
              </Button>
            </Empty>
          ) : (
            <Row gutter={[16, 16]}>
              {visibleKbs.map((kb) => {
                const isFaq = kb.type === "faq";
                const graphOn = Boolean(kb.indexing_strategy?.graph_enabled);
                return (
                  <Col key={kb.id} xs={24} sm={12} lg={8} xl={6}>
                    <Card
                      className="kb-list-card"
                      size="small"
                      hoverable
                      style={{ height: 150, overflow: "hidden" }}
                      onClick={() => openKbSection(kb, "docs")}
                      title={
                        <Space>
                          <span
                            style={{
                              fontSize: 20,
                              color: isFaq ? "#52c41a" : "#3261ce",
                            }}
                          >
                            {isFaq ? <MessageOutlined /> : <FileTextOutlined />}
                          </span>
                          <Typography.Text strong ellipsis style={{ maxWidth: 140 }}>
                            {kb.name}
                          </Typography.Text>
                        </Space>
                      }
                      extra={
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
                          <Button
                            type="text"
                            size="small"
                            icon={<MoreOutlined />}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </Dropdown>
                      }
                    >
                      <Typography.Paragraph
                        type="secondary"
                        ellipsis={{ rows: 2 }}
                        style={{ minHeight: 40, marginBottom: 8, fontSize: 12, color: "#4d5e7d" }}
                      >
                        {kb.description || "（无描述）"}
                      </Typography.Paragraph>
                      <Space wrap size={4}>
                        <Tag color="blue">
                          <FileTextOutlined /> 文档 {kb.doc_count ?? 0}
                        </Tag>
                        <Tag color="green">
                          <BookOutlined /> Wiki {kb.page_count ?? 0}
                        </Tag>
                        {kb.label && <Tag>{kb.label}</Tag>}
                        {graphOn && (
                          <Tag icon={<ApartmentOutlined />} color="purple">
                            图谱
                          </Tag>
                        )}
                        {isFaq && <Tag color="cyan">FAQ</Tag>}
                      </Space>
                    </Card>
                  </Col>
                );
              })}
            </Row>
          )}
        </div>

        {/* 分页常驻底栏 */}
        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
            showTotal={(t) => `共 ${t} 个知识库`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
            }}
          />
        </div>

        <Drawer
          title="新建知识库"
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          width={560}
          extra={
            <Space>
              <Button onClick={() => setCreateOpen(false)}>取消</Button>
              <Button type="primary" onClick={() => void onCreate()}>
                创建
              </Button>
            </Space>
          }
        >
          <Form form={form} layout="vertical" preserve={false}>
            <Form.Item
              name="name"
              label="知识库名称"
              rules={[{ required: true, message: "请输入知识库名称" }]}
            >
              <Input placeholder="如：监管制度知识库" />
            </Form.Item>
            <Form.Item name="label" label="标签（可选）">
              <Input placeholder="如：制度 / 技术" />
            </Form.Item>
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
            <Form.Item
              name="build_engine"
              label="构建引擎"
              extra="选择服务编排中已发布的编排程序，文档解析完成后自动按该编排构建 Wiki（录入需先到「服务编排」设计并发布）；留空 = 使用内置构建"
            >
              <Select
                allowClear
                placeholder={buildEngines.length ? "选择构建引擎" : "暂无已发布的编排，请先到服务编排发布"}
                options={buildEngines.map((t) => ({
                  label: `${t.tape_label || t.tape_name}${t.tape_descr ? `（${t.tape_descr}）` : ""}`,
                  value: t.id,
                }))}
              />
            </Form.Item>
            <Form.Item
              name="summary_strategy"
              label="文档摘要策略"
              initialValue="auto"
              extra="上传文档解析成功后自动生成摘要页：自动＝按文档大小与是否已有摘要选择（小文档全文直塞，大文档分块合并，已有摘要增量修订）；也可固定使用某一种模式；关＝不生成"
            >
              <Select
                options={[
                  { label: "自动（按文档规模选择）", value: "auto" },
                  { label: "小文档全文直塞（Stuff）", value: "stuff" },
                  { label: "大文档分块合并（Map-Reduce）", value: "mapreduce" },
                  { label: "增量修订（Refine，需已有摘要）", value: "refine" },
                  { label: "关（不生成摘要）", value: "off" },
                ]}
              />
            </Form.Item>
            <Form.Item
              name="pipelines"
              label="索引流水线"
              initialValue={["vector", "keyword"]}
              extra="向量/关键词检索默认开启；Wiki 构建与知识图谱需显式开启（上传文档后才会自动构建）"
            >
              <Checkbox.Group options={PIPELINE_OPTIONS.map((o) => ({ label: o.label, value: o.value }))} />
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
            <Form.Item
              name="summary_model_id"
              label="大语言模型（LLM）"
              extra="Wiki 合成/文档理解用；留空 = 系统默认"
            >
              <Select allowClear options={chatOptions} placeholder="选择大语言模型" />
            </Form.Item>
          </Form>
        </Drawer>

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
        padding: 0,
        background: "#fff",
        height: "calc(100vh - var(--app-header-h, 45px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
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
          <Button type="primary" onClick={openCreate}>
            新建知识库
          </Button>
        }
        items={[
          { key: "home", label: "知识库管理", children: listPane },
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