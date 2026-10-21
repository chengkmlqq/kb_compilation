"use client";

import { Card, Empty, Typography } from "antd";

/**
 * 语义层占位页（2026-10-21）：元数据/文档库/知识本体/知识图谱四个入口先挂占位，
 * 功能后续按模块填充。页面骨架对齐平台惯例（白底圆角卡片 + 标题 + 空态）。
 */
export function SemanticPlaceholder({ title, desc }: { title: string; desc: string }) {
  return (
    <div style={{ padding: 10, height: "100%", boxSizing: "border-box" }}>
      <Card
        style={{ height: "100%", borderRadius: 8 }}
        styles={{ body: { padding: "16px 20px", height: "100%", display: "flex", flexDirection: "column" } }}
      >
        <Typography.Title level={4} style={{ marginTop: 0, marginBottom: 12 }}>
          {title}
        </Typography.Title>
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Empty description={desc} />
        </div>
      </Card>
    </div>
  );
}