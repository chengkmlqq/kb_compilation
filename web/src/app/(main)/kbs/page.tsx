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
  Radio,
  Row,
  Select,
  Space,
  Switch,
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
  PictureOutlined,
  PlusOutlined,
  SlidersOutlined,
} from "@ant-design/icons";
import {
  apiCreateKb,
  apiDeleteKb,
  apiListDatasources,
  apiListKbs,
  apiListModels,
  apiListOntologySchemas,
  apiListSkillsRegistry,
  DatasourceItem,
  KbCreatePayload,
  KbItem,
  ModelItem,
  OntologySchemaGroup,
} from "@/lib/api";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import KBConfigModal from "@/components/KBConfigModal";
import KbDocsPane from "@/components/kb/KbDocsPane";
import KbWikiPane from "@/components/kb/KbWikiPane";
import KbGraphPane from "@/components/kb/KbGraphPane";

const PIPELINE_OPTIONS = [
  { label: "向量检索", value: "vector", default: true },
  { label: "关键词检索", value: "keyword", default: true },
  { label: "Wiki 构建", value: "wiki", default: false },
  { label: "知识图谱", value: "graph", default: false },
];

/** 知识库详情分段：文档 / Wiki / 知识图谱，各自成为顶层动态选项卡 */
type KbSection = "docs" | "wiki" | "graph";
/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务分段 */
type KbSectionTab = { key: string; title: string; kbId: string; section: KbSection };

const SECTION_LABEL: Record<KbSection, string> = {
  docs: "文档",
  wiki: "Wiki",
  graph: "图谱",
};

export default function KbsPage() {
  const { message, modal } = App.useApp();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [skills, setSkills] = useState<{ name: string; description?: string }[]>([]);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [vectorStores, setVectorStores] = useState<DatasourceItem[]>([]);
  const [ontologySchemas, setOntologySchemas] = useState<OntologySchemaGroup[]>([]);
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

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  /** 打开某知识库的某个分段选项卡：已存在则仅激活，否则新增后激活 */
  const openKbSection = (kb: KbItem, section: KbSection) => {
    const key = `kb-${kb.id}-${section}`;
    setTabs((prev) =>
      prev.some((t) => t.key === key)
        ? prev
        : [
            ...prev,
            {
              key,
              title: `${SECTION_LABEL[section]} · ${kb.name}`,
              kbId: kb.id,
              section,
            },
          ]
    );
    setActiveTab(key);
  };

  /** 按分段类型渲染对应 pane（渲染时构造 children，避免把 ReactNode 塞进 state） */
  const renderKbSection = (t: KbSectionTab) => {
    if (t.section === "wiki") {
      return (
        <KbWikiPane
          kbId={t.kbId}
          onOpenGraph={() => {
            const kb = kbs.find((k) => k.id === t.kbId);
            if (kb) openKbSection(kb, "graph");
          }}
        />
      );
    }
    if (t.section === "graph") return <KbGraphPane kbId={t.kbId} />;
    return <KbDocsPane kbId={t.kbId} />;
  };

  const customWiki = Form.useWatch("custom_wiki_generation", form) ?? false;

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

  // 本体 Schema 下拉数据源（抽取分类结构，多领域）
  useEffect(() => {
    void (async () => {
      try {
        const res = await apiListOntologySchemas();
        setOntologySchemas(res.data?.schemas || []);
      } catch {
        // schema 列表加载失败不阻断创建（Select 为空时由必填校验提示）
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
      custom_wiki_generation: values.custom_wiki_generation,
      embedding_model_id: values.embedding_model_id || undefined,
      summary_model_id: values.summary_model_id || undefined,
      vector_store_id: values.vector_store_id || undefined,
      ontology_schema_name: values.ontology_schema_name,
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
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            padding: "12px 16px 0",
          },
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

        {/* 卡片视图（保留）——flex:1 占满剩余高度，内容超出时内部滚动 */}
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
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
                const vlmOn = kb.vlm_config && Object.keys(kb.vlm_config).length > 0;
                return (
                  <Col key={kb.id} xs={24} sm={12} lg={8} xl={6}>
                    <Card
                      className="kb-list-card"
                      size="small"
                      hoverable
                      style={{ height: "100%" }}
                      onClick={() => openKbSection(kb, "docs")}
                      title={
                        <Space>
                          <span
                            style={{
                              fontSize: 20,
                              color: isFaq ? "#52c41a" : "#1677ff",
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
                              { key: "config", label: "知识库配置" },
                              { type: "divider" },
                              { key: "delete", label: "删除", danger: true },
                            ],
                            onClick: ({ key, domEvent }) => {
                              domEvent.stopPropagation();
                              if (key === "open") openKbSection(kb, "docs");
                              else if (key === "wiki") openKbSection(kb, "wiki");
                              else if (key === "graph") openKbSection(kb, "graph");
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
                        style={{ minHeight: 44, marginBottom: 8 }}
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
                        {vlmOn && (
                          <Tag icon={<PictureOutlined />} color="orange">
                            多模态
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
            <Form.Item name="type" label="知识库类型" initialValue="document">
              <Radio.Group optionType="button" buttonStyle="solid">
                <Radio.Button value="document">文档型</Radio.Button>
                <Radio.Button value="faq">问答对型</Radio.Button>
              </Radio.Group>
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
              name="pipelines"
              label="索引流水线"
              initialValue={["vector", "keyword"]}
              extra="向量/关键词检索默认开启；Wiki 构建与知识图谱需显式开启（上传文档后才会自动构建）"
            >
              <Checkbox.Group options={PIPELINE_OPTIONS.map((o) => ({ label: o.label, value: o.value }))} />
            </Form.Item>
            <Form.Item
              name="ontology_schema_name"
              label="本体 Schema（抽取分类结构）"
              rules={[{ required: true, message: "请选择本体 Schema" }]}
              extra="定义文档抽取的业务/规则分类结构与提示词，支持多领域（市场监管法规、供管制度等）"
            >
              <Select
                placeholder="选择 Schema"
                options={ontologySchemas.map((s) => ({
                  label: `${s.schema_label}（业务${s.business.length}类/规则${s.rule.length}类）`,
                  value: s.schema_name,
                }))}
              />
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
            <Form.Item
              name="custom_wiki_generation"
              label="自定义 Wiki 生成"
              valuePropName="checked"
              extra="开启后上传文档不会自动构建 Wiki，需手动触发（构建引擎由所选本体 Schema 决定）"
            >
              <Switch />
            </Form.Item>
            {customWiki && (
              <Form.Item name="wiki_skill" hidden>
                <Select />
              </Form.Item>
            )}
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
      </Card>
    </div>
  );

  return (
    // 2026-10-08: 对齐用户管理页范式——外层定高不滚动，卡片内「筛选固定 / 卡片视图滚动 / 分页钉底」
    <div
      className="kbs-page"
      style={{
        padding: 8,
        height: "calc(100vh - 45px)",
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
          if (action === "remove") closeTab(String(key));
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
        ]}
      />
    </div>
  );
}