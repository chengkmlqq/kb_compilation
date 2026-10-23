"use client";

/**
 * 质量评估公共面板（2026-10-21 抽取，供四个评估页面复用）。
 *
 * 按 dim 参数化：
 * - dim="all"   → 三维度合一（wiki评估 页，兼容「质量评估-QA」编排产物）
 * - dim="doc"   → 文档转换评估（单维度）
 * - dim="chunk" → 切片评估（单维度）
 * - dim="graph" → 图数据评估（单维度）
 *
 * 单维度页只显示本维度得分/指标/问题；发起评估时携带 dim 走对应单维度编排。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { App, Button, Card, Empty, Form, Input, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import {
  ApiOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoModal } from "@/components/biz/modo-modal";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import { ModoTable } from "@/components/biz/modo-table";
import {
  apiDeleteQualityRun,
  apiGetQualityRun,
  apiLaunchQualityRun,
  apiListDocuments,
  apiListQualityDocEval,
  apiListQualityRuns,
  apiListKbs,
  ChunkDocEval,
  DocItem,
  KbItem,
  QualityAttribution,
  QualityDocItem,
  QualityIssue,
  QualityMetrics,
  QualityRunDetail,
  QualityRunItem,
  QualityScores,
} from "@/lib/api";

const { Text } = Typography;

export type QualityDim = "all" | "doc" | "chunk" | "graph";

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  queued: { label: "排队中", color: "default" },
  running: { label: "评估中", color: "processing" },
  success: { label: "完成", color: "success" },
  failed: { label: "失败", color: "error" },
};

const SCOPE_MAP: Record<string, string> = {
  single: "单文档",
  multi: "批量",
  all: "整库",
};

const DIM_MAP: Record<string, { label: string; icon: React.ReactNode }> = {
  doc: { label: "文档转换质量", icon: <SwapOutlined /> },
  chunk: { label: "切片质量", icon: <FileTextOutlined /> },
  wiki: { label: "Wiki质量", icon: <DatabaseOutlined /> },
  graph: { label: "图数据质量", icon: <ApiOutlined /> },
};

/** 单维度页的维度键（all 页在渲染处硬编码三维度） */
const DIM_KEYS: Record<Exclude<QualityDim, "all">, Exclude<QualityDim, "all">[]> = {
  doc: ["doc"],
  chunk: ["chunk"],
  graph: ["graph"],
};

const ISSUE_TYPE_LABEL: Record<string, string> = {
  // 文档转换（dim=doc）
  no_docs: "无文档",
  parse_failed: "转换失败",
  empty_conversion: "空转换产物",
  conversion_pending: "转换处理中",
  garbled_text: "内容乱码",
  garbled_filename: "文件名乱码",
  // 切片（dim=chunk）
  too_many_short_chunks: "切片过碎",
  too_many_long_chunks: "切片过长",
  truncated_chunks: "切片截断",
  duplicate_chunks: "切片重复",
  no_chunks: "无切片",
  // 切片评估 LLM 版（2026-10-22）
  chunk_size_unreasonable: "切片大小不合理",
  chunk_split_error: "切分错误",
  chunk_semantic_loss: "语义丢失",
  chunk_coverage_incomplete: "覆盖不完整",
  // 切片评估 v3（2026-10-23：对照原文评估 Wiki）
  wiki_no_pages: "无 Wiki 页面",
  wiki_content_error: "Wiki 内容错误",
  wiki_missing_content: "Wiki 内容缺失",
  wiki_not_meaningful: "Wiki 无意义",
  // wiki
  empty_content: "空页面",
  orphan: "孤立页",
  broken_links: "断链",
  llm_placeholder: "LLM占位",
  missing_source: "缺出处",
  no_folder: "未挂目录",
  no_pages: "无页面",
  // graph
  dumb_nodes: "哑节点",
  illegal_rel_types: "非法边类型",
  untraceable_rels: "不可回溯边",
  wiki_missing_nodes: "图页缺失",
  wiki_sem_unclear: "单文档语义不明",
  wiki_sem_error: "Wiki与原文不一致",
  graph_sem_node: "图节点语义问题",
  graph_sem_rel: "图边语义问题",
  sem_eval_error: "语义评估失败",
};

function scoreColor(score?: number | null) {
  if (score == null) return "default";
  if (score >= 85) return "success";
  if (score >= 60) return "warning";
  return "error";
}

