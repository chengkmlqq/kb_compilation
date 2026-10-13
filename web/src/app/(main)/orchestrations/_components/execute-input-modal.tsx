"use client";

/**
 * 编排执行入参填写弹窗 —— 列表页与设计器共用。
 *
 * 不让用户手写 JSON：改为「选知识库 → 选文件」，确认时自动转换为
 * { kb_id, document_id } 对象提交（TapeRun.inputs，注入执行引擎初始 bindings，
 * 供 {{ 变量 }} 模板引用）。右下角只读预览展示将生成的参数。
 */
import { useEffect, useRef, useState } from "react";
import { App, Modal, Select, Typography } from "antd";
import { apiListDocuments, apiListKbs, DocItem, KbItem } from "@/lib/api";

const { Text } = Typography;

interface ExecuteInputModalProps {
  open: boolean;
  tapeName: string;
  /** 提交中（执行请求进行中） */
  loading?: boolean;
  onCancel: () => void;
  onOk: (inputs: Record<string, unknown>) => void;
}

interface LabelValue {
  value: string;
  label: string;
}

export function ExecuteInputModal({ open, tapeName, loading, onCancel, onOk }: ExecuteInputModalProps) {
  const { message } = App.useApp();
  const [kbOptions, setKbOptions] = useState<LabelValue[]>([]);
  const [kbLoading, setKbLoading] = useState(false);
  const [kbId, setKbId] = useState<string | undefined>();
  const [docOptions, setDocOptions] = useState<LabelValue[]>([]);
  const [docLoading, setDocLoading] = useState(false);
  const [docId, setDocId] = useState<string | undefined>();
  // 文件下拉搜索词（打开后聚焦输入即触发远程检索）
  const docSearchRef = useRef("");

  const loadKbs = async () => {
    setKbLoading(true);
    try {
      const res = await apiListKbs(1, 1000, "");
      if (res.success) {
        setKbOptions((res.data?.items || []).map((k: KbItem) => ({ value: k.id, label: k.name })));
      } else {
        message.error(res.message || "加载知识库失败");
      }
    } finally {
      setKbLoading(false);
    }
  };

  const loadDocs = async (kb: string, keyword = "") => {
    setDocLoading(true);
    try {
      const res = await apiListDocuments(kb, 1, 200, { keyword });
      if (res.success) {
        setDocOptions((res.data?.items || []).map((d: DocItem) => ({ value: d.id, label: d.file_name })));
      } else {
        message.error(res.message || "加载文件列表失败");
      }
    } finally {
      setDocLoading(false);
    }
  };

  // 每次打开重置选择，并刷新知识库列表
  useEffect(() => {
    if (open) {
      setKbId(undefined);
      setDocId(undefined);
      setDocOptions([]);
      docSearchRef.current = "";
      void loadKbs();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleKbChange = (v: string) => {
    setKbId(v);
    setDocId(undefined);
    setDocOptions([]);
    docSearchRef.current = "";
    void loadDocs(v);
  };

  const handleDocSearch = (kw: string) => {
    docSearchRef.current = kw;
    if (kbId) void loadDocs(kbId, kw);
  };

  const derivedInputs: Record<string, unknown> = { kb_id: kbId ?? "" };
  if (docId) derivedInputs.document_id = docId;

  const handleOk = () => {
    if (!kbId) {
      message.warning("请先选择知识库");
      return;
    }
    const inputs: Record<string, unknown> = { kb_id: kbId };
    if (docId) inputs.document_id = docId;
    onOk(inputs);
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
      <div>
        <Text type="secondary" style={{ fontSize: 12 }}>
          选择本次执行的知识库与文件，确认后自动转换为参数（键名可在编排中以 {"{{ 键名 }}"} 模板引用）。
        </Text>
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 8 }}>
          <div>
            <div style={{ marginBottom: 4, fontSize: 13, color: "#4e5969" }}>知识库 *</div>
            <Select
              showSearch
              placeholder="选择知识库"
              value={kbId}
              loading={kbLoading}
              onChange={handleKbChange}
              options={kbOptions}
              filterOption={(input, option) => String(option?.label || "").toLowerCase().includes(input.toLowerCase())}
              style={{ width: "100%" }}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4, fontSize: 13, color: "#4e5969" }}>文件</div>
            <Select
              showSearch
              allowClear
              placeholder={kbId ? "选择文件（可输入名称搜索）" : "请先选择知识库"}
              value={docId}
              loading={docLoading}
              disabled={!kbId}
              onChange={(v) => setDocId(v ?? undefined)}
              onSearch={handleDocSearch}
              options={docOptions}
              filterOption={false}
              notFoundContent={docSearchRef.current ? "未找到匹配文件" : "暂无文件"}
              style={{ width: "100%" }}
            />
          </div>
          <div>
            <div style={{ marginBottom: 4, fontSize: 13, color: "#4e5969" }}>生成的参数（只读预览）</div>
            <pre
              style={{
                margin: 0,
                fontSize: 12,
                fontFamily: "Menlo, Consolas, monospace",
                background: "#fafafa",
                border: "1px solid #f0f0f0",
                borderRadius: 6,
                padding: 8,
                minHeight: 40,
                color: Object.keys(derivedInputs).length > 0 && derivedInputs.kb_id ? "#242e43" : "#bbb",
              }}
            >
              {JSON.stringify(derivedInputs, null, 2)}
            </pre>
          </div>
        </div>
      </div>
    </Modal>
  );
}

export default ExecuteInputModal;