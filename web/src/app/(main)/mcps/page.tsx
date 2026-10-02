"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import {
  apiCreateMcp,
  apiDeleteMcp,
  apiListMcps,
  apiTestMcp,
  apiUpdateMcp,
  McpRegistryItem,
  McpTestResult,
  ModelScope,
} from "@/lib/api";

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

export default function McpManagePage() {
  const { message } = App.useApp();
  const [items, setItems] = useState<McpRegistryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<McpRegistryItem | null>(null);
  const [testTarget, setTestTarget] = useState<McpRegistryItem | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListMcps();
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

  const filtered = useMemo(() => {
    if (typeFilter === "all") return items;
    return items.filter((it) => it.type === typeFilter);
  }, [items, typeFilter]);

  const countByType = useCallback(
    (t: string) => items.filter((it) => it.type === t).length,
    [items],
  );

  const handleDelete = async (item: McpRegistryItem) => {
    const res = await apiDeleteMcp(item.id);
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

  const openEdit = (item: McpRegistryItem) => {
    setEditing(item);
    setEditOpen(true);
  };

  return (
    <Card
      title="MCP 管理"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增服务器
          </Button>
        </Space>
      }
    >
      <Tabs
        activeKey={typeFilter}
        onChange={setTypeFilter}
        style={{ marginBottom: 8 }}
        items={[
          { key: "all", label: `全部 (${items.length})` },
          { key: "streamable_http", label: `streamable_http (${countByType("streamable_http")})` },
          { key: "stdio", label: `stdio (${countByType("stdio")})` },
        ]}
      />
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={filtered}
        pagination={false}
        locale={{ emptyText: <Text type="secondary">暂无 MCP 服务器</Text> }}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            render: (v: string, r: McpRegistryItem) => (
              <Space size={4}>
                <Text strong>{v}</Text>
                <Tag color={SCOPE_COLOR[r.scope]}>{SCOPE_LABEL[r.scope]}</Tag>
                {r.enabled !== false && <Tag color="green">启用</Tag>}
              </Space>
            ),
          },
          {
            title: "类型",
            dataIndex: "type",
            width: 130,
            render: (v: string) => <Tag>{v === "stdio" ? "stdio" : "streamable_http"}</Tag>,
          },
          { title: "URL", dataIndex: "url", ellipsis: true },
          {
            title: "密钥",
            width: 150,
            render: (_: unknown, r: McpRegistryItem) => (
              <Text type={r.headers && Object.keys(r.headers).length ? "success" : "secondary"} style={{ fontSize: 12 }}>
                {r.headers && Object.keys(r.headers).length ? "已配置" : "无"}
              </Text>
            ),
          },
          {
            title: "操作",
            width: 200,
            render: (_: unknown, r: McpRegistryItem) => (
              <Space size={4}>
                <Button size="small" icon={<ThunderboltOutlined />} onClick={() => setTestTarget(r)}>
                  测试
                </Button>
                <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>
                  编辑
                </Button>
                <Popconfirm title={`删除 ${r.name}？`} onConfirm={() => void handleDelete(r)}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      {editOpen && (
        <McpEditorModal
          open={editOpen}
          item={editing}
          scope="personal"
          isAdmin={isAdmin}
          onClose={() => setEditOpen(false)}
          onSaved={() => {
            setEditOpen(false);
            void load();
          }}
          message={message}
        />
      )}
      <McpTestDrawer target={testTarget} onClose={() => setTestTarget(null)} message={message} />
    </Card>
  );
}

function McpEditorModal({
  open,
  item,
  scope,
  isAdmin,
  onClose,
  onSaved,
  message,
}: {
  open: boolean;
  item: McpRegistryItem | null;
  scope: ModelScope;
  isAdmin: boolean;
  onClose: () => void;
  onSaved: () => void;
  message: ReturnType<typeof App.useApp>["message"];
}) {
  const [form] = Form.useForm();
  const editing = !!item;
  const [saving, setSaving] = useState(false);
  const [headers, setHeaders] = useState<{ key: string; value: string }[]>([]);

  useEffect(() => {
    if (item) {
      form.setFieldsValue({
        scope: item.scope,
        name: item.name,
        type: item.type || "streamable_http",
        url: item.url,
        enabled: item.enabled !== false,
      });
      setHeaders(
        Object.entries(item.headers || {}).map(([k, v]) => ({ key: k, value: String(v) })),
      );
    } else {
      form.setFieldsValue({ scope, type: "streamable_http", enabled: true });
      setHeaders([]);
    }
  }, [item, form, scope]);

  const doSave = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      const headersMap: Record<string, string> = {};
      for (const h of headers) {
        if (h.key.trim()) headersMap[h.key.trim()] = h.value;
      }
      const payload = {
        scope: v.scope ?? scope,
        name: v.name,
        type: v.type || "streamable_http",
        url: v.url || "",
        headers: headersMap,
        enabled: v.enabled !== false,
      };
      const res = editing
        ? await apiUpdateMcp(item!.id, payload)
        : await apiCreateMcp(payload);
      if (res.success) {
        message.success(editing ? "已保存并热生效" : "已创建并热生效");
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
      title={editing ? `编辑 MCP 服务器: ${item?.name}` : "新增 MCP 服务器"}
      width={640}
      okText={editing ? "保存" : "创建"}
      confirmLoading={saving}
      onOk={() => void doSave()}
      onCancel={onClose}
      destroyOnClose
    >
      <Form form={form} layout="vertical" initialValues={{ scope, type: "streamable_http", enabled: true }}>
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
        <Space size={16} style={{ width: "100%" }} align="start">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: "请输入名称" }]}
            style={{ flex: 1 }}
          >
            <Input placeholder="如 mcp-gateway" />
          </Form.Item>
          <Form.Item name="type" label="传输类型" style={{ flex: 1 }}>
            <Select
              options={[
                { label: "streamable_http", value: "streamable_http" },
                { label: "stdio", value: "stdio" },
              ]}
            />
          </Form.Item>
        </Space>
        <Form.Item name="url" label="端点地址">
          <Input placeholder="http://host:8000/mcp/" />
        </Form.Item>
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary">自定义请求头（敏感键名自动加密）</Text>
        </div>
        {headers.map((h, i) => (
          <Space key={i} style={{ display: "flex", marginBottom: 8 }} align="center">
            <Input
              placeholder="Header 名（如 X-API-Key）"
              style={{ width: 200 }}
              value={h.key}
              onChange={(e) =>
                setHeaders((prev) => prev.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))
              }
            />
            <Input.Password
              placeholder="值（留空=保持不变）"
              style={{ width: 260 }}
              value={h.value}
              onChange={(e) =>
                setHeaders((prev) => prev.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))
              }
            />
            <Button
              size="small"
              danger
              onClick={() => setHeaders((prev) => prev.filter((_, j) => j !== i))}
            >
              删除
            </Button>
          </Space>
        ))}
        <Button
          size="small"
          onClick={() => setHeaders((prev) => [...prev, { key: "", value: "" }])}
        >
          添加请求头
        </Button>
        <Form.Item name="enabled" label="启用" valuePropName="checked" style={{ marginTop: 12 }}>
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function McpTestDrawer({
  target,
  onClose,
  message,
}: {
  target: McpRegistryItem | null;
  onClose: () => void;
  message: ReturnType<typeof App.useApp>["message"];
}) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<McpTestResult | null>(null);

  useEffect(() => {
    if (target) {
      setResult(null);
      void run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  const run = async () => {
    if (!target) return;
    setRunning(true);
    setResult(null);
    try {
      const res = await apiTestMcp(target);
      setResult(res.data ?? { ok: false, error: "无返回" });
      if (!res.data?.ok) message.warning(res.data?.error || "测试失败");
    } finally {
      setRunning(false);
    }
  };

  return (
    <Drawer
      title={target ? `MCP 测试：${target.name}` : "MCP 测试"}
      width={560}
      open={!!target}
      onClose={onClose}
      destroyOnClose
    >
      {target && (
        <Space direction="vertical" style={{ display: "flex" }} size={12}>
          <Space wrap>
            <Tag color={target.enabled !== false ? "green" : "default"}>
              {target.enabled !== false ? "启用" : "停用"}
            </Tag>
            <Text code>{target.type}</Text>
            {target.url ? <Text type="secondary" style={{ fontSize: 12 }}>{target.url}</Text> : null}
          </Space>
          <Button type="primary" loading={running} onClick={() => void run()}>
            重新测试
          </Button>
          {result && (
            <>
              <Alert
                type={result.ok ? "success" : "error"}
                showIcon
                message={result.ok ? `连通成功，${result.tool_count ?? 0} 个工具` : `连通失败：${result.error || "未知错误"}`}
                description={
                  result.ok && result.transport
                    ? `传输: ${result.transport} · 耗时: ${result.elapsed_ms ?? "-"}ms`
                    : undefined
                }
              />
              {result.ok && (result.tools || []).length > 0 && (
                <Card size="small" title={`工具清单（${result.tools!.length}）`}>
                  <Space direction="vertical" style={{ display: "flex" }} size={4}>
                    {(result.tools || []).map((t) => (
                      <Text key={t.name} style={{ fontSize: 12 }}>
                        <Text strong>{t.name}</Text>
                        {t.description ? ` — ${t.description.slice(0, 80)}` : ""}
                      </Text>
                    ))}
                  </Space>
                </Card>
              )}
            </>
          )}
        </Space>
      )}
    </Drawer>
  );
}
