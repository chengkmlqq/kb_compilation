"use client";

/**
 * LLM 使用记录与性能监控看板（2026-10-20 补建页面）。
 *
 * 数据源：/api/v1/usage/*（埋点 chat/wiki_build/agent 三处），展示
 * 汇总 KPI + Token 趋势 + 分布（知识库/模型/操作）+ 明细分页。
 * 布局对齐 feedback-stats（统计卡 + 筛选栏 + 内容区），无任何金额口径。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Button, DatePicker, Empty, Form, Select, Space, Table, Tag } from "antd";
import {
  ApiOutlined,
  CalendarOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  LineChartOutlined,
  ReloadOutlined,
  RiseOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import dayjs, { type Dayjs } from "dayjs";
import {
  apiUsageDetails,
  apiUsageDistribution,
  apiUsageOptions,
  apiUsageSummary,
  apiUsageTrend,
  type UsageDetailItem,
  type UsageDistItem,
  type UsageOptions,
  type UsagePreset,
  type UsageSummary,
  type UsageTrend,
} from "@/lib/api";
import KpiCards, { formatTokens } from "@/components/usage/KpiCards";
import TokenTrendChart from "@/components/usage/TokenTrendChart";
import DistributionChart, { type Metric } from "@/components/usage/DistributionChart";
import ModoPagination from "@/components/biz/modo-pagination";

interface RangeState {
  preset: UsagePreset;
  start: Dayjs | null;
  end: Dayjs | null;
}

const PRESET_OPTIONS = [
  { label: "近24小时", value: "24h" },
  { label: "近7天", value: "7d" },
  { label: "近30天", value: "30d" },
  { label: "自定义", value: "custom" },
];

const STATUS_META: Record<string, { label: string; color: string }> = {
  success: { label: "成功", color: "green" },
  failed: { label: "失败", color: "red" },
};

const SOURCE_META: Record<string, { label: string; color: string }> = {
  chat: { label: "智能问答", color: "blue" },
  wiki_build: { label: "知识构建", color: "purple" },
  agent: { label: "智能体", color: "cyan" },
};

export default function UsagePage() {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [trend, setTrend] = useState<UsageTrend | null>(null);
  const [kbDist, setKbDist] = useState<UsageDistItem[]>([]);
  const [modelDist, setModelDist] = useState<UsageDistItem[]>([]);
  const [operDist, setOperDist] = useState<UsageDistItem[]>([]);
  const [rows, setRows] = useState<UsageDetailItem[]>([]);
  const [options, setOptions] = useState<UsageOptions | null>(null);
  const [loading, setLoading] = useState(false);
  const [tableLoading, setTableLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [kbMetric, setKbMetric] = useState<Metric>("tokens");

  const [range, setRange] = useState<RangeState>({ preset: "7d", start: null, end: null });
  const [kbId, setKbId] = useState<string | undefined>(undefined);
  const [model, setModel] = useState<string | undefined>(undefined);
  const [operation, setOperation] = useState<string | undefined>(undefined);
  const [status, setStatus] = useState<string | undefined>(undefined);

  const [searchForm] = Form.useForm<{ preset?: UsagePreset; startDate?: Dayjs; endDate?: Dayjs }>();

  // 筛选下拉选项（知识库/模型/操作）
  useEffect(() => {
    void apiUsageOptions(30).then((res) => {
      if (res.success) setOptions(res.data || null);
    });
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const p = {
        preset: range.preset,
        ...(range.preset === "custom" && range.start && range.end
          ? { start: range.start.format("YYYY-MM-DD HH:mm:ss"), end: range.end.format("YYYY-MM-DD HH:mm:ss") }
          : {}),
      };
      const [sRes, tRes, kbRes, modelRes, operRes] = await Promise.all([
        apiUsageSummary(p, kbId, model, operation),
        apiUsageTrend(range.preset === "24h" ? "hour" : "day", p, kbId, model, operation),
        apiUsageDistribution("kb", p, kbId, model, operation),
        apiUsageDistribution("model", p, kbId, model, operation),
        apiUsageDistribution("operation", p, kbId, model, operation),
      ]);
      if (sRes.success) setSummary(sRes.data || null);
      if (tRes.success) setTrend(tRes.data || null);
      if (kbRes.success) setKbDist(kbRes.data || []);
      if (modelRes.success) setModelDist(modelRes.data || []);
      if (operRes.success) setOperDist(operRes.data || []);
    } finally {
      setLoading(false);
    }
  }, [range, kbId, model, operation]);

  const loadList = useCallback(async () => {
    setTableLoading(true);
    try {
      const p = {
        preset: range.preset,
        ...(range.preset === "custom" && range.start && range.end
          ? { start: range.start.format("YYYY-MM-DD HH:mm:ss"), end: range.end.format("YYYY-MM-DD HH:mm:ss") }
          : {}),
      };
      const res = await apiUsageDetails(p, {
        page,
        pageSize,
        status: status || "",
        kbId,
        model,
        operation,
      });
      if (res.success) {
        setRows(res.data?.items || []);
        setTotal(res.data?.total ?? 0);
      }
    } finally {
      setTableLoading(false);
    }
  }, [page, pageSize, range, kbId, model, operation, status]);

  useEffect(() => {
    void loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  useEffect(() => {
    void loadData();
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kbId, model, operation]);

  useEffect(() => {
    void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, status]);

  const doSearch = (v: { preset?: UsagePreset; startDate?: Dayjs; endDate?: Dayjs }) => {
    const preset = v.preset || "7d";
    setRange({
      preset,
      start: preset === "custom" ? v.startDate ?? null : null,
      end: preset === "custom" ? v.endDate ?? null : null,
    });
    setPage(1);
  };

  const doReset = () => {
    searchForm.resetFields();
    setRange({ preset: "7d", start: null, end: null });
    setKbId(undefined);
    setModel(undefined);
    setOperation(undefined);
    setStatus(undefined);
    setPage(1);
  };

  const statCards: Array<{
    key: string;
    label: string;
    value: ReactNode;
    icon: ReactNode;
    color: string;
    bg: string;
  }> = useMemo(
    () => [
      {
        key: "requests",
        label: "总请求数",
        value: summary?.totalRequests ?? 0,
        icon: <ThunderboltOutlined style={{ fontSize: 22 }} />,
        color: "#1677ff",
        bg: "#E6F4FF",
      },
      {
        key: "tokens",
        label: "总 Token",
        value: formatTokens(summary?.totalTokens),
        icon: <LineChartOutlined style={{ fontSize: 22 }} />,
        color: "#722ed1",
        bg: "#F9F0FF",
      },
      {
        key: "latency",
        label: "平均耗时",
        value: summary?.avgLatency?.total != null ? `${summary.avgLatency.total.toFixed(0)}ms` : "—",
        icon: <RiseOutlined style={{ fontSize: 22 }} />,
        color: "#389e0d",
        bg: "#F6FFED",
      },
      {
        key: "err",
        label: "错误率",
        value: summary && summary.totalRequests ? `${(summary.errorRate * 100).toFixed(1)}%` : "0.0%",
        icon: <ApiOutlined style={{ fontSize: 22 }} />,
        color: "#cf1322",
        bg: "#FFF1F0",
      },
    ],
    [summary]
  );

  return (
    <div
      className="usage-page"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - var(--app-header-h, 48px))",
        minHeight: 0,
        background: "#EFF4F9",
        padding: 10,
        overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0, overflow: "hidden", background: "#F5F7FA" }}>
        {/* KPI 卡（对齐 feedback-stats：4 列 grid） */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 16, flexShrink: 0 }}>
          {statCards.map((stat) => (
            <div
              key={stat.key}
              className="modo-stat-card"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: 18,
                background: "#fff",
                borderRadius: 8,
                border: "1px solid #E3E9EF",
                transition: "box-shadow 0.25s",
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                <span style={{ color: "#79879C", fontSize: 13, marginBottom: 8, fontWeight: 500 }}>{stat.label}</span>
                <span
                  style={{
                    fontSize: 26,
                    fontWeight: 700,
                    lineHeight: "26px",
                    color: stat.color,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {stat.value}
                </span>
              </div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 48,
                  height: 48,
                  borderRadius: 12,
                  background: stat.bg,
                  color: stat.color,
                  opacity: 0.75,
                  flexShrink: 0,
                }}
              >
                {stat.icon}
              </div>
            </div>
          ))}
        </div>

        {/* 筛选栏 */}
        <div
          style={{
            background: "#fff",
            borderRadius: 8,
            border: "1px solid #E3E9EF",
            padding: "14px 16px",
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 16,
            flexWrap: "wrap",
            flexShrink: 0,
          }}
        >
          <Form
            form={searchForm}
            layout="inline"
            onFinish={doSearch}
            initialValues={{ preset: "7d" }}
            style={{ gap: 12, rowGap: 8 }}
          >
            <Form.Item name="preset" label="时间范围" style={{ marginBottom: 0 }}>
              <Select style={{ width: 130 }} options={PRESET_OPTIONS} />
            </Form.Item>
            <Form.Item noStyle shouldUpdate>
              {({ getFieldValue }) =>
                getFieldValue("preset") === "custom" ? (
                  <>
                    <Form.Item name="startDate" style={{ marginBottom: 0 }}>
                      <DatePicker showTime placeholder="开始时间" />
                    </Form.Item>
                    <Form.Item name="endDate" style={{ marginBottom: 0 }}>
                      <DatePicker showTime placeholder="结束时间" />
                    </Form.Item>
                  </>
                ) : null
              }
            </Form.Item>
            <Form.Item label="知识库" style={{ marginBottom: 0 }}>
              <Select
                allowClear
                placeholder="全部"
                style={{ width: 160 }}
                showSearch
                optionFilterProp="label"
                value={kbId}
                onChange={setKbId}
                options={(options?.knowledgeBases || []).map((kb) => ({ label: kb.name, value: kb.id }))}
              />
            </Form.Item>
            <Form.Item label="模型" style={{ marginBottom: 0 }}>
              <Select
                allowClear
                placeholder="全部"
                style={{ width: 170 }}
                showSearch
                optionFilterProp="label"
                value={model}
                onChange={setModel}
                options={(options?.models || []).map((m) => ({ label: m, value: m }))}
              />
            </Form.Item>
            <Form.Item label="操作" style={{ marginBottom: 0 }}>
              <Select
                allowClear
                placeholder="全部"
                style={{ width: 170 }}
                showSearch
                optionFilterProp="label"
                value={operation}
                onChange={setOperation}
                options={(options?.operations || []).map((o) => ({ label: o.label, value: o.value }))}
              />
            </Form.Item>
            <Form.Item label="状态" style={{ marginBottom: 0 }}>
              <Select
                allowClear
                placeholder="全部"
                style={{ width: 120 }}
                value={status}
                onChange={setStatus}
                options={[
                  { label: "成功", value: "success" },
                  { label: "失败", value: "failed" },
                ]}
              />
            </Form.Item>
          </Form>
          <Space>
            <Button type="primary" icon={<SearchOutlined />} onClick={() => searchForm.submit()}>
              查询
            </Button>
            <Button icon={<ReloadOutlined />} onClick={doReset}>
              重置
            </Button>
          </Space>
        </div>

        {/* 图表 + 明细区：左上图表区与右下明细表堆叠，滚动区吃满剩余 */}
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 16, overflowY: "auto", paddingBottom: 4 }}>
          {/* 趋势 + 分布组合行（flexShrink:0 防止内容超高时被压塌导致子元素溢出重叠）。
              2026-10-21：两列改为 1fr 1fr，与第二行模型/操作分布等宽 */ }
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, flexShrink: 0 }}>
            <div style={{ background: "#fff", borderRadius: 8, border: "1px solid #E3E9EF", padding: 16, display: "flex", flexDirection: "column", minHeight: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: "#242E43" }}>Token 使用趋势</div>
              <div style={{ flex: 1, minHeight: 0 }}>
                <TokenTrendChart trend={trend} title="" height="100%" />
              </div>
            </div>
            <div style={{ background: "#fff", borderRadius: 8, border: "1px solid #E3E9EF", padding: 16 }}>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: "#242E43" }}>知识库消耗分布</div>
              <DistributionChart
                title=""
                data={kbDist}
                loading={loading}
                metricSwitch
                metric={kbMetric}
                onMetricChange={setKbMetric}
                placeholder="暂无分布数据"
              />
            </div>
          </div>
          {/* 第二行分布：模型 + 操作 */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, flexShrink: 0 }}>
            <DistributionChart title="模型使用分布" data={modelDist} loading={loading} placeholder="暂无分布数据" />
            <DistributionChart title="操作类型分布" data={operDist} loading={loading} placeholder="暂无分布数据" />
          </div>
          {/* 明细表（2026-10-21：去掉固定高度限制，表格按内容自然撑开，页面滚动查看） */}
          <div
            style={{
              flexShrink: 0,
              display: "flex",
              flexDirection: "column",
              background: "#fff",
              borderRadius: 8,
              border: "1px solid #E3E9EF",
            }}
          >
            <div style={{ padding: "14px 16px 0" }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: "#242E43" }}>调用明细</span>
            </div>
            <div style={{ padding: "8px 16px 0" }}>
              <Table<UsageDetailItem>
                rowKey="id"
                size="middle"
                loading={tableLoading}
                dataSource={rows}
                pagination={false}
                scroll={{ x: 1280 }}
                locale={{ emptyText: <Empty description="暂无调用记录（LLM 提问/构建产生调用后在此展示）" /> }}
                columns={[
                  {
                    title: "时间",
                    dataIndex: "ts",
                    width: 160,
                    render: (v: string | null) => (v ? dayjs(v).format("YYYY-MM-DD HH:mm:ss") : "-"),
                  },
                  {
                    title: "来源",
                    dataIndex: "source",
                    width: 110,
                    render: (v: string) => {
                      const meta = SOURCE_META[v] || { label: v || "-", color: "default" };
                      return <Tag color={meta.color}>{meta.label}</Tag>;
                    },
                  },
                  {
                    title: "知识库",
                    dataIndex: "kbName",
                    width: 140,
                    ellipsis: true,
                    render: (v: string) => v || "-",
                  },
                  {
                    title: "文档",
                    dataIndex: "docName",
                    width: 160,
                    ellipsis: true,
                    render: (v: string) => v || "-",
                  },
                  {
                    title: "模型",
                    dataIndex: "model",
                    width: 150,
                    ellipsis: true,
                    render: (v: string) => v || "-",
                  },
                  {
                    title: "操作",
                    dataIndex: "operationLabel",
                    width: 140,
                    ellipsis: true,
                    render: (v: string) => v || "-",
                  },
                  {
                    title: "输入 Token",
                    dataIndex: "inputTokens",
                    width: 100,
                    align: "right" as const,
                    render: (v: number) => formatTokens(v),
                  },
                  {
                    title: "输出 Token",
                    dataIndex: "outputTokens",
                    width: 100,
                    align: "right" as const,
                    render: (v: number) => formatTokens(v),
                  },
                  {
                    title: "缓存 Token",
                    dataIndex: "cacheTokens",
                    width: 100,
                    align: "right" as const,
                    render: (v: number) => formatTokens(v),
                  },
                  {
                    title: "耗时",
                    dataIndex: "latency",
                    width: 110,
                    align: "right" as const,
                    render: (v: { ttft: number | null; total: number | null }) =>
                      v?.total != null ? `${v.total.toFixed(0)}ms` : "-",
                  },
                  {
                    title: "状态",
                    dataIndex: "status",
                    width: 90,
                    render: (v: string) => {
                      const meta = STATUS_META[v] || { label: v || "-", color: "default" };
                      return <Tag color={meta.color}>{meta.label}</Tag>;
                    },
                  },
                  {
                    title: "错误",
                    dataIndex: "errorMessage",
                    ellipsis: true,
                    width: 200,
                    render: (v: string) => v || "-",
                  },
                ]}
              />
            </div>
            <div style={{ padding: "10px 16px", borderTop: "1px solid #F0F2F5", display: "flex", justifyContent: "flex-end" }}>
              <ModoPagination
                current={page}
                pageSize={pageSize}
                total={total}
                onChange={(p, ps) => {
                  setPage(p);
                  setPageSize(ps);
                }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}