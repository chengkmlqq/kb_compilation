"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Spin,
  Switch,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  AudioOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  EllipsisOutlined,
  ExperimentOutlined,
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
  SortAscendingOutlined,
  StarFilled,
  StarOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  apiCopyModel,
  apiCreateModel,
  apiDebugModel,
  apiDeleteModel,
  apiListModelProviders,
  apiListModels,
  apiSetModelDefault,
  apiTestModel,
  apiUpdateModel,
  ModelDebugResult,
  ModelItem,
  ModelProvider,
  ModelScope,
  ModelType,
} from "@/lib/api";

const { Text } = Typography;

const SCOPE_LABEL: Record<ModelScope, string> = {
  personal: "我的模型",
  team: "团队模型",
  system: "系统模型",
};

const SCOPE_TIP: Record<ModelScope, string> = {
  personal: "仅自己可见和使用的模型配置",
  team: "本团队所有成员可见和使用的模型配置",
  system: "仅管理员可见和使用的平台级模型配置",
};

// WeKnora 对齐：类型 tabs 带数量（all/chat/embedding/rerank/vllm/asr）
const ALL_TYPES: ModelType[] = ["chat", "embedding", "rerank", "vllm", "asr"];

const TYPE_LABEL: Record<ModelType, string> = {
  chat: "问答",
  embedding: "向量",
  rerank: "重排",
  vllm: "推理",
  asr: "语音",
};

const TYPE_ICON: Record<ModelType, React.ReactNode> = {
  chat: <MessageOutlined />,
  embedding: <DatabaseOutlined />,
  rerank: <SortAscendingOutlined />,
  vllm: <ThunderboltOutlined />,
  asr: <AudioOutlined />,
};

const TYPE_COLOR: Record<ModelType, string> = {
  chat: "geekblue",
  embedding: "orange",
  rerank: "cyan",
  vllm: "volcano",
  asr: "gold",
};

const SCOPE_COLOR: Record<ModelScope, string> = {
  personal: "blue",
  team: "green",
  system: "purple",
};