function formatTime(value?: string | null) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", { hour12: false });
}

/** 毫秒 → 可读耗时（"350ms" / "1.2s" / "3分05秒"） */
function fmtDuration(ms?: number | null) {
  if (ms == null) return "-";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60000);
  const s = Math.round((ms % 60000) / 1000);
  return `${m}分${String(s).padStart(2, "0")}秒`;
}

/** 单文件明细表的状态 Tag */
function docStateTag(state?: string) {
  if (state === "READY") return <Tag color="success">就绪</Tag>;
  if (state === "FAILED") return <Tag color="error">失败</Tag>;
  if (state) return <Tag color="warning">{state}</Tag>;
  return <Tag>未知</Tag>;
}

export default function QualityAssessPane({ dim }: { dim: QualityDim }) {
  const { message, modal } = App.useApp();
  const [items, setItems] = useState<QualityRunItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  const [applied, setApplied] = useState<{ kb_id?: string; status?: string }>({});
  const [searchForm] = Form.useForm<{ kb_id?: string; status?: string }>();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [detail, setDetail] = useState<QualityRunDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  // 文档转换评估-文件粒度明细（kb_quality_doc_eval，主页平铺，服务端分页）
  const [docEvalItems, setDocEvalItems] = useState<QualityDocItem[]>([]);
  const [docEvalTotal, setDocEvalTotal] = useState(0);
  const [docEvalPage, setDocEvalPage] = useState(1);
  const [docEvalLoading, setDocEvalLoading] = useState(false);
  const [docApplied, setDocApplied] = useState<{ kb_id?: string; status?: string; keyword?: string }>({});
  const [docSearchForm] = Form.useForm<{ kb_id?: string; status?: string; keyword?: string }>();
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [launchOpen, setLaunchOpen] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [launchForm] = Form.useForm<{ kb_id: string; mode: string; doc_ids: string[] }>();
  const [docOptions, setDocOptions] = useState<{ value: string; label: string }[]>([]);
  const [docLoading, setDocLoading] = useState(false);
  const docSearchRef = useRef("");

  // all 页在渲染处硬编码三维度；单维度页取对应键
  const dimKeys: Exclude<QualityDim, "all">[] =
    dim === "all" ? [] : DIM_KEYS[dim as Exclude<QualityDim, "all">];

  const loadDocs = useCallback(async (kb: string, keyword = "") => {
    setDocLoading(true);
    try {
      const res = await apiListDocuments(kb, 1, 200, { keyword });
      if (res.success) {
        setDocOptions(
          (res.data?.items || []).map((d: DocItem) => ({ value: d.id, label: d.file_name })),
        );
      } else {
        message.error(res.message || "加载文档列表失败");
      }
    } catch (e) {
      message.error(`加载文档列表失败: ${String(e)}`);
    } finally {
      setDocLoading(false);
    }
  }, [message]);

  const handleKbChange = (v: string) => {
    launchForm.setFieldValue("doc_ids", undefined);
    docSearchRef.current = "";
    if (v) void loadDocs(v);
  };

  const handleDocSearch = (kw: string) => {
    docSearchRef.current = kw;
    const kbId = launchForm.getFieldValue("kb_id");
    if (kbId) void loadDocs(kbId, kw);
  };

  const loadDocEval = useCallback(
    async (filters: { kb_id?: string; status?: string; keyword?: string }, pageNum = 1) => {
      setDocEvalLoading(true);
      try {
        const res = await apiListQualityDocEval({
          kb_id: filters.kb_id || undefined,
          status: filters.status || undefined,
          keyword: filters.keyword || undefined,
          page: pageNum,
          page_size: 20,
        });
        setDocEvalItems(res.data?.items ?? []);
        setDocEvalTotal(res.data?.total ?? 0);
        setDocEvalPage(pageNum);
      } catch (e) {
        message.error(`文件评估结果加载失败: ${String(e)}`);
      } finally {
        setDocEvalLoading(false);
      }
    },
    [message],
  );

  const load = useCallback(async () => {
    // dim=doc 页面主页平铺文件评估结果（kb_quality_doc_eval），不走 run 列表
    if (dim === "doc") {
      await loadDocEval(docApplied, docEvalPage);
      return;
    }
    setLoading(true);
    try {
      const res = await apiListQualityRuns({
        kb_id: applied.kb_id || undefined,
        status: applied.status || undefined,
        dim: dim === "all" ? undefined : dim,
        page,
        page_size: pageSize,
      });
      const r = res.data;
      setItems(r?.items ?? []);
      setTotal(r?.total ?? 0);
    } catch (e) {
      message.error(`加载失败: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [applied, page, pageSize, dim, message, loadDocEval, docApplied, docEvalPage]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    apiListKbs(1, 100)
      .then((res) => setKbs(res.data?.items ?? []))
      .catch(() => setKbs([]));
  }, []);

  const doSearch = (v: { kb_id?: string; status?: string }) => {
    setPage(1);
    setApplied({ kb_id: v.kb_id, status: v.status });
  };

  const doReset = () => {
    searchForm.resetFields();
    setPage(1);
    setApplied({});
  };

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    setDetailLoading(true);
    try {
      const res = await apiGetQualityRun(id);
      setDetail(res.data ?? null);
    } catch (e) {
      message.error(`详情加载失败: ${String(e)}`);
    } finally {
      setDetailLoading(false);
    }
  };

  const doLaunch = async () => {
    const v = await launchForm.validateFields();
    setLaunching(true);
    try {
      const scope =
        v.mode === "all" ? "all" : v.mode === "multi" ? "multi" : "single";
      const docIds = scope === "all" ? [] : v.doc_ids ?? [];
      await apiLaunchQualityRun({
        kb_id: v.kb_id,
        doc_ids: docIds,
        scope,
        dim: dim === "all" ? undefined : dim,
      });
      message.success("评估任务已提交");
      setLaunchOpen(false);
      launchForm.resetFields();
      load();
    } catch (e) {
      message.error(`提交失败: ${String(e)}`);
    } finally {
      setLaunching(false);
    }
  };

  const doDelete = (r: QualityRunItem) => {
    modal.confirm({
      title: "删除评估任务？",
      content: "该任务的评估结果与问题清单将一并删除，删除后不可恢复。",
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        setDeletingId(r.id);
        try {
          const res = await apiDeleteQualityRun(r.id);
          if (res.success) {
            message.success("已删除");
            if (detail?.id === r.id) {
              setDetail(null);
              setDetailOpen(false);
            }
            load();
          } else {
            message.error(res.message || "删除失败");
          }
        } catch (e) {
          message.error(`删除失败: ${String(e)}`);
        } finally {
          setDeletingId(null);
        }
      },
    });
  };

  /** 单维度页：得分列显示本维度（fallback all 页显示三维度） */
  const scoreCell = (s: QualityRunItem["scores"]) => (
    <Space size={6}>
      {(dim === "all" ? (["chunk", "wiki", "graph"] as const) : dimKeys).map((d) => (
        <Tag key={d} color={scoreColor(s?.[d] ?? null)} style={{ marginInlineEnd: 0 }}>
          {DIM_MAP[d].label.replace("质量", "")}={s?.[d] ?? "-"}
        </Tag>
      ))}
    </Space>
  );

  const columns: ColumnsType<QualityRunItem> = [
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (v: string) => {
        const m = STATUS_MAP[v] ?? { label: v, color: "default" };
        return <Tag color={m.color}>{m.label}</Tag>;
      },
    },
    {
      title: "范围",
      dataIndex: "scope",
      width: 90,
      render: (v: string) => SCOPE_MAP[v] ?? v,
    },
    {
      title: "知识库",
      dataIndex: "kb_id",
      ellipsis: true,
      render: (v: string) => {
        const kb = kbs.find((k) => k.id === v);
        return kb?.name ?? v.slice(0, 8) + "…";
      },
    },
    {
      title: dim === "all" ? "三维度得分" : `${DIM_MAP[dim].label}得分`,
      dataIndex: "scores",
      width: dim === "all" ? 220 : 140,
      render: scoreCell,
    },
    {
      title: "问题",
      dataIndex: "issues_count",
      width: 70,
    },
    {
      title: "提交时间",
      dataIndex: "created_at",
      width: 160,
      render: formatTime,
    },
    {
      title: "操作",
      key: "op",
      width: 90,
      render: (_, r) => (
        <ModoActionGroup
          maxCount={2}
          actions={[
            { key: "detail", label: "详情", onClick: () => void openDetail(r.id) },
            {
              key: "delete",
              label: "删除",
              danger: true,
              loading: deletingId === r.id,
              disabled: deletingId !== null,
              onClick: () => doDelete(r),
            },
          ]}
        />
      ),
    },
  ];

  const scoreTag = (label: string, score?: number | null) => (
    <div style={{ marginBottom: 8 }}>
      <Text strong>{label}: </Text>
      <Tag color={scoreColor(score)}>{score == null ? "-" : `${score} 分`}</Tag>
    </div>
  );

  /** 单维度页详情：本维度指标摘要行 */
  const metricSummary = (d: string, m: { metrics?: QualityMetrics } | undefined) => {
    const mm = m?.metrics ?? {};
    const parts: string[] = [];
    if (d === "doc") {
      parts.push(`文档 ${mm.docs ?? "-"}`);
      parts.push(`就绪 ${mm.ready ?? 0}`);
      parts.push(`失败 ${mm.failed ?? 0}`);
      parts.push(`处理中 ${mm.pending ?? 0}`);
      if (mm.empty_conversion) parts.push(`空转换 ${mm.empty_conversion}`);
      if (mm.garbled) parts.push(`乱码 ${mm.garbled}`);
      if (mm.avg_duration_ms != null) parts.push(`平均耗时 ${fmtDuration(mm.avg_duration_ms)}`);
      if (mm.engines && Object.keys(mm.engines).length) {
        parts.push(
          `引擎 ${Object.entries(mm.engines)
            .map(([k, v]) => `${k}×${v}`)
            .join(" ")}`,
        );
      }
    } else if (d === "chunk") {
      parts.push(`文件 ${mm.files_eval ?? mm.docs ?? "-"}`);
      parts.push(`切片 ${mm.chunks ?? "-"}`);
      if (mm.wiki_pages != null) parts.push(`Wiki页 ${mm.wiki_pages}`);
      if (mm.avg_len != null) parts.push(`均长 ${mm.avg_len} 字`);
      if (mm.eval_errors) parts.push(`评估失败 ${mm.eval_errors}`);
    } else if (d === "wiki") {
      parts.push(`页面 ${mm.pages ?? "-"}`);
      parts.push(`孤立 ${mm.orphan ?? 0}`);
      parts.push(`断链 ${mm.broken_links ?? 0}`);
    } else {
      parts.push(`节点 ${mm.nodes ?? "-"}`);
      parts.push(`边 ${mm.rels ?? 0}`);
      parts.push(`哑节点 ${mm.dumb_nodes ?? 0}`);
    }
    return parts.join(" · ");
  };

  const issueRows = (issues?: QualityIssue[]) => {
    if (!issues || issues.length === 0)
      return <Text type="secondary">无问题</Text>;
    return (
      <ul style={{ paddingLeft: 18, margin: 0 }}>
        {issues.slice(0, 100).map((it, i) => (
          <li key={i} style={{ marginBottom: 4 }}>
            <Text type="secondary">
              [{ISSUE_TYPE_LABEL[it.issue_type ?? ""] ?? it.issue_type}]
            </Text>{" "}
            {it.slug && <Text code>{it.slug}</Text>} {it.detail}
          </li>
        ))}
        {issues.length > 100 && (
          <li>
            <Text type="secondary">… 共 {issues.length} 条</Text>
          </li>
        )}
      </ul>
    );
  };

  const attributionRows = (attrs?: QualityAttribution[]) => {
    if (!attrs || attrs.length === 0)
      return <Text type="secondary">暂无归因建议</Text>;
    return (
      <ul style={{ paddingLeft: 18, margin: 0 }}>
        {attrs.map((a, i) => (
          <li key={i} style={{ marginBottom: 8 }}>
            <Tag color="blue" style={{ marginInlineEnd: 6 }}>
              {DIM_MAP[a.dim ?? ""]?.label ?? a.dim}
            </Tag>
            <div>
              <Text>原因：{a.cause}</Text>
              <br />
              <Text type="secondary">建议：{a.suggestion}</Text>
            </div>
          </li>
        ))}
      </ul>
    );
  };

  /** 文档转换评估-文件评估结果表列（kb_quality_doc_eval，每文件一行，最新状态） */
  const docColumns: ColumnsType<QualityDocItem> = [
    {
      title: "知识库",
      dataIndex: "kb_id",
      width: 160,
      ellipsis: true,
      render: (v: string) => {
        const kb = kbs.find((k) => k.id === v);
        return kb?.name ?? v.slice(0, 8) + "…";
      },
    },
    { title: "文件名", dataIndex: "file_name", ellipsis: true },
    {
      title: "状态",
      dataIndex: "parse_state",
      width: 90,
      render: (v: string) => docStateTag(v),
    },
    {
      title: "引擎",
      dataIndex: "parse_engine",
      width: 100,
      render: (v: string) => v || "-",
    },
    {
      title: "耗时",
      dataIndex: "duration_ms",
      width: 90,
      render: (v: number | null) => fmtDuration(v),
    },
    { title: "切片", dataIndex: "chunk_count", width: 64 },
    {
      title: "乱码",
      dataIndex: "garbled",
      width: 64,
      render: (v: boolean) =>
        v ? <Tag color="error">是</Tag> : <Tag>否</Tag>,
    },
    {
      title: "评估时间",
      dataIndex: "updated_at",
      width: 150,
      render: formatTime,
    },
  ];

  /** 切片评估-单文件明细表（metrics.per_doc，LLM 版：对照原文评估 Wiki） */
  const chunkDetailTable = (items?: ChunkDocEval[]) => {
    if (!items || items.length === 0) return null;
    const columns: ColumnsType<ChunkDocEval> = [
      { title: "文件名", dataIndex: "file_name", ellipsis: true },
      { title: "切片", dataIndex: "chunks", width: 60 },
      { title: "均长", dataIndex: "avg_len", width: 70, render: (v: number) => v ?? "-" },
      { title: "Wiki页", dataIndex: "wiki_pages", width: 70, render: (v?: number) => v ?? 0 },
      {
        title: "Wiki构建起始",
        dataIndex: "wiki_start_time",
        width: 132,
        render: (v?: string | null) => formatTime(v),
      },
      {
        title: "Wiki构建耗时",
        dataIndex: "wiki_build_sec",
        width: 96,
        render: (v?: number | null) => fmtDuration(v != null ? v * 1000 : null),
      },
      {
        title: "处理时长",
        dataIndex: "doc_process_sec",
        width: 80,
        render: (v?: number | null) => fmtDuration(v != null ? v * 1000 : null),
      },
      {
        title: "评估耗时",
        dataIndex: "eval_duration_sec",
        width: 80,
        render: (v?: number | null) => fmtDuration(v != null ? v * 1000 : null),
      },
      {
        title: "评分",
        dataIndex: "score",
        width: 70,
        render: (v?: number | null) => (
          <Tag color={scoreColor(v)}>{v ?? "-"}</Tag>
        ),
      },
      {
        title: "结论",
        dataIndex: "conclusion",
        ellipsis: true,
        render: (v: string, r) =>
          r.error ? (
            <Text type="danger" style={{ fontSize: 12 }}>
              LLM 评估失败
            </Text>
          ) : (
            <Tooltip title={`${v ?? ""}${r.issues?.length ? `\n问题: ${r.issues.join("、")}` : ""}`}>
              <Text style={{ fontSize: 12 }}>{v || "-"}</Text>
            </Tooltip>
          ),
      },
    ];
    return (
      <Table<ChunkDocEval>
        rowKey="doc_id"
        size="small"
        columns={columns}
        dataSource={items}
        pagination={{ pageSize: 10, showSizeChanger: false, size: "small" }}
      />
    );
  };

  const launchLabel = dim === "all" ? "发起评估" : `发起${DIM_MAP[dim].label.replace("质量", "")}评估`;

  const doDocSearch = (v: { kb_id?: string; status?: string; keyword?: string }) => {
    setDocEvalPage(1);
    setDocApplied({ kb_id: v.kb_id, status: v.status, keyword: v.keyword });
  };

  const doDocReset = () => {
    docSearchForm.resetFields();
    setDocEvalPage(1);
    setDocApplied({});
  };

  const listPane = (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            padding: "12px 0 0",
          },
        }}
      >
        {/* dim=doc：主页直接平铺文件评估结果（不展示库级统计/run 列表） */}
        {dim === "doc" ? (
          <>
            <Form
              form={docSearchForm}
              layout="inline"
              onFinish={doDocSearch}
              style={{ margin: "0 16px 12px", flexShrink: 0 }}
            >
              <Form.Item name="kb_id" label="知识库">
                <Select
                  allowClear
                  showSearch
                  placeholder="全部知识库"
                  style={{ width: 200 }}
                  optionFilterProp="label"
                  options={kbs.map((k) => ({ label: k.name, value: k.id }))}
                />
              </Form.Item>
              <Form.Item name="status" label="转换状态">
                <Select
                  allowClear
                  placeholder="全部"
                  style={{ width: 130 }}
                  options={[
                    { label: "就绪", value: "READY" },
                    { label: "失败", value: "FAILED" },
                    { label: "处理中", value: "PENDING" },
                    { label: "解析中", value: "PARSING" },
                    { label: "向量化中", value: "EMBEDDING" },
                  ]}
                />
              </Form.Item>
              <Form.Item name="keyword" label="文件名">
                <Input allowClear placeholder="输入文件名搜索" style={{ width: 200 }} />
              </Form.Item>
              <Form.Item>
                <Space>
                  <Button type="primary" htmlType="submit">
                    查询
                  </Button>
                  <Button onClick={doDocReset}>重置</Button>
                </Space>
              </Form.Item>
            </Form>
            <ModoTable<QualityDocItem>
              rowKey="doc_id"
              size="small"
              loading={docEvalLoading}
              columns={docColumns}
              dataSource={docEvalItems}
              locale={{ emptyText: <Empty description="暂无文件评估结果（先发起评估）" /> }}
            />
            <ModoPagination
              current={docEvalPage}
              pageSize={20}
              total={docEvalTotal}
              showTotal={(t) => `共 ${t} 个文件`}
              onChange={(p) => setDocEvalPage(p)}
            />
          </>
        ) : (
          <>
            <Form
              form={searchForm}
              layout="inline"
              onFinish={doSearch}
              style={{ margin: "0 16px 12px", flexShrink: 0 }}
            >
              <Form.Item name="kb_id" label="知识库">
                <Select
                  allowClear
                  showSearch
                  placeholder="全部知识库"
                  style={{ width: 200 }}
                  optionFilterProp="label"
                  options={kbs.map((k) => ({ label: k.name, value: k.id }))}
                />
              </Form.Item>
              <Form.Item name="status" label="状态">
                <Select
                  allowClear
                  placeholder="全部"
                  style={{ width: 130 }}
                  options={Object.entries(STATUS_MAP).map(([k, v]) => ({
                    label: v.label,
                    value: k,
                  }))}
                />
              </Form.Item>
              <Form.Item>
                <Space>
                  <Button type="primary" htmlType="submit">
                    查询
                  </Button>
                  <Button onClick={doReset}>重置</Button>
                </Space>
              </Form.Item>
            </Form>

            <ModoTable<QualityRunItem>
              rowKey="id"
              size="small"
              columns={columns}
              dataSource={items}
              loading={loading}
              locale={{ emptyText: <Empty description="暂无评估记录" /> }}
            />
            <ModoPagination
              current={page}
              pageSize={pageSize}
              total={total}
              showTotal={(t) => `共 ${t} 条`}
              onChange={(p, ps) => {
                setPage(p);
                setPageSize(ps);
              }}
            />
          </>
        )}
      </Card>
    </div>
  );

  return (
    <div
      className="quality-page"
      style={{
        padding: 10,
        height: "calc(100vh - var(--app-header-h, 48px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey="home"
        tabBarExtraContent={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={() => {
                setDocOptions([]);
                docSearchRef.current = "";
                setLaunchOpen(true);
              }}
            >
              {launchLabel}
            </Button>
          </Space>
        }
        items={[{ key: "home", label: dim === "all" ? "wiki评估" : DIM_MAP[dim].label, children: listPane }]}
      />

      <ModoModal
        title={launchLabel}
        open={launchOpen}
        onOk={doLaunch}
        confirmLoading={launching}
        onCancel={() => setLaunchOpen(false)}
        okText="提交"
        cancelText="取消"
      >
        <Form form={launchForm} layout="vertical" initialValues={{ mode: "single" }}>
          <Form.Item name="kb_id" label="知识库" rules={[{ required: true, message: "请选择知识库" }]}>
            <Select
              showSearch
              placeholder="选择知识库"
              optionFilterProp="label"
              onChange={handleKbChange}
              options={kbs.map((k) => ({ label: k.name, value: k.id }))}
            />
          </Form.Item>
          <Form.Item name="mode" label="评估范围">
            <Select
              options={[
                { label: "单文档", value: "single" },
                { label: "批量文档", value: "multi" },
                { label: "整库（全部文档）", value: "all" },
              ]}
            />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) => prev.mode !== cur.mode}
          >
            {({ getFieldValue }) =>
              getFieldValue("mode") === "all" ? null : (
                <Form.Item
                  name="doc_ids"
                  label={getFieldValue("mode") === "multi" ? "批量选择文档" : "选择文档"}
                  rules={[{ required: true, message: "请选择文档" }]}
                >
                  <Select
                    mode="multiple"
                    showSearch
                    loading={docLoading}
                    placeholder={launchForm.getFieldValue("kb_id") ? "选择文档（可输入名称搜索）" : "请先选择知识库"}
                    options={docOptions}
                    onSearch={handleDocSearch}
                    filterOption={false}
                    notFoundContent={docSearchRef.current ? "未找到匹配文档" : "请先选择知识库"}
                  />
                </Form.Item>
              )
            }
          </Form.Item>
        </Form>
      </ModoModal>

      <ModoDrawer
        title="评估详情"
        width={720}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        showFooter={false}
        loading={detailLoading}
      >
        {detail && (
          <div>
            <Space size={12} style={{ marginBottom: 12 }}>
              <Tag color={STATUS_MAP[detail.status]?.color ?? "default"}>
                {STATUS_MAP[detail.status]?.label ?? detail.status}
              </Tag>
              <Tag>{SCOPE_MAP[detail.scope] ?? detail.scope}</Tag>
              <Text type="secondary">{formatTime(detail.created_at)}</Text>
            </Space>
            {detail.summary && (
              <Text style={{ display: "block", marginBottom: 12 }}>{detail.summary}</Text>
            )}
            {detail.error && (
              <Text type="danger" style={{ display: "block", marginBottom: 12 }}>
                {detail.error}
              </Text>
            )}

            {/* 得分区：all 页显示三维度卡片，单维度页只显示本维度 */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: dim === "all" ? "1fr 1fr 1fr" : "1fr",
                gap: 12,
                marginBottom: 16,
              }}
            >
              {dimKeys.map((d) => (
                <div
                  key={d}
                  style={{
                    border: "1px solid #E3E9EF",
                    borderRadius: 6,
                    padding: 12,
                  }}
                >
                  {scoreTag(
                    `${DIM_MAP[d].label}综合`,
                    detail.reports?.[d]?.metrics?.final_score ?? detail.scores?.[d] ?? null,
                  )}
                  <div style={{ marginBottom: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {d === "chunk" ? "评估分" : "程序分"}{" "}
                      <Tag color={scoreColor(detail.reports?.[d]?.metrics?.health_score)}>
                        {detail.reports?.[d]?.metrics?.health_score ?? "-"}
                      </Tag>
                      {d !== "chunk" && d !== "doc" && (
                        <>
                          {" "}
                          语义分{" "}
                          <Tag color={scoreColor(detail.reports?.[d]?.metrics?.semantic_score)}>
                            {detail.reports?.[d]?.metrics?.semantic_score ?? "-"}
                          </Tag>
                        </>
                      )}
                    </Text>
                  </div>
                  {detail.reports?.[d]?.metrics && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {metricSummary(d, detail.reports![d])}
                    </Text>
                  )}
                </div>
              ))}
            </div>

            <div style={{ marginBottom: 16 }}>
              <Typography.Title level={5}>原因分析</Typography.Title>
              {attributionRows(detail.attribution)}
            </div>

            {dim === "chunk" &&
              detail.reports?.chunk?.metrics?.per_doc &&
              detail.reports.chunk.metrics.per_doc.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <Typography.Title level={5}>
                    单文件评估明细（{detail.reports.chunk.metrics.per_doc.length}）
                  </Typography.Title>
                  {chunkDetailTable(
                    detail.reports.chunk.metrics.per_doc as ChunkDocEval[],
                  )}
                </div>
              )}

            {dimKeys.map((d) => (
              <div key={d} style={{ marginBottom: 12 }}>
                <Typography.Title level={5}>
                  {DIM_MAP[d].label}问题
                  {detail.reports?.[d]?.issues
                    ? `（${detail.reports[d]!.issues!.length} 条）`
                    : ""}
                </Typography.Title>
                {issueRows(detail.reports?.[d]?.issues)}
              </div>
            ))}
          </div>
        )}
      </ModoDrawer>
    </div>
  );
}
