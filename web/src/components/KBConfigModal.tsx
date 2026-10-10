"use client";

import { useEffect, useState } from "react";
import {
  Button,
  Checkbox,
  Col,
  Divider,
  Drawer,
  Form,
  InputNumber,
  Radio,
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
  apiListModels,
  apiListParserEngines,
  apiListSkillsRegistry,
  apiPutChunkingConfig,
  apiUpdateKb,
  ChunkingConfig,
  KbItem,
  KbUpdatePayload,
  ModelItem,
  ParserEngineItem,
} from "@/lib/api";
import { App } from "antd";
import ChunkDebugDrawer from "@/components/ChunkDebugDrawer";

const { Text } = Typography;

const PIPELINE_OPTS = [
  { label: "向量检索", value: "vector" },
  { label: "关键词检索", value: "keyword" },
  { label: "Wiki 构建", value: "wiki" },
  { label: "知识图谱", value: "graph" },
];

// 切片策略下拉（中文 label + 说明）
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

// 表单里属于切片配置的字段（保存时从整体表单值中挑出）
const CHUNKING_KEYS = [
  "strategy",
  "chunk_size",
  "chunk_overlap",
  "token_limit",
  "separators",
  "enable_parent_child",
  "parent_chunk_size",
  "child_chunk_size",
  "languages",
  "parser_engine_rules",
] as const;

const pickChunking = (values: Record<string, unknown>): ChunkingConfig => {
  const picked: Partial<ChunkingConfig> = {};
  for (const k of CHUNKING_KEYS) {
    const v = values[k];
    if (v !== undefined) (picked as Record<string, unknown>)[k] = v;
  }
  return { ...DEFAULT_CHUNKING, ...picked };
};

interface Props {
  kb: KbItem | null;
  open: boolean;
  onClose: (changed?: boolean) => void;
}

