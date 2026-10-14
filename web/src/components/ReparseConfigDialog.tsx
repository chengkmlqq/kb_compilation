"use client";

/**
 * 重新解析配置弹窗（2026-10-14）：单文档重解析前可选处理配置，对齐 WeKnora「重解析换配置」。
 *
 * 此前 kb「重新解析」按钮直接调接口、无弹窗——后端 reparse 本就支持
 * ReparseRequest.process_config（更新 kb_document.process_config，切片/解析引擎/
 * 摘要策略均在本次及后续解析生效），只是前端没给表单。
 *
 * 本弹窗复用 UploadConfirmDialog 的处理配置面板（解析引擎 / 切片配置 / 构建配置
 * 三个 Collapse 项），确认后返回：
 *   - processConfig：{chunking?, parser_engine_rules?, summary_strategy?}（改动才带）
 *   - buildTapeId：文档级构建引擎（重选覆盖 KB 默认）
 * 调用方调 apiReparseDocument(kbId, docId, { process_config, build_tape_id })。
 *
 * 语义：留空/未改动 = 沿用文档原配置；确认即「清 chunks + 按新配置重解析 + 重新入队」。
 */
import React, { useEffect, useMemo, useState } from "react";
import { App, Button, Col, Collapse, Form, InputNumber, Modal, Row, Select, Space, Switch, Typography } from "antd";
import { apiListParserEngines, BuildEngineItem, ParserEngineItem } from "@/lib/api";
import {
  DEFAULT_CHUNKING,
  FILE_TYPE_OPTIONS,
  LANG_OPTIONS,
  SEPARATOR_PRESETS,
  SUMMARY_STRATEGY_OPTIONS,
} from "@/components/UploadConfirmDialog";

const { Text } = Typography;

export interface ReparseConfirmResult {
  /** 处理配置（仅用户改过才带；null = 沿用文档原配置） */
  processConfig: Record<string, unknown> | null;
  /** 文档级构建引擎（空 = 沿用文档/KB 默认） */
  buildTapeId?: string;
}

interface Props {
  open: boolean;
  /** 当前文档（用于标题展示与扩展名推断） */
  doc?: { id: string; file_name: string; file_ext?: string | null } | null;
  /** 可选的构建服务（服务编排中已发布的编排） */
  buildEngines?: BuildEngineItem[];
  /** 知识库默认构建引擎（kb_datasource.build_tape_id），用于展示「跟随默认」状态 */
  defaultBuildTapeId?: string;
  onCancel: () => void;
  onConfirm: (result: ReparseConfirmResult) => void;
}

function getDocExt(doc?: { id: string; file_name: string; file_ext?: string | null } | null): string {
  if (!doc) return "";
  const fromExt = (doc.file_ext || "").replace(/^\./, "").toLowerCase();
  if (fromExt) return fromExt;
  const fromName = (doc.file_name || "").split(".").pop() || "";
  return fromName.toLowerCase();
}

