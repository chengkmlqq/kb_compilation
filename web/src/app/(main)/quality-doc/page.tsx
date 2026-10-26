"use client";

/**
 * 文档转换评估页（2026-10-21 质量评估拆分）。
 *
 * 单维度（dim=doc）：评估文档解析/转换质量——parse_state 分布、失败原因、
 * 空转换产物；走「文档转换评估」编排。列表按 dim=doc 过滤。
 */
import QualityAssessPane from "@/components/quality/QualityAssessPane";

export default function QualityDocPage() {
  return <QualityAssessPane dim="doc" />;
}