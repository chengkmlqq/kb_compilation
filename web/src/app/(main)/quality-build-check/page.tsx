"use client";

/**
 * 构建任务完成检查页（2026-10-22，质量评估目录「构建任务检查」子菜单）。
 *
 * 目的：任务监控页把构建链任务（文档解析/摘要编排/wiki构建编排/题材分析编排）
 * 显示为「成功」，但历史出现过多类「假成功」（ORCH job 状态未回写、run 成功但
 * 节点失败、run 成功但产物缺失）。本页把这些任务逐条拉出做真实性校验：
 *   1. 状态一致性：页面显示状态 vs 真实状态（run.status / parse_state）
 *   2. 节点一致性：run success 但节点 failed/skipped（内部矛盾）
 *   3. 产物验证：摘要落库 / wiki 页产出 / 领域编排选定
 *   4. 僵尸检测：queued/running 长时间未推进
 * 列表行直接给检查结论（真实完成/疑似假成功/失败/进行中/疑似卡死/无法判定）。
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Empty, Form, Select, Space, Tag, Tooltip, Typography } from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  FileDoneOutlined,
  PauseCircleOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  SafetyOutlined,
  SyncOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import { ModoTable } from "@/components/biz/modo-table";
import {
  apiBuildCheckOverview,
  apiGetBuildCheckTask,
  apiListBuildCheckTasks,
  apiListKbs,
  BuildCheckDetail,
  BuildCheckItem,
  BuildCheckOverview,
  BuildCheckStep,
  KbItem,
} from "@/lib/api";

const { Text } = Typography;

const RESULT_META: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  ok: { label: "真实完成", color: "success", icon: <CheckCircleOutlined /> },
  fake_success: { label: "疑似假成功", color: "warning", icon: <WarningOutlined /> },
  failed: { label: "失败", color: "error", icon: <CloseCircleOutlined /> },
  running: { label: "进行中", color: "processing", icon: <SyncOutlined /> },
  stale: { label: "疑似卡死", color: "volcano", icon: <PauseCircleOutlined /> },
  unknown: { label: "无法判定", color: "default", icon: <QuestionCircleOutlined /> },
};

const KIND_OPTIONS = [
  { label: "文档解析", value: "doc_parse" },
  { label: "摘要生成", value: "summary" },
  { label: "Wiki构建", value: "build" },
  { label: "题材分析", value: "domain_resolve" },
  { label: "无法判定", value: "unknown" },
];

const STEP_STATUS_META: Record<string, { label: string; color: string }> = {
  success: { label: "成功", color: "success" },
  failed: { label: "失败", color: "error" },
  skipped: { label: "跳过", color: "warning" },
  running: { label: "执行中", color: "processing" },
};

function formatTime(value?: string | null) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", { hour12: false });
}

/** 顶部统计卡（仿任务监控 job-stats 形态；loading 时数值占位“-”） */
function StatCards({ overview, loading }: { overview: BuildCheckOverview | null; loading?: boolean }) {
  const cards: Array<{ key: string; label: string; icon: React.ReactNode; color: string; bg: string; value: number }> = [
    { key: "total", label: "构建任务", icon: <FileDoneOutlined style={{ fontSize: 22 }} />, color: "#242E43", bg: "#F0F2F5", value: overview?.total ?? 0 },
    { key: "ok", label: "真实完成", icon: <CheckCircleOutlined style={{ fontSize: 22 }} />, color: "#389e0d", bg: "#F6FFED", value: overview?.by_result?.ok ?? 0 },
    { key: "fake_success", label: "疑似假成功", icon: <WarningOutlined style={{ fontSize: 22 }} />, color: "#d48806", bg: "#FFFBE6", value: overview?.by_result?.fake_success ?? 0 },
    { key: "failed", label: "失败", icon: <CloseCircleOutlined style={{ fontSize: 22 }} />, color: "#cf1322", bg: "#FFF1F0", value: overview?.by_result?.failed ?? 0 },
    { key: "stale", label: "疑似卡死", icon: <PauseCircleOutlined style={{ fontSize: 22 }} />, color: "#fa541c", bg: "#FFF2E8", value: overview?.by_result?.stale ?? 0 },
    { key: "running", label: "进行中", icon: <SyncOutlined style={{ fontSize: 22 }} />, color: "#1677ff", bg: "#E6F4FF", value: overview?.by_result?.running ?? 0 },
  ];
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(6, 1fr)",
        gap: 16,
        marginBottom: 16,
      }}
    >
      {cards.map((c) => (
        <div
          key={c.key}
          style={{
            background: "#fff",
            border: "1px solid #E3E9EF",
            borderRadius: 8,
            padding: "14px 16px",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: 8,
              background: c.bg,
              color: c.color,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            {c.icon}
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 22, fontWeight: 600, lineHeight: 1.2, color: "#242E43" }}>
              {loading ? "-" : c.value}
            </div>
            <div style={{ fontSize: 12, color: "#8C95A5" }}>{c.label}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function QualityBuildCheckPage() {
  const { message } = App.useApp();
  const [items, setItems] = useState<BuildCheckItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);
  const [overview, setOverview] = useState<BuildCheckOverview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [applied, setApplied] = useState<{ kb_id?: string; kind?: string; result?: string }>({});
  const [searchForm] = Form.useForm<{ kb_id?: string; kind?: string; result?: string }>();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [kbsLoading, setKbsLoading] = useState(false);
  const [detail, setDetail] = useState<BuildCheckDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListBuildCheckTasks({
        kb_id: applied.kb_id || undefined,
        kind: applied.kind || undefined,
        result: applied.result || undefined,
        page,
        page_size: pageSize,
      });
      setItems(res.data?.items ?? []);
      setTotal(res.data?.total ?? 0);
    } catch (e) {
      message.error(`加载失败: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [applied, page, pageSize, message]);

  const loadOverview = useCallback(async () => {
    setOverviewLoading(true);
    try {
      const res = await apiBuildCheckOverview();
      setOverview(res.data ?? null);
    } catch {
      setOverview(null);
    } finally {
      setOverviewLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadOverview();
  }, [loadOverview, applied]);

  useEffect(() => {
    setKbsLoading(true);
    apiListKbs(1, 100)
      .then((res) => setKbs(res.data?.items ?? []))
      .catch(() => setKbs([]))
      .finally(() => setKbsLoading(false));
  }, []);

  const doSearch = (v: { kb_id?: string; kind?: string; result?: string }) => {
    setPage(1);
    setApplied({ kb_id: v.kb_id, kind: v.kind, result: v.result });
  };

  const doReset = () => {
    searchForm.resetFields();
    setPage(1);
    setApplied({});
  };

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetail(null);
    try {
      const res = await apiGetBuildCheckTask(id);
      setDetail(res.data ?? null);
    } catch (e) {
      message.error(`详情加载失败: ${String(e)}`);
    } finally {
      setDetailLoading(false);
    }
  };

  const columns: ColumnsType<BuildCheckItem> = [
    {
      title: "检查结果",
      dataIndex: "result",
      width: 130,
      render: (v: string, r) => {
        const m = RESULT_META[v] ?? RESULT_META.unknown;
        const bad = r.checks?.filter((c) => c.ok === false);
        const tip = bad?.length
          ? `异常检查点：${bad.map((c) => `${c.label}：${c.detail}`).join("；")}`
          : r.checks?.map((c) => `${c.label}：${c.detail}`).join("；");
        return (
          <Tooltip title={tip}>
            <Tag color={m.color} icon={m.icon} style={{ marginInlineEnd: 0 }}>
              {m.label}
            </Tag>
          </Tooltip>
        );
      },
    },
    {
      title: "任务",
      key: "task",
      width: 240,
      render: (_, r) => (
        <div style={{ minWidth: 0 }}>
          <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {r.tape_name || r.doc_name || r.id}
          </div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.tape_name ? (r.doc_name || r.id.slice(0, 20)) : r.id.slice(0, 20)}
          </Text>
        </div>
      ),
    },
    { title: "类型", dataIndex: "kind_label", width: 100 },
    {
      title: "知识库",
      dataIndex: "kb_name",
      width: 140,
      ellipsis: true,
      render: (v: string, r) => v || r.kb_id.slice(0, 8) + "…",
    },
    {
      title: "文档",
      dataIndex: "doc_name",
      width: 160,
      ellipsis: true,
      render: (v: string, r) => v || (r.doc_id ? r.doc_id.slice(0, 8) + "…" : "-"),
    },
    {
      title: "页面显示",
      dataIndex: "display_state",
      width: 100,
      render: (v: string) => (
        <Tag color={v === "SUCCESS" ? "success" : v === "FAILED" ? "error" : "processing"}>{v}</Tag>
      ),
    },
    {
      title: "真实状态",
      dataIndex: "real_state",
      width: 100,
      render: (v: string) => (
        <Tag
          color={v === "success" ? "success" : v === "failed" || v === "cancelled" ? "error" : "default"}
        >
          {v}
        </Tag>
      ),
    },
    {
      title: "产物",
      dataIndex: "artifact_summary",
      width: 200,
      ellipsis: true,
      render: (v: string, r) => (
        <Tooltip title={r.error || undefined}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {v || "-"}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "时间",
      dataIndex: "created_at",
      width: 150,
      render: formatTime,
    },
    {
      title: "操作",
      key: "op",
      width: 80,
      fixed: "right",
      render: (_, r) => (
        <ModoActionGroup
          maxCount={1}
          actions={[{ key: "detail", label: "详情", onClick: () => void openDetail(r.id) }]}
        />
      ),
    },
  ];

  const checkIcon = (ok: boolean | null) => {
    if (ok === true) return <CheckCircleOutlined style={{ color: "#389e0d" }} />;
    if (ok === false) return <CloseCircleOutlined style={{ color: "#cf1322" }} />;
    return <QuestionCircleOutlined style={{ color: "#8C95A5" }} />;
  };

  const listPane = (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
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
              style={{ width: 190 }}
              optionFilterProp="label"
              loading={kbsLoading}
              options={kbs.map((k) => ({ label: k.name, value: k.id }))}
            />
          </Form.Item>
          <Form.Item name="kind" label="类型">
            <Select allowClear placeholder="全部" style={{ width: 130 }} options={KIND_OPTIONS} />
          </Form.Item>
          <Form.Item name="result" label="检查结果">
            <Select
              allowClear
              placeholder="全部"
              style={{ width: 130 }}
              options={Object.entries(RESULT_META).map(([k, v]) => ({ label: v.label, value: k }))}
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

        <ModoTable<BuildCheckItem>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={items}
          loading={loading}
          scroll={{ x: 1360 }}
          locale={{ emptyText: <Empty description="暂无构建任务" /> }}
          containerStyle={{ flex: 1, minHeight: 0 }}
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
      <StatCards overview={overview} loading={overviewLoading} />
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey="home"
        tabBarExtraContent={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                void load();
                void loadOverview();
              }}
            >
              刷新
            </Button>
          </Space>
        }
        items={[
          {
            key: "home",
            label: (
              <>
                <SafetyOutlined /> 构建任务检查
              </>
            ),
            children: listPane,
          },
        ]}
      />

      <ModoDrawer
        title="构建任务检查详情"
        width={860}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        showFooter={false}
        loading={detailLoading}
      >
        {detail && (
          <div>
            <Space size={8} wrap style={{ marginBottom: 12 }}>
              <Tag color={RESULT_META[detail.result]?.color ?? "default"} icon={RESULT_META[detail.result]?.icon}>
                {detail.result_label}
              </Tag>
              <Tag>{detail.kind_label}</Tag>
              <Tag>页面显示 {detail.display_state}</Tag>
              <Tag>真实 {detail.real_state}</Tag>
              {detail.env && <Tag color={detail.env === "prod" ? "blue" : "purple"}>{detail.env}</Tag>}
              <Text type="secondary">{formatTime(detail.created_at)}</Text>
            </Space>
            <div style={{ marginBottom: 12 }}>
              <Text strong>{detail.tape_name || detail.doc_name || detail.id}</Text>
              {detail.kb_name && (
                <Text type="secondary" style={{ marginLeft: 8 }}>
                  知识库：{detail.kb_name}
                </Text>
              )}
              {detail.doc_name && (
                <Text type="secondary" style={{ marginLeft: 8 }}>
                  文档：{detail.doc_name}
                </Text>
              )}
            </div>
            {detail.error && (
              <Text type="danger" style={{ display: "block", marginBottom: 12 }}>
                {detail.error}
              </Text>
            )}

            <Typography.Title level={5}>检查项</Typography.Title>
            <div style={{ marginBottom: 16 }}>
              {detail.checks?.map((c) => (
                <div
                  key={c.key}
                  style={{
                    display: "flex",
                    gap: 8,
                    alignItems: "flex-start",
                    padding: "6px 0",
                    borderBottom: "1px dashed #F0F2F5",
                  }}
                >
                  <span style={{ marginTop: 4 }}>{checkIcon(c.ok)}</span>
                  <div>
                    <Text strong style={{ marginRight: 8 }}>
                      {c.label}
                    </Text>
                    <Text type="secondary">{c.detail}</Text>
                  </div>
                </div>
              ))}
            </div>

            {detail.has_run && (
              <>
                <Typography.Title level={5}>编排节点执行记录（{detail.steps?.length ?? 0}）</Typography.Title>
                <ModoTable<BuildCheckStep>
                  rowKey={(s) => `${s.step_id ?? ""}-${s.round ?? 0}`}
                  size="small"
                  columns={[
                    { title: "节点", dataIndex: "step_label", ellipsis: true },
                    { title: "组件", dataIndex: "step_inst", width: 110 },
                    {
                      title: "状态",
                      dataIndex: "status",
                      width: 90,
                      render: (v: string) => {
                        const m = STEP_STATUS_META[v] ?? { label: v ?? "?", color: "default" };
                        return <Tag color={m.color}>{m.label}</Tag>;
                      },
                    },
                    {
                      title: "耗时",
                      dataIndex: "duration_ms",
                      width: 90,
                      render: (v?: number) => (v == null ? "-" : `${(v / 1000).toFixed(1)}s`),
                    },
                    {
                      title: "错误",
                      dataIndex: "error",
                      ellipsis: true,
                      render: (v: string) => v || "-",
                    },
                  ]}
                  dataSource={detail.steps ?? []}
                  containerStyle={{ marginBottom: 8 }}
                />
              </>
            )}
          </div>
        )}
      </ModoDrawer>
    </div>
  );
}