export default function ReparseConfigDialog({
  open,
  doc,
  buildEngines = [],
  defaultBuildTapeId,
  onCancel,
  onConfirm,
}: Props) {
  const { message } = App.useApp();
  const [chunking, setChunking] = useState<Record<string, unknown>>({ ...DEFAULT_CHUNKING });
  const [rules, setRules] = useState<Array<{ file_types: string[]; engine: string }>>([]);
  const [engines, setEngines] = useState<ParserEngineItem[]>([]);
  const [showParser, setShowParser] = useState(false);
  const [buildEngine, setBuildEngine] = useState<string>("");
  const [summaryStrategy, setSummaryStrategy] = useState<string>("");

  // 打开时拉引擎列表（供解析引擎 Select 使用）
  useEffect(() => {
    if (!open) return;
    setChunking({ ...DEFAULT_CHUNKING });
    setRules([]);
    setShowParser(false);
    setBuildEngine("");
    setSummaryStrategy("");
    void apiListParserEngines()
      .then((res) => {
        if (res.success && Array.isArray(res.data)) setEngines(res.data);
        else setEngines([]);
      })
      .catch(() => setEngines([]));
  }, [open]);

  // 本文档扩展名（解析引擎规则默认只给相关扩展）
  const docExt = getDocExt(doc);
  const batchExts = useMemo(() => (docExt ? [docExt] : []), [docExt]);

  const engineOptions = engines.map((e) => ({
    value: e.name,
    label: e.display_name || e.name,
    disabled: !e.available,
    title: e.available ? e.description || `解析引擎 ${e.display_name || e.name}` : e.reason || "引擎不可用",
  }));

  // 解析引擎摘要（ext → engine）
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

  // 切片摘要
  const chunkingSummary = useMemo(() => {
    const parts = [
      `分块 ${chunking.chunk_size ?? 800}`,
      `重叠 ${chunking.chunk_overlap ?? 80}`,
      chunking.enable_parent_child ? "父子分块" : "普通分块",
    ];
    return parts.join(" · ");
  }, [chunking]);

  // 构建配置摘要（wiki 构建服务 + 摘要生成服务）
  const buildSummary = useMemo(() => {
    const wiki = buildEngine
      ? (buildEngines.find((t) => t.id === buildEngine)?.tape_label || buildEngine)
      : "沿用文档/KB 默认";
    const summary = summaryStrategy
      ? (SUMMARY_STRATEGY_OPTIONS.find((o) => o.value === summaryStrategy)?.label || summaryStrategy)
      : "沿用文档/KB 默认";
    return `Wiki 构建：${wiki} · 摘要：${summary}`;
  }, [buildEngine, summaryStrategy, buildEngines]);

  const panelLabel = (title: string, value: string, highlight = false) => (
    <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
      <span style={{ fontSize: 13, color: "rgba(0,0,0,.65)", width: 80, flexShrink: 0 }}>{title}</span>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 12,
          color: highlight ? "#1677ff" : "rgba(0,0,0,.88)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={value}
      >
        {value}
      </span>
    </span>
  );

  const handleConfirm = () => {
    const pc: Record<string, unknown> = {};
    // 仅当用户改过（与默认不同或有规则）才携带 process_config，否则空 = 沿用文档原配置
    const hasChunkingDiff =
      JSON.stringify(chunking) !== JSON.stringify(DEFAULT_CHUNKING) || rules.length > 0;
    if (hasChunkingDiff) {
      pc.chunking = { ...chunking };
      if (rules.length > 0) pc.parser_engine_rules = rules;
    }
    if (summaryStrategy) pc.summary_strategy = summaryStrategy;
    onConfirm({
      processConfig: hasChunkingDiff || !!summaryStrategy ? pc : null,
      buildTapeId: buildEngine || undefined,
    });
  };

  return (
    <Modal
      open={open}
      onCancel={onCancel}
      width={760}
      title={
        <Space size={8}>
          <span>重新解析</span>
          {doc?.file_name ? (
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
              {doc.file_name.length > 40 ? `${doc.file_name.slice(0, 40)}…` : doc.file_name}
            </Text>
          ) : null}
        </Space>
      }
      footer={
        <Space>
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" onClick={() => void handleConfirm()}>
            确认重新解析
          </Button>
        </Space>
      }
      destroyOnClose
    >
      <div style={{ marginBottom: 12 }}>
        <Text style={{ fontSize: 15, fontWeight: 600 }}>处理配置</Text>
        <div style={{ fontSize: 12, color: "rgba(0,0,0,.45)", marginTop: 2 }}>
          本次重新解析将清空旧分块并按下列配置重新解析；留空/不改动则沿用文档原配置
        </div>
      </div>
      <Collapse
        items={[
          {
            key: "parser",
            label: panelLabel("解析引擎", parserSummary, showParser),
            children: (
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                  <Switch checked={showParser} onChange={setShowParser} size="small" />
                  <Text style={{ fontSize: 13 }}>启用按文件类型指定解析引擎</Text>
                </div>
                {showParser && (
                  <>
                    {batchExts.length > 0 && (
                      <div style={{ fontSize: 12, color: "rgba(0,0,0,.45)", marginBottom: 8 }}>
                        本文档类型：{batchExts.map((e) => `.${e}`).join(" ")}
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
            ),
          },
          {
            key: "chunking",
            label: panelLabel("切片配置", chunkingSummary),
            children: (
              <div>
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
                <Text type="secondary" style={{ fontSize: 12 }}>仅对本次重新解析生效，不修改知识库默认配置</Text>
              </div>
            ),
          },
          {
            key: "build",
            label: panelLabel("构建配置", buildSummary),
            children: (
              <div>
                <Form.Item
                  label="摘要生成服务"
                  tooltip="重解析成功后按所选策略生成摘要页；留空沿用文档/KB 默认摘要策略"
                  extra={summaryStrategy ? undefined : "当前沿用文档/KB 默认摘要策略"}
                >
                  <Select
                    style={{ width: "100%" }}
                    allowClear
                    placeholder="沿用文档/KB 默认"
                    value={summaryStrategy || undefined}
                    onChange={setSummaryStrategy}
                    options={SUMMARY_STRATEGY_OPTIONS}
                  />
                </Form.Item>
                <Form.Item
                  label="Wiki 构建服务"
                  tooltip="选择后本文档构建 Wiki 走该编排（服务编排中已发布的编排）；留空沿用文档/KB 默认"
                  extra={buildEngine ? undefined : defaultBuildTapeId ? "当前跟随知识库默认编排" : "当前跟随知识库默认（未设置）"}
                >
                  <Select
                    style={{ width: "100%" }}
                    allowClear
                    placeholder="沿用文档/KB 默认"
                    value={buildEngine || undefined}
                    onChange={setBuildEngine}
                    options={buildEngines.map((t) => ({ label: t.tape_label || t.tape_name, value: t.id }))}
                  />
                </Form.Item>
                <Text type="secondary" style={{ fontSize: 12 }}>仅对本次重新解析生效，不修改知识库默认配置</Text>
              </div>
            ),
          },
        ]}
      />
      <div style={{ fontSize: 12, color: "rgba(0,0,0,.35)", marginTop: 8 }}>
        确认后清空旧分块并重新解析，wiki 构建 / 摘要将按上述配置自动触发
      </div>
    </Modal>
  );
}
