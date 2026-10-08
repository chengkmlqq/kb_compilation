"use client";

import React, { useState } from "react";
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
  /** 2026-10-08: 动作执行详情（工具参数/完整结果/耗时）——点击节点展开 */
  detail?: string;
  duration?: number;
}

const STAGE_META: Record<string, { icon: React.ReactNode }> = {
  retrieval: { icon: <SearchOutlined /> },
  generation: { icon: <RobotOutlined /> },
};

// 对齐 WeKnora RagPipelineProgress：RAG 链路步骤树（运行中高亮、完成打勾、结果摘要）
export default function RagPipelineProgress({ steps }: { steps: RagStep[] }) {
  const [openIdx, setOpenIdx] = useState<number | null>(null);
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
        const expanded = openIdx === i;
        return (
          <div key={`${s.stage}-${i}`}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ color: "#1677ff", fontSize: 12 }}>{meta.icon}</span>
              <span
                style={{
                  fontWeight: s.status === "running" ? 600 : 400,
                  color: s.status === "running" ? "#fa8c16" : "#333",
                  cursor: s.detail ? "pointer" : "default",
                }}
                onClick={() => (s.detail ? setOpenIdx(expanded ? null : i) : undefined)}
              >
                {s.title}
              </span>
              {s.status === "running" ? (
                <Spin size="small" style={{ marginLeft: 4 }} />
              ) : (
                <CheckCircleFilled style={{ color: "#52c41a", fontSize: 12 }} />
              )}
              {s.status === "done" && s.summary && (
                <span style={{ color: "#999", marginLeft: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 240 }}>
                  {s.summary}
                </span>
              )}
              {s.status === "done" && s.duration != null && (
                <span style={{ color: "#bbb", fontSize: 11 }}>{s.duration}s</span>
              )}
              {s.detail && (
                <span style={{ color: "#1677ff", cursor: "pointer", marginLeft: "auto" }} onClick={() => setOpenIdx(expanded ? null : i)}>
                  {expanded ? "收起" : "详情"}
                </span>
              )}
            </div>
            {expanded && s.detail && (
              <pre
                style={{
                  margin: "4px 0 2px 18px",
                  padding: "6px 8px",
                  background: "#fff",
                  border: "1px solid #f0f0f0",
                  borderRadius: 4,
                  fontSize: 11,
                  color: "#555",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  maxHeight: 160,
                  overflow: "auto",
                }}
              >
                {s.detail}
              </pre>
            )}
          </div>
        );
      })}
    </div>
  );
}