export default function ModelRegistryPage() {
  const { message } = App.useApp();
  const [items, setItems] = useState<ModelItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [scope, setScope] = useState<ModelScope>("personal");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [editing, setEditing] = useState<ModelItem | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalSaving, setModalSaving] = useState(false);
  const [debugTarget, setDebugTarget] = useState<ModelItem | null>(null);

  const load = useCallback(
    async (targetScope?: ModelScope) => {
      setLoading(true);
      try {
        const res = await apiListModels({ scope: targetScope ?? scope });
        if (res.success && res.data) {
          setItems(res.data.items);
          setIsAdmin(!!res.data.is_admin);
        } else {
          message.error(res.message || "加载失败");
        }
      } finally {
        setLoading(false);
      }
    },
    [scope, message],
  );

  const loadProviders = useCallback(async () => {
    const res = await apiListModelProviders();
    if (res.success && res.data) setProviders(res.data.items);
  }, []);

  useEffect(() => {
    void load();
    void loadProviders();
  }, [load, loadProviders]);

  const visibleScopes = useMemo(() => {
    if (isAdmin) return ["personal", "team", "system"] as ModelScope[];
    return ["personal", "team"] as ModelScope[];
  }, [isAdmin]);

  const filtered = useMemo(() => {
    let list = items.filter((it) => it.scope === scope);
    if (typeFilter !== "all") list = list.filter((it) => it.type === typeFilter);
    return list;
  }, [items, scope, typeFilter]);

  const countByType = useCallback(
    (t: string) => items.filter((it) => it.scope === scope && it.type === t).length,
    [items, scope],
  );

  const handleDelete = async (item: ModelItem) => {
    const res = await apiDeleteModel(item.id);
    if (res.success) {
      message.success(`已删除模型 ${item.name}`);
      void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const handleSetDefault = async (item: ModelItem) => {
    const res = await apiSetModelDefault(item.id);
    if (res.success) {
      message.success(`已将 ${item.name} 设为默认${TYPE_LABEL[item.type]}模型`);
      void load();
    } else {
      message.error(res.message || "设置失败");
    }
  };

  const handleCopy = async (item: ModelItem) => {
    const res = await apiCopyModel(item.id);
    if (res.success) {
      message.success(`已复制为 ${res.data?.item.name ?? "新模型"}`);
      void load();
    } else {
      message.error(res.message || "复制失败");
    }
  };

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };

  const openEdit = (item: ModelItem) => {
    setEditing(item);
    setModalOpen(true);
  };

  if (loading && items.length === 0) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 80 }}>
        <Spin />
      </div>
    );
  }

  return (
    <Card
      title="模型配置"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建模型
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="模型配置分三级：我的模型（仅本人）/ 团队模型（团队成员）/ 系统模型（仅管理员）。密钥加密落库，回显只显示掩码；编辑时留空=保持不变。"
      />
      <Tabs
        activeKey={scope}
        onChange={(k) => {
          setScope(k as ModelScope);
          setTypeFilter("all");
          void load(k as ModelScope);
        }}
        items={visibleScopes.map((s) => ({
          key: s,
          label: (
            <Space size={4}>
              {SCOPE_LABEL[s]}
              <Text type="secondary" style={{ fontSize: 12 }}>
                {SCOPE_TIP[s]}
              </Text>
            </Space>
          ),
        }))}
      />
      <Space style={{ marginBottom: 16 }}>
        <Radio.Group
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          optionType="button"
          buttonStyle="solid"
          options={[
            { label: `全部 (${items.filter((it) => it.scope === scope).length})`, value: "all" },
            ...ALL_TYPES.map((t) => ({
              label: `${TYPE_LABEL[t]} (${countByType(t)})`,
              value: t,
            })),
          ]}
        />
      </Space>

      {filtered.length === 0 ? (
        <Alert
          type="warning"
          showIcon
          message="当前级别下还没有模型配置"
          description={`点击右上角「新建模型」创建${SCOPE_LABEL[scope]}配置。`}
        />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
            gap: 16,
          }}
        >
          {filtered.map((item) => (
            <Card
              key={item.id}
              size="small"
              hoverable
              onClick={() => openEdit(item)}
              style={{ borderColor: item.is_default ? "#52c41a" : undefined, cursor: "pointer" }}
              title={
                <Space>
                  <span style={{ color: TYPE_COLOR[item.type as ModelType] }}>
                    {TYPE_ICON[item.type as ModelType]}
                  </span>
                  <Text strong>{item.display_name || item.name}</Text>
                  <Tag color={SCOPE_COLOR[item.scope]}>{SCOPE_LABEL[item.scope]}</Tag>
                  <Tag color={TYPE_COLOR[item.type as ModelType]}>{TYPE_LABEL[item.type as ModelType]}</Tag>
                  {item.is_default && (
                    <Tag color="success" icon={<StarFilled />}>
                      默认
                    </Tag>
                  )}
                </Space>
              }
              extra={
                <Space size={0} onClick={(e) => e.stopPropagation()}>
                  {!item.is_default && (
                    <Tooltip title="设为默认">
                      <Button
                        size="small"
                        type="text"
                        icon={<StarOutlined />}
                        onClick={() => void handleSetDefault(item)}
                      />
                    </Tooltip>
                  )}
                  <Dropdown
                    menu={{
                      items: [
                        { key: "edit", icon: <EditOutlined />, label: "编辑" },
                        { key: "copy", icon: <CopyOutlined />, label: "复制" },
                      ],
                      onClick: ({ key }) => {
                        if (key === "edit") openEdit(item);
                        else if (key === "copy") void handleCopy(item);
                      },
                    }}
                  >
                    <Button size="small" type="text" icon={<EllipsisOutlined />} />
                  </Dropdown>
                  <Popconfirm
                    title={`删除模型 ${item.name}？`}
                    description="删除后不可恢复"
                    onConfirm={() => void handleDelete(item)}
                  >
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              }
            >
              <Space direction="vertical" size={2}>
                <Text type="secondary" code>
                  {item.name}
                  {item.provider ? ` · ${item.provider}` : ""}
                </Text>
                {item.base_url ? <Text type="secondary" style={{ fontSize: 12 }}>{item.base_url}</Text> : null}
                <Space size={4} wrap>
                  {item.type === "embedding" && item.dimension ? (
                    <Tag>维度 {item.dimension}</Tag>
                  ) : null}
                  {item.supports_vision && item.type === "chat" ? <Tag>视觉</Tag> : null}
                  {item.max_concurrency ? <Tag>并发 {item.max_concurrency}</Tag> : null}
                  {item.thinking_control ? <Tag>思考 {item.thinking_control}</Tag> : null}
                </Space>
                {item.description ? (
                  <Text type="secondary" style={{ fontSize: 12 }} ellipsis={{ tooltip: item.description }}>
                    {item.description}
                  </Text>
                ) : null}
                <Text type={item.api_key_configured ? "success" : "danger"} style={{ fontSize: 12 }}>
                  {item.api_key_configured ? `密钥已配置 ${item.api_key_masked}` : "未配置密钥"}
                </Text>
                <Button size="small" icon={<ExperimentOutlined />} onClick={(e) => {
                  e.stopPropagation();
                  setDebugTarget(item);
                }}>
                  调试
                </Button>
              </Space>
            </Card>
          ))}
        </div>
      )}

      {modalOpen && (
        <ModelEditorModal
          open={modalOpen}
          item={editing}
          scope={scope}
          isAdmin={isAdmin}
          providers={providers}
          onClose={() => setModalOpen(false)}
          onSaved={() => {
            setModalOpen(false);
            void load();
          }}
          message={message}
        />
      )}

      <ModelDebugDrawer target={debugTarget} onClose={() => setDebugTarget(null)} message={message} />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 新建 / 编辑弹窗
