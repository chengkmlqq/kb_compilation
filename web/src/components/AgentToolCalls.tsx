"use client";

// Agent 多轮工具调用卡片（2026-10-21 完全对齐 WeKnora AgentStreamDisplay 的
// action-card / tree 形态）：
// - 每工具一张卡片：工具图标 + 中文名标题（搜索类带 query）+ 状态（执行中/完成/失败）
// - 完成后固定展示结果摘要行（搜索结果数/关键词命中/wiki 读页等）
// - 点击展开详情：参数（可选）+ 结果文本；失败卡显示失败原因
// - 完成态自动折叠成「N 个中间步骤」树根（对齐 WeKnora tree-container），
//   点击展开中间步骤时间线
// 数据来自 chat SSE 的 tool_call / tool 事件（ToolCallEvent）。

import { useState } from "react";
import {
  BulbOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  DatabaseOutlined,
  DownOutlined,
  FileSearchOutlined,
  GlobalOutlined,
  LoadingOutlined,
  OrderedListOutlined,
  PaperClipOutlined,
  RightOutlined,
  SearchOutlined,
  ShareAltOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import {
  toolDetailLabel,
  toolIconKey,
  toolStatusText,
  toolSummaryText,
  toolTitle,
  zhToolName,
} from "@/lib/agent-tool-display";

export interface ToolCallEvent {
  id: string;
  name: string;
  /** 参数（JSON 字符串，未格式化原始值） */
  arguments?: string;
  status: "running" | "done";
  /** 工具执行结果（文本） */
  result?: string;
  /** 执行是否成功（2026-10-21 后端 tool 事件补的字段；缺省 true） */
  success?: boolean;
  /** 失败原因（success=false 时） */
  error?: string;
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

function iconOf(name: string) {
  const key = toolIconKey(name);
  const map: Record<string, React.ReactNode> = {
    bulb: <BulbOutlined />,
    search: <SearchOutlined />,
    global: <GlobalOutlined />,
    fileSearch: <FileSearchOutlined />,
    orderedList: <OrderedListOutlined />,
    paperClip: <PaperClipOutlined />,
    database: <DatabaseOutlined />,
    shareAlt: <ShareAltOutlined />,
    code: <CodeOutlined />,
    tool: <ToolOutlined />,
  };
  return map[key] ?? <ToolOutlined />;
}

interface Props {
  events: ToolCallEvent[];
}

/** 完成态工具卡折叠成树根的「N 个中间步骤」摘要（对齐 WeKnora intermediateStepsSummary）。 */
function TreeRoot({ count, open, onToggle }: { count: number; open: boolean; onToggle: () => void }) {
  return (
    <div className="kb-agent-tree-root">
      <div className="kb-agent-tree-root-head" onClick={onToggle} role="button" tabIndex={0}>
        <span className="kb-agent-tree-root-icon">
          <CheckCircleOutlined />
        </span>
        <span className="kb-agent-tree-root-text">
          <strong>{count}</strong> 个中间步骤
        </span>
        <span className="kb-agent-tool-arrow">{open ? <DownOutlined /> : <RightOutlined />}</span>
      </div>
    </div>
  );
}

export default function AgentToolCalls({ events }: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [treeOpen, setTreeOpen] = useState(false);
  if (!events || events.length === 0) return null;

  const allDone = events.every((ev) => ev.status === "done");
  const collapsed = allDone && events.length > 1 && !treeOpen;

  const cards = (
    <div className="kb-agent-tool-calls">
      {events.map((ev) => {
        const open = !!expanded[ev.id];
        const pending = ev.status === "running";
        const failed = ev.status === "done" && ev.success === false;
        const summary = !open && !pending && ev.success !== false ? toolSummaryText(ev) : "";
        const title = toolTitle({
          name: ev.name,
          pending,
          success: ev.success,
          arguments: ev.arguments,
        });
        return (
          <div
            key={ev.id}
            className={`kb-agent-tool-card${pending ? " kb-agent-tool-card--pending" : ""}${failed ? " kb-agent-tool-card--failed" : ""}`}
          >
            <div
              className="kb-agent-tool-card-head"
              onClick={() => setExpanded((p) => ({ ...p, [ev.id]: !open }))}
              title={summary || undefined}
            >
              {pending ? (
                <LoadingOutlined spin className="kb-agent-tool-spin" />
              ) : failed ? (
                <ClockCircleOutlined className="kb-agent-tool-fail" />
              ) : (
                <CheckCircleOutlined className="kb-agent-tool-done" />
              )}
              <span className="kb-agent-tool-tool">{iconOf(ev.name)}</span>
              <span className="kb-agent-tool-name">{pending ? title : title || zhToolName(ev.name)}</span>
              <span className="kb-agent-tool-status">{toolStatusText(ev)}</span>
              {summary && <span className="kb-agent-tool-summary">{summary}</span>}
              <span className="kb-agent-tool-arrow">{open ? <DownOutlined /> : <RightOutlined />}</span>
            </div>
            {open && (
              <div className="kb-agent-tool-details">
                {ev.arguments ? (
                  <>
                    <div className="kb-agent-tool-section-title">参数</div>
                    <pre className="kb-agent-tool-pre">{prettyArgs(ev.arguments)}</pre>
                  </>
                ) : null}
                {failed && ev.error ? (
                  <>
                    <div className="kb-agent-tool-section-title">失败原因</div>
                    <pre className="kb-agent-tool-pre kb-agent-tool-pre--error">{ev.error}</pre>
                  </>
                ) : null}
                {ev.result ? (
                  <>
                    <div className="kb-agent-tool-section-title">{toolDetailLabel({ name: ev.name, success: ev.success })}</div>
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

  // 完成态且多张卡 → 折叠成树根（对齐 WeKnora tree-container），展开后显示时间线
  if (collapsed) {
    return (
      <div className="kb-agent-tool-calls kb-agent-tool-calls--tree">
        <TreeRoot count={events.length} open={treeOpen} onToggle={() => setTreeOpen((v) => !v)} />
        {treeOpen && cards}
      </div>
    );
  }
  return cards;
}
