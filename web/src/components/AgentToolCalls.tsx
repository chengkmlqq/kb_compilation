"use client";

// Agent 多轮工具调用时间线（2026-10-21 完全对齐 WeKnora AgentStreamDisplay 的
// tree-container / tree-child 形态）：
// - 完成后自动折叠为树根摘要「思考 N 轮 · 调用 N 次工具 · 耗时 N」（对齐
//   intermediateStepsSummary），点击切换收缩/展开
// - 展开后按时间线渲染：每行左侧纵向竖线（tree-branch 对齐），工具图标 + 中文名
//   （搜索类带 query）+ 状态（执行中/完成/失败）+ 结果摘要行
// - 完成/失败不用圆圈勾，用颜色与竖线表达（对齐 WeKnora action-card 无勾形态）
// 数据来自 chat SSE 的 tool_call / tool 事件（ToolCallEvent）。

import { useMemo, useState } from "react";
import {
  BulbOutlined,
  CodeOutlined,
  DatabaseOutlined,
  DownOutlined,
  FileSearchOutlined,
  GlobalOutlined,
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

function formatDuration(seconds: number): string {
  if (seconds == null || seconds < 0) return "";
  if (seconds < 60) return `${Math.round(seconds * 10) / 10}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

interface Props {
  events: ToolCallEvent[];
  /** 2026-10-21 后端 done 事件统计（整体耗时/思考轮次/工具调用数），优先于前端推算 */
  stats?: { totalDurationMs?: number; thinkingRounds?: number; toolCalls?: number };
  /**
   * 2026-10-21 答案是否仍在流式生成（m.streaming）：
   * true = 答案未完成 → 工具链强制展开（用户随时看进度）；
   * false = 答案已完成 → 折叠成树根摘要，可手动展开/收缩。
   */
  answerStreaming?: boolean;
}

export default function AgentToolCalls({ events, stats, answerStreaming }: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // 2026-10-21 树形收缩/展开：默认收起（对齐 WeKnora showIntermediateSteps=false），
  // 点树根切换；无论展开还是收缩树根始终存在（之前只有展开、无收缩入口）。
  const [treeOpen, setTreeOpen] = useState(false);

  // 折叠规则（2026-10-21 对齐用户反馈「答案没出来前展开、回复完后折叠」）：
  // - 答案仍在流式（answerStreaming）→ 无论工具是否完成都展开时间线
  // - 答案已完成（!answerStreaming）且所有工具 done → 显示树根摘要（可手动展开/收缩）
  const allDone = useMemo(() => events.every((ev) => ev.status === "done"), [events]);
  const showTree = allDone && !answerStreaming;
  const showTimeline = !showTree || treeOpen;
  // 统计（对齐 WeKnora intermediateStepsSummary：思考轮数/工具数/耗时）。
  // 优先用后端 done 事件统计（整体耗时含检索/生成/工具全链，思考轮次=generation 轮数），
  // 前端推算仅作兜底：thinking 工具数作为思考轮、非 thinking 作为工具数。
  const statsMerged = useMemo(() => {
    const fallbackThinking = events.filter((ev) => ev.name === "thinking").length;
    const fallbackTools = events.filter((ev) => ev.name !== "thinking").length;
    const fallbackTotalSec = events.reduce((acc, ev) => acc + (ev.duration || 0), 0);
    return {
      thinkingRounds: stats?.thinkingRounds ?? fallbackThinking,
      toolCalls: stats?.toolCalls ?? fallbackTools,
      totalDurationMs: stats?.totalDurationMs ?? (fallbackTotalSec > 0 ? fallbackTotalSec * 1000 : undefined),
    };
  }, [events, stats]);

  if (!events || events.length === 0) return null;

  // 树根摘要（对齐 WeKnora intermediateStepsSummary 文案）
  const summaryParts: string[] = [];
  if (statsMerged.thinkingRounds > 0) summaryParts.push(`思考 ${statsMerged.thinkingRounds} 轮`);
  if (statsMerged.toolCalls > 0) summaryParts.push(`调用 ${statsMerged.toolCalls} 次工具`);
  if (statsMerged.toolCalls === 0 && statsMerged.thinkingRounds === 0) summaryParts.push(`已完成 ${events.length} 个步骤`);
  if (statsMerged.totalDurationMs != null && statsMerged.totalDurationMs > 0) {
    summaryParts.push(`耗时 ${formatDuration(statsMerged.totalDurationMs / 1000)}`);
  }
  const treeSummary = summaryParts.join(" · ");

  return (
    <div className="kb-agent-tree">
      {showTree && (
        <div
          className={`kb-agent-tree-root${treeOpen ? " kb-agent-tree-root--open" : ""}`}
          onClick={() => setTreeOpen((v) => !v)}
          role="button"
          tabIndex={0}
        >
          <span className="kb-agent-tree-root-icon">{iconOf("thinking")}</span>
          <span className="kb-agent-tree-root-text">{treeSummary}</span>
          <span className="kb-agent-tree-root-arrow">
            {treeOpen ? <DownOutlined /> : <RightOutlined />}
          </span>
        </div>
      )}

      {showTimeline && (
        <div className="kb-agent-tree-children">
          {events.map((ev, index) => {
            const open = !!expanded[ev.id];
            const pending = ev.status === "running";
            const failed = ev.status === "done" && ev.success === false;
            const isLast = index === events.length - 1;
            const summary = !open && !pending && ev.success !== false ? toolSummaryText(ev) : "";
            return (
              <div
                key={ev.id}
                className={`kb-agent-tree-child${isLast ? " kb-agent-tree-child--last" : ""}`}
              >
                <div
                  className={`kb-agent-tool-card${pending ? " kb-agent-tool-card--pending" : ""}${failed ? " kb-agent-tool-card--failed" : ""}`}
                >
                  <div
                    className="kb-agent-tool-card-head"
                    onClick={() => setExpanded((p) => ({ ...p, [ev.id]: !open }))}
                    title={summary || undefined}
                  >
                    {/* 工具图标固定挂在纵向竖线处（对齐 WeKnora .action-title-icon），
                        执行中由卡片动画表达，不加圆圈勾 */}
                    <span className="kb-agent-tool-tool">{iconOf(ev.name)}</span>
                    <span className="kb-agent-tool-name">
                      {toolTitle({ name: ev.name, pending, success: ev.success, arguments: ev.arguments })}
                    </span>
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
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
