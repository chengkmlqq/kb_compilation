"use client";

import { useEffect, useState } from "react";
import {
  Button,
  Col,
  Drawer,
  Form,
  InputNumber,
  Row,
  Select,
  Slider,
  Space,
  Spin,
  Switch,
  Typography,
} from "antd";
import {
  BookOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  SearchOutlined,
} from "@ant-design/icons";
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

// 分区导航（对齐智能体编辑器 AgentEditorModal 的左侧分区模式）
const KB_SECTIONS = [
  { key: "parse", label: "文档解析", icon: <FileTextOutlined /> },
  { key: "wiki", label: "Wiki 构建", icon: <BookOutlined /> },
  { key: "retrieval", label: "检索配置", icon: <SearchOutlined /> },
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

/** 知识库配置弹窗（WeKnora 对齐）：索引开关/模型绑定/图谱/问题生成 + 切片配置与解析引擎规则。 */
export default function KBConfigModal({ kb, open, onClose }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [skills, setSkills] = useState<{ name: string }[]>([]);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [engines, setEngines] = useState<ParserEngineItem[]>([]);
  const [chunkLoading, setChunkLoading] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [section, setSection] = useState("parse");
  const customWiki = Form.useWatch("custom_wiki_generation", form) ?? false;
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
      vector_enabled: !!strat.vector_enabled,
      keyword_enabled: !!strat.keyword_enabled,
      graph_enabled: !!strat.graph_enabled,
      custom_wiki_generation: !!kb.custom_wiki_generation,
      wiki_skill: kb.wiki_config?.skill || undefined,
      extraction_granularity: kb.wiki_config?.extraction_granularity || "standard",
      embedding_model_id: kb.embedding_model_id || undefined,
      summary_model_id: kb.wiki_config?.synthesis_model_id || kb.summary_model_id || undefined,
      // 摘要策略固定 auto（系统按文档规模自动路由），不出现在表单中
      graph_extract: !!kb.extract_config?.enabled,
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
    const payload: KbUpdatePayload = {
      type: "document",
      indexing_strategy: {
        vector_enabled: !!v.vector_enabled,
        keyword_enabled: !!v.keyword_enabled,
        // 「Wiki 构建」开关表单已移除（2026-10-15）：触发不再依赖该开关——
        // 配置了构建引擎（KB 级或文档级）即自动触发构建；保存时保留 KB 原值，
        // 不走 !!v.wiki_enabled（undefined→false 会误关存量 KB）。
        wiki_enabled: !!kb.indexing_strategy?.wiki_enabled,
        // 知识图谱开关表单已移除（2026-10-15）：保存时保留 KB 原值，
        // 不走 !!v.graph_enabled（undefined→false 会误关已开启图谱的存量 KB）。
        graph_enabled: !!kb.indexing_strategy?.graph_enabled,
      },
      custom_wiki_generation: v.custom_wiki_generation,
      embedding_model_id: v.embedding_model_id || null,
      summary_model_id: v.summary_model_id || null,
      // 摘要策略固定 auto：系统按文档规模自动路由（小文档全文直塞 / 大文档分块合并）
      summary_strategy: "auto",
      configs: {
        wiki_config: {
          skill: v.wiki_skill || "",
          extraction_granularity: v.extraction_granularity || "standard",
        },
        extract_config: kb.extract_config ?? { enabled: false },
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
        width={860}
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
          <div style={{ display: "flex", gap: 16, minHeight: 440 }}>
            {/* 左侧分区导航（对齐智能体编辑器 AgentEditorModal） */}
            <div
              style={{
                width: 150,
                borderRight: "1px solid #f0f0f0",
                paddingRight: 12,
                flexShrink: 0,
              }}
            >
              {KB_SECTIONS.map((s) => (
                <div
                  key={s.key}
                  onClick={() => setSection(s.key)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "8px 12px",
                    borderRadius: 6,
                    cursor: "pointer",
                    marginBottom: 4,
                    background: section === s.key ? "rgba(22,119,255,0.1)" : undefined,
                    color: section === s.key ? "#1677ff" : undefined,
                    fontWeight: section === s.key ? 600 : 400,
                  }}
                >
                  {s.icon}
                  {s.label}
                </div>
              ))}
            </div>

            {/* 右侧分区内容 */}
            <div style={{ flex: 1, overflowY: "auto", maxHeight: 560 }}>
              {section === "parse" && (
                <>
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
                </>
              )}

              {section === "wiki" && (
                <>
                  <Form.Item
                    name="custom_wiki_generation"
                    label="自定义 Wiki 生成"
                    valuePropName="checked"
                    extra="关闭自动构建、改为手动触发（配置了构建引擎时仍会自动构建）"
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
                  <Form.Item name="summary_model_id" label="Wiki 合成模型（LLM）">
                    <Select allowClear placeholder="留空 = 系统默认" options={modelOptions("chat")} />
                  </Form.Item>

                  <Form.Item name="question_generation" label="问题生成" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                  <Form.Item name="question_count" label="每文档问题数">
                    <InputNumber min={0} max={20} style={{ width: 120 }} />
                  </Form.Item>
                </>
              )}

              {section === "retrieval" && (
                <>
                  <Form.Item name="vector_enabled" label="向量检索" valuePropName="checked" extra="基于语义相似度召回，需配置 Embedding 模型">
                    <Switch />
                  </Form.Item>
                  <Form.Item name="keyword_enabled" label="关键词检索" valuePropName="checked" extra="基于关键词子串匹配召回（LIKE 检索）">
                    <Switch />
                  </Form.Item>
                  <Form.Item name="embedding_model_id" label="Embedding 模型（向量化）">
                    <Select allowClear placeholder="留空 = 系统默认" options={modelOptions("embedding")} />
                  </Form.Item>
                </>
              )}
            </div>
          </div>
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