// ---------------------------------------------------------------------------

function ModelEditorModal({
  open,
  item,
  scope,
  isAdmin,
  providers,
  onClose,
  onSaved,
  message,
}: {
  open: boolean;
  item: ModelItem | null;
  scope: ModelScope;
  isAdmin: boolean;
  providers: ModelProvider[];
  onClose: () => void;
  onSaved: () => void;
  message: ReturnType<typeof App.useApp>["message"];
}) {
  const [form] = Form.useForm();
  const editing = !!item;
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok?: boolean; error?: string; kind?: string } | null>(null);

  // switch type -> adjust provider default url hint
  const watchType = Form.useWatch("type", form);

  const providerOptions = useMemo(() => {
    const list = providers.filter((p) => p.modelTypes.includes(watchType || "chat"));
    return list.map((p) => ({ label: p.label, value: p.value }));
  }, [providers, watchType]);

  const defaultUrlFor = useCallback(
    (type?: string, provider?: string) => {
      if (!type || !provider) return "";
      const p = providers.find((x) => x.value === provider);
      return p?.defaultUrls?.[type] || "";
    },
    [providers],
  );

  useEffect(() => {
    if (editing && item) {
      form.setFieldsValue({
        scope: item.scope,
        name: item.name,
        display_name: item.display_name,
        type: item.type,
        provider: item.provider,
        description: item.description,
        base_url: item.base_url,
        dimension: item.dimension,
        supports_vision: item.supports_vision,
        is_default: item.is_default,
        max_concurrency: item.max_concurrency ?? undefined,
        thinking_control: item.thinking_control ?? undefined,
      });
    } else {
      form.setFieldsValue({
        scope: scope,
        type: "chat",
        provider: "deepseek",
        is_default: true,
      });
    }
  }, [editing, item, scope, form]);

  const doTest = async () => {
    const v = form.getFieldsValue();
    if (!v.base_url || !v.name) {
      message.warning("请先填写端点地址与模型名");
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const t = await apiTestModel({ base_url: v.base_url, api_key: v.api_key || "", model: v.name });
      setTestResult(t.data ?? { error: "无返回" });
      if (t.data?.ok) message.success("连通正常");
      else message.warning(t.data?.error || "连通失败");
    } finally {
      setTesting(false);
    }
  };

  const doSave = async () => {
    const v = form.getFieldsValue();
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        scope: v.scope ?? scope,
        name: v.name,
        display_name: v.display_name || null,
        type: v.type,
        provider: v.provider || null,
        description: v.description || null,
        base_url: v.base_url || null,
        interface_type: "openai",
        dimension: v.type === "embedding" ? v.dimension || null : null,
        supports_vision: !!v.supports_vision,
        is_default: !!v.is_default,
        max_concurrency: v.max_concurrency || null,
        thinking_control: v.thinking_control || null,
      };
      if (v.api_key) payload.api_key = v.api_key;
      const res = editing
        ? await apiUpdateModel(item!.id, payload)
        : await apiCreateModel(payload as never);
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
      title={editing ? "编辑模型" : "新建模型"}
      width={680}
      okText={editing ? "保存" : "创建"}
      confirmLoading={saving}
      onOk={() => void doSave()}
      onCancel={onClose}
      destroyOnClose
    >
      <Form form={form} layout="vertical" initialValues={{ scope, type: "chat", provider: "deepseek", is_default: true }}>
        <Form.Item name="scope" label="配置级别" style={{ maxWidth: 240 }}>
          <Select
            disabled={editing}
            options={[
              { label: "我的模型（仅自己）", value: "personal" },
              { label: "团队模型（团队共用）", value: "team" },
              ...(isAdmin ? [{ label: "系统模型（仅管理员）", value: "system" }] : []),
            ]}
          />
        </Form.Item>
        <Form.Item name="type" label="模型类型" style={{ maxWidth: 420 }}>
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            options={ALL_TYPES.map((t) => ({ label: TYPE_LABEL[t], value: t }))}
          />
        </Form.Item>
        <Form.Item name="provider" label="厂商" style={{ maxWidth: 400 }}>
          <Select
            placeholder="选择厂商（自动填充默认端点）"
            options={providerOptions}
            onChange={(p) => {
              const url = defaultUrlFor(form.getFieldValue("type") || watchType, p);
              if (url && !form.getFieldValue("base_url")) form.setFieldValue("base_url", url);
            }}
          />
        </Form.Item>
        <Space size={16} style={{ width: "100%" }} align="start">
          <Form.Item
            name="name"
            label="模型名称"
            rules={[{ required: true, message: "请输入模型名称" }]}
            style={{ flex: 1 }}
          >
            <Input placeholder="如 deepseek-v4-pro / bge-m3" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称" style={{ flex: 1 }}>
            <Input placeholder="可选" />
          </Form.Item>
        </Space>
        <Form.Item
          name="base_url"
          label="端点地址 (OpenAI 兼容 /v1)"
          rules={[{ required: true, message: "请输入端点地址" }]}
        >
          <Input placeholder="https://api.deepseek.com/v1" />
        </Form.Item>
        <Form.Item name="api_key" label={editing ? "API 密钥（留空=保持不变）" : "API 密钥"}>
          <Input.Password
            placeholder={
              item?.api_key_configured ? `已配置 ${item.api_key_masked}，留空保持不变` : "Bearer 密钥"
            }
            autoComplete="new-password"
          />
        </Form.Item>
        {watchType === "embedding" && (
          <Form.Item name="dimension" label="向量维度" style={{ maxWidth: 240 }}>
            <InputNumber min={128} max={4096} placeholder="如 1024" style={{ width: "100%" }} />
          </Form.Item>
        )}
        {(watchType === "chat" || watchType === "vllm") && (
          <Form.Item name="supports_vision" label="支持多模态（图片输入）" valuePropName="checked">
            <Switch />
          </Form.Item>
        )}
        {watchType === "chat" && (
          <Form.Item name="thinking_control" label="深度思考控制" style={{ maxWidth: 240 }}>
            <Select
              allowClear
              placeholder="默认（跟随模型）"
              options={[
                { label: "关闭", value: "off" },
                { label: "自动", value: "auto" },
                { label: "开启", value: "on" },
              ]}
            />
          </Form.Item>
        )}
        {["chat", "embedding", "vllm"].includes(watchType) && (
          <Form.Item name="max_concurrency" label="并发上限（0/空=默认）" style={{ maxWidth: 240 }}>
            <InputNumber min={0} placeholder="如 8" style={{ width: "100%" }} />
          </Form.Item>
        )}
        <Form.Item name="is_default" label="设为默认" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={2} placeholder="可选" />
        </Form.Item>
      </Form>
      <Space style={{ marginTop: -12 }}>
        <Button loading={testing} onClick={() => void doTest()}>
          测试连通性
        </Button>
        {testResult && (
          <Text type={testResult.ok ? "success" : "danger"}>
            {testResult.ok ? "连通正常" : `失败: ${testResult.error}`}
          </Text>
        )}
      </Space>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// 调试抽屉
