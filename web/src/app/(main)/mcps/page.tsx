"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import CodeViewer from "@/components/CodeViewer";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoTabs } from "@/components/biz/modo-tabs";
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

/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type McpTab = { key: string; title: string; children: ReactNode };

export default function McpManagePage() {
  const { message, modal } = App.useApp();
  const [items, setItems] = useState<McpRegistryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<McpRegistryItem | null>(null);
  const [testTarget, setTestTarget] = useState<McpRegistryItem | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「MCP 管理」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<McpTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

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
  // 客户端分页（对齐 data-synth 常驻底栏分页；切换类型 Tab 时回退到第 1 页）
  const totalRows = filtered.length;
  const safePage = Math.min(page, Math.max(1, Math.ceil(totalRows / pageSize)));
  const pagedItems = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

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

  // 列表区（首个选项卡内容）：类型 Tab + 表格 + 钉底分页
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
      <Tabs
              activeKey={typeFilter}
              onChange={(k) => {
                setTypeFilter(k);
                setPage(1);
              }}
              style={{ marginBottom: 8, flexShrink: 0 }}
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
              dataSource={pagedItems}
              pagination={false}
              scroll={{ x: 1000, y: "calc(100vh - 262px)" }}
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
            width: 140,
            render: (_: unknown, r: McpRegistryItem) => (
              <ModoActionGroup
                maxCount={2}
                actions={[
                  { key: "test", label: "测试", onClick: () => setTestTarget(r) },
                  { key: "edit", label: "编辑", onClick: () => openEdit(r) },
                  {
                    key: "delete",
                    label: "删除",
                    danger: true,
                    onClick: () =>
                      modal.confirm({
                        title: `确定删除 ${r.name}？`,
                        onOk: () => handleDelete(r),
                      }),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      <div style={{ flexShrink: 0, marginTop: "auto" }}>
        <ModoPagination
          current={safePage}
          pageSize={pageSize}
          total={totalRows}
          showTotal={(t) => `共 ${t} 条`}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      </div>
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
    </div>
  );

  return (
    <div
      className="mcps-page"
      style={{ padding: 8, height: "calc(100vh - 45px)", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
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
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新增服务器
            </Button>
          </Space>
        }
        items={[
          { key: "home", label: "MCP 管理", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />
    </div>
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
  const [tab, setTab] = useState("form");
  const watched = Form.useWatch([], form);

  // JSON 视图：表单当前值 + headers 派生的完整 server 配置（只读高亮）
  const jsonText = useMemo(() => {
    const headersMap: Record<string, string> = {};
    for (const h of headers) {
      if (h.key.trim()) headersMap[h.key.trim()] = h.value;
    }
    return JSON.stringify(
      {
        scope: watched?.scope ?? item?.scope ?? scope,
        name: watched?.name ?? item?.name ?? "",
        type: watched?.type ?? item?.type ?? "streamable_http",
        url: watched?.url ?? item?.url ?? "",
        headers: headersMap,
        enabled: watched?.enabled !== false,
      },
      null,
      2,
    );
  }, [watched, headers, item, scope]);

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
      <Tabs
        size="small"
        activeKey={tab}
        onChange={setTab}
        items={[
          { key: "form", label: "配置表单" },
          { key: "json", label: "JSON 视图" },
        ]}
      />
      {tab === "form" ? (
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
      ) : (
        <CodeViewer value={jsonText} fileName="mcp.json" height={380} />
      )}
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
