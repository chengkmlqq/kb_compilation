"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Empty, Form, Input, List, Modal, Space, Tag, Typography } from "antd";
import { BookOutlined, DeleteOutlined, FileTextOutlined, FolderOpenOutlined, PlusOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { apiCreateKb, apiDeleteKb, apiListKbs, KbItem } from "@/lib/api";

export default function KbsPage() {
  const { message, modal } = App.useApp();
  const router = useRouter();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();

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

  const onCreate = async () => {
    const values = await form.validateFields();
    const res = await apiCreateKb(values);
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
    modal.confirm({
      title: `删除知识库「${kb.name}」？`,
      content: "知识库内仍有文档时无法删除（需先删除文档）。",
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
            <Input.TextArea rows={3} placeholder="知识库用途说明" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}