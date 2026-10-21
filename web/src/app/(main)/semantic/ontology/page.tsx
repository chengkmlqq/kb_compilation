"use client";

/**
 * 语义层 · 知识本体（2026-10-21）
 *
 * 照抄知识库 Wiki 页面（KbWikiPane → WikiBrowseView）：左侧 wiki 目录树 +
 * 右侧页面浏览/全文检索，数据取「电信语义层」知识库的 wiki。
 *
 * 绑定知识库 id 由参数管理表（modo_dim，SEMANTIC_LAYER 组）读取；
 * 参数缺失时自动按「电信语义层」库名发现。
 */
import { Spin, Typography } from "antd";
import KbWikiPane from "@/components/kb/KbWikiPane";
import { useSemanticBinding } from "../_lib/use-semantic-binding";

export default function SemanticOntologyPage() {
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
      <KbWikiPane kbId={kbId} />
    </div>
  );
}