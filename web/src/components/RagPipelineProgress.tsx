"use client";

import React from "react";
import { Spin } from "antd";
import {
  SearchOutlined,
  RobotOutlined,
  ToolOutlined,
  CheckCircleFilled,
} from "@ant-design/icons";

export interface RagStep {
  stage: string;
  status: "running" | "done";
  title: string;
  summary?: string;
  hitsCount?: number;
}

const STAGE_META: Record<string, { icon: React.ReactNode }> = {
  retrieval: { icon: <SearchOutlined /> },
  generation: { icon: <RobotOutlined /> },
};

// 对齐 WeKnora RagPipelineProgress：RAG 链路步骤树（运行中高亮、完成打勾、结果摘要）
export default function RagPipelineProgress({ steps }: { steps: RagStep[] }) {
  if (!steps || steps.length === 0) return null;
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        marginBottom: 8,
        padding: "6px 10px",
        background: "#fafafa",
        border: "1px solid #f0f0f0",
        borderRadius: 8,
        fontSize: 12,
      }}
    >
      {steps.map((s, i) => {
        const meta = STAGE_META[s.stage] || { icon: <ToolOutlined /> };
        return (
          <div
            key={`${s.stage}-${i}`}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <span style={{ color: "#1677ff", fontSize: 12 }}>{meta.icon}</span>
            <span
              style={{
                fontWeight: s.status === "running" ? 600 : 400,
                color: s.status === "running" ? "#fa8c16" : "#333",
              }}
            >
              {s.title}
            </span>
            {s.status === "running" ? (
              <Spin size="small" style={{ marginLeft: 4 }} />
            ) : (
              <CheckCircleFilled style={{ color: "#52c41a", fontSize: 12 }} />
            )}
            {s.status === "done" && s.summary && (
              <span style={{ color: "#999", marginLeft: 2 }}>{s.summary}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}