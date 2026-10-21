"use client";

/**
 * 语义层 · 元数据（2026-10-21）
 *
 * 复用知识库详情页文档面板（KbDocsPane），锁定「电信语义层」知识库的
 * 「元数据」根目录：左侧目录树只显示元数据分支（模型/程序/调度/数据源/
 * 质量规则/血缘/指标/指标维度/模型授权），右侧文档列表只展示该目录下的数据。
 *
 * 绑定知识库 id 由参数管理表（modo_dim，SEMANTIC_LAYER 组）读取；
 * 参数缺失时自动按「电信语义层」库名 + 「元数据」根目录名发现。
 */
import { Spin, Typography } from "antd";
import KbDocsPane from "@/components/kb/KbDocsPane";
import { useSemanticBinding } from "../_lib/use-semantic-binding";

export default function SemanticMetadataPage() {
  const { kbId, metadataFolderId, loading, error } = useSemanticBinding();

  if (loading) {
    return (
      <div style={{ padding: 10, height: "100%" }}>
        <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin tip="加载语义层绑定…" />
        </div>
      </div>
    );
  }

  if (!kbId || !metadataFolderId) {
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
            {error || "未绑定知识库/元数据目录，请在「参数管理」配置 SEMANTIC_LAYER 组参数"}
          </Typography.Text>
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 10, height: "100%", boxSizing: "border-box" }}>
      <KbDocsPane kbId={kbId} lockedFolderId={metadataFolderId} onOpenDoc={() => undefined} />
    </div>
  );
}