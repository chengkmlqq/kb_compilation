"use client";

/**
 * 图数据评估页（2026-10-21 质量评估拆分）。
 *
 * 单维度（dim=graph）：评估 Neo4j 图数据质量——哑节点/边类型/可回溯性；走「图数据评估」编排。
 * 列表按 dim=graph 过滤。
 */
import QualityAssessPane from "@/components/quality/QualityAssessPane";

export default function QualityGraphPage() {
  return <QualityAssessPane dim="graph" />;
}