"use client";

/**
 * 语义层 · 文档库（2026-10-21）
 *
 * 复用知识库详情页文档面板（KbDocsPane），锁定「电信语义层」知识库的
 * 「文档库」根目录：左侧目录树只显示文档库分支，右侧文档列表只展示
 * 该目录下的数据。
 *
 * 绑定知识库 id 由参数管理表（modo_dim，SEMANTIC_LAYER 组）读取；
 * 参数缺失时自动按「电信语义层」库名 + 「文档库」根目录名发现。
 */
import { Spin, Typography } from "antd";
import KbDocsPane from "@/components/kb/KbDocsPane";
import { useSemanticBinding } from "../_lib/use-semantic-binding";

export default function SemanticDocsPage() {
  const { kbId, docsFolderId, loading, error } = useSemanticBinding();

  if (loading) {
    return (
      <div style={{ padding: 10, height: "100%" }}>
        <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin tip="加载语义层绑定…" />
        </div>
      </div>
    );
  }

  if (!kbId || !docsFolderId) {
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
            {error || "未绑定知识库/文档库目录，请在「参数管理」配置 SEMANTIC_LAYER 组参数"}
          </Typography.Text>
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 10, height: "100%", boxSizing: "border-box" }}>
      <KbDocsPane kbId={kbId} lockedFolderId={docsFolderId} onOpenDoc={() => undefined} />
    </div>
  );
}