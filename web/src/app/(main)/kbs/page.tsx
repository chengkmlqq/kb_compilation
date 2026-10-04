"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Radio,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import {
  BookOutlined,
  DeleteOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  PlusOutlined,
  SlidersOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import {
  apiCreateKb,
  apiDeleteKb,
  apiListKbs,
  apiListSkillsRegistry,
  KbCreatePayload,
  KbItem,
} from "@/lib/api";
import ChunkingConfigModal from "@/components/ChunkingConfigModal";

const PIPELINE_OPTIONS = [
  { label: "向量检索", value: "vector", default: true },
  { label: "关键词检索", value: "keyword", default: true },
  { label: "Wiki 构建", value: "wiki", default: false },
  { label: "知识图谱", value: "graph", default: false },
];

export default function KbsPage() {
  const { message, modal } = App.useApp();
  const router = useRouter();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [chunkingKbId, setChunkingKbId] = useState<string | null>(null);
  const [skills, setSkills] = useState<{ name: string; description?: string }[]>([]);
  const [form] = Form.useForm();

  const customWiki = Form.useWatch("custom_wiki_generation", form) ?? false;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListKbs(1, 50);
      if (res.success) {
        setKbs(res.data?.items || []);
      } else {
        message.error(res.message || "加载失败");
      }
    } finally {
      setLoading(false);
    }
  }, [message]);

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

  const onCreate = async () => {
    const values = await form.validateFields();
    const picked: string[] = values.pipelines || [];
    const payload: KbCreatePayload = {
      name: values.name,
      label: values.label,
      description: values.description,
      scope: values.scope || "system",
      type: values.type || "document",
      indexing_strategy: {
        vector_enabled: picked.includes("vector"),
        keyword_enabled: picked.includes("keyword"),
        wiki_enabled: picked.includes("wiki"),
        graph_enabled: picked.includes("graph"),
      },
      custom_wiki_generation: values.custom_wiki_generation,
      configs: values.wiki_skill ? { wiki_config: { skill: values.wiki_skill } } : undefined,
    };
    const res = await apiCreateKb(payload);
    if (res.success) {
      message.success("知识库已创建");
      setCreateOpen(false);
      form.resetFields();
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
          void load();
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
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建知识库
        </Button>
      }
    >
      <List
        loading={loading}
        dataSource={kbs}
        locale={{ emptyText: <Empty description="暂无知识库，点击右上角新建" /> }}
        renderItem={(kb) => (
          <List.Item
            actions={[
              <Button
                key="open"
                type="link"
                icon={<FolderOpenOutlined />}
                onClick={() => router.push(`/kbs/${kb.id}`)}
              >
                打开
              </Button>,
              <Button
                key="chunking"
                type="link"
                icon={<SlidersOutlined />}
                onClick={() => setChunkingKbId(kb.id)}
              >
                切片配置
              </Button>,
              <Button
                key="del"
                type="link"
                danger
                icon={<DeleteOutlined />}
                onClick={() => onDelete(kb)}
              >
                删除
              </Button>,
            ]}
          >
            <List.Item.Meta
              title={
                <Space>
                  <Typography.Link onClick={() => router.push(`/kbs/${kb.id}`)}>
                    {kb.name}
                  </Typography.Link>
                  {kb.label && <Tag>{kb.label}</Tag>}
                </Space>
              }
              description={
                <Space size="large">
                  <span>
                    <FileTextOutlined /> 文档 {kb.doc_count ?? 0}
                  </span>
                  <span>
                    <BookOutlined /> Wiki 页 {kb.page_count ?? 0}
                  </span>
                  {kb.description && <Typography.Text type="secondary">{kb.description}</Typography.Text>}
                </Space>
              }
            />
          </List.Item>
        )}
      />

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
            initialValue="system"
            extra="个人知识库仅自己可见，团队知识库团队成员可见，系统知识库所有用户可见"
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
    </Card>
  );
}