/** 知识库配置弹窗（WeKnora 对齐）：类型/索引开关/模型绑定/图谱/FAQ + 切片配置与解析引擎规则。 */
export default function KBConfigModal({ kb, open, onClose }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [skills, setSkills] = useState<{ name: string }[]>([]);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [engines, setEngines] = useState<ParserEngineItem[]>([]);
  const [chunkLoading, setChunkLoading] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const customWiki = Form.useWatch("custom_wiki_generation", form) ?? false;
  const kbType = Form.useWatch("type", form) ?? "document";
  const enableParentChild = Form.useWatch("enable_parent_child", form);
  const strategy = Form.useWatch("strategy", form);

  // 技能/模型下拉数据源
  useEffect(() => {
    if (!open) return;
    void (async () => {
      try {
        const s = await apiListSkillsRegistry();
        if (s.success && s.data) setSkills(s.data.items);
      } catch { /* 不阻断 */ }
      try {
        const m = await apiListModels();
        if (m.success && m.data) setModels(m.data.items);
      } catch { /* 不阻断 */ }
    })();
  }, [open]);

  // 打开时回填当前配置
  useEffect(() => {
    if (!open || !kb) return;
    const strat = kb.indexing_strategy || {};
    form.setFieldsValue({
      type: kb.type || "document",
      pipelines: [
        ...(strat.vector_enabled ? ["vector"] : []),
        ...(strat.keyword_enabled ? ["keyword"] : []),
        ...(strat.wiki_enabled ? ["wiki"] : []),
        ...(strat.graph_enabled ? ["graph"] : []),
      ],
      custom_wiki_generation: !!kb.custom_wiki_generation,
      wiki_skill: kb.wiki_config?.skill || undefined,
      extraction_granularity: kb.wiki_config?.extraction_granularity || "standard",
      embedding_model_id: kb.embedding_model_id || undefined,
      summary_model_id: kb.wiki_config?.synthesis_model_id || kb.summary_model_id || undefined,
      graph_extract: !!kb.extract_config?.enabled,
      faq_index_mode: kb.faq_config?.index_mode || "question_answer",
      question_generation: !!kb.question_generation_config?.enabled,
      question_count: kb.question_generation_config?.question_count || 3,
    });
  }, [open, kb, form]);

  // 打开时回填切片配置 + 解析引擎列表（接口不可用时优雅降级为默认值）
  useEffect(() => {
    if (!open || !kb) return;
    let alive = true;
    void (async () => {
      setChunkLoading(true);
      try {
        const [cfgRes, engRes] = await Promise.all([
          apiGetChunkingConfig(kb.id),
          apiListParserEngines(),
        ]);
        if (!alive) return;
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
        if (alive) setChunkLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [open, kb, form, message]);

  const engineOptions = engines.map((e) => ({
    value: e.name,
    label: e.display_name || e.name,
    disabled: !e.available,
    // 不可用引擎置灰，悬停显示原因（契约：带 reason tooltip）
    title: e.available
      ? e.description || `解析引擎 ${e.display_name || e.name}`
      : e.reason || "引擎不可用",
  }));

  const onSave = async () => {
    if (!kb) return;
    const v = await form.validateFields().catch(() => null);
    if (!v) return;
    const picked: string[] = v.pipelines || [];
    const payload: KbUpdatePayload = {
      type: v.type,
      indexing_strategy: {
        vector_enabled: picked.includes("vector"),
        keyword_enabled: picked.includes("keyword"),
        wiki_enabled: picked.includes("wiki"),
        graph_enabled: picked.includes("graph"),
      },
      custom_wiki_generation: v.custom_wiki_generation,
      embedding_model_id: v.embedding_model_id || null,
      summary_model_id: v.summary_model_id || null,
      configs: {
        wiki_config: {
          skill: v.wiki_skill || "",
          extraction_granularity: v.extraction_granularity || "standard",
        },
        extract_config: { enabled: !!v.graph_extract },
        faq_config: { index_mode: v.faq_index_mode || "question_answer" },
        question_generation_config: {
          enabled: !!v.question_generation,
          question_count: v.question_count || 3,
        },
      },
    };
    setSaving(true);
    try {
      const res = await apiUpdateKb(kb.id, payload);
      if (!res.success) {
        message.error(res.message || "保存失败");
        return;
      }
      const chunkRes = await apiPutChunkingConfig(kb.id, pickChunking(v));
      if (!chunkRes.success) {
        message.error(`知识库配置已保存，切片配置保存失败：${chunkRes.message || "接口未上线？"}`);
        return;
      }
      message.success("知识库配置已保存");
      onClose(true);
    } catch {
      message.error("保存请求失败");
    } finally {
      setSaving(false);
    }
  };

  const modelOptions = (type: string) =>
    models.filter((m) => m.type === type).map((m) => ({
      label: m.name,
      value: m.id,
    }));

  return (
    <>
      <Drawer
        title={`知识库配置 · ${kb?.name ?? ""}`}
        open={open}
        onClose={() => onClose()}
        width={620}
        extra={
          <Space>
            <Button onClick={() => onClose()}>取消</Button>
            <Button type="primary" loading={saving} onClick={() => void onSave()}>
              保存
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
          <Form.Item name="type" label="知识库类型">
            <Radio.Group optionType="button" buttonStyle="solid">
              <Radio.Button value="document">文档型</Radio.Button>
              <Radio.Button value="faq">问答对型</Radio.Button>
            </Radio.Group>
          </Form.Item>

          <Form.Item name="pipelines" label="索引流水线">
            <Checkbox.Group options={PIPELINE_OPTS} />
          </Form.Item>

          <Form.Item
            name="custom_wiki_generation"
            label="自定义 Wiki 生成"
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          {customWiki && (
            <>
              <Form.Item name="wiki_skill" hidden>
                <Select />
              </Form.Item>
              <Form.Item name="extraction_granularity" label="抽取粒度">
                <Select
                  options={[
                    { label: "聚焦（focused）", value: "focused" },
                    { label: "标准（standard）", value: "standard" },
                    { label: "详尽（exhaustive）", value: "exhaustive" },
                  ]}
                />
              </Form.Item>
            </>
          )}

          <Divider plain style={{ margin: "8px 0" }}>
            <Typography.Text type="secondary">模型绑定</Typography.Text>
          </Divider>
          <Form.Item name="embedding_model_id" label="Embedding 模型（向量化）">
            <Select allowClear placeholder="留空 = 系统默认" options={modelOptions("embedding")} />
          </Form.Item>
          <Form.Item name="summary_model_id" label="Wiki 合成模型（LLM）">
            <Select allowClear placeholder="留空 = 系统默认" options={modelOptions("chat")} />
          </Form.Item>

          {kbType === "document" && (
            <>
              <Divider plain style={{ margin: "8px 0" }}>
                <Typography.Text type="secondary">图谱与问题生成</Typography.Text>
              </Divider>
              <Form.Item name="graph_extract" label="知识图谱抽取" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="question_generation" label="问题生成" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="question_count" label="每文档问题数">
                <InputNumber min={0} max={20} style={{ width: 120 }} />
              </Form.Item>
            </>
          )}
          {kbType === "faq" && (
            <>
              <Divider plain style={{ margin: "8px 0" }}>
                <Typography.Text type="secondary">FAQ 索引</Typography.Text>
              </Divider>
              <Form.Item name="faq_index_mode" label="索引模式">
                <Select
                  options={[
                    { label: "问题与答案一起索引", value: "question_answer" },
                    { label: "仅索引问题", value: "question_only" },
                  ]}
                />
              </Form.Item>
            </>
          )}

          <Divider plain style={{ margin: "8px 0" }}>
            <Typography.Text type="secondary">切片配置</Typography.Text>
          </Divider>
          <Spin spinning={chunkLoading}>
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
            <Text type="secondary" style={{ fontSize: 12 }}>
              提示：不可用的引擎已置灰，鼠标悬停查看原因（如 MinerU endpoint 未配置）。
            </Text>
          </Spin>
        </Form>
      </Drawer>

      <ChunkDebugDrawer
        open={debugOpen}
        kbId={kb?.id ?? ""}
        config={pickChunking(form.getFieldsValue())}
        onClose={() => setDebugOpen(false)}
      />
    </>
  );
}
