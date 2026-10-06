"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Dropdown,
  Empty,
  Form,
  Input,
  Modal,
  Pagination,
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
  PushpinOutlined,
  SlidersOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import {
  apiCreateKb,
  apiDeleteKb,
  apiListDatasources,
  apiListKbs,
  apiListModels,
  apiListSkillsRegistry,
  DatasourceItem,
  KbCreatePayload,
  KbItem,
  ModelItem,
} from "@/lib/api";
import ChunkingConfigModal from "@/components/ChunkingConfigModal";
import KBConfigModal from "@/components/KBConfigModal";

const PIPELINE_OPTIONS = [
  { label: "向量检索", value: "vector", default: true },
  { label: "关键词检索", value: "keyword", default: true },
  { label: "Wiki 构建", value: "wiki", default: false },
  { label: "知识图谱", value: "graph", default: false },
];

const PIN_KEY = "kb.top";

const getPinned = (): Set<string> => {
  try {
    return new Set(JSON.parse(localStorage.getItem(PIN_KEY) || "[]") as string[]);
  } catch {
    return new Set();
  }
};

export default function KbsPage() {
  const { message, modal } = App.useApp();
  const router = useRouter();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [chunkingKbId, setChunkingKbId] = useState<string | null>(null);
  const [skills, setSkills] = useState<{ name: string; description?: string }[]>([]);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [vectorStores, setVectorStores] = useState<DatasourceItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [pinned, setPinned] = useState<Set<string>>(() => getPinned());
  const [configKb, setConfigKb] = useState<KbItem | null>(null);
  const [form] = Form.useForm();

  const customWiki = Form.useWatch("custom_wiki_generation", form) ?? false;

  const togglePin = (id: string) => {
    setPinned((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      try {
        localStorage.setItem(PIN_KEY, JSON.stringify([...next]));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const visibleKbs = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q
      ? kbs.filter(
          (k) =>
            k.name.toLowerCase().includes(q) ||
            (k.label || "").toLowerCase().includes(q)
        )
      : kbs;
    return [...filtered].sort((a, b) => {
      const pa = pinned.has(a.id) ? 1 : 0;
      const pb = pinned.has(b.id) ? 1 : 0;
      if (pa !== pb) return pb - pa;
      return 0;
    });
  }, [kbs, search, pinned]);

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
        if (res.success && res.data) setModels(res.data.items);
      } catch {
        // 模型列表加载失败不阻断创建（留空走系统默认模型）
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

  const onCreate = async () => {
    const values = await form.validateFields();
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
      configs: values.wiki_skill ? { wiki_config: { skill: values.wiki_skill } } : undefined,
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

  return (
    <Card
      title="知识库管理"
      extra={
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="搜索知识库名称/标签"
            style={{ width: 240 }}
            onSearch={(v) => setSearch(v)}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建知识库
          </Button>
        </Space>
      }
    >
      {visibleKbs.length === 0 ? (
        <Empty description="暂无知识库，点击右上角「新建知识库」创建">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建知识库
          </Button>
        </Empty>
      ) : (
        <Row gutter={[16, 16]}>
          {visibleKbs.map((kb) => {
            const isFaq = kb.type === "faq";
            const graphOn = Boolean(kb.indexing_strategy?.graph_enabled);
            const vlmOn = kb.vlm_config && Object.keys(kb.vlm_config).length > 0;
            const pinnedNow = pinned.has(kb.id);
            return (
              <Col key={kb.id} xs={24} sm={12} lg={8} xl={6}>
                <Card
                  size="small"
                  hoverable
                  style={{ height: "100%" }}
                  onClick={() => router.push(`/kbs/${kb.id}`)}
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
                          { key: "config", label: "知识库配置" },
                          { key: "chunking", label: "切片配置" },
                          { type: "divider" },
                          pinnedNow
                            ? { key: "unpin", label: "取消置顶" }
                            : { key: "pin", label: "置顶", icon: <PushpinOutlined /> },
                          { type: "divider" },
                          { key: "delete", label: "删除", danger: true },
                        ],
                        onClick: ({ key, domEvent }) => {
                          domEvent.stopPropagation();
                          if (key === "open") router.push(`/kbs/${kb.id}`);
                          else if (key === "config") setConfigKb(kb);
                          else if (key === "chunking") setChunkingKbId(kb.id);
                          else if (key === "pin") togglePin(kb.id);
                          else if (key === "unpin") togglePin(kb.id);
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
                    {pinnedNow && (
                      <Tag icon={<PushpinOutlined />} color="gold">
                        置顶
                      </Tag>
                    )}
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
        <Pagination
          current={page}
          pageSize={pageSize}
          total={total}
          showSizeChanger
          showTotal={(t) => `共 ${t} 个知识库`}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      </div>

      <Modal
        title="新建知识库"
        open={createOpen}
        onOk={onCreate}
        onCancel={() => setCreateOpen(false)}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
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
            extra="开启后上传文档不会自动构建 Wiki，需手动触发（可在开启时指定构建技能）"
          >
            <Switch />
          </Form.Item>
          {customWiki && (
            <Form.Item
              name="wiki_skill"
              label="构建技能"
              extra="选择 Wiki 构建使用的技能（留空则使用系统默认技能）。技能由 agent worker 内联执行"
            >
              <Select
                allowClear
                placeholder="留空 = 系统默认技能"
                options={skills.map((s) => ({
                  label: s.description ? `${s.name}（${s.description}）` : s.name,
                  value: s.name,
                }))}
              />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <ChunkingConfigModal
        open={chunkingKbId !== null}
        kbId={chunkingKbId ?? ""}
        onClose={() => setChunkingKbId(null)}
      />
      <KBConfigModal
        kb={configKb}
        open={configKb !== null}
        onClose={(changed) => {
          setConfigKb(null);
          if (changed) void load();
        }}
      />
    </Card>
  );
}