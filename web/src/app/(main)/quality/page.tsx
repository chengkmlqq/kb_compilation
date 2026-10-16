"use client";

/**
 * 质量评估页（2026-10-14 v1）—— 文档构建质量评估。
 *
 * 三大能力：
 * 1. 发起评估（单文档 / 批量 / 整库）：调 /api/v1/quality/runs（执行「质量评估-QA」编排）
 * 2. 任务列表：kb_quality_run 分页（状态 / 三维度得分 / 问题数 / 时间），顶部按知识库+状态过滤
 * 3. 详情抽屉：三维度（切片/wiki/图数据）健康分 + 问题清单 + 规则归因建议
 *
 * 布局（2026-10-14）：对齐整体 UI —— 外层定高不滚动 + ModoTabs（首 tab「质量评估」= 页面标题 + 列表）
 * + Card 内「筛选固定 / 表格吃满剩余高度 / 分页钉底」；抽屉/弹窗统一 ModoDrawer/ModoModal。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { App, Button, Card, Empty, Form, Select, Space, Tag, Typography } from "antd";
import {
  ApiOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
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
  apiListQualityRuns,
  apiListKbs,
  DocItem,
  KbItem,
  QualityAttribution,
  QualityIssue,
  QualityRunDetail,
  QualityRunItem,
} from "@/lib/api";

const { Text } = Typography;

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
  chunk: { label: "切片质量", icon: <FileTextOutlined /> },
  wiki: { label: "Wiki质量", icon: <DatabaseOutlined /> },
  graph: { label: "图数据质量", icon: <ApiOutlined /> },
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

const ISSUE_TYPE_LABEL: Record<string, string> = {
  too_many_short_chunks: "切片过碎",
  too_many_long_chunks: "切片过长",
  truncated_chunks: "切片截断",
  duplicate_chunks: "切片重复",
  no_chunks: "无切片",
  empty_content: "空页面",
  orphan: "孤立页",
  broken_links: "断链",
  llm_placeholder: "LLM占位",
  missing_source: "缺出处",
  no_folder: "未挂目录",
  no_pages: "无页面",
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

export default function QualityPage() {
  const { message, modal } = App.useApp();
  const [items, setItems] = useState<QualityRunItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  // 筛选：applied 为「已提交」条件（驱动查询），表单值由 searchForm 托管
  const [applied, setApplied] = useState<{ kb_id?: string; status?: string }>({});
  const [searchForm] = Form.useForm<{ kb_id?: string; status?: string }>();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [detail, setDetail] = useState<QualityRunDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  // 删除中行 id（防重复提交；删除动作带 loading）
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [launchOpen, setLaunchOpen] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [launchForm] = Form.useForm<{ kb_id: string; mode: string; doc_ids: string[] }>();
  // 发起评估弹窗：文档下拉（随知识库联动 + 输入远程搜索，参照 ExecuteInputModal 模式）
  const [docOptions, setDocOptions] = useState<{ value: string; label: string }[]>([]);
  const [docLoading, setDocLoading] = useState(false);
  const docSearchRef = useRef("");

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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListQualityRuns({
        kb_id: applied.kb_id || undefined,
        status: applied.status || undefined,
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
  }, [applied, page, pageSize, message]);

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
      await apiLaunchQualityRun({ kb_id: v.kb_id, doc_ids: docIds, scope });
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
            // 详情抽屉正打开该任务时一并关闭
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
      title: "三维度得分",
      dataIndex: "scores",
      width: 220,
      render: (s: QualityRunItem["scores"]) => (
        <Space size={6}>
          {(["chunk", "wiki", "graph"] as const).map((d) => (
            <Tag key={d} color={scoreColor(s?.[d])} style={{ marginInlineEnd: 0 }}>
              {DIM_MAP[d].label.replace("质量", "")}={s?.[d] ?? "-"}
            </Tag>
          ))}
        </Space>
      ),
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

  // 列表区（首个选项卡内容）：筛选固定 + 表格吃满剩余高度 + 分页钉底
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
      </Card>
    </div>
  );

  return (
    // 2026-10-14: 对齐整体 UI —— 外层定高不滚动，ModoTabs 首 tab「质量评估」= 页面标题 + 列表。
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
              发起评估
            </Button>
          </Space>
        }
        items={[{ key: "home", label: "质量评估", children: listPane }]}
      />

      {/* 发起评估弹窗 */}
      <ModoModal
        title="发起质量评估"
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

      {/* 详情抽屉 */}
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

            {/* 三维度得分 */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 1fr",
                gap: 12,
                marginBottom: 16,
              }}
            >
              {(["chunk", "wiki", "graph"] as const).map((d) => (
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
                    detail.reports?.[d]?.metrics?.final_score ?? detail.scores?.[d],
                  )}
                  <div style={{ marginBottom: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      程序分{" "}
                      <Tag color={scoreColor(detail.reports?.[d]?.metrics?.health_score)}>
                        {detail.reports?.[d]?.metrics?.health_score ?? "-"}
                      </Tag>
                      {d !== "chunk" && (
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
                      {(() => {
                        const m = detail.reports![d]!.metrics!;
                        const parts: string[] = [];
                        if (d === "chunk") {
                          parts.push(`切片 ${m.chunks ?? "-"}`);
                          parts.push(`过短 ${Math.round((m.short_ratio as number ?? 0) * 100)}%`);
                          parts.push(`重复 ${m.dup ?? 0}`);
                        } else if (d === "wiki") {
                          parts.push(`页面 ${m.pages ?? "-"}`);
                          parts.push(`孤立 ${m.orphan ?? 0}`);
                          parts.push(`断链 ${m.broken_links ?? 0}`);
                          if (m.semantic === "single_doc") parts.push(`语义评估 ${m.pages_eval ?? 0} 页`);
                          if (m.semantic === "doc_compare") parts.push(`原文对比 ${m.error_pages ?? 0}/${m.pages_cmp ?? 0} 错误`);
                        } else {
                          parts.push(`节点 ${m.nodes ?? "-"}`);
                          parts.push(`边 ${m.rels ?? 0}`);
                          parts.push(`哑节点 ${m.dumb_nodes ?? 0}`);
                          if (m.semantic === "graph_3layer") parts.push(`语义抽样 ${m.nodes_sampled ?? 0}点/${m.rels_sampled ?? 0}边`);
                        }
                        return parts.join(" · ");
                      })()}
                    </Text>
                  )}
                </div>
              ))}
            </div>

            {/* 归因建议 */}
            <div style={{ marginBottom: 16 }}>
              <Typography.Title level={5}>原因分析</Typography.Title>
              {attributionRows(detail.attribution)}
            </div>

            {/* 各维度问题 */}
            {(["chunk", "wiki", "graph"] as const).map((d) => (
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
