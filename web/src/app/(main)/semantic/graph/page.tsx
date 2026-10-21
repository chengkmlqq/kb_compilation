"use client";

/**
 * 语义层 · 知识图谱（2026-10-21）
 *
 * 照抄知识库 Wiki 关系图页面（WikiGraphView）：按 slug/关键词浏览 wiki 页的
 * 关系图（ego 聚焦/全库概览），数据用「电信语义层」知识库的 wiki 关系数据。
 *
 * 绑定知识库 id 由参数管理表（modo_dim，SEMANTIC_LAYER 组）读取；
 * 参数缺失时自动按「电信语义层」库名发现。
 */
import { Spin, Typography } from "antd";
import WikiGraphView from "@/components/WikiGraphView";
import { useSemanticBinding } from "../_lib/use-semantic-binding";

export default function SemanticGraphPage() {
  const { kbId, loading, error } = useSemanticBinding();

  if (loading) {
    return (
      <div style={{ padding: 10, height: "100%" }}>
        <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin tip="加载语义层绑定…" />
        </div>
      </div>
    );
  }

  if (!kbId) {
    return (
      <div style={{ padding: 10, height: "100%" }}>
        <div
          style={{
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#fff",
            borderRadius: 8,
          }}
        >
          <Typography.Text type="warning">
            {error || "未绑定知识库，请在「参数管理」配置 SEMANTIC_LAYER 组参数"}
          </Typography.Text>
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 10, height: "100%", boxSizing: "border-box" }}>
      <WikiGraphView kbId={kbId} />
    </div>
  );
}