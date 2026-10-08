"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import {
  apiCreateWebsearchProvider,
  apiDeleteWebsearchProvider,
  apiListWebsearchProviders,
  apiUpdateWebsearchProvider,
  ModelScope,
  WebSearchProviderItem,
  WebSearchProviderType,
} from "@/lib/api";
import { ModoActionGroup } from "@/components/biz/modo-action-group";

const { Text } = Typography;

const SCOPE_LABEL: Record<ModelScope, string> = {
  personal: "我的",
  team: "团队",
  system: "系统",
};
const SCOPE_COLOR: Record<ModelScope, string> = {
  personal: "blue",
  team: "green",
  system: "purple",
};

const TYPE_OPTIONS: { label: string; value: WebSearchProviderType }[] = [
  { label: "tavily", value: "tavily" },
  { label: "serper（Google）", value: "serper" },
  { label: "bing", value: "bing" },
  { label: "exa", value: "exa" },
  { label: "generic（自定义端点）", value: "generic" },
];

export default function WebSearchManagePage() {
  const { message, modal } = App.useApp();
  const [items, setItems] = useState<WebSearchProviderItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<WebSearchProviderItem | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListWebsearchProviders();
      if (res.success && res.data) {
        setItems(res.data.items);
        setIsAdmin(!!res.data.is_admin);
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

  const handleDelete = async (item: WebSearchProviderItem) => {
    const res = await apiDeleteWebsearchProvider(item.id);
    if (res.success) {
      message.success(`已删除 ${item.name}`);
      void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const openCreate = () => {
    setEditing(null);
    setEditOpen(true);
  };

  const openEdit = (item: WebSearchProviderItem) => {
    setEditing(item);
    setEditOpen(true);
  };

  return (
    // 2026-10-08: 对齐用户管理页范式——外层固定视口高度不滚动，卡片内表格占满剩余高度
    <div
      className="websearch-page"
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
        title="联网搜索"
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
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <Button type="primary" onClick={openCreate}>
              新增提供方
            </Button>
          </Space>
        }
      >
        <Table<WebSearchProviderItem>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={items}
          pagination={false}
          scroll={{ x: 900, y: "calc(100vh - 168px)" }}
          locale={{ emptyText: <Empty description="暂无搜索提供方" /> }}
          columns={[
            {
              title: "名称",
              dataIndex: "name",
              render: (v: string, r: WebSearchProviderItem) => (
                <Space size={4}>
                  <Text strong>{v}</Text>
                  <Tag color={SCOPE_COLOR[r.scope]}>{SCOPE_LABEL[r.scope]}</Tag>
                  {r.enabled !== false && <Tag color="green">启用</Tag>}
                </Space>
              ),
            },
            {
              title: "类型",
              dataIndex: "provider_type",
              width: 160,
              render: (v: WebSearchProviderType) => <Tag>{v}</Tag>,
            },
            { title: "描述", dataIndex: "description", ellipsis: true },
            { title: "端点", dataIndex: "base_url", ellipsis: true },
            {
              title: "API Key",
              width: 120,
              render: (_: unknown, r: WebSearchProviderItem) => (
                <Text type={r.api_key ? "success" : "secondary"} style={{ fontSize: 12 }}>
                  {r.api_key ? "已配置" : "无"}
                </Text>
              ),
            },
            {
              title: "操作",
              width: 140,
              render: (_: unknown, r: WebSearchProviderItem) => (
                <ModoActionGroup
                  maxCount={2}
                  actions={[
                    { key: "edit", label: "编辑", onClick: () => openEdit(r) },
                    {
                      key: "delete",
                      label: "删除",
                      danger: true,
                      onClick: () =>
                        modal.confirm({
                          title: `删除 ${r.name}？`,
                          okButtonProps: { danger: true },
                          onOk: () => handleDelete(r),
                        }),
                    },
                  ]}
                />
              ),
            },
          ]}
        />
        {editOpen && (
          <WebSearchEditorModal
            open={editOpen}
            item={editing}
            isAdmin={isAdmin}
            onClose={() => setEditOpen(false)}
            onSaved={() => {
              setEditOpen(false);
              void load();
            }}
            message={message}
          />
        )}
      </Card>
    </div>
  );
}

function WebSearchEditorModal({
  open,
  item,
  isAdmin,
  onClose,
  onSaved,
  message,
}: {
  open: boolean;
  item: WebSearchProviderItem | null;
  isAdmin: boolean;
  onClose: () => void;
  onSaved: () => void;
  message: ReturnType<typeof App.useApp>["message"];
}) {
  const [form] = Form.useForm();
  const editing = !!item;
  const [saving, setSaving] = useState(false);
  const watchedType = Form.useWatch("provider_type", form);

  useEffect(() => {
    if (item) {
      form.setFieldsValue({
        scope: item.scope,
        name: item.name,
        provider_type: item.provider_type,
        description: item.description,
        api_key: item.api_key,
        base_url: item.base_url,
        enabled: item.enabled !== false,
      });
    } else {
      form.setFieldsValue({ scope: "personal", provider_type: "tavily", enabled: true });
    }
  }, [item, form]);

  const doSave = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      const payload = {
        scope: v.scope ?? "personal",
        name: v.name,
        provider_type: v.provider_type || "tavily",
        description: v.description || "",
        // 编辑时后端回传的是脱敏值，原样提交即"保持不变"
        api_key: v.api_key || "",
        base_url: v.base_url || "",
        enabled: v.enabled !== false,
      };
      const res = editing
        ? await apiUpdateWebsearchProvider(item!.id, payload)
        : await apiCreateWebsearchProvider(payload);
      if (res.success) {
        message.success(editing ? "已保存" : "已创建");
        onSaved();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={editing ? `编辑搜索提供方: ${item?.name}` : "新增搜索提供方"}
      width={560}
      okText={editing ? "保存" : "创建"}
      confirmLoading={saving}
      onOk={() => void doSave()}
      onCancel={onClose}
      destroyOnClose
    >
      <Form form={form} layout="vertical" initialValues={{ scope: "personal", provider_type: "tavily", enabled: true }}>
        <Form.Item name="scope" label="配置级别" style={{ maxWidth: 240 }}>
          <Select
            disabled={editing}
            options={[
              { label: "我的（仅自己）", value: "personal" },
              { label: "团队（团队共用）", value: "team" },
              ...(isAdmin ? [{ label: "系统（仅管理员）", value: "system" }] : []),
            ]}
          />
        </Form.Item>
        <Form.Item
          name="name"
          label="名称"
          rules={[{ required: true, message: "请输入名称" }]}
        >
          <Input placeholder="如 tavily-主账号" />
        </Form.Item>
        <Form.Item name="provider_type" label="服务类型">
          <Select options={TYPE_OPTIONS} />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={2} placeholder="选填，用于说明用途" />
        </Form.Item>
        <Form.Item
          name="api_key"
          label="API Key"
          extra={editing ? "留空或保持脱敏值即不修改" : undefined}
        >
          <Input.Password placeholder="搜索服务 API Key" />
        </Form.Item>
        {watchedType === "generic" && (
          <Form.Item
            name="base_url"
            label="端点地址"
            rules={[{ required: true, message: "generic 类型必须填写端点地址" }]}
          >
            <Input placeholder="http://search.internal:9000/query" />
          </Form.Item>
        )}
        <Form.Item name="enabled" label="启用" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
  );
}