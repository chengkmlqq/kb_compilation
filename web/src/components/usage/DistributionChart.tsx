"use client";

/** 分布统计图表（环形图 + 右侧伴随小表格）。
 *
 * 图1 知识库消耗分布（按 Token / 请求数切换）、图2 模型使用分布、
 * 图3 操作类型分布。环形图 hover 出 tooltip，点击图例可隐藏/显示扇区；
 * 伴随表格列：名称 | 请求数 | 输入Token | 输出Token | 缓存Token（无金额列）。
 */
import { useMemo } from "react";
import { Card, Segmented, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { formatTokens } from "./KpiCards";
import EChart from "./EChart";
import { PIE_COLORS } from "./KpiCards";

export interface UsageDistItem {
  key: string;
  name: string;
  requests: number;
  inputTokens: number;
  outputTokens: number;
  cacheTokens: number;
}

type Metric = "tokens" | "requests";

interface DistributionChartProps {
  title: string;
  data: UsageDistItem[];
  loading?: boolean;
  /** 是否展示「按 Token / 请求数」切换（仅知识库消耗分布图） */
  metricSwitch?: boolean;
  metric?: Metric;
  onMetricChange?: (m: Metric) => void;
  placeholder?: string;
}

function itemTokens(d: UsageDistItem): number {
  return d.inputTokens + d.outputTokens + d.cacheTokens;
}

function ChartInner({ data, metric, chartTitle }: { data: UsageDistItem[]; metric: Metric; chartTitle: string }) {
  const option = useMemo(() => {
    const total = data.reduce((acc, d) => acc + (metric === "tokens" ? itemTokens(d) : d.requests), 0);
    const base = {
      color: PIE_COLORS,
      tooltip: {
        trigger: "item" as const,
        formatter: (p: { name?: string; value?: unknown; percent?: number }) => {
          const name = p.name ?? "";
          const val = typeof p.value === "number" ? p.value : 0;
          const pct = p.percent ?? 0;
          return `${name}<br/>${metric === "tokens" ? formatTokens(val) : val}（${pct}%）`;
        },
      },
      legend: {
        type: "scroll" as const,
        orient: "vertical" as const,
        right: 4,
        top: "middle" as const,
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { fontSize: 12 },
      },
      series: [
        {
          name: chartTitle,
          type: "pie" as const,
          radius: ["46%", "70%"],
          center: ["36%", "50%"],
          avoidLabelOverlap: true,
          itemStyle: { borderRadius: 4, borderColor: "#fff", borderWidth: 1 },
          label: { show: false },
          emphasis: { label: { show: false } },
          data: data.map((d, i) => ({
            name: d.name || "未知",
            value: metric === "tokens" ? itemTokens(d) || 1 : d.requests,
            itemStyle: { color: PIE_COLORS[i % PIE_COLORS.length] },
          })),
        },
      ],
    };
    return total > 0 ? base : { ...base, title: { text: "暂无数据", left: "center", top: "middle", textStyle: { color: "#bbb", fontSize: 13, fontWeight: "normal" } } };
  }, [data, metric, chartTitle]);
  return <EChart option={option} height={230} />;
}

const columns: ColumnsType<UsageDistItem> = [
  { title: "名称", dataIndex: "name", ellipsis: true },
  { title: "请求数", dataIndex: "requests", width: 70, align: "right" },
  { title: "输入Token", dataIndex: "inputTokens", width: 88, align: "right", render: (v: number) => formatTokens(v) },
  { title: "输出Token", dataIndex: "outputTokens", width: 88, align: "right", render: (v: number) => formatTokens(v) },
  { title: "缓存Token", dataIndex: "cacheTokens", width: 88, align: "right", render: (v: number) => formatTokens(v) },
];

export default function DistributionChart({
  title,
  data,
  loading,
  metricSwitch,
  metric = "tokens",
  onMetricChange,
}: DistributionChartProps) {
  return (
    <Card
      size="small"
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span>{title}</span>
          {metricSwitch && (
            <Segmented
              size="small"
              value={metric}
              options={[
                { label: "按Token", value: "tokens" },
                { label: "按请求数", value: "requests" },
              ]}
              onChange={(v) => onMetricChange?.(v as Metric)}
            />
          )}
        </div>
      }
      styles={{ body: { paddingTop: 8 } }}
      style={{ height: "100%" }}
    >
      <ChartInner data={data} metric={metric} chartTitle={title} />
      <Table<UsageDistItem>
        rowKey="key"
        size="small"
        loading={loading}
        dataSource={data}
        columns={columns}
        pagination={{ pageSize: 5, showSizeChanger: false, size: "small" }}
        tableLayout="fixed"
        scroll={{ y: 198, x: "100%" }}
      />
    </Card>
  );
}