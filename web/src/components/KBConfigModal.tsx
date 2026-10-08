"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Checkbox,
  Divider,
  Drawer,
  Form,
  InputNumber,
  Radio,
  Select,
  Space,
  Switch,
  Typography,
} from "antd";
import {
  apiListModels,
  apiListSkillsRegistry,
  apiUpdateKb,
  KbItem,
  KbUpdatePayload,
  ModelItem,
} from "@/lib/api";
import { App } from "antd";

const PIPELINE_OPTS = [
  { label: "向量检索", value: "vector" },
  { label: "关键词检索", value: "keyword" },
  { label: "Wiki 构建", value: "wiki" },
  { label: "知识图谱", value: "graph" },
];

interface Props {
  kb: KbItem | null;
  open: boolean;
  onClose: (changed?: boolean) => void;
}

/** 知识库配置弹窗（WeKnora 对齐）：索引开关/类型/技能绑定/模型绑定/图谱/FAQ。 */
export default function KBConfigModal({ kb, open, onClose }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [skills, setSkills] = useState<{ name: string }[]>([]);
  const [models, setModels] = useState<ModelItem[]>([]);
  const customWiki = Form.useWatch("custom_wiki_generation", form) ?? false;
  const kbType = Form.useWatch("type", form) ?? "document";

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
    const res = await apiUpdateKb(kb.id, payload);
    if (res.success) {
      message.success("知识库配置已保存");
      onClose(true);
    } else {
      message.error(res.message || "保存失败");
    }
  };

  const modelOptions = (type: string) =>
    models.filter((m) => m.type === type).map((m) => ({
      label: m.name,
      value: m.id,
    }));

  return (
    <Drawer
      title={`知识库配置 · ${kb?.name ?? ""}`}
      open={open}
      onClose={() => onClose()}
      width={620}
      extra={
        <Space>
          <Button onClick={() => onClose()}>取消</Button>
          <Button type="primary" onClick={() => void onSave()}>
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
      </Form>
    </Drawer>
  );
}
