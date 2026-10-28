"use client";

import { useEffect, useState } from "react";
import {
  Button,
  Col,
  Form,
  Input,
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
import {
  BookOutlined,
  ClusterOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  PictureOutlined,
  ProfileOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  apiGetChunkingConfig,
  apiListBuildEngines,
  apiListKbCategories,
  apiListModels,
  apiListParserEngines,
  apiListSkillsRegistry,
  apiPutChunkingConfig,
  apiUpdateKb,
  BuildEngineItem,
  ChunkingConfig,
  KbCategoryItem,
  KbItem,
  KbUpdatePayload,
  ModelItem,
  ParserEngineItem,
} from "@/lib/api";
import { groupBuildEngineOptions } from "@/lib/tape-func";
import { App } from "antd";
import ChunkDebugDrawer from "@/components/ChunkDebugDrawer";

const { Text } = Typography;

// 分区导航（对齐智能体编辑器 AgentEditorModal 的左侧分区模式）
const KB_SECTIONS = [
  { key: "basic", label: "基本信息", icon: <ProfileOutlined /> },
  { key: "parse", label: "文档解析", icon: <FileTextOutlined /> },
  { key: "image", label: "图片处理", icon: <PictureOutlined /> },
  { key: "wiki", label: "Wiki 构建", icon: <BookOutlined /> },
  { key: "retrieval", label: "检索配置", icon: <SearchOutlined /> },
  { key: "aggregation", label: "知识聚合", icon: <ClusterOutlined /> },
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
  const [buildEngines, setBuildEngines] = useState<BuildEngineItem[]>([]);
  // 分类下拉（2026-10-20 配置弹窗补入口）：数据源 = 分类树（kb_category 行 ∪ 可见 KB label）
  const [categories, setCategories] = useState<KbCategoryItem[]>([]);
  const [chunkLoading, setChunkLoading] = useState(false);
  // 选项数据源（技能/模型/构建引擎/分类）加载中
  const [optionsLoading, setOptionsLoading] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [section, setSection] = useState("basic");
  // 切片智能增强（2026-10-25）：开关联动密度参数显隐
  const [enhanceEnabled, setEnhanceEnabled] = useState(false);
  const enableParentChild = Form.useWatch("enable_parent_child", form);
  const strategy = Form.useWatch("strategy", form);

  // 技能/模型下拉数据源
  useEffect(() => {
    if (!open) return;
    setOptionsLoading(true);
    void (async () => {
      try {
        try {
          const s = await apiListSkillsRegistry();
          if (s.success && s.data) setSkills(s.data.items);
        } catch { /* 不阻断 */ }
        try {
          const m = await apiListModels();
          if (m.success && m.data) setModels(m.data.items);
        } catch { /* 不阻断 */ }
        try {
          // 构建引擎下拉（2026-10-16 配置弹窗补入口）：服务编排中已发布的编排
          const be = await apiListBuildEngines();
          if (be.success && Array.isArray(be.data?.items)) setBuildEngines(be.data.items);
        } catch { /* 不阻断 */ }
        try {
          // 分类下拉（2026-10-20）：左侧分类树数据（kb_category 行 ∪ 可见 KB label）
          const c = await apiListKbCategories();
          if (c.success) setCategories(c.data?.items ?? []);
        } catch { /* 不阻断 */ }
      } finally {
        setOptionsLoading(false);
      }
    })();
  }, [open]);

  // 打开时回填当前配置
  useEffect(() => {
    if (!open || !kb) return;
    const strat = kb.indexing_strategy || {};
    form.setFieldsValue({
      name: kb.name || "",
      description: kb.description || "",
      label: kb.label || undefined,
      vector_enabled: !!strat.vector_enabled,
      keyword_enabled: !!strat.keyword_enabled,
      graph_enabled: !!strat.graph_enabled,
      build_engine: kb.build_tape_id || undefined,
      // 自定义 Wiki 生成表单已移除（2026-10-20）：保存不传该字段，后端保留原值
      // （custom_wiki_generation=true 的老库仍按手动模式，不被动切换）
      embedding_model_id: kb.embedding_model_id || undefined,
      summary_model_id: kb.wiki_config?.synthesis_model_id || kb.summary_model_id || undefined,
      // 摘要策略固定 auto（系统按文档规模自动路由），不出现在表单中
      graph_extract: !!kb.extract_config?.enabled,
      // 问题生成表单已移除（2026-10-20）：保存不传该字段，后端保留原值
      // 图片处理：对齐 WeKnora vlm_config（EnableMultimodel + 绑定的视觉模型）
      vlm_enabled: !!kb.vlm_config?.enabled,
      vlm_model_id: (kb.vlm_config?.model_id as string) || undefined,
      vlm_description_language:
        (kb.vlm_config?.description_language as string) || "",
      // 知识聚合（2026-10-25）：配置弹窗回填开关 + 主题列表
      topic_cluster_enabled: !!kb.aggregation_config?.topic_cluster_enabled,
      topic_cluster_topics: (kb.aggregation_config?.topic_cluster_topics as string[] | undefined) ?? [],
      version_cluster_enabled: !!kb.aggregation_config?.version_cluster_enabled,
      // 切片智能增强（2026-10-25）：回填开关 + 密度参数
      enhance_enabled: !!kb.enhancement_config?.enabled,
      enhance_keywords_dividend: kb.enhancement_config?.keywords_dividend || undefined,
      enhance_questions_dividend: kb.enhancement_config?.questions_dividend || undefined,
      enhance_max_chunks: kb.enhancement_config?.max_chunks || undefined,
    });
    setEnhanceEnabled(!!kb.enhancement_config?.enabled);
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
    // 分区表单坑（2026-10-21 修复，对齐 kbs/page.tsx / AgentEditorModal）：
    // 字段按 section 条件渲染，validateFields() 只返回「当前挂载分区」的值——
    // 停在「基本信息」保存时，检索/VLM/切片等分区字段已卸载不在 v 里，
    // 直接取 v 会把 vector_enabled 当 false 误关、model_id 当 null 解绑、
    // 切片配置重置默认。用 getFieldsValue(true) 取 store 全量
    // （打开时已回填全部字段，preserve 保留）合并后提交。
    const all = form.getFieldsValue(true);
    const values = { ...all, ...v };
    const payload: KbUpdatePayload = {
      // 基本信息（2026-10-20）：名称 / 描述 / 分类
      name: (values.name as string | undefined)?.trim() || kb.name,
      description: (values.description as string | undefined) ?? "",
      // 分类（2026-10-20）：空 = 移出分类（后端 exclude_none 丢 null，必须传空串清空）
      label: (values.label as string | undefined) ?? "",
      type: "document",
      indexing_strategy: {
        vector_enabled: !!values.vector_enabled,
        keyword_enabled: !!values.keyword_enabled,
        // 「Wiki 构建」开关表单已移除（2026-10-15）：触发不再依赖该开关——
        // 配置了构建引擎（KB 级或文档级）即自动触发构建；保存时保留 KB 原值，
        // 不走 !!v.wiki_enabled（undefined→false 会误关存量 KB）。
        wiki_enabled: !!kb.indexing_strategy?.wiki_enabled,
        // 知识图谱开关表单已移除（2026-10-15）：保存时保留 KB 原值，
        // 不走 !!v.graph_enabled（undefined→false 会误关已开启图谱的存量 KB）。
        graph_enabled: !!kb.indexing_strategy?.graph_enabled,
      },
      // 自定义 Wiki 生成 / 问题生成表单已移除（2026-10-20）：
      // 不传 custom_wiki_generation / question_generation_config / wiki_config，
      // 后端「字段在 payload 才更新」→ 存量值保持不变（老库手动模式不被动切换、问题生成不被动关闭）
      // 构建引擎（2026-10-16 配置弹窗补入口）：回填保证未动时表单值=原绑定；
      // allowClear 清空 → null = 解除绑定（后端「字段在 payload 才更新」语义）
      build_tape_id: (values.build_engine as string | undefined) || null,
      embedding_model_id: values.embedding_model_id || null,
      summary_model_id: values.summary_model_id || null,
      // 摘要策略固定 auto：系统按文档规模自动路由（小文档全文直塞 / 大文档分块合并）
      summary_strategy: "auto",
      configs: {
        extract_config: kb.extract_config ?? { enabled: false },
        // 图片处理（对齐 WeKnora vlm_config）：开关 + 绑定的视觉模型 + 描述语言
        vlm_config: {
          enabled: !!values.vlm_enabled,
          model_id: (values.vlm_model_id as string | undefined) || "",
          description_language:
            (values.vlm_description_language as string | undefined) || "",
        },
      },
      // 知识聚合（2026-10-25）：开关 + 主题列表（后端校验主题必填 + 同步定时任务 + 立即聚合）
      aggregation_config: {
        topic_cluster_enabled: !!values.topic_cluster_enabled,
        topic_cluster_topics: Array.isArray(values.topic_cluster_topics)
          ? (values.topic_cluster_topics as string[])
              .map((t: string) => t.trim())
              .filter(Boolean)
          : [],
        version_cluster_enabled: !!values.version_cluster_enabled,
      },
      // 切片智能增强（2026-10-25）：开关 + 密度参数（后端归一，非法值回退默认）
      enhancement_config: {
        enabled: !!values.enhance_enabled,
        keywords_dividend:
          (values.enhance_keywords_dividend as number | undefined) ?? 120,
        questions_dividend:
          (values.enhance_questions_dividend as number | undefined) ?? 180,
        max_chunks: (values.enhance_max_chunks as number | undefined) ?? 0,
      },
    };
    setSaving(true);
    try {
      const res = await apiUpdateKb(kb.id, payload);
      if (!res.success) {
        message.error(res.message || "保存失败");
        return;
      }
      const chunkRes = await apiPutChunkingConfig(kb.id, pickChunking(values));
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
        label: m.display_name || m.name,
        value: m.id,
      }));

    // 视觉模型下拉（图片处理）：chat / vllm 类型且标记支持视觉（对齐 models 侧
    // resolve_vision_model_config 的 type in (chat, vllm) + supports_vision 门禁）
    const visionModelOptions = models
      .filter(
        (m) =>
          (m.type === "chat" || m.type === "vllm") && m.supports_vision,
      )
      .map((m) => ({
        label: `${m.display_name || m.name}${m.type === "vllm" ? "（视觉）" : ""}`,
        value: m.id,
      }));

  // 可选模型唯一时默认选上（仅在字段为空时填充，不覆盖已保存配置或用户已选）
  useEffect(() => {
    if (!open || models.length === 0) return;
    const chat = modelOptions("chat");
    const embedding = modelOptions("embedding");
    if (chat.length === 1 && !form.getFieldValue("summary_model_id")) {
      form.setFieldValue("summary_model_id", chat[0].value);
    }
    if (embedding.length === 1 && !form.getFieldValue("embedding_model_id")) {
      form.setFieldValue("embedding_model_id", embedding[0].value);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, models, form]);

  return (
    <>
      <Modal
        title={`知识库配置 · ${kb?.name ?? ""}`}
        open={open}
        onCancel={() => onClose()}
        width={860}
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onOk={() => void onSave()}
        destroyOnClose
      >
        <Spin spinning={optionsLoading}>
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
              {section === "basic" && (
                <>
                  <Form.Item
                    name="name"
                    label="知识库名称"
                    rules={[{ required: true, whitespace: true, message: "请输入知识库名称" }]}
                  >
                    <Input placeholder="如：监管制度知识库" maxLength={255} />
                  </Form.Item>
                  <Form.Item
                    name="description"
                    label="知识库描述"
                    extra="一句话说明知识库内容范围，供问答/检索场景展示"
                  >
                    <Input.TextArea
                      placeholder="如：收录集团市场监督管理相关制度文件"
                      rows={3}
                      maxLength={1000}
                      showCount
                    />
                  </Form.Item>
                  <Form.Item
                    name="label"
                    label="所属分类"
                    extra="选择左侧分类树中的已有分类；留空 = 未分类"
                  >
                    <Select
                      allowClear
                      showSearch
                      optionFilterProp="label"
                      placeholder="选择分类"
                      options={categories.map((c) => ({ label: c.name, value: c.name }))}
                    />
                  </Form.Item>
                  <div
                    style={{
                      marginTop: 16,
                      paddingTop: 12,
                      borderTop: "1px solid #f0f0f0",
                      fontSize: 12,
                      color: "#79879c",
                      lineHeight: "22px",
                    }}
                  >
                    <div>知识库 ID：<Text copyable={{ text: kb?.id ?? "" }} style={{ fontSize: 12 }}>{kb?.id ?? ""}</Text></div>
                    <div>文档数：{kb?.doc_count ?? 0} 篇{kb?.page_count ? ` · Wiki 页面 ${kb.page_count} 个` : ""}</div>
                    {kb?.created_at ? <div>创建时间：{new Date(kb.created_at).toLocaleString()}</div> : null}
                  </div>
                </>
              )}

              {section === "parse" && (
                <>
                  <Spin spinning={chunkLoading}>
                    <Row gutter={16}>
                      <Col span={16}>
                        <Form.Item name="strategy" label="切片策略">
                          <Select showSearch optionFilterProp="label" options={STRATEGY_OPTIONS} />
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
                      <Select mode="tags" showSearch optionFilterProp="label" options={SEPARATOR_PRESETS.map((v) => ({ value: v, label: JSON.stringify(v) }))} />
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
                      <Select mode="multiple" showSearch optionFilterProp="label" options={LANG_OPTIONS} allowClear />
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
                                      showSearch
                                      optionFilterProp="label"
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
                                    <Select placeholder="解析引擎" showSearch optionFilterProp="label" options={engineOptions} style={{ width: "100%" }} />
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

              {section === "parse" && (
                <>
                  <Form.Item
                    name="enhance_enabled"
                    label="切片智能增强"
                    valuePropName="checked"
                    extra="开启后文档解析切分完成时，自动调用 LLM 逐切片提取关键词、生成用户可能提问的问题，并汇总文档级摘要与关键词（随向量同介质存储，检索命中率提升；对标 duowen 智能问答的增强检索）"
                  >
                    <Switch
                      checkedChildren="开"
                      unCheckedChildren="关"
                      onChange={(e) => setEnhanceEnabled(!!e)}
                    />
                  </Form.Item>
                  {enhanceEnabled && (
                    <Row gutter={16}>
                      <Col span={8}>
                        <Form.Item name="enhance_keywords_dividend" label="关键词密度" tooltip="每 N 字提取 1 个关键词（默认 120）">
                          <InputNumber min={30} max={1000} step={10} style={{ width: "100%" }} placeholder="120" />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="enhance_questions_dividend" label="问题密度" tooltip="每 N 字生成 1 个问题（默认 180）">
                          <InputNumber min={60} max={2000} step={20} style={{ width: "100%" }} placeholder="180" />
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item name="enhance_max_chunks" label="最多切片数" tooltip="0 = 不限（大文档增强耗时较长，可按需限制）">
                          <InputNumber min={0} max={5000} step={50} style={{ width: "100%" }} placeholder="0" />
                        </Form.Item>
                      </Col>
                    </Row>
                  )}
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    增强为入库前同步执行：大文档（数百切片）可能增加数分钟解析耗时。
                  </Text>
                </>
              )}

              {section === "image" && (
                <>
                  <Form.Item
                    name="vlm_enabled"
                    label="文档图片理解（VLM OCR + 描述）"
                    valuePropName="checked"
                    extra="开启后，文档解析时对提取的图片调用视觉模型生成 OCR 文本与内容描述，作为可检索的子块入库（对齐 WeKnora 多模态图片处理）"
                  >
                    <Switch checkedChildren="开" unCheckedChildren="关" />
                  </Form.Item>
                  <Form.Item
                    name="vlm_model_id"
                    label="视觉模型（图片理解）"
                    extra="需支持视觉能力的 chat 或 vllm 模型；留空 = 系统默认视觉模型"
                  >
                    <Select
                      allowClear
                      showSearch
                      optionFilterProp="label"
                      placeholder="选择视觉模型"
                      options={visionModelOptions}
                    />
                  </Form.Item>
                  <Form.Item
                    name="vlm_description_language"
                    label="图片描述语言"
                    extra="留空 = 中文（Chinese）"
                  >
                    <Select
                      allowClear
                      showSearch
                      optionFilterProp="label"
                      placeholder="Chinese"
                      options={[
                        { label: "中文（Chinese）", value: "Chinese" },
                        { label: "英语（English）", value: "English" },
                        { label: "日语（Japanese）", value: "Japanese" },
                      ]}
                    />
                  </Form.Item>
                </>
              )}

              {section === "wiki" && (
                <>
                  <Form.Item
                    name="build_engine"
                    label="构建引擎"
                    extra="选择服务编排中已发布的编排程序，文档解析完成后自动按该编排构建 Wiki（未选 = 走内置构建/摘要驱动自动领域分发）"
                  >
                    <Select
                      allowClear
                      showSearch
                      optionFilterProp="label"
                      placeholder={buildEngines.length ? "选择构建引擎" : "暂无已发布的编排，请先到服务编排发布"}
                      options={groupBuildEngineOptions(
                        // 2026-10-21 后端已过滤仅 kb_build 类型；存量 KB 若绑定过
                        // 摘要/其他类型编排，把已绑定值作为保留项附上（避免裸 id 显示）
                        kb?.build_tape_id &&
                          !buildEngines.some((e) => e.id === kb.build_tape_id)
                          ? [
                              ...buildEngines,
                              {
                                id: kb.build_tape_id,
                                tape_name: kb.build_tape_id,
                                tape_label: `${kb.build_tape_id}（已绑定，类型已下线）`,
                                tape_descr: "",
                                tape_type: "general",
                              },
                            ]
                          : buildEngines
                      )}
                    />
                  </Form.Item>
                  <Form.Item
                    name="summary_model_id"
                    label="大语言模型（LLM）"
                    required
                    rules={[{ required: true, message: "大语言模型（LLM）为必配项，请选择" }]}
                    extra="摘要 / 增强 / Wiki 合成均使用此模型；创建后不可清空"
                  >
                    <Select showSearch optionFilterProp="label" placeholder="请选择 LLM 模型" options={modelOptions("chat")} />
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
                    <Select allowClear showSearch optionFilterProp="label" placeholder="留空 = 系统默认" options={modelOptions("embedding")} />
                  </Form.Item>
                </>
              )}

              {section === "aggregation" && (
                <>
                  <Form.Item
                    name="topic_cluster_enabled"
                    label="主题聚合"
                    valuePropName="checked"
                    extra="开启后自动为知识库创建「主题聚类」每日定时任务（每日 02:00 执行），按主题归纳跨文档综述页"
                  >
                    <Switch checkedChildren="开" unCheckedChildren="关" />
                  </Form.Item>
                  <Form.Item
                    name="topic_cluster_topics"
                    label="主题列表"
                    rules={[
                      ({ getFieldValue }) => ({
                        validator: (_, value) => {
                          if (getFieldValue("topic_cluster_enabled") && !(value ?? []).length) {
                            return Promise.reject(new Error("开启主题聚合后请至少填写一个主题"));
                          }
                          return Promise.resolve();
                        },
                      }),
                    ]}
                    extra="按业务关注主题填写，如：采购管理、合同管理；每个主题生成一篇综述页（可新增自定义主题）"
                  >
                    <Select
                      mode="tags"
                      open={false}
                      suffixIcon={null}
                      placeholder="输入主题后回车添加"
                      tokenSeparators={[","]}
                      maxTagCount={20}
                    />
                  </Form.Item>
                  <div style={{ padding: 8, background: "#f5f7fa", borderRadius: 6, color: "#4d5e7d", fontSize: 12, marginBottom: 16 }}>
                    保存后平台自动创建「每日 02:00」定时任务调用主题聚类编排，并立即执行一次聚合。
                  </div>
                  <Form.Item
                    name="version_cluster_enabled"
                    label="按版本聚类"
                    valuePropName="checked"
                    extra="开启后自动为知识库创建「版本聚类」每日定时任务（每日 02:00 执行），按同名多版本文件家族生成版本演进总览页"
                  >
                    <Switch checkedChildren="开" unCheckedChildren="关" />
                  </Form.Item>
                  <div style={{ padding: 8, background: "#f5f7fa", borderRadius: 6, color: "#4d5e7d", fontSize: 12 }}>
                    保存后平台自动创建「每日 02:00」定时任务调用版本演进编排，并立即执行一次聚类。
                  </div>
                </>
              )}
            </div>
          </div>
        </Form>
        </Spin>
      </Modal>

      <ChunkDebugDrawer
        open={debugOpen}
        kbId={kb?.id ?? ""}
        config={pickChunking(form.getFieldsValue())}
        onClose={() => setDebugOpen(false)}
      />
    </>
  );
}
