"use client";

// 提示词模板选择器（2026-10-21 对齐 WeKnora PromptTemplateSelector）：
// 贴在提示词文本框右下角（position: corner），提供「恢复默认」+「使用模板」
// 两个操作。模板库来自 GET /api/v1/agents/prompt-templates（api/assets/prompts/
// *.yaml 加载），点击模板后通过 onSelect 把模板正文填回表单字段。
import { useEffect, useMemo, useState } from "react";
import { Button, Popover, Tag, Typography } from "antd";
import { ReloadOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { apiAgentPromptTemplates, AgentPromptTemplate } from "@/lib/api";

const { Text } = Typography;

interface Props {
  type: "system_prompt" | "context_template";
  /** 点选模板后回调（模板正文由调用方填入表单字段） */
  onSelect: (template: AgentPromptTemplate) => void;
  /** 「恢复默认」回调（system_prompt 恢复平台默认、context_template 清空） */
  onReset: () => void;
}

export default function PromptTemplateSelector({ type, onSelect, onReset }: Props) {
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState<AgentPromptTemplate[]>([]);

  useEffect(() => {
    if (!open || templates.length > 0) return;
    void apiAgentPromptTemplates()
      .then((res) => {
        if (res.success && res.data) {
          setTemplates(res.data[type] ?? []);
        }
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, type]);

  const content = useMemo(() => {
    if (templates.length === 0) {
      return <div style={{ padding: "32px 16px", textAlign: "center", color: "#999", fontSize: 13 }}>暂无可用模板</div>;
    }
    return (
      <div style={{ width: 380, maxHeight: 360, overflowY: "auto" }}>
        {templates.map((t) => (
          <div
            key={t.id}
            onClick={() => {
              onSelect(t);
              setOpen(false);
            }}
            style={{
              padding: "10px 12px",
              borderRadius: 8,
              cursor: "pointer",
              marginBottom: 4,
              transition: "background 0.2s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "#f5f7fa";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
              <span style={{ fontSize: 14, fontWeight: 500, color: "#242E43" }}>{t.name}</span>
              {t.default && <Tag color="warning" style={{ marginInlineEnd: 0, fontSize: 11 }}>默认</Tag>}
              {t.has_knowledge_base && <Tag color="blue" style={{ marginInlineEnd: 0, fontSize: 11 }}>知识库</Tag>}
              {t.has_web_search && <Tag color="green" style={{ marginInlineEnd: 0, fontSize: 11 }}>联网搜索</Tag>}
            </div>
            <Text type="secondary" style={{ fontSize: 12, lineHeight: 1.5 }}>
              {t.description}
            </Text>
          </div>
        ))}
      </div>
    );
  }, [templates, onSelect]);

  return (
    <div style={{ position: "absolute", right: 8, bottom: 8, zIndex: 10, display: "inline-flex", gap: 4 }}>
      <Button
        size="small"
        type="text"
        icon={<ReloadOutlined />}
        style={{ fontSize: 12, color: "#999", height: 26, padding: "0 6px" }}
        onClick={onReset}
      >
        恢复默认
      </Button>
      <Popover
        open={open}
        onOpenChange={setOpen}
        trigger="click"
        placement="topRight"
        content={content}
        styles={{ root: { zIndex: 1080 } }}
      >
        <Button
          size="small"
          icon={<UnorderedListOutlined />}
          style={{ fontSize: 12, color: "#4D5E7D", height: 26, padding: "0 8px" }}
        >
          使用模板
        </Button>
      </Popover>
    </div>
  );
}
