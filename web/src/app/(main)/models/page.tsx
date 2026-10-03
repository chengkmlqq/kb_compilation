"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  UploadOutlined,
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
  ModelDebugPayload,
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

// WeKnora 对齐：类型 tabs 带数量（all/chat/embedding/rerank/vllm/asr）
const ALL_TYPES: ModelType[] = ["chat", "embedding", "rerank", "vllm", "asr"];

const TYPE_LABEL: Record<ModelType, string> = {
  chat: "对话",
  embedding: "Embedding",
  rerank: "Rerank",
  vllm: "视觉",
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
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [editing, setEditing] = useState<ModelItem | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalSaving, setModalSaving] = useState(false);
  const [debugTarget, setDebugTarget] = useState<ModelItem | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListModels();
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

  const loadProviders = useCallback(async () => {
    const res = await apiListModelProviders();
    if (res.success && res.data) setProviders(res.data.items);
  }, []);

  useEffect(() => {
    void load();
    void loadProviders();
  }, [load, loadProviders]);

  const filtered = useMemo(() => {
    if (typeFilter === "all") return items;
    return items.filter((it) => it.type === typeFilter);
  }, [items, typeFilter]);

  const countByType = useCallback(
    (t: string) => items.filter((it) => it.type === t).length,
    [items],
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
      <Tabs
        activeKey={typeFilter}
        onChange={setTypeFilter}
        items={[
          { key: "all", label: `全部 (${items.length})` },
          ...ALL_TYPES.map((t) => ({
            key: t,
            label: `${TYPE_LABEL[t]} (${countByType(t)})`,
          })),
        ]}
      />

      {filtered.length === 0 ? (
        <Alert
          type="warning"
          showIcon
          message="还没有模型配置"
          description="点击右上角「新建模型」创建模型配置。"
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
          scope="personal"
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

      <ModelDebugDrawer
        target={debugTarget}
        models={items}
        onClose={() => setDebugTarget(null)}
        message={message}
      />
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
        custom_headers:
          item.custom_headers && Object.keys(item.custom_headers).length > 0
            ? JSON.stringify(item.custom_headers, null, 2)
            : "",
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
      if (editing && item?.id) {
        // 编辑模式：api_key 留空 = 保持已保存密钥不变，表单里没有明文，
        // 直接拿空 key 去测必然 401 —— 改走 /models/{id}/debug 用服务端
        // 解密后的已保存密钥做真实探测。
        if (item.type === "vllm" || item.type === "asr") {
          message.info("该类型需在「调试」抽屉中上传文件测试，此处仅校验连通性可先保存后到调试里验证");
          setTestResult({ ok: false, error: "vllm/asr 类型请到调试抽屉上传文件测试" });
          return;
        }
        const debugPayload: ModelDebugPayload = { input: "ping" };
        if (item.type === "rerank") debugPayload.documents = ["ping"];
        const t = await apiDebugModel(item.id, debugPayload);
        setTestResult(t.data ?? { error: "无返回" });
        if (t.data?.ok) message.success("连通正常");
        else message.warning(t.data?.error || "连通失败");
        return;
      }
      if (!v.api_key) {
        message.warning("请先填写 API 密钥（新建模型未保存，无可用的已存密钥）");
        setTestResult({ ok: false, error: "缺少 API 密钥" });
        return;
      }
      const t = await apiTestModel({ base_url: v.base_url, api_key: v.api_key, model: v.name });
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
      if (v.custom_headers && String(v.custom_headers).trim()) {
        try {
          const parsed = JSON.parse(String(v.custom_headers));
          if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
            message.error("自定义请求头必须是 JSON 对象，如 {\"X-Api-Key\":\"xxx\"}");
            return;
          }
          payload.custom_headers = parsed as Record<string, string>;
        } catch {
          message.error("自定义请求头不是合法 JSON，如 {\"X-Api-Key\":\"xxx\"}");
          return;
        }
      }
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
        <Form.Item
          name="custom_headers"
          label="自定义请求头 (JSON)"
          extra={'可选。追加到该模型的请求头，如 {"X-Api-Key":"xxx"}（键名会在调试请求预览中列出，值不泄露）'}
        >
          <Input.TextArea rows={3} placeholder={'{"X-Api-Key": "xxx"}'} />
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
  models,
  onClose,
  message,
}: {
  target: ModelItem | null;
  models: ModelItem[];
  onClose: () => void;
  message: ReturnType<typeof App.useApp>["message"];
}) {
  const [selectedType, setSelectedType] = useState<ModelType | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [input, setInput] = useState("");
  const [documentsText, setDocumentsText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [thinking, setThinking] = useState(false);
  const [temperature, setTemperature] = useState(0.7);
  const [topP, setTopP] = useState(1);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ModelDebugResult | null>(null);
  const [resultTab, setResultTab] = useState<"response" | "request">("response");
  const [history, setHistory] = useState<
    Array<{ id: number; label: string; result: ModelDebugResult }>
  >([]);
  const runSequence = useRef(0);
  const [drawerWidth, setDrawerWidth] = useState(560);

  const availableTypes = useMemo(
    () => ALL_TYPES.filter((t) => models.some((m) => m.type === t)),
    [models],
  );
  const filteredModels = useMemo(
    () => models.filter((m) => m.type === selectedType),
    [models, selectedType],
  );
  const selectedModel = useMemo(
    () => models.find((m) => m.id === selectedId) ?? null,
    [models, selectedId],
  );
  const isChat = selectedModel?.type === "chat";
  const needsFile = selectedModel?.type === "vllm" || selectedModel?.type === "asr";
  const supportsThinking = isChat && (selectedModel?.thinking_control ?? "") !== "off";

  // 打开抽屉：初始化类型/模型选择（优先定位到来源卡片对应的模型）
  useEffect(() => {
    if (!target || availableTypes.length === 0) return;
    const t: ModelType = availableTypes.includes(target.type)
      ? target.type
      : availableTypes[0];
    setSelectedType(t);
    const firstOfType = models.find((m) => m.type === t);
    setSelectedId(target.type === t ? target.id : (firstOfType?.id ?? ""));
    setInput("");
    setDocumentsText("");
    setFile(null);
    setThinking(false);
    setTemperature(0.7);
    setTopP(1);
    setMaxTokens(1024);
    setSystemPrompt("");
    setResult(null);
    setHistory([]);
    setResultTab("response");
  }, [target]); // eslint-disable-line react-hooks/exhaustive-deps

  const resetResult = () => {
    setResult(null);
    setHistory([]);
    setResultTab("response");
  };

  const selectType = (t: ModelType) => {
    if (selectedType === t) return;
    setSelectedType(t);
    const first = models.find((m) => m.type === t);
    setSelectedId(first?.id ?? "");
    setInput("");
    setDocumentsText("");
    setFile(null);
    resetResult();
  };

  const selectModel = (id: string) => {
    setSelectedId(id);
    setFile(null);
    resetResult();
  };

  const canRun = useMemo(() => {
    if (!selectedModel) return false;
    if (needsFile && !file) return false;
    if (selectedModel.type === "asr") return true;
    if (selectedModel.type === "rerank") {
      const docs = documentsText
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      return !!input.trim() && docs.length > 0;
    }
    return !!input.trim();
  }, [selectedModel, needsFile, file, input, documentsText]);

  const run = async () => {
    if (!selectedModel || !canRun || running) return;
    setRunning(true);
    try {
      const docs = documentsText
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      const payload: ModelDebugPayload = {
        input: input.trim(),
        ...(selectedModel.type === "rerank" ? { documents: docs } : {}),
        ...(isChat
          ? {
              options: {
                ...(systemPrompt.trim() ? { system_prompt: systemPrompt.trim() } : {}),
                temperature,
                top_p: topP,
                max_tokens: maxTokens,
                thinking: supportsThinking ? thinking : undefined,
              },
            }
          : {}),
        ...(file ? { file } : {}),
      };
      const res = await apiDebugModel(selectedModel.id, payload);
      if (!res.success || !res.data) {
        message.error(res.message || "调试失败");
        return;
      }
      const next = res.data;
      setResult(next);
      runSequence.current += 1;
      setHistory((prev) => {
        const label = supportsThinking
          ? thinking
            ? "思考开"
            : "思考关"
          : `运行 ${runSequence.current}`;
        return [{ id: runSequence.current, label, result: next }, ...prev].slice(0, 6);
      });
      setResultTab("response");
      if (!next.ok) message.warning(next.error || "调试失败");
    } catch (e) {
      message.error((e as Error)?.message || "请求失败");
    } finally {
      setRunning(false);
    }
  };

  const copyResult = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(result, null, 2));
      message.success("已复制");
    } catch {
      message.error("复制失败");
    }
  };

  const formattedResult = useMemo(() => {
    if (!result) return "";
    return JSON.stringify(
      resultTab === "response" ? result.raw_response : result.request,
      null,
      2,
    );
  }, [result, resultTab]);

  const METRIC_LABELS: Record<string, string> = {
    dimension: "维度",
    result_count: "结果数",
    answer_characters: "回答字符数",
    reasoning_characters: "思考字符数",
    reasoning_returned: "返回思考",
    text_characters: "文本字符数",
    segment_count: "段落数",
  };
  const metrics = useMemo(() => {
    const obs = result?.observations ?? {};
    return Object.entries(METRIC_LABELS)
      .filter(([k]) => obs[k] !== undefined && obs[k] !== null)
      .map(([k, label]) => ({
        key: k,
        label,
        value: typeof obs[k] === "boolean" ? (obs[k] ? "是" : "否") : String(obs[k]),
      }));
  }, [result]);

  const inputPlaceholder =
    selectedModel?.type === "embedding"
      ? "输入一段文本，返回向量维度（实际调一次向量化）"
      : selectedModel?.type === "rerank"
        ? "输入查询文本"
        : selectedModel?.type === "vllm"
          ? "输入提示词（可留空，默认「描述这张图片」）"
          : "输入问题，用该模型真实调用一次并返回回答";

  return (
    <Drawer
      title={target ? `模型调试：${target.display_name || target.name}` : "模型调试"}
      width={drawerWidth}
      extra={
        <Button
          size="small"
          type="text"
          onClick={() => setDrawerWidth(drawerWidth === 560 ? 840 : 560)}
        >
          {drawerWidth === 560 ? "加宽" : "收窄"}
        </Button>
      }
      open={!!target}
      onClose={onClose}
      destroyOnClose
    >
      {target && (
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          {/* 模型选择 */}
          {availableTypes.length > 0 && (
            <Space wrap size={4}>
              {availableTypes.map((t) => (
                <Button
                  key={t}
                  size="small"
                  type={selectedType === t ? "primary" : "default"}
                  icon={TYPE_ICON[t]}
                  onClick={() => selectType(t)}
                >
                  {TYPE_LABEL[t]}
                </Button>
              ))}
            </Space>
          )}
          <Select
            showSearch
            style={{ width: "100%" }}
            value={selectedId || undefined}
            placeholder="选择模型"
            onChange={selectModel}
            options={filteredModels.map((m) => ({
              label: `${m.display_name || m.name}${m.provider ? ` · ${m.provider}` : ""}`,
              value: m.id,
            }))}
          />

          {selectedModel && (
            <>
              {/* 输入区 */}
              {selectedModel.type !== "asr" && (
                <Input.TextArea
                  rows={4}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder={inputPlaceholder}
                />
              )}
              {selectedModel.type === "rerank" && (
                <>
                  <Input.TextArea
                    rows={4}
                    value={documentsText}
                    onChange={(e) => setDocumentsText(e.target.value)}
                    placeholder={"候选文档，每行一条"}
                  />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    候选文档：换行分隔，逐条传给 rerank 端点
                  </Text>
                </>
              )}
              {needsFile && (
                <Space direction="vertical" size={4}>
                  <Space>
                    <input
                      ref={fileInputRef}
                      type="file"
                      style={{ display: "none" }}
                      accept={selectedModel.type === "vllm" ? "image/*" : "audio/*"}
                      onChange={(e) => {
                        setFile(e.target.files?.[0] ?? null);
                        resetResult();
                      }}
                    />
                    <Button
                      size="small"
                      icon={<UploadOutlined />}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      选择{selectedModel.type === "vllm" ? "图片" : "音频"}文件
                    </Button>
                    {file && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {file.name} · {(file.size / 1024).toFixed(1)} KB
                      </Text>
                    )}
                  </Space>
                </Space>
              )}

              {/* chat 参数 */}
              {isChat && (
                <>
                  <Space wrap size={12}>
                    <Form.Item label="Temperature" style={{ marginBottom: 0 }}>
                      <InputNumber
                        min={0}
                        max={2}
                        step={0.1}
                        value={temperature}
                        onChange={(v) => setTemperature(v ?? 0.7)}
                        style={{ width: 110 }}
                      />
                    </Form.Item>
                    <Form.Item label="Top P" style={{ marginBottom: 0 }}>
                      <InputNumber
                        min={0.01}
                        max={1}
                        step={0.1}
                        value={topP}
                        onChange={(v) => setTopP(v ?? 1)}
                        style={{ width: 110 }}
                      />
                    </Form.Item>
                    <Form.Item label="Max Tokens" style={{ marginBottom: 0 }}>
                      <InputNumber
                        min={1}
                        max={8192}
                        step={128}
                        value={maxTokens}
                        onChange={(v) => setMaxTokens(v ?? 1024)}
                        style={{ width: 130 }}
                      />
                    </Form.Item>
                  </Space>
                  <Input.TextArea
                    rows={2}
                    value={systemPrompt}
                    onChange={(e) => setSystemPrompt(e.target.value)}
                    placeholder="系统提示词（可选）"
                  />
                  {supportsThinking && (
                    <Space>
                      <Switch checked={thinking} onChange={setThinking} />
                      <Text type="secondary">深度思考</Text>
                    </Space>
                  )}
                </>
              )}

              <Button
                type="primary"
                loading={running}
                disabled={!canRun}
                onClick={() => void run()}
              >
                运行调试
              </Button>

              {/* 结果区 */}
              {(result || history.length > 0) && (
                <Card
                  size="small"
                  title="调试结果"
                  extra={
                    result ? (
                      <Button
                        size="small"
                        icon={<CopyOutlined />}
                        onClick={() => void copyResult()}
                      >
                        复制
                      </Button>
                    ) : undefined
                  }
                >
                  <Space direction="vertical" style={{ width: "100%" }} size={8}>
                    {history.length > 1 && (
                      <Space wrap size={4}>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          历史：
                        </Text>
                        {history.map((h) => (
                          <Tag
                            key={h.id}
                            color={result === h.result ? "blue" : undefined}
                            style={{ cursor: "pointer" }}
                            onClick={() => {
                              setResult(h.result);
                              setResultTab("response");
                            }}
                          >
                            {h.label} · {h.result.elapsed_ms ?? "-"} ms
                          </Tag>
                        ))}
                      </Space>
                    )}
                    {result && (
                      <>
                        <Space size={8}>
                          {result.ok ? (
                            <Tag color="success">成功</Tag>
                          ) : (
                            <Tag color="error">失败</Tag>
                          )}
                          {typeof result.elapsed_ms === "number" && (
                            <Text type="secondary">{result.elapsed_ms} ms</Text>
                          )}
                        </Space>
                        {metrics.length > 0 && (
                          <Space wrap size={4}>
                            {metrics.map((m2) => (
                              <Tag key={m2.key}>
                                {m2.label}: {m2.value}
                              </Tag>
                            ))}
                          </Space>
                        )}
                        {result.error && <Text type="danger">{result.error}</Text>}
                        <Tabs
                          size="small"
                          activeKey={resultTab}
                          onChange={(k) => setResultTab(k as "response" | "request")}
                          items={[
                            { key: "response", label: "响应" },
                            { key: "request", label: "请求预览" },
                          ]}
                        />
                        <pre
                          style={{
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-all",
                            margin: 0,
                            maxHeight: 260,
                            overflow: "auto",
                            fontSize: 12,
                          }}
                        >
                          {formattedResult || "(空)"}
                        </pre>
                      </>
                    )}
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
