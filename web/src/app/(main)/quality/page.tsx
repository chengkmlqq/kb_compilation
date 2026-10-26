"use client";

/**
 * wiki评估页（原质量评估页，2026-10-21 重构为公共面板的 all 维度形态）。
 *
 * 展示「质量评估-QA」编排（三维度合一：切片/wiki/图数据）产物；
 * 发起/列表/详情逻辑在 QualityAssessPane（dim="all"）。
 */
import QualityAssessPane from "@/components/quality/QualityAssessPane";

export default function QualityPage() {
  return <QualityAssessPane dim="all" />;
}