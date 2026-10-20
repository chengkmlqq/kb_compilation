"use client";

// 智能体分区编辑器（P0 对齐 WeKnora AgentEditorModal 13 分区交互：
// 左侧导航分区 → 右侧配置表单；新建与编辑共用，字段全覆盖）。
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Checkbox,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Row,
  Select,
  Slider,
  Space,
  Switch,
  Typography,
} from "antd";
import {
  ApiOutlined,
  CommentOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  GlobalOutlined,
  PictureOutlined,
  ProfileOutlined,
  SearchOutlined,
  StarOutlined,
  ThunderboltOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import {
  apiAgentToolCatalog,
  apiListKbs,
  apiListMcps,
  apiListModels,
  apiListSkillsRegistry,
  AgentToolCatalogItem,
  KbItem,
  ModelItem,
} from "@/lib/api";
import AgentAvatar from "@/components/AgentAvatar";

const { TextArea } = Input;

// 2026-10-20 提示词占位符（对齐 WeKnora /agents/placeholders 的 5 个通用占位符）
const PROMPT_PLACEHOLDER_TAGS = [
  { token: "{{query}}", label: "用户问题" },
  { token: "{{contexts}}", label: "检索片段" },
  { token: "{{current_time}}", label: "当前时间" },
  { token: "{{current_week}}", label: "当前星期" },
  { token: "{{language}}", label: "语言" },
];

export interface AgentEditorValues {
  name: string;
  description?: string;
  config: Record<string, unknown>;
}

interface Props {
  mode: "create" | "edit";
  open: boolean;
  initial?: { name: string; description?: string; config?: Record<string, unknown> };
  onClose: (changed?: boolean) => void;
  onSubmit: (values: AgentEditorValues) => Promise<{ success: boolean; message?: string }>;
}

// 分区定义（对齐 WeKnora navGroups：basic/prompts/model/conversation/suggestions/
// knowledge/retrieval/websearch/multimodal/tools/mcp/skills）
const SECTIONS = [
  { key: "basic", label: "基本信息", icon: <ProfileOutlined /> },
  { key: "prompts", label: "提示词", icon: <FileTextOutlined /> },
  { key: "model", label: "模型设置", icon: <ThunderboltOutlined /> },
  { key: "conversation", label: "对话设置", icon: <CommentOutlined /> },
  { key: "knowledge", label: "知识库", icon: <DatabaseOutlined /> },
  { key: "retrieval", label: "检索策略", icon: <SearchOutlined /> },
  { key: "websearch", label: "联网搜索", icon: <GlobalOutlined /> },
  { key: "multimodal", label: "多模态", icon: <PictureOutlined /> },
  { key: "tools", label: "工具", icon: <ToolOutlined /> },
  { key: "mcp", label: "MCP", icon: <ApiOutlined /> },
  { key: "skills", label: "技能", icon: <StarOutlined /> },
];

export default function AgentEditorModal({ mode, open, initial, onClose, onSubmit }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [section, setSection] = useState("basic");
  const [models, setModels] = useState<ModelItem[]>([]);
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [mcpOptions, setMcpOptions] = useState<{ value: string; label: string }[]>([]);
  const [skillOptions, setSkillOptions] = useState<{ value: string; label: string }[]>([]);
  const [toolCatalog, setToolCatalog] = useState<AgentToolCatalogItem[]>([]);

  const isEdit = mode === "edit";
  const agentMode = Form.useWatch("agent_mode", form) ?? "quick-answer";
  const agentName = Form.useWatch("name", form) ?? "";
  const kbMode = Form.useWatch("kb_selection_mode", form) ?? "all";
  const mcpMode = Form.useWatch("mcp_selection_mode", form) ?? "none";
  const skillsMode = Form.useWatch("skills_selection_mode", form) ?? "none";
  const webSearch = Form.useWatch("web_search_enabled", form) ?? false;
  const skillsEnabled = Form.useWatch("skills_enabled", form) ?? false;

  // 数据源：模型 / 知识库 / MCP / 技能
  useEffect(() => {
    if (!open) return;
    void (async () => {
      try {
        const [m, k, mc, sk, tc] = await Promise.all([
          apiListModels().catch(() => null),
          apiListKbs(1, 100).catch(() => null),
          apiListMcps().catch(() => null),
          apiListSkillsRegistry().catch(() => null),
          apiAgentToolCatalog().catch(() => null),
        ]);
        if (m?.success && m.data) setModels(m.data.items);
        if (k?.success && k.data) setKbs(k.data.items);
        if (mc?.success && mc.data) {
          const items = mc.data.items ?? (Array.isArray(mc.data) ? mc.data : []);
          setMcpOptions(items.map((i) => ({ value: i.id ?? i.name, label: i.name ?? i.id })));
        }
        if (sk?.success && sk.data) {
          const items = sk.data.items ?? [];
          setSkillOptions(items.map((s) => ({ value: s.name, label: s.name })));
        }
        if (tc?.success && tc.data?.tools) setToolCatalog(tc.data.tools);
      } catch { /* 不阻断 */ }
    })();
  }, [open]);

  // 回填初始值
  useEffect(() => {
    if (!open) return;
    const c = initial?.config ?? {};
    form.setFieldsValue({
      name: initial?.name ?? "",
      description: initial?.description ?? "",
      agent_mode: c.agent_mode ?? "quick-answer",
      system_prompt: c.system_prompt ?? "",
      context_template: c.context_template ?? "",
      model_id: c.model_id ?? "",
      rerank_model_id: c.rerank_model_id ?? "",
      temperature: c.temperature ?? 0.7,
      max_completion_tokens: c.max_completion_tokens ?? 2048,
      thinking: c.thinking ?? false,
      citation_enabled: c.citation_enabled ?? true,
      max_iterations: c.max_iterations ?? 10,
      llm_call_timeout: c.llm_call_timeout ?? 120,
      reflection_enabled: c.reflection_enabled ?? false,
      multi_turn_enabled: c.multi_turn_enabled ?? false,
      history_turns: c.history_turns ?? 5,
      kb_selection_mode: c.kb_selection_mode ?? "all",
      knowledge_bases: c.knowledge_bases ?? [],
      retrieve_kb_only_when_mentioned: c.retrieve_kb_only_when_mentioned ?? false,
      embedding_top_k: c.embedding_top_k ?? 10,
      keyword_threshold: c.keyword_threshold ?? 0.3,
      vector_threshold: c.vector_threshold ?? 0.5,
      rerank_top_k: c.rerank_top_k ?? 5,
      rerank_threshold: c.rerank_threshold ?? 0.5,
      enable_query_expansion: c.enable_query_expansion ?? true,
      enable_rewrite: c.enable_rewrite ?? true,
      web_search_enabled: c.web_search_enabled ?? false,
      web_search_max_results: c.web_search_max_results ?? 5,
      web_search_provider_id: c.web_search_provider_id ?? "",
      image_upload_enabled: c.image_upload_enabled ?? false,
      vlm_model_id: c.vlm_model_id ?? "",
      attachment_image_understanding: c.attachment_image_understanding ?? false,
      attachment_ocr_max_pages: c.attachment_ocr_max_pages ?? 0,
      allowed_tools: c.allowed_tools ?? [],
      mcp_selection_mode: c.mcp_selection_mode ?? "none",
      mcp_services: c.mcp_services ?? [],
      mcp_auth_wait_timeout: c.mcp_auth_wait_timeout ?? 600,
      skills_enabled: c.skills_enabled ?? false,
      skills_selection_mode: c.skills_selection_mode ?? "none",
      selected_skills: c.selected_skills ?? [],
      question_suggestions: c.question_suggestions ?? { starters: { enabled: true, items: [] } },
    });
    setSection("basic");
  }, [open, initial, form]);

  const modelOptions = useMemo(
    () => models.filter((m) => m.type === "chat").map((m) => ({ label: m.name, value: m.id })),
    [models]
  );
  const rerankOptions = useMemo(
    () => models.filter((m) => m.type === "rerank").map((m) => ({ label: m.name, value: m.id })),
    [models]
  );
  const vlmOptions = useMemo(
    () => models.filter((m) => m.type === "vllm").map((m) => ({ label: m.name, value: m.id })),
    [models]
  );
  const kbOptions = useMemo(
    () => kbs.map((k) => ({ label: k.name, value: k.id })),
    [kbs]
  );

  // 2026-10-20 占位符插入：在系统提示词/上下文模板光标处插入 {{xxx}}
  const insertPlaceholder = (field: "system_prompt" | "context_template", token: string) => {
    const el = document.activeElement as HTMLTextAreaElement | null;
    const current = String(form.getFieldValue(field) || "");
    if (el && el.tagName === "TEXTAREA" && el.selectionStart != null) {
      const start = el.selectionStart;
      const end = el.selectionEnd;
      const next = current.slice(0, start) + token + current.slice(end);
      form.setFieldValue(field, next);
      // 光标回到插入点之后，方便连续插入
      requestAnimationFrame(() => {
        el.focus();
        const pos = start + token.length;
        el.setSelectionRange(pos, pos);
      });
    } else {
      form.setFieldValue(field, current + token);
    }
  };

  const onOk = async () => {
    const v = await form.validateFields().catch(() => null);
    if (!v) return;
    // 汇总 config（对齐 WeKnora config JSON 形状，缺省值后端 config_from_dict 兜底）
    const config: Record<string, unknown> = {
      agent_mode: v.agent_mode,
      system_prompt: v.system_prompt,
      use_custom_system_prompt: v.use_custom_system_prompt ?? false,
      context_template: v.context_template,
      model_id: v.model_id,
      rerank_model_id: v.rerank_model_id,
      temperature: v.temperature,
      max_completion_tokens: v.max_completion_tokens,
      thinking: v.thinking,
      citation_enabled: v.citation_enabled,
      max_iterations: v.max_iterations,
      llm_call_timeout: v.llm_call_timeout,
      reflection_enabled: v.reflection_enabled,
      multi_turn_enabled: v.multi_turn_enabled,
      history_turns: v.history_turns,
      kb_selection_mode: v.kb_selection_mode,
      knowledge_bases: v.knowledge_bases ?? [],
      retrieve_kb_only_when_mentioned: v.retrieve_kb_only_when_mentioned,
      embedding_top_k: v.embedding_top_k,
      keyword_threshold: v.keyword_threshold,
      vector_threshold: v.vector_threshold,
      rerank_top_k: v.rerank_top_k,
      rerank_threshold: v.rerank_threshold,
      enable_query_expansion: v.enable_query_expansion,
      enable_rewrite: v.enable_rewrite,
      web_search_enabled: v.web_search_enabled,
      web_search_max_results: v.web_search_max_results,
      web_search_provider_id: v.web_search_provider_id,
      image_upload_enabled: v.image_upload_enabled,
      vlm_model_id: v.vlm_model_id,
      attachment_image_understanding: v.attachment_image_understanding,
      attachment_ocr_max_pages: v.attachment_ocr_max_pages ?? 0,
      allowed_tools: v.allowed_tools ?? [],
      mcp_selection_mode: v.mcp_selection_mode,
      mcp_services: v.mcp_services ?? [],
      mcp_auth_wait_timeout: v.mcp_auth_wait_timeout,
      skills_enabled: v.skills_enabled,
      skills_selection_mode: v.skills_selection_mode,
      selected_skills: v.selected_skills ?? [],
      question_suggestions: v.question_suggestions ?? {},
    };
    const res = await onSubmit({ name: v.name, description: v.description, config });
    if (res.success) {
      message.success(isEdit ? "智能体已更新" : "智能体已创建");
      onClose(true);
    } else {
      message.error(res.message || "保存失败");
    }
  };

  return (
    <Modal
      title={isEdit ? "编辑智能体" : "新建智能体"}
      open={open}
      onOk={() => void onOk()}
      onCancel={() => onClose()}
      okText="保存"
      cancelText="取消"
      width={860}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <div style={{ display: "flex", gap: 16, minHeight: 420 }}>
          {/* 左侧分区导航（对齐 WeKnora navGroups） */}
          <div
            style={{
              width: 150,
              borderRight: "1px solid #f0f0f0",
              paddingRight: 12,
              flexShrink: 0,
            }}
          >
            {SECTIONS.map((s) => (
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
                  label={
                    <Space size={6}>
                      <AgentAvatar name={String(agentName || "").trim() || "?"} size="small" />
                      <span>智能体名称</span>
                    </Space>
                  }
                  rules={[{ required: true, message: "请输入名称" }]}
                  extra="用于列表展示与对话中的称呼，建议简洁易识别（如「制度问答助手」）"
                >
                  <Input placeholder="如：制度问答助手" />
                </Form.Item>
                <Form.Item name="description" label="描述（可选）" extra="一句话说明智能体的用途，展示在列表与详情中">
                  <Input placeholder="一句话说明用途" />
                </Form.Item>
              </>
            )}

            {section === "prompts" && (
              <>
                <Form.Item
                  name="system_prompt"
                  label="系统提示词"
                  extra="留空使用平台默认提示词；填写即自定义。支持占位符：{{query}} 用户问题、{{contexts}} 检索片段、{{current_time}}、{{current_week}}、{{language}}"
                >
                  <TextArea rows={6} placeholder="设定智能体的角色、行为与回答风格…（支持 {{query}} / {{contexts}} 等占位符）" />
                </Form.Item>
                <Form.Item
                  label="可用占位符"
                  extra="点击插入到光标处，用于在提示词中引用用户问题或检索片段"
                >
                  <Space wrap size={[4, 4]}>
                    {PROMPT_PLACEHOLDER_TAGS.map((p) => (
                      <Button
                        key={p.token}
                        size="small"
                        type="dashed"
                        style={{ fontFamily: "monospace", fontSize: 12 }}
                        onClick={() => insertPlaceholder("system_prompt", p.token)}
                      >
                        {p.token}
                        <span style={{ marginLeft: 4, color: "#999", fontSize: 12 }}>
                          {p.label}
                        </span>
                      </Button>
                    ))}
                  </Space>
                </Form.Item>
                <Form.Item
                  name="context_template"
                  label="上下文模板（可选）"
                  extra="组织发送给模型的用户消息结构（问题与检索片段的排布方式）；留空 = 问题原文直接发送。同样支持占位符"
                >
                  <TextArea rows={3} placeholder='如：参考材料：\n{{contexts}}\n\n问题：{{query}}' />
                </Form.Item>
                <Form.Item
                  label="上下文模板可用占位符"
                  extra="点击插入到光标处"
                >
                  <Space wrap size={[4, 4]}>
                    {PROMPT_PLACEHOLDER_TAGS.map((p) => (
                      <Button
                        key={p.token}
                        size="small"
                        type="dashed"
                        style={{ fontFamily: "monospace", fontSize: 12 }}
                        onClick={() => insertPlaceholder("context_template", p.token)}
                      >
                        {p.token}
                        <span style={{ marginLeft: 4, color: "#999", fontSize: 12 }}>
                          {p.label}
                        </span>
                      </Button>
                    ))}
                  </Space>
                </Form.Item>
              </>
            )}

            {section === "model" && (
              <>
                <Form.Item name="agent_mode" label="运行模式" extra="快速问答 = 直接回答；智能推理（Agent）= 多步规划并根据需要调用工具后再回答">
                  <Radio.Group optionType="button" buttonStyle="solid">
                    <Radio.Button value="quick-answer">快速问答</Radio.Button>
                    <Radio.Button value="smart-reasoning">智能推理（Agent）</Radio.Button>
                  </Radio.Group>
                </Form.Item>
                <Form.Item name="model_id" label="问答模型" extra="负责生成回答的大语言模型；留空 = 系统默认">
                  <Select allowClear showSearch optionFilterProp="label" placeholder="留空 = 系统默认" options={modelOptions} />
                </Form.Item>
                <Form.Item name="rerank_model_id" label="Rerank 模型" extra="对检索片段重新排序，提升答案相关性；留空 = 不重排">
                  <Select allowClear showSearch optionFilterProp="label" placeholder="留空 = 不重排" options={rerankOptions} />
                </Form.Item>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="temperature" label="温度" extra="值越高回答越发散、值越低越稳定；默认 0.7">
                      <InputNumber min={0} max={2} step={0.1} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="max_completion_tokens" label="最大输出 Tokens" extra="单次回答的最大生成长度上限">
                      <InputNumber min={128} max={32768} step={128} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="max_iterations" label="最大迭代轮数" extra="智能推理模式下最多执行工具调用的轮数上限">
                      <InputNumber min={1} max={50} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                </Row>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="thinking" label="思考模式" valuePropName="checked" extra="开启后回答前进行深度思考，答案更严谨但响应更慢">
                      <Switch />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="citation_enabled" label="来源引用" valuePropName="checked" extra="回答中标注引用的知识库文档来源">
                      <Switch />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="reflection_enabled" label="反思校验" valuePropName="checked" extra="回答前对推理过程进行自查校验，减少错误">
                      <Switch />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item name="llm_call_timeout" label="单次 LLM 调用超时（秒）" extra="单次模型调用的最大等待时长，超时后按失败处理">
                  <InputNumber min={10} max={3600} style={{ width: 160 }} />
                </Form.Item>
              </>
            )}

            {section === "conversation" && (
              <>
                <Form.Item name="multi_turn_enabled" label="多轮对话" valuePropName="checked" extra="开启后保留历史上下文，支持追问与多轮连续对话">
                  <Switch />
                </Form.Item>
                <Form.Item name="history_turns" label="保留历史轮数" extra="多轮对话时带入上下文的最近对话轮数">
                  <InputNumber min={1} max={30} style={{ width: 160 }} />
                </Form.Item>
              </>
            )}

            {section === "knowledge" && (
              <>
                <Form.Item name="kb_selection_mode" label="知识库范围" extra="全部 = 检索全部知识库；指定 = 仅检索选中的知识库；不绑定 = 不使用知识库检索">
                  <Select
                    showSearch
                    optionFilterProp="label"
                    options={[
                      { value: "all", label: "全部知识库" },
                      { value: "selected", label: "指定知识库" },
                      { value: "none", label: "不绑定" },
                    ]}
                  />
                </Form.Item>
                {kbMode === "selected" && (
                  <Form.Item name="knowledge_bases" label="指定知识库" extra="选择该智能体可检索的知识库（可多选）">
                    <Select mode="multiple" showSearch optionFilterProp="label" options={kbOptions} placeholder="选择知识库" />
                  </Form.Item>
                )}
                <Form.Item
                  name="retrieve_kb_only_when_mentioned"
                  label="仅当问题提到知识库时检索"
                  valuePropName="checked"
                  extra="开启后只有问题明确提到知识库时才会触发检索，减少无关召回"
                >
                  <Switch />
                </Form.Item>
              </>
            )}

            {section === "retrieval" && (
              <>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="embedding_top_k" label="向量 Top-K" extra="语义检索召回的片段数量">
                      <InputNumber min={1} max={50} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="keyword_threshold" label="关键词阈值" extra="关键词命中相似度下限（0-1）">
                      <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="vector_threshold" label="向量阈值" extra="语义相似度下限，低于阈值的片段不参与回答">
                      <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                </Row>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="rerank_top_k" label="Rerank Top-K" extra="重排后保留的片段数量">
                      <InputNumber min={1} max={20} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="rerank_threshold" label="Rerank 阈值" extra="重排分数下限，低于该分的片段不参与回答">
                      <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="enable_query_expansion" label="查询扩展" valuePropName="checked" extra="自动扩展同义词与相关词，提升召回率">
                      <Switch />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item name="enable_rewrite" label="问题改写" valuePropName="checked" extra="多轮对话中自动改写问题，补齐指代（如「它」「上面说的」）">
                  <Switch />
                </Form.Item>
              </>
            )}

            {section === "websearch" && (
              <>
                <Form.Item name="web_search_enabled" label="启用联网搜索" valuePropName="checked" extra="开启后智能体可调用联网搜索获取实时信息">
                  <Switch />
                </Form.Item>
                {webSearch && (
                  <>
                    <Form.Item name="web_search_max_results" label="最多返回结果数" extra="单次搜索返回的结果条数上限">
                      <InputNumber min={1} max={20} style={{ width: 160 }} />
                    </Form.Item>
                    <Form.Item name="web_search_provider_id" label="搜索服务商" extra="联网搜索服务提供商；P1 上线后可选">
                      <Select allowClear showSearch optionFilterProp="label" placeholder="选择 WebSearch Provider（P1 上线后可选）" />
                    </Form.Item>
                  </>
                )}
              </>
            )}

            {section === "multimodal" && (
              <>
                <Form.Item name="image_upload_enabled" label="图片上传" valuePropName="checked" extra="开启后会话中允许上传图片给智能体查看">
                  <Switch />
                </Form.Item>
                <Form.Item name="vlm_model_id" label="VLM 视觉模型（图片理解）" extra="负责理解图片内容的视觉模型；留空 = 系统默认">
                  <Select allowClear showSearch optionFilterProp="label" placeholder="留空 = 系统默认" options={vlmOptions} />
                </Form.Item>
                <Form.Item name="attachment_image_understanding" label="附件图片理解" valuePropName="checked" extra="对附件中的图片自动生成描述并参与回答">
                  <Switch />
                </Form.Item>
                <Form.Item name="attachment_ocr_max_pages" label="附件 OCR 最大页数" extra="对附件图片执行 OCR 识别的最大页数；0 = 不限制">
                  <InputNumber min={0} max={100} style={{ width: 160 }} />
                </Form.Item>
              </>
            )}

            {section === "tools" && (
              <>
                <Form.Item
                  name="allowed_tools"
                  label="可用工具"
                  extra={
                    <>
                      留空 = 启用默认工具集（知识库检索、图谱、Wiki 查看、数据分析等只读工具，以及按开关生效的联网搜索与技能执行）；
                      勾选 = 仅启用勾选项。Wiki 写入/修改/删除、问题处理等工具默认不启用，需勾选后生效。
                      <br />
                      联网搜索与技能执行除勾选外，还需在「联网搜索」「技能」分区开启对应配置后才会真正生效。
                    </>
                  }
                >
                  <Checkbox.Group style={{ width: "100%" }}>
                    {(() => {
                      const groups: { group: string; items: AgentToolCatalogItem[] }[] = [];
                      const byGroup = new Map<string, AgentToolCatalogItem[]>();
                      for (const t of toolCatalog) {
                        if (!byGroup.has(t.group)) byGroup.set(t.group, []);
                        byGroup.get(t.group)!.push(t);
                      }
                      for (const [g, items] of byGroup) groups.push({ group: g, items });
                      return groups.map((g) => (
                        <div key={g.group} style={{ marginBottom: 12 }}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {g.group}
                          </Typography.Text>
                          <div style={{ marginTop: 2 }}>
                            {g.items.map((t) => (
                              <div
                                key={t.name}
                                style={{ display: "flex", alignItems: "flex-start", marginBottom: 4 }}
                              >
                                <Checkbox value={t.name} style={{ marginRight: 8, flexShrink: 0 }}>
                                  <span style={{ fontWeight: 500 }}>{t.name}</span>
                                </Checkbox>
                                <Typography.Text type="secondary" style={{ fontSize: 12, lineHeight: "22px" }}>
                                  {t.description}
                                </Typography.Text>
                              </div>
                            ))}
                          </div>
                        </div>
                      ));
                    })()}
                  </Checkbox.Group>
                </Form.Item>
              </>
            )}

            {section === "mcp" && (
              <>
                <Form.Item name="mcp_selection_mode" label="MCP 工具范围" extra="不启用 = 不使用 MCP 工具；指定服务 = 仅使用选中的服务；全部可用 = 使用所有已连接的服务">
                  <Radio.Group optionType="button" buttonStyle="solid">
                    <Radio.Button value="none">不启用</Radio.Button>
                    <Radio.Button value="selected">指定服务</Radio.Button>
                    <Radio.Button value="all">全部可用</Radio.Button>
                  </Radio.Group>
                </Form.Item>
                {mcpMode === "selected" && (
                  <Form.Item name="mcp_services" label="选择 MCP 服务" extra="勾选该智能体可调用的 MCP 服务（可多选）">
                    <Select mode="multiple" showSearch optionFilterProp="label" options={mcpOptions} placeholder="选择 MCP 服务" />
                  </Form.Item>
                )}
                <Form.Item name="mcp_auth_wait_timeout" label="MCP 授权等待超时（秒）" extra="等待 MCP 服务完成授权的最大时长，超时视为不可用">
                  <InputNumber min={0} max={3600} style={{ width: 160 }} />
                </Form.Item>
              </>
            )}

            {section === "skills" && (
              <>
                <Form.Item name="skills_enabled" label="启用技能" valuePropName="checked" extra="开启后该智能体可调用平台技能扩展能力">
                  <Switch />
                </Form.Item>
                {skillsEnabled && (
                  <>
                    <Form.Item name="skills_selection_mode" label="技能范围" extra="全部已安装 = 使用所有已安装技能；指定技能 = 仅使用选中的技能">
                      <Radio.Group optionType="button" buttonStyle="solid">
                        <Radio.Button value="none">不启用</Radio.Button>
                        <Radio.Button value="selected">指定技能</Radio.Button>
                        <Radio.Button value="all">全部已安装</Radio.Button>
                      </Radio.Group>
                    </Form.Item>
                    {skillsMode === "selected" && (
                      <Form.Item name="selected_skills" label="选择技能（白名单）" extra="仅选中的技能对该智能体可用（可多选）">
                        <Select mode="multiple" showSearch optionFilterProp="label" options={skillOptions} placeholder="选择技能" />
                      </Form.Item>
                    )}
                  </>
                )}
              </>
            )}
          </div>
        </div>
      </Form>
    </Modal>
  );
}