// ---------------------------------------------------------------------------

function ModelDebugDrawer({
  target,
  onClose,
  message,
}: {
  target: ModelItem | null;
  onClose: () => void;
  message: ReturnType<typeof App.useApp>["message"];
}) {
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ModelDebugResult | null>(null);

  useEffect(() => {
    if (!target) {
      setInput("");
      setResult(null);
    }
  }, [target]);

  const run = async () => {
    if (!target) return;
    setRunning(true);
    setResult(null);
    try {
      const res = await apiDebugModel(target.id, { input: input || "ping" });
      setResult(res.data ?? { error: "无返回" });
      if (!res.data?.ok) message.warning(res.data?.error || "调试失败");
    } finally {
      setRunning(false);
    }
  };

  return (
    <Drawer
      title={target ? `模型调试：${target.display_name || target.name}` : "模型调试"}
      width={480}
      open={!!target}
      onClose={onClose}
      destroyOnClose
    >
      {target && (
        <>
          <Space direction="vertical" style={{ width: "100%" }} size={12}>
            <Space wrap>
              <Tag color={SCOPE_COLOR[target.scope]}>{SCOPE_LABEL[target.scope]}</Tag>
              <Tag color={TYPE_COLOR[target.type as ModelType]}>{TYPE_LABEL[target.type as ModelType]}</Tag>
              <Text code>{target.name}</Text>
            </Space>
            <Input.TextArea
              rows={5}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                target.type === "embedding"
                  ? "输入一段文本，返回向量维度（实际调一次向量化）"
                  : target.type === "rerank"
                    ? "输入查询文本，返回重排探测结果"
                    : target.type === "asr"
                      ? "输入任意文本，探测 ASR 端点可达性与模型列表"
                      : "输入问题，用该模型真实调用一次并返回回答"
              }
            />
            <Button type="primary" loading={running} onClick={() => void run()}>
              运行调试
            </Button>
            {result && (
              <Card size="small" title="调试结果">
                {result.ok ? (
                  target.type === "embedding" ? (
                    <Text>向量维度：{result.dimension}</Text>
                  ) : (
                    <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{result.text}</pre>
                  )
                ) : (
                  <Text type="danger">{result.error}</Text>
                )}
              </Card>
            )}
          </Space>
        </>
      )}
    </Drawer>
  );
}
