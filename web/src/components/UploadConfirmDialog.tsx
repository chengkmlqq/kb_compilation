"use client";

/**
 * 上传确认对话框（对齐 WeKnora UploadConfirmDialog.vue，2026-10-10）。
 *
 * 此前 kb 上传是「选文件直接传」，处理配置只能在知识库创建时（KBConfigModal）
 * 定死；WeKnora 是「选文件 → 弹确认框 → 可选处理配置（切片/解析引擎）→ 确认上传」，
 * 配置随 process_config 走文件级覆盖。
 *
 * 本组件结构对齐 WeKnora：
 *  - 左侧 files-panel：文件/URL 列表（可继续添加、删除、显示大小）
 *  - 右侧 main-panel：overview 概览（切片配置摘要 + 解析引擎摘要，点行进编辑）
 *    切到 section 后编辑 chunking / parser 配置（复用 KBConfigModal 字段语义）
 *  - footer：取消 / 确认上传
 *
 * 确认后返回 UploadConfirmResult { files, processConfig }，由调用方逐文件上传。
 */
import React, { useEffect, useMemo, useState } from "react";
import { App, Button, Col, Form, InputNumber, Modal, Row, Select, Space, Switch, Typography } from "antd";
import { DeleteOutlined, FileTextOutlined, LinkOutlined, PlusOutlined } from "@ant-design/icons";
import { apiListParserEngines, ParserEngineItem } from "@/lib/api";

const { Text } = Typography;

export interface UploadProcessConfig {
  chunking?: Record<string, unknown>;
  parser_engine_rules?: Array<{ file_types: string[]; engine: string }>;
}

export interface UploadConfirmResult {
  files: File[];
  processConfig: UploadProcessConfig | null;
}

interface Props {
  open: boolean;
  initialFiles: File[];
  urls?: string[];
  onCancel: () => void;
  onConfirm: (result: UploadConfirmResult) => void;
}

const SEPARATOR_PRESETS = ["\n## ", "\n\n", "\n", "\n---", "\n### ", "。", "；"];
const LANG_OPTIONS = [
  { value: "chinese", label: "中文" },
  { value: "english", label: "English" },
  { value: "japanese", label: "日本語" },
  { value: "korean", label: "한국어" },
  { value: "russian", label: "Русский" },
  { value: "french", label: "Français" },
  { value: "german", label: "Deutsch" },
  { value: "spanish", label: "Español" },
];
const FILE_TYPE_OPTIONS = [
  "pdf", "docx", "doc", "pptx", "xlsx", "xls", "csv", "md", "markdown", "txt",
  "epub", "html", "htm", "json", "xml", "yaml", "yml", "py", "js", "ts", "java",
].map((v) => ({ value: v, label: v }));

const DEFAULT_CHUNKING = {
  chunk_size: 800,
  chunk_overlap: 80,
  separators: ["\n## ", "\n\n", "\n"],
  enable_parent_child: false,
  parent_chunk_size: 4096,
  child_chunk_size: 384,
  strategy: "auto",
  token_limit: 0,
  languages: [],
};

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function getFileIcon(name: string): React.ReactNode {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return <FileTextOutlined style={{ color: ext === "pdf" ? "#f5222d" : "#1677ff" }} />;
}

