"use client";

// 智能体分区编辑器（P0 对齐 WeKnora AgentEditorModal 13 分区交互：
// 左侧导航分区 → 右侧配置表单；新建与编辑共用，字段全覆盖）。
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
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
  Switch,
  Tabs,
  Typography,
} from "antd";
import { apiListKbs, apiListMcps, apiListModels, apiListSkillsRegistry, KbItem, ModelItem } from "@/lib/api";

const { TextArea } = Input;

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
  { key: "prompts", label: "提示词", icon: "📝" },
  { key: "model", label: "模型设置", icon: "🧠" },
  { key: "conversation", label: "对话设置", icon: "💬" },
  { key: "knowledge", label: "知识库", icon: "📚" },
  { key: "retrieval", label: "检索策略", icon: "🔍" },
  { key: "websearch", label: "联网搜索", icon: "🌐" },
  { key: "multimodal", label: "多模态", icon: "🖼️" },
  { key: "tools", label: "工具", icon: "🛠️" },
  { key: "mcp", label: "MCP", icon: "🔌" },
  { key: "skills", label: "技能", icon: "✨" },
];

export default function AgentEditorModal({ mode, open, initial, onClose, onSubmit }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [section, setSection] = useState("prompts");
  const [models, setModels] = useState<ModelItem[]>([]);
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [mcpOptions, setMcpOptions] = useState<{ value: string; label: string }[]>([]);
  const [skillOptions, setSkillOptions] = useState<{ value: string; label: string }[]>([]);

  const isEdit = mode === "edit";
  const agentMode = Form.useWatch("agent_mode", form) ?? "quick-answer";
  const kbMode = Form.useWatch("kb_selection_mode", form) ?? "all";
  const mcpMode = Form.useWatch("mcp_selection_mode", form) ?? "none";
  const skillsMode = Form.useWatch("skills_selection_mode", form) ?? "none";
  const webSearch = Form.useWatch("web_search_enabled", form) ?? false;

  // 数据源：模型 / 知识库 / MCP / 技能
  useEffect(() => {
    if (!open) return;
    void (async () => {
      try {
        const [m, k, mc, sk] = await Promise.all([
          apiListModels().catch(() => null),
          apiListKbs(1, 100).catch(() => null),
          apiListMcps().catch(() => null),
          apiListSkillsRegistry().catch(() => null),
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
      use_custom_system_prompt: c.use_custom_system_prompt ?? false,
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
      faq_priority_enabled: c.faq_priority_enabled ?? true,
      question_suggestions: c.question_suggestions ?? { starters: { enabled: true, items: [] } },
    });
    setSection("prompts");
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

  const onOk = async () => {
    const v = await form.validateFields().catch(() => null);
    if (!v) return;
    // 汇总 config（对齐 WeKnora config JSON 形状，缺省值后端 config_from_dict 兜底）
    const config: Record<string, unknown> = {
      agent_mode: v.agent_mode,
      system_prompt: v.system_prompt,
      use_custom_system_prompt: v.use_custom_system_prompt,
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
      faq_priority_enabled: v.faq_priority_enabled,
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
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="name" label="智能体名称" rules={[{ required: true, message: "请输入名称" }]}>
              <Input placeholder="如：制度问答助手" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="description" label="描述（可选）">
              <Input placeholder="一句话说明用途" />
            </Form.Item>
          </Col>
        </Row>

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
                  padding: "8px 12px",
                  borderRadius: 6,
                  cursor: "pointer",
                  marginBottom: 4,
                  background: section === s.key ? "rgba(22,119,255,0.1)" : undefined,
                  color: section === s.key ? "#1677ff" : undefined,
                  fontWeight: section === s.key ? 600 : 400,
                }}
              >
                {s.icon} {s.label}
              </div>
            ))}
          </div>

          {/* 右侧分区内容 */}
          <div style={{ flex: 1, overflowY: "auto", maxHeight: 560 }}>
            {section === "prompts" && (
              <>
                <Form.Item name="use_custom_system_prompt" label="启用自定义系统提示词" valuePropName="checked">
                  <Switch />
                </Form.Item>
                <Form.Item name="system_prompt" label="系统提示词">
                  <TextArea rows={6} placeholder="设定智能体的角色、行为与回答风格…" />
                </Form.Item>
                <Form.Item name="context_template" label="上下文模板（可选）">
                  <TextArea rows={3} />
                </Form.Item>
              </>
            )}

            {section === "model" && (
              <>
                <Form.Item name="agent_mode" label="运行模式">
                  <Radio.Group optionType="button" buttonStyle="solid">
                    <Radio.Button value="quick-answer">快速问答</Radio.Button>
                    <Radio.Button value="smart-reasoning">智能推理（Agent）</Radio.Button>
                  </Radio.Group>
                </Form.Item>
                <Form.Item name="model_id" label="问答模型">
                  <Select allowClear placeholder="留空 = 系统默认" options={modelOptions} />
                </Form.Item>
                <Form.Item name="rerank_model_id" label="Rerank 模型">
                  <Select allowClear placeholder="留空 = 不重排" options={rerankOptions} />
                </Form.Item>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="temperature" label="温度">
                      <InputNumber min={0} max={2} step={0.1} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="max_completion_tokens" label="最大输出 Tokens">
                      <InputNumber min={128} max={32768} step={128} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="max_iterations" label="最大迭代轮数">
                      <InputNumber min={1} max={50} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                </Row>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="thinking" label="思考模式" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="citation_enabled" label="来源引用" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="reflection_enabled" label="反思校验" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item name="llm_call_timeout" label="单次 LLM 调用超时（秒）">
                  <InputNumber min={10} max={3600} style={{ width: 160 }} />
                </Form.Item>
              </>
            )}

            {section === "conversation" && (
              <>
                <Form.Item name="multi_turn_enabled" label="多轮对话" valuePropName="checked" extra="开启后保留历史上下文">
                  <Switch />
                </Form.Item>
                <Form.Item name="history_turns" label="保留历史轮数">
                  <InputNumber min={1} max={30} style={{ width: 160 }} />
                </Form.Item>
              </>
            )}

            {section === "knowledge" && (
              <>
                <Form.Item name="kb_selection_mode" label="知识库范围">
                  <Select
                    options={[
                      { value: "all", label: "全部知识库" },
                      { value: "selected", label: "指定知识库" },
                      { value: "none", label: "不绑定" },
                    ]}
                  />
                </Form.Item>
                {kbMode === "selected" && (
                  <Form.Item name="knowledge_bases" label="指定知识库">
                    <Select mode="multiple" options={kbOptions} placeholder="选择知识库" />
                  </Form.Item>
                )}
                <Form.Item
                  name="retrieve_kb_only_when_mentioned"
                  label="仅当问题提到知识库时检索"
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
              </>
            )}

            {section === "retrieval" && (
              <>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="embedding_top_k" label="向量 Top-K">
                      <InputNumber min={1} max={50} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="keyword_threshold" label="关键词阈值">
                      <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="vector_threshold" label="向量阈值">
                      <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                </Row>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="rerank_top_k" label="Rerank Top-K">
                      <InputNumber min={1} max={20} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="rerank_threshold" label="Rerank 阈值">
                      <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="enable_query_expansion" label="查询扩展" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item name="enable_rewrite" label="问题改写" valuePropName="checked">
                  <Switch />
                </Form.Item>
              </>
            )}

            {section === "websearch" && (
              <>
                <Form.Item name="web_search_enabled" label="启用联网搜索" valuePropName="checked">
                  <Switch />
                </Form.Item>
                {webSearch && (
                  <>
                    <Form.Item name="web_search_max_results" label="最多返回结果数">
                      <InputNumber min={1} max={20} style={{ width: 160 }} />
                    </Form.Item>
                    <Form.Item name="web_search_provider_id" label="搜索服务商">
                      <Select allowClear placeholder="选择 WebSearch Provider（P1 上线后可选）" />
                    </Form.Item>
                  </>
                )}
              </>
            )}

            {section === "multimodal" && (
              <>
                <Form.Item name="image_upload_enabled" label="图片上传" valuePropName="checked">
                  <Switch />
                </Form.Item>
                <Form.Item name="vlm_model_id" label="VLM 视觉模型（图片理解）">
                  <Select allowClear placeholder="留空 = 系统默认" options={vlmOptions} />
                </Form.Item>
                <Form.Item name="attachment_image_understanding" label="附件图片理解" valuePropName="checked">
                  <Switch />
                </Form.Item>
                <Form.Item name="attachment_ocr_max_pages" label="附件 OCR 最大页数">
                  <InputNumber min={0} max={100} style={{ width: 160 }} />
                </Form.Item>
              </>
            )}

            {section === "tools" && (
              <>
                <Form.Item
                  name="allowed_tools"
                  label="可用工具白名单"
                  extra="留空 = 全部允许；勾选后仅启用勾选项"
                >
                  <Select
                    mode="multiple"
                    allowClear
                    placeholder="选择工具（如 run_skill_script / web_search…）"
                    options={[
                      { value: "web_search", label: "web_search 联网搜索" },
                      { value: "run_skill_script", label: "run_skill_script 技能执行" },
                      { value: "kb_search", label: "kb_search 知识库检索" },
                    ]}
                  />
                </Form.Item>
              </>
            )}

            {section === "mcp" && (
              <>
                <Form.Item name="mcp_selection_mode" label="MCP 工具范围">
                  <Radio.Group optionType="button" buttonStyle="solid">
                    <Radio.Button value="none">不启用</Radio.Button>
                    <Radio.Button value="selected">指定服务</Radio.Button>
                    <Radio.Button value="all">全部可用</Radio.Button>
                  </Radio.Group>
                </Form.Item>
                {mcpMode === "selected" && (
                  <Form.Item name="mcp_services" label="选择 MCP 服务">
                    <Select mode="multiple" options={mcpOptions} placeholder="选择 MCP 服务" />
                  </Form.Item>
                )}
                <Form.Item name="mcp_auth_wait_timeout" label="MCP 授权等待超时（秒）">
                  <InputNumber min={0} max={3600} style={{ width: 160 }} />
                </Form.Item>
              </>
            )}

            {section === "skills" && (
              <>
                <Form.Item name="skills_enabled" label="启用技能" valuePropName="checked">
                  <Switch />
                </Form.Item>
                {Form.useWatch("skills_enabled", form) && (
                  <>
                    <Form.Item name="skills_selection_mode" label="技能范围">
                      <Radio.Group optionType="button" buttonStyle="solid">
                        <Radio.Button value="none">不启用</Radio.Button>
                        <Radio.Button value="selected">指定技能</Radio.Button>
                        <Radio.Button value="all">全部已安装</Radio.Button>
                      </Radio.Group>
                    </Form.Item>
                    {skillsMode === "selected" && (
                      <Form.Item name="selected_skills" label="选择技能（白名单）">
                        <Select mode="multiple" options={skillOptions} placeholder="选择技能" />
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
