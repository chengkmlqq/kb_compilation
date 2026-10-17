"use client";

/** EChart — 极简 ECharts React 包装（init/setOption/resize/dispose）。
 *
 * 用 echarts 原生 API（不引 echarts-for-react，避免 React 19 兼容风险）：
 * ResizeObserver 跟随容器尺寸自动 resize，option 变化 notMerge 重设。
 */
import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

interface EChartProps {
  /** echarts option。EChartsOption 为深度联合类型，动态拼装的字面量
   * 直接赋值常触发 TS2322（tooltip formatter 逆变/可选字段联合展开），
   * 放宽为 Record 分支 + setOption 处断言——运行时 option 结构由实现保证。
   */
  option: EChartsOption | Record<string, unknown>;
  height?: number | string;
  /** 图表点击回调（下钻用，可空） */
  onChartClick?: (params: unknown) => void;
}

export default function EChart({ option, height = 280, onChartClick }: EChartProps) {
  const holderRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = holderRef.current;
    if (!el) return;
    const chart = echarts.init(el);
    chartRef.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option as EChartsOption, { notMerge: true });
  }, [option]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onChartClick) return;
    chart.on("click", onChartClick);
    return () => {
      chart.off("click", onChartClick);
    };
  }, [onChartClick]);

  return <div ref={holderRef} style={{ width: "100%", height }} />;
}