export default function UploadConfirmDialog({ open, initialFiles, urls = [], onCancel, onConfirm }: Props) {
  const { message } = App.useApp();
  const [files, setFiles] = useState<File[]>([]);
  const [activeSection, setActiveSection] = useState<string>("overview");
  const [chunking, setChunking] = useState<Record<string, unknown>>({ ...DEFAULT_CHUNKING });
  const [rules, setRules] = useState<Array<{ file_types: string[]; engine: string }>>([]);
  const [engines, setEngines] = useState<ParserEngineItem[]>([]);
  const [showParser, setShowParser] = useState(false);

  // 打开时同步外部文件并拉引擎列表
  useEffect(() => {
    if (!open) return;
    setFiles([...initialFiles]);
    setActiveSection("overview");
    void apiListParserEngines()
      .then((res) => {
        if (res.success && Array.isArray(res.data)) setEngines(res.data);
        else setEngines([]);
      })
      .catch(() => setEngines([]));
  }, [open, initialFiles]);

  // 本批次扩展名（解析引擎规则默认只给相关扩展，对齐 WeKnora batchFileExts）
  const batchExts = useMemo(() => {
    const set = new Set<string>();
    for (const f of files) {
      const ext = (f.name.split(".").pop() || "").toLowerCase();
      if (ext) set.add(ext);
    }
    return [...set];
  }, [files]);

  const addFiles = (list: File[]) => {
    setFiles((prev) => [...prev, ...list]);
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const engineOptions = engines.map((e) => ({
    value: e.name,
    label: e.display_name || e.name,
    disabled: !e.available,
    title: e.available ? e.description || `解析引擎 ${e.display_name || e.name}` : e.reason || "引擎不可用",
  }));

  // 解析引擎摘要（对齐 WeKnora parserOverviewValue：ext → engine）
  const parserSummary = useMemo(() => {
    if (!rules.length || batchExts.length === 0) {
      return "内置解析器（docreader）";
    }
    const resolve = (ext: string): string => {
      for (const r of rules) {
        if (r.file_types.includes(ext)) {
          const eng = engines.find((e) => e.name === r.engine);
          if (eng) return eng.display_name || eng.name;
          return r.engine;
        }
      }
      return "内置解析器";
    };
    return batchExts.map((ext) => `.${ext} → ${resolve(ext)}`).join(" · ");
  }, [rules, engines, batchExts]);

  // 切片摘要（对齐 WeKnora chunkingOverviewValue）
  const chunkingSummary = useMemo(() => {
    const parts = [
      `分块 ${chunking.chunk_size ?? 800}`,
      `重叠 ${chunking.chunk_overlap ?? 80}`,
      chunking.enable_parent_child ? "父子分块" : "普通分块",
    ];
    return parts.join(" · ");
  }, [chunking]);

  const canConfirm = files.length > 0 || urls.length > 0;

  const handleConfirm = () => {
    const pc: UploadProcessConfig = {};
    // 仅当用户改过（与默认不同或有规则）才携带 process_config，否则空 = 用 KB 默认
    const hasChunkingDiff =
      JSON.stringify(chunking) !== JSON.stringify(DEFAULT_CHUNKING) || rules.length > 0;
    if (hasChunkingDiff) {
      pc.chunking = { ...chunking };
      if (rules.length > 0) pc.parser_engine_rules = rules;
    }
    onConfirm({ files: [...files], processConfig: hasChunkingDiff ? pc : null });
  };

  const confirmBtn = (
    <Button type="primary" disabled={!canConfirm} onClick={() => void handleConfirm()}>
      确认上传
    </Button>
  );

  // overview 行
  const overviewRows = [
    { key: "chunking", title: "切片配置", value: chunkingSummary },
    { key: "parser", title: "解析引擎", value: parserSummary },
  ];

  return (
    <Modal
      open={open}
      onCancel={onCancel}
      width={920}
      title="上传文件"
      footer={
        <Space>
          <Button onClick={onCancel}>取消</Button>
          {confirmBtn}
        </Space>
      }
      destroyOnClose
    >
      <div style={{ display: "flex", gap: 16, minHeight: 380 }}>
        {/* 左侧文件列表（对齐 files-panel） */}
        <div
          style={{
            width: 280,
            flexShrink: 0,
            borderRight: "1px solid #e8eaed",
            paddingRight: 12,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <Text strong style={{ fontSize: 13 }}>文件列表</Text>
            <span style={{ fontSize: 12, color: "rgba(0,0,0,.45)" }}>{files.length + urls.length} 个</span>
          </div>
          <div style={{ flex: 1, overflow: "auto", maxHeight: 300 }}>
            {urls.map((u, i) => (
              <div key={`url-${i}`} style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 0", borderBottom: "1px solid #f5f5f5" }}>
                <LinkOutlined style={{ color: "#1677ff" }} />
                <span style={{ flex: 1, fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={u}>
                  {u}
                </span>
                <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => {}} style={{ padding: 0 }} />
              </div>
            ))}
            {files.map((f, i) => (
              <div key={`file-${i}`} style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 0", borderBottom: "1px solid #f5f5f5" }}>
                {getFileIcon(f.name)}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.name}>
                    {f.name}
                  </div>
                  <div style={{ fontSize: 11, color: "rgba(0,0,0,.45)" }}>{formatSize(f.size)}</div>
                </div>
                <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => removeFile(i)} style={{ padding: 0 }} />
              </div>
            ))}
            {files.length === 0 && urls.length === 0 && (
              <div style={{ fontSize: 12, color: "rgba(0,0,0,.35)", padding: "16px 0", textAlign: "center" }}>
                尚未添加文件
              </div>
            )}
          </div>
          <label style={{ marginTop: 8 }}>
            <Button block icon={<PlusOutlined />} size="small">
              继续添加
            </Button>
            <input
              type="file"
              multiple
              style={{ display: "none" }}
              onChange={(e) => {
                if (e.target.files) addFiles(Array.from(e.target.files));
                e.target.value = "";
              }}
            />
          </label>
        </div>

        {/* 右侧配置（对齐 main-panel：overview / section） */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          {activeSection === "overview" ? (
            <>
              <div style={{ marginBottom: 12 }}>
                <Text style={{ fontSize: 15, fontWeight: 600 }}>处理配置</Text>
                <div style={{ fontSize: 12, color: "rgba(0,0,0,.45)", marginTop: 2 }}>
                  可为本次上传指定处理配置（文件级覆盖），留空则使用知识库默认配置
                </div>
              </div>
              {overviewRows.map((row) => (
                <button
                  key={row.key}
                  type="button"
                  onClick={() => setActiveSection(row.key)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    width: "100%",
                    padding: "10px 12px",
                    border: "1px solid #e8eaed",
                    borderRadius: 4,
                    background: "#fff",
                    cursor: "pointer",
                    marginBottom: 8,
                    textAlign: "left",
                  }}
                >
                  <span style={{ fontSize: 13, color: "rgba(0,0,0,.65)", width: 80, flexShrink: 0 }}>{row.title}</span>
                  <span
                    style={{
                      flex: 1,
                      fontSize: 12,
                      color: row.key === "parser" && showParser ? "#1677ff" : "rgba(0,0,0,.88)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={row.value}
                  >
                    {row.value}
                  </span>
                  <span style={{ color: "#bfbfbf" }}>›</span>
                </button>
              ))}
              <div style={{ fontSize: 12, color: "rgba(0,0,0,.35)", marginTop: 4 }}>
                本次配置仅作用于本次上传的文件，不修改知识库默认配置
              </div>
            </>
          ) : (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <Button type="text" size="small" onClick={() => setActiveSection("overview")} style={{ padding: "0 4px" }}>
                  ‹ 返回概览
                </Button>
                <Text style={{ fontSize: 15, fontWeight: 600 }}>
                  {activeSection === "chunking" ? "切片配置" : "解析引擎"}
                </Text>
              </div>

              {activeSection === "chunking" && (
                <div style={{ overflow: "auto" }}>
                  <Row gutter={16}>
                    <Col span={8}>
                      <Form.Item label="分块大小">
                        <InputNumber min={64} max={32768} style={{ width: "100%" }} value={chunking.chunk_size as number}
                          onChange={(v) => setChunking((p) => ({ ...p, chunk_size: v ?? 800 }))} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item label="重叠长度">
                        <InputNumber min={0} max={4096} style={{ width: "100%" }} value={chunking.chunk_overlap as number}
                          onChange={(v) => setChunking((p) => ({ ...p, chunk_overlap: v ?? 80 }))} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item label="Token 上限" tooltip="0 = 按字符数">
                        <InputNumber min={0} style={{ width: "100%" }} value={chunking.token_limit as number}
                          onChange={(v) => setChunking((p) => ({ ...p, token_limit: v ?? 0 }))} />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Form.Item label="分隔符">
                    <Select mode="tags" style={{ width: "100%" }} value={chunking.separators as string[]}
                      options={SEPARATOR_PRESETS.map((v) => ({ value: v, label: JSON.stringify(v) }))}
                      onChange={(v) => setChunking((p) => ({ ...p, separators: v }))} />
                  </Form.Item>
                  <Form.Item label="父子分块">
                    <Space>
                      <Switch checked={!!chunking.enable_parent_child}
                        onChange={(v) => setChunking((p) => ({ ...p, enable_parent_child: v }))} />
                      <Text type="secondary" style={{ fontSize: 12 }}>开启后子块用于匹配、命中返回父块全文</Text>
                    </Space>
                  </Form.Item>
                  {!!chunking.enable_parent_child && (
                    <Row gutter={16}>
                      <Col span={12}>
                        <Form.Item label="父块大小" tooltip="512–8192">
                          <InputNumber min={512} max={8192} step={128} style={{ width: "100%" }} value={chunking.parent_chunk_size as number}
                            onChange={(v) => setChunking((p) => ({ ...p, parent_chunk_size: v ?? 4096 }))} />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item label="子块大小" tooltip="64–2048">
                          <InputNumber min={64} max={2048} step={32} style={{ width: "100%" }} value={chunking.child_chunk_size as number}
                            onChange={(v) => setChunking((p) => ({ ...p, child_chunk_size: v ?? 384 }))} />
                        </Form.Item>
                      </Col>
                    </Row>
                  )}
                  <Form.Item label="语言（留空自动识别）">
                    <Select mode="multiple" style={{ width: "100%" }} value={chunking.languages as string[]}
                      options={LANG_OPTIONS} allowClear
                      onChange={(v) => setChunking((p) => ({ ...p, languages: v }))} />
                  </Form.Item>
                  <Text type="secondary" style={{ fontSize: 12 }}>仅对本次上传生效，不修改知识库默认配置</Text>
                </div>
              )}

              {activeSection === "parser" && (
                <div style={{ overflow: "auto" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                    <Switch checked={showParser} onChange={setShowParser} size="small" />
                    <Text style={{ fontSize: 13 }}>启用按文件类型指定解析引擎</Text>
                  </div>
                  {showParser && (
                    <>
                      {batchExts.length > 0 && (
                        <div style={{ fontSize: 12, color: "rgba(0,0,0,.45)", marginBottom: 8 }}>
                          本批次文件类型：{batchExts.map((e) => `.${e}`).join(" ")}
                        </div>
                      )}
                      {rules.map((r, idx) => (
                        <Row key={idx} gutter={8} align="middle" style={{ marginBottom: 8 }}>
                          <Col span={11}>
                            <Select mode="multiple" placeholder="文件类型" style={{ width: "100%" }}
                              value={r.file_types} options={FILE_TYPE_OPTIONS}
                              onChange={(v) => setRules((prev) => prev.map((x, i) => (i === idx ? { ...x, file_types: v } : x)))} />
                          </Col>
                          <Col span={11}>
                            <Select placeholder="解析引擎" style={{ width: "100%" }} value={r.engine}
                              options={engineOptions}
                              onChange={(v) => setRules((prev) => prev.map((x, i) => (i === idx ? { ...x, engine: v } : x)))} />
                          </Col>
                          <Col span={2}>
                            <Button type="text" danger size="small" onClick={() => setRules((prev) => prev.filter((_, i) => i !== idx))}>
                              删除
                            </Button>
                          </Col>
                        </Row>
                      ))}
                      <Button type="dashed" block onClick={() => setRules((prev) => [...prev, { file_types: [], engine: "docreader" }])}>
                        添加规则
                      </Button>
                    </>
                  )}
                  <div style={{ marginTop: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      提示：不可用的引擎已置灰（如 MinerU endpoint 未配置）
                    </Text>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}