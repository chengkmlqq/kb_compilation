"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Empty, Form, Input, List, Modal, Select, Space, Tag, Typography } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { apiCreateAgent, apiDeleteAgent, apiListAgents, AgentItem, apiListKbs } from "@/lib/api";

export default function AgentsPage() {
  const { message, modal } = App.useApp();
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [kbs, setKbs] = useState<{ value: string; label: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();

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
    void (async () => {
      const res = await apiListKbs(1, 100);
      if (res.success) {
        setKbs((res.data?.items || []).map((kb) => ({ value: kb.id, label: kb.name })));
      }
    })();
  }, [load]);

  const onCreate = async () => {
    const values = await form.validateFields();
    const config: Record<string, unknown> = {
      agent_mode: "quick-answer",
      kb_selection_mode: values.kb_selection_mode || "all",
      top_k: 5,
      threshold: 0.2,
      embed_query: true,
      citation_enabled: true,
    };
    if (values.kb_selection_mode === "selected" && values.knowledge_bases?.length) {
      config.knowledge_bases = values.knowledge_bases;
    }
    const res = await apiCreateAgent({
      name: values.name,
      description: values.description,
      config,
    });
    if (res.success) {
      message.success("智能体已创建");
      setCreateOpen(false);
      form.resetFields();
      void load();
    } else {
      message.error(res.message || "创建失败");
    }
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

  return (
    <Card
      title="智能体配置"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建智能体
        </Button>
      }
    >
      <List
        loading={loading}
        dataSource={agents}
        locale={{ emptyText: <Empty description="暂无智能体" /> }}
        renderItem={(agent) => {
          const cfg = (agent.config || {}) as {
            kb_selection_mode?: string;
            knowledge_bases?: string[];
            top_k?: number;
          };
          return (
            <List.Item
              actions={[
                <Button
                  key="del"
                  type="link"
                  danger
                  icon={<DeleteOutlined />}
                  disabled={Boolean(agent.is_builtin)}
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
                    {agent.is_builtin && <Tag color="gold">内置</Tag>}
                    <Tag>{cfg.kb_selection_mode || "all"}</Tag>
                    <Tag>top_k={cfg.top_k ?? 5}</Tag>
                  </Space>
                }
                description={agent.description || "（无描述）"}
              />
            </List.Item>
          );
        }}
      />

      <Modal
        title="新建智能体"
        open={createOpen}
        onOk={onCreate}
        onCancel={() => setCreateOpen(false)}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" initialValues={{ kb_selection_mode: "all" }}>
          <Form.Item name="name" label="智能体名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="如：制度问答助手" />
          </Form.Item>
          <Form.Item name="description" label="描述（可选）">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="kb_selection_mode" label="知识库范围">
            <Select
              options={[
                { value: "all", label: "全部知识库" },
                { value: "selected", label: "指定知识库" },
                { value: "none", label: "不绑定" },
              ]}
            />
          </Form.Item>
          <Form.Item name="knowledge_bases" label="指定知识库（范围=指定时）">
            <Select mode="multiple" options={kbs} placeholder="选择知识库" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
