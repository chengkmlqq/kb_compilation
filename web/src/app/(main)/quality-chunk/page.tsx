"use client";

/**
 * 切片评估页（2026-10-21 质量评估拆分）。
 *
 * 单维度（dim=chunk）：评估文档切片质量——长度分布/截断/重复；走「切片评估」编排。
 * 列表按 dim=chunk 过滤。
 */
import QualityAssessPane from "@/components/quality/QualityAssessPane";

export default function QualityChunkPage() {
  return <QualityAssessPane dim="chunk" />;
}