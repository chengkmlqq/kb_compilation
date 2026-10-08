"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Col,
  Dropdown,
  Empty,
  Form,
  Input,
  Row,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  MessageOutlined,
  MoreOutlined,
  PlusOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import {
  apiCopyAgent,
  apiCreateAgent,
  apiDeleteAgent,
  apiListAgents,
  apiUpdateAgent,
  AgentItem,
} from "@/lib/api";
import AgentEditorModal, { AgentEditorValues } from "@/components/AgentEditorModal";

const modeLabel = (c: Record<string, unknown> | undefined) =>
  c?.agent_mode === "smart-reasoning" ? "智能推理" : "快速问答";

const modeIcon = (c: Record<string, unknown> | undefined, builtin?: boolean) => {
  if (builtin) {
    return c?.agent_mode === "smart-reasoning" ? (
      <RobotOutlined style={{ fontSize: 28, color: "#1677ff" }} />
    ) : (
      <MessageOutlined style={{ fontSize: 28, color: "#52c41a" }} />
    );
  }
  return (
    <span style={{ fontSize: 28 }}>
      {c?.avatar ? String(c.avatar) : "🤖"}
    </span>
  );
};

const featureBadges = (c: Record<string, unknown> | undefined) => {
  const out: React.ReactNode[] = [];
  const kbCount = Array.isArray(c?.knowledge_bases) ? c.knowledge_bases.length : 0;
  if (kbCount > 0) out.push(<Tag key="kb" color="blue">知识库 {kbCount}</Tag>);
  const tools = Array.isArray(c?.allowed_tools) ? c.allowed_tools : [];
  if (tools.length > 0) out.push(<Tag key="tools" color="purple">工具 {tools.length}</Tag>);
  if (c?.enable_query_expansion || c?.enable_rewrite)
    out.push(<Tag key="rewrite" color="cyan">意图改写</Tag>);
  if (c?.vlm_model_id) out.push(<Tag key="vlm" color="orange">多模态</Tag>);
  return out;
};

