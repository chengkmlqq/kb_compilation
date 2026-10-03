"use client";

/**
 * 知识库「切片配置 + 解析引擎规则」设置弹窗。
 *
 * 契约端点（后端未上线时优雅降级，不崩溃）：
 *   GET  /api/v1/kbs/{kbId}/chunking-config   读取当前配置 + 默认值
 *   PUT  /api/v1/kbs/{kbId}/chunking-config   partial 合并保存
 *   GET  /api/v1/parsers/engines               解析引擎列表（不可用置灰带原因）
 *   POST /api/v1/kbs/chunk-preview            分块调试（见 ChunkDebugDrawer）
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Col,
  Form,
  InputNumber,
  Modal,
  Row,
  Select,
  Slider,
  Space,
  Spin,
  Switch,
  Typography,
} from "antd";
import { ExperimentOutlined } from "@ant-design/icons";
import {
  apiGetChunkingConfig,
  apiListParserEngines,
  apiPutChunkingConfig,
  ChunkingConfig,
  ParserEngineItem,
} from "@/lib/api";
import ChunkDebugDrawer from "@/components/ChunkDebugDrawer";

const { Text } = Typography;

// 策略下拉（中文 label + 说明）
const STRATEGY_OPTIONS = [
  { value: "auto", label: "自动（auto）", desc: "按文档特征自动选择档位，推荐" },
  { value: "heading", label: "标题优先（heading）", desc: "按 Markdown 标题层级切分" },
  { value: "heuristic", label: "启发式（heuristic）", desc: "按编号章节/分隔线等结构标记切分" },
  { value: "recursive", label: "递归分隔符（recursive）", desc: "按分隔符优先级递归切分" },
  { value: "legacy", label: "基础分隔符（legacy）", desc: "固定分隔符切分，最终兜底" },
];

const STRATEGY_DESC: Record<string, string> = Object.fromEntries(
  STRATEGY_OPTIONS.map((o) => [o.value, o.desc]),
);

// 分隔符预设（可 creatable 自定义）
const SEPARATOR_PRESETS = ["\n## ", "\n\n", "\n", "。", ". ", " "];

const LANG_OPTIONS = [
  { value: "en", label: "英语" },
  { value: "de", label: "德语" },
  { value: "zh", label: "中文" },
];

const FILE_TYPE_OPTIONS = [
  "pdf", "docx", "doc", "pptx", "xlsx", "md", "txt", "epub", "html", "csv",
].map((v) => ({ value: v, label: v }));

const DEFAULT_CHUNKING: ChunkingConfig = {
  chunk_size: 800,
  chunk_overlap: 80,
  separators: ["\n## ", "\n\n", "\n"],
  enable_parent_child: false,
  parent_chunk_size: 4096,
  child_chunk_size: 384,
  strategy: "auto",
  token_limit: 0,
  languages: [],
  parser_engine_rules: [],
};

interface ChunkingConfigModalProps {
  open: boolean;
  kbId: string;
  onClose: () => void;
}

export default function ChunkingConfigModal({ open, kbId, onClose }: ChunkingConfigModalProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm<ChunkingConfig>();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [engines, setEngines] = useState<ParserEngineItem[]>([]);
  const [debugOpen, setDebugOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [cfgRes, engRes] = await Promise.all([
        apiGetChunkingConfig(kbId),
        apiListParserEngines(),
      ]);
      // 引擎列表：失败时给一个最小可用的 docreader 占位，不阻塞配置编辑
      if (engRes.success && Array.isArray(engRes.data)) {
        setEngines(engRes.data);
      } else {
        setEngines([
          {
            name: "docreader",
            display_name: "DocReader（内置）",
            description: "默认解析引擎",
            available: true,
            reason: null,
            file_types: [],
          },
        ]);
      }
      if (cfgRes.success && cfgRes.data) {
        form.setFieldsValue({ ...DEFAULT_CHUNKING, ...cfgRes.data.chunking });
      } else {
        form.setFieldsValue({ ...DEFAULT_CHUNKING });
        message.info("切片配置接口暂不可用，展示默认配置");
      }
    } finally {
      setLoading(false);
    }
  }, [kbId, form, message]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const engineOptions = useMemo(
    () =>
      engines.map((e) => ({
        value: e.name,
        label: e.display_name || e.name,
        disabled: !e.available,
        // 不可用引擎置灰，悬停显示原因（契约：带 reason tooltip）
        title: e.available
          ? e.description || `解析引擎 ${e.display_name || e.name}`
          : e.reason || "引擎不可用",
      })),
    [engines],
  );

  const onSave = async () => {
    let values: ChunkingConfig;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    try {
      const res = await apiPutChunkingConfig(kbId, values);
      if (res.success) {
        message.success("切片配置已保存");
        onClose();
      } else {
        message.error(res.message || "保存失败（接口未上线？）");
      }
    } catch {
      message.error("保存请求失败");
    } finally {
      setSaving(false);
    }
  };

  // 传给调试抽屉的当前（未保存）配置
  const currentConfig = form.getFieldsValue();
  // 用 useWatch 监听联动字段，确保开关/策略变化时界面实时响应
  const enableParentChild = Form.useWatch("enable_parent_child", form);
  const strategy = Form.useWatch("strategy", form);

  return (
    <>
      <Modal
        title="切片配置"
        open={open}
        onCancel={onClose}
        onOk={() => void onSave()}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={720}
        destroyOnClose
      >
        <Spin spinning={loading}>
          <Form form={form} layout="vertical" initialValues={DEFAULT_CHUNKING}>
          <Row gutter={16}>
            <Col span={16}>
              <Form.Item name="strategy" label="切片策略">
                <Select options={STRATEGY_OPTIONS} />
              </Form.Item>
              <Text type="secondary" style={{ fontSize: 12, marginTop: -8, display: "block" }}>
                {STRATEGY_DESC[strategy ?? "auto"] ?? STRATEGY_DESC.auto}
              </Text>
            </Col>
            <Col span={8}>
              <Form.Item label=" " colon={false}>
                <Button
                  icon={<ExperimentOutlined />}
                  onClick={() => setDebugOpen(true)}
                  style={{ width: "100%" }}
                >
                  测试分块
                </Button>
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="chunk_size" label="分块大小">
                <InputNumber min={64} max={32768} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="chunk_overlap" label="重叠长度">
                <InputNumber min={0} max={4096} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="token_limit" label="Token 上限" tooltip="0 = 按字符数">
                <InputNumber min={0} style={{ width: "100%" }} placeholder="0=按字符" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="separators" label="分隔符">
            <Select mode="tags" options={SEPARATOR_PRESETS.map((v) => ({ value: v, label: JSON.stringify(v) }))} />
          </Form.Item>

          <Form.Item label="父子分块">
            <Space>
              <Form.Item name="enable_parent_child" valuePropName="checked" noStyle>
                <Switch checkedChildren="开" unCheckedChildren="关" />
              </Form.Item>
              <Text type="secondary" style={{ fontSize: 12 }}>
                开启后子块用于匹配、命中返回父块全文
              </Text>
            </Space>
          </Form.Item>

          {enableParentChild && (
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item name="parent_chunk_size" label="父块大小" tooltip="512–8192">
                  <Slider min={512} max={8192} step={128} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="child_chunk_size" label="子块大小" tooltip="64–2048">
                  <Slider min={64} max={2048} step={32} />
                </Form.Item>
              </Col>
            </Row>
          )}

          <Form.Item name="languages" label="语言（留空自动识别）">
            <Select mode="multiple" options={LANG_OPTIONS} allowClear />
          </Form.Item>

          <Form.Item label="解析引擎规则（按文件类型选引擎）" style={{ marginBottom: 8 }}>
            <Form.List name="parser_engine_rules">
              {(fields, { add, remove }) => (
                <>
                  {fields.map((field) => (
                    <Row key={field.key} gutter={8} align="middle" style={{ marginBottom: 8 }}>
                      <Col span={11}>
                        <Form.Item
                          {...field}
                          name={[field.name, "file_types"]}
                          noStyle
                          rules={[{ required: true, message: "选文件类型" }]}
                        >
                          <Select
                            mode="multiple"
                            placeholder="文件类型"
                            options={FILE_TYPE_OPTIONS}
                            style={{ width: "100%" }}
                          />
                        </Form.Item>
                      </Col>
                      <Col span={11}>
                        <Form.Item {...field} name={[field.name, "engine"]} noStyle
                          rules={[{ required: true, message: "选引擎" }]}
                        >
                          <Select placeholder="解析引擎" options={engineOptions} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col span={2}>
                        <Button type="text" danger size="small" onClick={() => remove(field.name)}>
                          删除
                        </Button>
                      </Col>
                    </Row>
                  ))}
                  <Button type="dashed" onClick={() => add({ file_types: [], engine: "docreader" })} block>
                    添加规则
                  </Button>
                </>
              )}
            </Form.List>
          </Form.Item>
        </Form>

        <Text type="secondary" style={{ fontSize: 12 }}>
          提示：不可用的引擎已置灰，鼠标悬停查看原因（如 MinerU endpoint 未配置）。
        </Text>
        </Spin>
      </Modal>

      <ChunkDebugDrawer
        open={debugOpen}
        kbId={kbId}
        config={currentConfig}
        onClose={() => setDebugOpen(false)}
      />
    </>
  );
}
