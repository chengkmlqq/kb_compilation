"use client";

// Agent 多轮工具调用卡片（迁移自 WeKnora AgentStreamDisplay 的 action-card）。
// 每个工具调用一张可展开卡片：图标 + 工具名 + 状态 + 摘要；点击展开参数与结果详情。
// 数据来自 chat SSE 的 tool_call / tool 事件，按消息内顺序排列。

import { useState } from "react";
import {
  CheckCircleOutlined,
  DownOutlined,
  LoadingOutlined,
  RightOutlined,
  ToolOutlined,
} from "@ant-design/icons";

export interface ToolCallEvent {
  id: string;
  name: string;
  /** 参数（JSON 字符串，未格式化原始值） */
  arguments?: string;
  status: "running" | "done";
  /** 工具执行结果（文本） */
  result?: string;
  /** 耗时（秒） */
  duration?: number;
}

function prettyArgs(args?: string): string {
  if (!args) return "";
  try {
    return JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    return args;
  }
}

interface Props {
  events: ToolCallEvent[];
}

export default function AgentToolCalls({ events }: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  if (!events || events.length === 0) return null;

  return (
    <div className="kb-agent-tool-calls">
      {events.map((ev) => {
        const open = !!expanded[ev.id];
        const pending = ev.status === "running";
        const summary =
          !open && ev.status === "done" && ev.result ? ev.result.slice(0, 80) : "";
        return (
          <div
            key={ev.id}
            className={`kb-agent-tool-card${pending ? " kb-agent-tool-card--pending" : ""}`}
          >
            <div
              className="kb-agent-tool-card-head"
              onClick={() => setExpanded((p) => ({ ...p, [ev.id]: !open }))}
              title={summary || undefined}
            >
              {pending ? (
                <LoadingOutlined spin className="kb-agent-tool-spin" />
              ) : (
                <CheckCircleOutlined className="kb-agent-tool-done" />
              )}
              <ToolOutlined className="kb-agent-tool-tool" />
              <span className="kb-agent-tool-name">{ev.name}</span>
              <span className="kb-agent-tool-status">
                {pending
                  ? "执行中…"
                  : ev.duration != null
                    ? `完成 ${ev.duration}s`
                    : "完成"}
              </span>
              {summary && <span className="kb-agent-tool-summary">{summary}</span>}
              <span className="kb-agent-tool-arrow">
                {open ? <DownOutlined /> : <RightOutlined />}
              </span>
            </div>
            {open && (
              <div className="kb-agent-tool-details">
                {ev.arguments ? (
                  <>
                    <div className="kb-agent-tool-section-title">参数</div>
                    <pre className="kb-agent-tool-pre">{prettyArgs(ev.arguments)}</pre>
                  </>
                ) : null}
                {ev.result ? (
                  <>
                    <div className="kb-agent-tool-section-title">结果</div>
                    <pre className="kb-agent-tool-pre">
                      {ev.result.length > 2000 ? `${ev.result.slice(0, 2000)}…` : ev.result}
                    </pre>
                  </>
                ) : null}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}