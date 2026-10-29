"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Drawer,
  Empty,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
} from "antd";
import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  apiGetJob,
  apiGetJobTrace,
  AgentTraceData,
  JobItem,
} from "@/lib/api";

interface JobLogDrawerProps {
  visible: boolean;
  jobId: string | null;
  onClose: () => void;
  onStateChange?: (job: JobItem) => void;
}

const { Text } = Typography;

const STATE_COLOR: Record<string, string> = {
  PENDING: "purple",
  RUNNING: "processing",
  SUCCESS: "success",
  FAILED: "error",
  STOPPED: "warning",
};

const STATE_LABEL: Record<string, string> = {
  PENDING: "待执行",
  RUNNING: "运行中",
  SUCCESS: "成功",
  FAILED: "失败",
  STOPPED: "已停止",
};

function getStateTag(state: string | null) {
  if (!state) return <Tag>未知</Tag>;
  return (
    <Tag color={STATE_COLOR[state] || "default"}>
      {STATE_LABEL[state] || state}
    </Tag>
  );
}

function fmtDur(ms?: number | null): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}min`;
}

function fmtTime(iso?: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

const JobLogDrawer: React.FC<JobLogDrawerProps> = ({
  visible,
  jobId,
  onClose,
  onStateChange,
}) => {
  const [loading, setLoading] = useState(false);
  const [job, setJob] = useState<JobItem | null>(null);
  // 2026-10-12 父子钻取：activeJobId 决定当前展示哪个任务的日志
  // （父任务 → 默认展示其本身；点子任务步骤流 → 切到该子任务的具体日志）
  const [activeJobId, setActiveJobId] = useState<string | null>(jobId);
  // 编排节点详情（childrenKind=orchestration 时点击节点内联展示，无 modo_job 行）
  const [orchStepDetail, setOrchStepDetail] = useState<Record<string, unknown> | null>(null);
  // 父任务的子任务列表（父任务打开时拉取，渲染步骤流）
  const [children, setChildren] = useState<Array<Record<string, unknown>>>([]);
  const [childrenKind, setChildrenKind] = useState<"job" | "orchestration">("job");
  // SSE 实时日志流状态（对齐 data-synth job-log-drawer）
  const [streamConnected, setStreamConnected] = useState(false);
  const [streamFinished, setStreamFinished] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [logContent, setLogContent] = useState("");
  const [logScrollPercent, setLogScrollPercent] = useState(0);
  const [trace, setTrace] = useState<AgentTraceData | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  // finish 之后浏览器 EventSource 仍会触发一次 onerror，用 ref 记录终态以区分
  // 「正常收尾」与「真实中断」，并避免 finish 后无限自动重连
  const streamFinishedRef = useRef(false);
  const logContainerRef = useRef<HTMLDivElement | null>(null);

  const closeStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setStreamConnected(false);
  }, []);

  const updateLogScrollPercent = useCallback((container: HTMLDivElement | null) => {
    if (!container) {
      setLogScrollPercent(0);
      return;
    }
    const maxScrollable = container.scrollHeight - container.clientHeight;
    if (maxScrollable <= 0) {
      setLogScrollPercent(100);
      return;
    }
    const percent = Math.round((container.scrollTop / maxScrollable) * 100);
    setLogScrollPercent(Math.max(0, Math.min(100, percent)));
  }, []);

  const handleLogScroll = useCallback(() => {
    updateLogScrollPercent(logContainerRef.current);
  }, [updateLogScrollPercent]);

  // onStateChange 每次父级渲染都是新引用，用 ref 取最新值，使 fetchDetail
  // 引用稳定 —— 否则 useEffect([fetchDetail]) 每渲染重跑 → SSE 流无限重建
  // （finish → onStateChange → 父级 setState → 重渲染 → 新 EventSource 循环）
  const onStateChangeRef = useRef(onStateChange);
  onStateChangeRef.current = onStateChange;

  // 2026-10-29：activeJobId 最新值 ref——父子钻取反复点击时，异步详情响应
  // 返回的闭包捕获的是「发起时的 activeJobId」，必须与当前值比对，过期
  // 响应直接丢弃，否则旧任务的日志流会串进当前任务的日志区
  const activeJobIdRef = useRef<string | null>(null);
  activeJobIdRef.current = activeJobId;

  const fetchDetail = useCallback(
    async (silent = false): Promise<JobItem | null> => {
      if (!activeJobId) return null;
      if (!silent) setLoading(true);
      try {
        const res = await apiGetJob(activeJobId);
        if (res.success && res.data) {
          setJob(res.data);
          onStateChangeRef.current?.(res.data);
          return res.data;
        }
        return null;
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [activeJobId],
  );

  // 2026-10-12 父子：打开的是父任务（jobId 本身有子任务）时拉取子任务步骤流
  useEffect(() => {
    if (!visible || !jobId) {
      setChildren([]);
      return;
    }
    let alive = true;
    import("@/lib/api")
      .then((m) => m.apiListJobChildren(jobId))
      .then((res) => {
        if (!alive) return;
        if (res.success && res.data) {
          setChildren(res.data.items || []);
          setChildrenKind(res.data.kind || "job");
        } else {
          setChildren([]);
        }
      })
      .catch(() => alive && setChildren([]));
    return () => {
      alive = false;
    };
  }, [visible, jobId]);

  // 打开抽屉 / 切到子任务时：重置并拉取 activeJobId 的数据 + 启动 SSE
  useEffect(() => {
    setActiveJobId((prev) => prev ?? jobId);
  }, [jobId]);

  // 构建轨迹/事件数据：activeJobId 变化/打开时拉取；抽屉打开期间每 6s 轮询
  // （运行中任务的 span 由 worker 每 3s 增量落盘，需轮询才能看到增长；
  //  2026-10-21 Agent Trace 区块已下线，trace 仍供构建步骤时间线/LLM 调用明细使用）
  useEffect(() => {
    if (!visible || !activeJobId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await apiGetJobTrace(activeJobId);
        if (!cancelled && res.success && res.data) setTrace(res.data);
        else if (!cancelled) setTrace({ spans: [], has_trace: false });
      } catch {
        if (!cancelled) setTrace({ spans: [], has_trace: false });
      }
    };
    void load();
    const timer = setInterval(() => {
      void load();
    }, 6000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [visible, activeJobId]);

  // 启动 SSE 实时日志流（数据源：error_message 增量 + log_path 文件）
  const startLogStream = useCallback(
    (targetJobId: string) => {
      closeStream();
      setStreamError(null);
      setStreamFinished(false);
      streamFinishedRef.current = false;

      const es = new EventSource(`/api/v1/jobs/${encodeURIComponent(targetJobId)}/log-stream`);
      eventSourceRef.current = es;

      es.onopen = () => {
        setStreamConnected(true);
        setStreamError(null);
      };

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as {
            type?: string;
            payload?: {
              content?: string;
              offset?: number;
              state?: string;
              source?: string;
              message?: string;
              reason?: string;
            };
          };
          const payload = data.payload || {};

          if (data.type === "snapshot") {
            // 编排 run：服务端每轮把 step_results 整体格式化推送，整体替换
            setLogContent(payload.content || "");
            return;
          }
          if (data.type === "chunk") {
            if (payload.content) {
              setLogContent((prev) => `${prev}${payload.content}`);
            }
            return;
          }
          if (data.type === "warning") {
            setStreamError(payload.message || "");
            return;
          }
          if (data.type === "error") {
            setStreamError(payload.message || "日志流读取异常");
            return;
          }
          if (data.type === "finish") {
            // 服务器发完 finish 后关闭连接，浏览器 EventSource 会把它当作
            // 「连接中断」无限自动重连（标准 SSE 行为）→ 收到终态必须立即
            // close() 掉这个闭包实例，否则请求风暴 + onerror 误报「日志流异常」
            streamFinishedRef.current = true;
            es.close();
            setStreamFinished(true);
            closeStream();
            void fetchDetail(true);
          }
        } catch {
          /* 忽略畸形 payload */
        }
      };

      es.onerror = () => {
        // finish 之后的 error 是正常收尾噪音，不算异常
        if (streamFinishedRef.current) {
          es.close();
          closeStream();
          return;
        }
        setStreamError("日志流连接中断，可点击刷新按钮兜底获取最新日志");
        es.close();
        closeStream();
      };
    },
    [closeStream, fetchDetail],
  );

  useEffect(() => {
    if (visible && activeJobId) {
      // 2026-10-29：切换任务（含反复点子任务）先关旧流，避免多条 SSE
      // 并发 append 进同一 logContent 导致日志串台
      closeStream();
      const targetId = activeJobId;
      setJob(null);
      setLogContent("");
      void fetchDetail().then((detail) => {
        // 过期响应守卫：详情返回时若已切到别的任务，丢弃（不建流、不置 job）
        if (activeJobIdRef.current !== targetId) return;
        if (!detail) return;
        // 2026-10-21 全量日志：不再用 error_message 预填 + 以其长度做 offset
        // （旧逻辑只能看到 error_message 槽位增量，历史日志全丢）。现在
        // SSE 从 offset=0 开始拉，服务端合并 local 日志文件 / events jsonl /
        // MinIO / error_message 全量推送（modo_job 增量 chunk / 编排 run 快照）。
        streamFinishedRef.current = false;
        const es = new EventSource(
          `/api/v1/jobs/${encodeURIComponent(targetId)}/log-stream`,
        );
        eventSourceRef.current = es;
        es.onopen = () => {
          setStreamConnected(true);
          setStreamError(null);
        };
        es.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data) as {
              type?: string;
              payload?: { content?: string; message?: string; reason?: string };
            };
            const payload = data.payload || {};
            if (data.type === "snapshot") {
              // 编排 run：快照整体替换
              setLogContent(payload.content || "");
            } else if (data.type === "chunk" && payload.content) {
              setLogContent((prev) => `${prev}${payload.content}`);
            } else if (data.type === "warning" || data.type === "error") {
              setStreamError(payload.message || "");
            } else if (data.type === "finish") {
              streamFinishedRef.current = true;
              es.close();
              setStreamFinished(true);
              closeStream();
              void fetchDetail(true);
            }
          } catch {
            /* ignore */
          }
        };
        es.onerror = () => {
          if (streamFinishedRef.current) {
            es.close();
            closeStream();
            return;
          }
          setStreamError("日志流连接中断，可点击刷新按钮兜底获取最新日志");
          es.close();
          closeStream();
        };
      });
      return () => {
        // 任务切换/抽屉关闭：关当前流，避免旧流继续写入
        closeStream();
      };
    }
    closeStream();
    setJob(null);
    setStreamError(null);
    setStreamFinished(false);
    setLogContent("");
  }, [visible, activeJobId, closeStream, fetchDetail]);

  // 日志滚动位置跟踪
  useEffect(() => {
    updateLogScrollPercent(logContainerRef.current);
  }, [logContent, visible, updateLogScrollPercent]);

  const handleRefresh = () => {
    if (!activeJobId) return;
    void fetchDetail().then(() => {
      // 2026-10-29：刷新期间若已切到别的任务，丢弃（startLogStream 的
      // 闭包捕获的是发起时的 activeJobId，过期回流会串台）
      if (activeJobIdRef.current !== activeJobId) return;
      setLogContent("");
      startLogStream(activeJobId);
    });
  };

  return (
    <Drawer
      title={
        <Space size={8}>
          <span>作业详情与日志</span>
          {job && getStateTag(job.state || null)}
          {streamError ? (
            <Tag color="error">日志流异常</Tag>
          ) : streamFinished ? (
            <Tag color="success">日志流已结束</Tag>
          ) : streamConnected ? (
            <Tag color="processing">实时流中</Tag>
          ) : (
            <Tag>未连接</Tag>
          )}
          <Button
            type="text"
            size="small"
            icon={<ReloadOutlined />}
            onClick={handleRefresh}
            loading={loading}
          />
        </Space>
      }
      placement="right"
      width={620}
      onClose={onClose}
      open={visible}
      destroyOnClose
      // 2026-10-28：抽屉 body 变 flex column 高度链——去掉基本信息/执行统计后
      // 日志区要占满剩余高度，body 溢出交给内部日志框滚动，不做整体滚动
      styles={{ body: { display: "flex", flexDirection: "column", overflow: "hidden" } }}
    >
      <Spin spinning={loading} className="kb-job-log-spin">
        {/* 2026-10-12 父子钻取：activeJobId ≠ 打开的 jobId 时显示「返回父任务」 */}
        {activeJobId !== jobId ? (
          <div style={{ marginBottom: 12 }}>
            <Button size="small" onClick={() => setActiveJobId(jobId)} icon={<ArrowLeftOutlined />}>
              返回主任务
            </Button>
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              当前查看子任务：{activeJobId}
            </Text>
          </div>
        ) : null}
        {activeJobId === jobId && children.length > 0 ? (
          // 2026-10-28：抽屉 body 不再整体滚动（日志区 flex:1 吃满剩余高度），
          // 子任务步骤流若很多会挤占日志区——限高 35vh 独立滚动
          <div style={{ marginBottom: 24, maxHeight: "35vh", overflow: "auto" }}>
            <div
              style={{
                fontWeight: 500,
                fontSize: 15,
                marginBottom: 8,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <span>子任务执行步骤流</span>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {childrenKind === "orchestration" ? "编排执行" : "文档处理链"}
                {childrenKind === "orchestration" ? "，点击查看执行详情" : "，点击子任务查看具体日志"}
              </Text>
            </div>
            <Timeline
              style={{ marginTop: 4 }}
              items={children.map((c: Record<string, unknown>, idx) => {
                const cid = String(c.id || c.step_id || "");
                // 2026-10-20：编排 run 的 children 为 run 概要一行（tape_name 取名），
                // 不再是 step_results 节点——编排内部节点归编排详情视图查看。
                const cname =
                  String(c.tape_name || c.step_label || c.task_class || "") ||
                  (childrenKind === "orchestration" ? String(c.step_inst || cid) : cid);
                const st = String(c.status || c.state || "");
                const color =
                  st === "success" || st === "SUCCESS"
                    ? "green"
                    : st === "failed" || st === "FAILED"
                      ? "red"
                      : st === "running" || st === "RUNNING"
                        ? "blue"
                        : "gray";
                const isActive =
                  (childrenKind === "orchestration"
                    ? orchStepDetail && (orchStepDetail.step_id || orchStepDetail.id) === cid
                    : activeJobId === cid) || false;
                return {
                  color,
                  dot: (
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        justifyContent: "center",
                        width: 24,
                        height: 24,
                        borderRadius: 12,
                        background: isActive ? "#1677ff" : "#f0f0f0",
                        color: isActive ? "#fff" : "#666",
                        fontSize: 11,
                        fontWeight: 600,
                      }}
                    >
                      {idx + 1}
                    </span>
                  ),
                  children: (
                    <div
                      style={{
                        cursor: "pointer",
                        padding: "8px 12px",
                        borderRadius: 6,
                        background: isActive ? "#e6f4ff" : "#fafafa",
                        border: isActive ? "1px solid #91caff" : "1px solid #f0f0f0",
                      }}
                      onClick={() => {
                        if (childrenKind === "orchestration") {
                          // 编排执行：children 为 run 概要一行，点击展开编排执行详情（概要 JSON）
                          setOrchStepDetail(orchStepDetail && (orchStepDetail.step_id || orchStepDetail.id) === cid ? null : c);
                        } else if (cid) {
                          setActiveJobId(cid);
                        }
                      }}
                    >
                      <Space size={8} style={{ flexWrap: "wrap" }}>
                        <Text strong style={{ fontSize: 13 }}>
                          {cname}
                        </Text>
                        {getStateTag(st)}
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {fmtDur(c.duration_ms as number | null)}
                        </Text>
                        {c.start_time ? (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {fmtTime(String(c.start_time))}
                          </Text>
                        ) : null}
                      </Space>
                    </div>
                  ),
                };
              })}
            />
            {/* 编排执行详情（run 概要展开，2026-10-20 起 children 为 run 概要而非节点数组） */}
            {orchStepDetail ? (
              <div
                style={{
                  marginTop: 12,
                  background: "#fafafa",
                  border: "1px solid #f0f0f0",
                  borderRadius: 6,
                  padding: 12,
                }}
              >
                <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 8 }}>
                  执行详情：{String(orchStepDetail.tape_name || orchStepDetail.step_label || orchStepDetail.step_inst || orchStepDetail.id || "编排运行")}
                  {getStateTag(String(orchStepDetail.status || orchStepDetail.state || ""))}
                </div>
                <pre
                  style={{
                    background: "#1f2630",
                    color: "#d7e0ea",
                    padding: 10,
                    borderRadius: 6,
                    fontSize: 12,
                    maxHeight: 320,
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                  }}
                >
                  {JSON.stringify(orchStepDetail, null, 2)}
                </pre>
              </div>
            ) : null}
          </div>
        ) : null}
        {job ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 20,
              // 2026-10-28：去掉基本信息/执行统计后日志区要占满剩余高度——
              // job 容器吃满 Spin 剩余空间，内部各区块由 flex 链撑开
              flex: 1,
              minHeight: 0,
              overflow: "hidden",
            }}
          >
            {/* 2026-10-28：作业详情抽屉去掉「基本信息」「执行统计」两段 Descriptions——
                主任务与子任务均不再展示，列表行已含状态/进度/时间，抽屉以执行日志为主体。 */}

            <div
              style={{
                // 日志区占满剩余高度：flex column，代码框 flex:1 内部滚动
                flex: 1,
                minHeight: 0,
                display: "flex",
                flexDirection: "column",
              }}
            >
              <div
                style={{
                  fontWeight: 500,
                  fontSize: 15,
                  marginBottom: 8,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                }}
              >
                <span>执行日志（全量）</span>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  当前位置：{logScrollPercent}%
                </Text>
              </div>
              {streamError ? (
                <div style={{ fontSize: 12, color: "#cf1322", marginBottom: 8 }}>
                  {streamError}
                </div>
              ) : null}
              {logContent || job?.error_message ? (
                <div
                  ref={logContainerRef}
                  onScroll={handleLogScroll}
                  style={{
                    background: "#1f2630",
                    color: "#d7e0ea",
                    padding: 12,
                    borderRadius: 6,
                    border: "1px solid #2e3a4a",
                    // 2026-10-28：去掉固定 maxHeight 320——flex:1 占满日志区剩余高度
                    flex: 1,
                    minHeight: 0,
                    overflow: "auto",
                    fontFamily: "monospace",
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                  }}
                >
                  {logContent || job?.error_message || ""}
                </div>
              ) : (
                <Text type="secondary" italic>
                  暂无日志内容（任务运行中实时进度将在此展示）
                </Text>
              )}
            </div>

            {/* 构建步骤时间线（flat 列表） */}
            {trace?.events && !trace.events.tree?.length ? (
              <div>
                <div style={{ fontWeight: 500, fontSize: 15, marginBottom: 8 }}>
                  构建步骤时间线
                  {trace.events.steps?.length ? (
                    <Space size={6} style={{ marginLeft: 8, fontWeight: 400, fontSize: 12 }}>
                      <Tag color="blue">
                        合计{" "}
                        {Math.round(
                          (trace.events.steps
                            .filter((s) => s.status === "done" || s.status === "fail")
                            .reduce((a, s) => a + s.ms, 0) ?? 0) / 1000,
                        )}
                        s
                      </Tag>
                      {(() => {
                        const done = trace.events.steps?.filter((s) => s.status === "done" || s.status === "fail") ?? [];
                        const slowest = done.reduce((a, s) => (s.ms > (a?.ms ?? 0) ? s : a), undefined as
                          | { step: string; status: string; ms: number }
                          | undefined);
                        return slowest ? (
                          <Tag color="orange">
                            最慢 {slowest.step} {Math.round(slowest.ms / 1000)}s
                          </Tag>
                        ) : null;
                      })()}
                    </Space>
                  ) : null}
                </div>
                {trace.events.steps?.length ? (
                  <Table
                    size="small"
                    pagination={false}
                    rowKey={(r) => `${r.step}-${r.ms}`}
                    dataSource={trace.events.steps}
                    columns={[
                      { title: "步骤", dataIndex: "step", width: 220 },
                      {
                        title: "耗时",
                        dataIndex: "ms",
                        width: 110,
                        render: (v: number) => `${(v / 1000).toFixed(1)}s`,
                      },
                      {
                        title: "占比",
                        dataIndex: "ms",
                        render: (v: number) => {
                          const done = (trace.events?.steps ?? []).filter(
                            (s) => s.status === "done" || s.status === "fail",
                          );
                          const tot = done.reduce((a, s) => a + s.ms, 0) || 1;
                          return (
                            <div style={{ background: "#f0f0f0", borderRadius: 4, height: 10 }}>
                              <div
                                style={{
                                  width: `${Math.round((v / tot) * 100)}%`,
                                  height: 10,
                                  borderRadius: 4,
                                  background: v / tot > 0.4 ? "#fa8c16" : "#1677ff",
                                }}
                              />
                            </div>
                          );
                        },
                      },
                      {
                        title: "状态",
                        dataIndex: "status",
                        width: 90,
                        render: (v: string) => (
                          <Tag color={v === "done" ? "green" : v === "fail" ? "red" : "blue"}>
                            {v === "done" ? "完成" : v === "fail" ? "失败" : v === "running" ? "进行中" : "中断"}
                          </Tag>
                        ),
                      },
                    ]}
                  />
                ) : (
                  <Text type="secondary" italic>
                    该任务无步骤埋点（旧版本技能包）
                  </Text>
                )}
              </div>
            ) : null}

            {/* LLM 调用明细 */}
            {trace?.events ? (
              <div>
                <div style={{ fontWeight: 500, fontSize: 15, marginBottom: 8 }}>
                  <Space size={6} style={{ marginLeft: 8, fontWeight: 400, fontSize: 12 }}>
                    <Tag color="cyan">调用 {trace.events.ok}</Tag>
                    {trace.events.error > 0 ? (
                      <Tag color="red">失败 {trace.events.error}</Tag>
                    ) : null}
                    <Tag color={trace.events.retries > 0 ? "orange" : "default"}>
                      重试 {trace.events.retries}
                    </Tag>
                    {trace.events.retry_reasons.http_429 ? (
                      <Tag color="red">429 ×{trace.events.retry_reasons.http_429}</Tag>
                    ) : null}
                    {trace.events.retry_reasons.network ? (
                      <Tag color="red">网络/超时 ×{trace.events.retry_reasons.network}</Tag>
                    ) : null}
                    <Tag>LLM 耗时 {trace.events.llm_total_s}s</Tag>
                    <Tag>节流等待 {trace.events.wait_total_s}s</Tag>
                    {trace.events.backoff_total_s > 0 ? (
                      <Tag color="orange">退避等待 {trace.events.backoff_total_s}s</Tag>
                    ) : null}
                  </Space>
                </div>
                <Table
                  size="small"
                  pagination={false}
                  rowKey={(r) => r.phase}
                  dataSource={Object.entries(trace.events.phases).map(([phase, n]) => ({ phase, n }))}
                  columns={[
                    { title: "构建阶段", dataIndex: "phase", width: 220 },
                    { title: "LLM 调用次数", dataIndex: "n", width: 120 },
                  ]}
                />
              </div>
            ) : null}
          </div>
        ) : (
          !loading &&
            !(activeJobId === jobId && children.length > 0) && <Empty description="暂无数据" />
        )}
      </Spin>
    </Drawer>
  );
};

export default JobLogDrawer;