export default function AgentsPage() {
  const { message, modal } = App.useApp();
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<AgentItem | null>(null);
  const [searchForm] = Form.useForm<{ keyword: string }>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListAgents(1, 100);
      if (res.success) setAgents(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        (a.description || "").toLowerCase().includes(q)
    );
  }, [agents, search]);

  const handleSubmit = async (values: AgentEditorValues) => {
    if (editing) {
      return apiUpdateAgent(editing.id, {
        name: values.name,
        description: values.description,
        config: values.config,
      });
    }
    return apiCreateAgent({
      name: values.name,
      description: values.description,
      config: values.config,
    });
  };

  const onDelete = (agent: AgentItem) => {
    modal.confirm({
      title: `删除智能体「${agent.name}」？`,
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiDeleteAgent(agent.id);
        if (res.success) {
          message.success("已删除");
          void load();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  const onCopy = async (agent: AgentItem) => {
    const res = await apiCopyAgent(agent.id);
    if (res.success) {
      message.success(`已复制为「${res.data?.name || agent.name} - 副本」`);
      void load();
    } else {
      message.error(res.message || "复制失败");
    }
  };

  const onToggleDisabled = async (agent: AgentItem) => {
    const cfg = { ...(agent.config || {}) };
    const disabled = Boolean(cfg.disabled);
    const res = await apiUpdateAgent(agent.id, {
      config: { ...cfg, disabled: !disabled },
    });
    if (res.success) {
      message.success(disabled ? "已启用" : "已停用");
      void load();
    } else {
      message.error(res.message || "操作失败");
    }
  };

  const menuItems = (agent: AgentItem) => {
    const disabled = Boolean(agent.config?.disabled);
    return [
      { key: "edit", label: "编辑", icon: <EditOutlined /> },
      { key: "copy", label: "复制", icon: <CopyOutlined /> },
      { type: "divider" as const },
      disabled
        ? { key: "enable", label: "启用" }
        : { key: "disable", label: "停用" },
      { type: "divider" as const },
      { key: "delete", label: "删除", danger: true, icon: <DeleteOutlined /> },
    ];
  };

  // 筛选（对齐用户管理页：查询/重置 显式触发；本地过滤）
  const doSearch = (vals: { keyword?: string }) => {
    setSearch(vals.keyword?.trim() || "");
  };

  const doReset = () => {
    searchForm.resetFields();
    setSearch("");
  };

  return (
    // 2026-10-08: 对齐用户管理页范式——外层定高不滚动，卡片内「筛选固定 / 卡片列表滚动」
    <div
      className="agents-page"
      style={{
        padding: 8,
        height: "calc(100vh - 45px)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <Card
        title="智能体配置"
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
        extra={
          <Button
            type="primary"
            onClick={() => {
              setEditing(null);
              setEditorOpen(true);
            }}
          >
            新建智能体
          </Button>
        }
      >
        {/* 筛选表单（对齐用户管理页：查询/重置 显式触发） */}
        <Form
          form={searchForm}
          layout="inline"
          style={{ marginBottom: 12, flexShrink: 0 }}
          onFinish={doSearch}
          initialValues={{ keyword: "" }}
        >
          <Form.Item name="keyword" label="智能体">
            <Input allowClear placeholder="搜索名称/描述" style={{ width: 220 }} />
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

        {/* 卡片列表（flex:1 占满剩余高度，内容超出时内部滚动） */}
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          {visible.length === 0 ? (
            <Empty description="暂无智能体">
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => {
                  setEditing(null);
                  setEditorOpen(true);
                }}
              >
                新建智能体
              </Button>
            </Empty>
          ) : (
            <Row gutter={[16, 16]}>
              {visible.map((agent) => {
                const disabled = Boolean(agent.config?.disabled);
                return (
                  <Col key={agent.id} xs={24} sm={12} lg={8} xl={6}>
                    <Card
                      size="small"
                      hoverable
                      style={{ height: "100%", opacity: disabled ? 0.55 : 1 }}
                      title={
                        <Space>
                          {modeIcon(agent.config, agent.is_builtin)}
                          <Typography.Text strong ellipsis style={{ maxWidth: 120 }}>
                            {agent.name}
                          </Typography.Text>
                        </Space>
                      }
                      extra={
                        <Dropdown
                          menu={{
                            items: menuItems(agent),
                            onClick: ({ key }) => {
                              if (key === "edit") {
                                setEditing(agent);
                                setEditorOpen(true);
                              } else if (key === "copy") void onCopy(agent);
                              else if (key === "enable" || key === "disable")
                                void onToggleDisabled(agent);
                              else if (key === "delete") onDelete(agent);
                            },
                          }}
                        >
                          <Button type="text" size="small" icon={<MoreOutlined />} />
                        </Dropdown>
                      }
                    >
                      <Typography.Paragraph
                        type="secondary"
                        ellipsis={{ rows: 2 }}
                        style={{ minHeight: 44, marginBottom: 8 }}
                      >
                        {agent.description || "（无描述）"}
                      </Typography.Paragraph>
                      <Space wrap size={4}>
                        {agent.is_builtin && <Tag color="blue">内置</Tag>}
                        <Tag>{modeLabel(agent.config)}</Tag>
                        {disabled && <Tag color="red">已停用</Tag>}
                        {featureBadges(agent.config)}
                      </Space>
                    </Card>
                  </Col>
                );
              })}
            </Row>
          )}
        </div>

        <AgentEditorModal
          mode={editing ? "edit" : "create"}
          open={editorOpen}
          initial={
            editing
              ? { name: editing.name, description: editing.description ?? "", config: editing.config }
              : undefined
          }
          onClose={(changed) => {
            setEditorOpen(false);
            setEditing(null);
            if (changed) void load();
          }}
          onSubmit={handleSubmit}
        />
      </Card>
    </div>
  );
}