"use client";

/** Token 使用趋势图（双 Y 轴折线）。
 *
 * 左轴 Token 数量：Input / Output / Cache Creation / Cache Read 四条实线；
 * 右轴百分比：Cache Hit Rate 一条虚线。hover tooltip、点击图例隐藏/显示数据线
 * 由 ECharts 原生支持（legend selectedMode 默认开启）。
 */
import { useMemo } from "react";
import { Card } from "antd";
import dayjs from "dayjs";
import EChart from "./EChart";
import { formatTokens } from "./KpiCards";

export interface UsageTrend {
  granularity: string;
  timestamps: string[];
  inputTokens: number[];
  outputTokens: number[];
  cacheReadTokens: number[];
  cacheCreationTokens: number[];
  cacheHitRate: number[];
}

const LINE_COLORS = ["#1677ff", "#52c41a", "#fa8c16", "#722ed1", "#ff4d4f"];

export default function TokenTrendChart({ trend }: { trend: UsageTrend | null }) {
  const option = useMemo(() => {
    const labels = (trend?.timestamps || []).map((t) => dayjs(t).format("MM-DD HH:00"));
    const t = trend;
    if (!t || labels.length === 0) {
      return {
        title: { text: "暂无数据", left: "center", top: "middle", textStyle: { color: "#bbb", fontSize: 13, fontWeight: "normal" as const } },
        xAxis: { type: "category" as const, data: [] },
        yAxis: [{ type: "value" as const }, { type: "value" as const, max: 100, axisLabel: { formatter: "{value}%" } }],
        series: [],
      };
    }
    return {
      color: LINE_COLORS,
      tooltip: {
        trigger: "axis" as const,
        formatter: (params: { seriesName?: string; value?: unknown; dataIndex?: number }[]) => {
          const first = params[0] || { dataIndex: 0 };
          const rows = params
            .map((p) => {
              const v = typeof p.value === "number" ? p.value : 0;
              return `${p.seriesName ?? ""}：${p.seriesName === "缓存命中率" ? `${v}%` : formatTokens(v)}`;
            })
            .join("<br/>");
          return `${labels[first.dataIndex ?? 0]}<br/>${rows}`;
        },
      },
      legend: {
        type: "scroll" as const,
        bottom: 0,
        itemWidth: 16,
        itemHeight: 8,
        textStyle: { fontSize: 12 },
      },
      grid: { left: 64, right: 56, top: 24, bottom: 34 },
      xAxis: {
        type: "category" as const,
        boundaryGap: false,
        data: labels,
        axisLabel: { fontSize: 11 },
      },
      yAxis: [
        { type: "value" as const, name: "Token", nameTextStyle: { fontSize: 11 }, axisLabel: { fontSize: 11, formatter: (v: number) => formatTokens(v) } },
        { type: "value" as const, name: "命中率", max: 100, nameTextStyle: { fontSize: 11 }, axisLabel: { fontSize: 11, formatter: "{value}%" } },
      ],
      series: [
        { name: "Input", type: "line" as const, data: t.inputTokens, smooth: true, showSymbol: false },
        { name: "Output", type: "line" as const, data: t.outputTokens, smooth: true, showSymbol: false },
        { name: "Cache Creation", type: "line" as const, data: t.cacheCreationTokens, smooth: true, showSymbol: false },
        { name: "Cache Read", type: "line" as const, data: t.cacheReadTokens, smooth: true, showSymbol: false },
        {
          name: "缓存命中率",
          type: "line" as const,
          yAxisIndex: 1,
          data: t.cacheHitRate.map((r) => Number((r * 100).toFixed(1))),
          smooth: true,
          showSymbol: false,
          lineStyle: { type: "dashed" as const, width: 2 },
          itemStyle: { color: LINE_COLORS[4] },
        },
      ],
    };
  }, [trend]);

  return (
    <Card size="small" title="Token 使用趋势" styles={{ body: { paddingTop: 8 } }}>
      <EChart option={option} height={300} />
    </Card>
  );
}
