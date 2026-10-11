"use client";

/**
 * 编排执行入参填写弹窗 —— 列表页与设计器共用。
 *
 * 以 JSON 对象填写本次执行的初始调用参数；提交的键值即 TapeRun.inputs，
 * 会注入执行引擎的初始 bindings（供 {{ 变量 }} 模板引用）。
 * 只做前端校验（必须是 JSON 对象），不改后端契约。
 */
import { useEffect, useState } from "react";
import { Input, Modal, Typography } from "antd";

const { Text } = Typography;

interface ExecuteInputModalProps {
  open: boolean;
  tapeName: string;
  /** 提交中（执行请求进行中） */
  loading?: boolean;
  onCancel: () => void;
  onOk: (inputs: Record<string, unknown>) => void;
}

export function ExecuteInputModal({ open, tapeName, loading, onCancel, onOk }: ExecuteInputModalProps) {
  const [text, setText] = useState("{}");
  const [error, setError] = useState("");

  // 每次打开重置为空对象，避免沿用上一次的入参
  useEffect(() => {
    if (open) {
      setText("{}");
      setError("");
    }
  }, [open]);

  const handleOk = () => {
    const raw = text.trim();
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw === "" ? "{}" : raw);
    } catch (e) {
      setError(`JSON 格式错误：${String(e).slice(0, 100)}`);
      return;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      setError('入参必须是 JSON 对象，例如 {"topic": "AI"}');
      return;
    }
    setError("");
    onOk(parsed as Record<string, unknown>);
  };

  return (
    <Modal
      title={`填写执行入参 · ${tapeName}`}
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      confirmLoading={loading}
      okText="执行"
      width={560}
    >
      <Text type="secondary" style={{ fontSize: 12 }}>
        以 JSON 对象填写本次执行的初始参数，键名可在编排中以 {"{{ 键名 }}"} 模板引用；留空视为 {"{}"}。
      </Text>
      <Input.TextArea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (error) setError("");
        }}
        autoSize={{ minRows: 6, maxRows: 14 }}
        spellCheck={false}
        placeholder='{"topic": "AI", "count": 3}'
        style={{ marginTop: 8, fontFamily: "Menlo, Consolas, monospace", fontSize: 12 }}
      />
      {error && <div style={{ marginTop: 6, color: "#cf1322", fontSize: 12 }}>{error}</div>}
    </Modal>
  );
}

export default ExecuteInputModal;
