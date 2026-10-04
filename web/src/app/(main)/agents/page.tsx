"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Empty, List, Space, Tag, Typography } from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import {
  apiCreateAgent,
  apiDeleteAgent,
  apiListAgents,
  apiUpdateAgent,
  AgentItem,
} from "@/lib/api";
import AgentEditorModal, { AgentEditorValues } from "@/components/AgentEditorModal";

export default function AgentsPage() {
  const { message, modal } = App.useApp();
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<AgentItem | null>(null);

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

  const handleSubmit = async (values: AgentEditorValues) => {
    if (editing) {
      const res = await apiUpdateAgent(editing.id, {
        name: values.name,
        description: values.description,
        config: values.config,
      });
      return res;
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

  const modeLabel = (c: Record<string, unknown> | undefined) =>
    c?.agent_mode === "smart-reasoning" ? "智能推理" : "快速问答";

  return (
    <Card
      title="智能体配置"
      extra={
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
      }
    >
      <List
        loading={loading}
        dataSource={agents}
        locale={{ emptyText: <Empty description="暂无智能体，点右上角新建" /> }}
        renderItem={(agent) => (
          <List.Item
            actions={[
              <Button
                key="edit"
                icon={<EditOutlined />}
                onClick={() => {
                  setEditing(agent);
                  setEditorOpen(true);
                }}
              >
                编辑
              </Button>,
              <Button
                key="del"
                danger
                icon={<DeleteOutlined />}
                onClick={() => onDelete(agent)}
              >
                删除
              </Button>,
            ]}
          >
            <List.Item.Meta
              title={
                <Space>
                  <Typography.Text strong>{agent.name}</Typography.Text>
                  {agent.is_builtin && <Tag color="blue">内置</Tag>}
                  <Tag>{modeLabel(agent.config)}</Tag>
                </Space>
              }
              description={
                agent.description || "（无描述）"
              }
            />
          </List.Item>
        )}
      />

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
  );
}