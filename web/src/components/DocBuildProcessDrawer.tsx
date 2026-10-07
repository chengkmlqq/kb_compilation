"use client";

/** 文档构建过程抽屉：知识库详情文档列表就地查看 wiki 构建过程。
 * 内容 = 任务状态 + 执行轨迹树（measure 嵌套步骤）+ LLM 明细（调用/重试/429/退避）
 * + 构建日志；运行中每 6s 轮询刷新（执行轨迹由 skill 步骤事件实时增长）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Drawer, Space, Spin, Tag, Tree, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import {
  apiGetJob,
  apiGetJobTrace,
  AgentTraceData,
  JobItem,
  TraceStepNode,
} from "@/lib/api";

const { Text } = Typography;

interface DocBuildProcessDrawerProps {
  visible: boolean;
  jobId: string | null;
  docTitle?: string;
  onClose: () => void;
}

const STATE_TAG: Record<string, { color: string; text: string }> = {
  PENDING: { color: "purple", text: "排队中" },
  RUNNING: { color: "processing", text: "构建中" },
  SUCCESS: { color: "success", text: "成功" },
  FAILED: { color: "error", text: "失败" },
};

interface TraceTreeNode {
  key: string;
  title: React.ReactNode;
  children?: TraceTreeNode[];
}

function buildTraceTree(nodes: TraceStepNode[], rootTotalMs: number): TraceTreeNode[] {
  return nodes.map((n, i) => {
    const done = n.status === "done" || n.status === "fail";
    const pct = rootTotalMs > 0 ? Math.round(((n.ms || 0) / rootTotalMs) * 100) : 0;
    const statusTag =
      n.status === "done" ? (
        <Tag color="green">完成</Tag>
      ) : n.status === "fail" ? (
        <Tag color="red">失败</Tag>
      ) : n.status === "running" ? (
        <Tag color="blue">进行中</Tag>
      ) : (
        <Tag>中断</Tag>
      );
    return {
      key: `${n.step}-${n.start_ts ?? i}`,
      title: (
        <Space size={8} style={{ fontSize: 12 }}>
          <span style={{ fontWeight: 500 }}>{n.step}</span>
          {statusTag}
          <Text type="secondary" style={{ fontSize: 12 }}>
            {((n.ms || 0) / 1000).toFixed(1)}s
          </Text>
          {done ? (
            <span style={{ background: "#f0f0f0", borderRadius: 4, height: 8, width: 120 }}>
              <span
                style={{
                  display: "block",
                  width: `${Math.min(100, pct)}%`,
                  height: 8,
                  borderRadius: 4,
                  background: pct > 40 ? "#fa8c16" : "#1677ff",
                }}
              />
            </span>
          ) : null}
        </Space>
      ),
      children: n.children?.length ? buildTraceTree(n.children, rootTotalMs) : undefined,
    };
  });
}

function sumMs(ns: TraceStepNode[]): number {
  return ns.reduce((a, n) => a + (n.ms || 0) + sumMs(n.children || []), 0);
}

export default function DocBuildProcessDrawer({
  visible,
  jobId,
  docTitle,
  onClose,
}: DocBuildProcessDrawerProps) {
  const [job, setJob] = useState<JobItem | null>(null);
  const [trace, setTrace] = useState<AgentTraceData | null>(null);
  const [loading, setLoading] = useState(false);
  const [logContent, setLogContent] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  const fetchTrace = useCallback(async () => {
    if (!jobId) return;
    try {
      const res = await apiGetJobTrace(jobId);
      if (res.success && res.data) setTrace(res.data);
    } catch {
      /* 忽略瞬时错误（轮询重试） */
    }
  }, [jobId]);

  useEffect(() => {
    if (!visible || !jobId) return;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const res = await apiGetJob(jobId);
        if (!cancelled && res.success && res.data) setJob(res.data);
      } catch {
        /* ignore */
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    void fetchTrace();
    const timer = setInterval(() => {
      void load();
      void fetchTrace();
    }, 6000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [visible, jobId, fetchTrace]);

  // 构建日志实时流（SSE，与任务监控页同协议：chunk 追加，finish/error 收尾）
  useEffect(() => {
    if (!visible || !jobId) return;
    setLogContent("");
    const es = new EventSource(`/api/v1/jobs/${encodeURIComponent(jobId)}/log-stream`);
    let closed = false;
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as {
          type?: string;
          payload?: { content?: string; message?: string };
        };
        if (data.type === "chunk" && data.payload?.content) {
          setLogContent((prev) => `${prev}${data.payload?.content ?? ""}`);
        } else if (data.type === "error") {
          setLogContent((prev) => `${prev}\n[错误] ${data.payload?.message || ""}`);
        } else if (data.type === "finish") {
          if (!closed) {
            closed = true;
            es.close();
          }
        }
      } catch {
        /* 忽略非 JSON 心跳 */
      }
    };
    es.onerror = () => {
      if (!closed) {
        closed = true;
        es.close();
      }
    };
    return () => {
      closed = true;
      es.close();
    };
  }, [visible, jobId]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logContent]);

  const isRunning = job?.state === "RUNNING" || job?.state === "PENDING";
  const tree = trace?.events?.tree;
  const st = STATE_TAG[job?.state ?? ""] || { color: "default", text: job?.state ?? "-" };
  const logText = logContent || job?.error_message || "";

  return (
    <Drawer
      title={
        <Space size={8}>
          <span>构建过程</span>
          {docTitle ? (
            <Text type="secondary" style={{ fontWeight: 400, fontSize: 13 }}>
              {docTitle.length > 40 ? `${docTitle.slice(0, 40)}…` : docTitle}
            </Text>
          ) : null}
          <Tag color={st.color}>{st.text}</Tag>
          {job?.duration_ms ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {Math.round(job.duration_ms / 60000)}min
            </Text>
          ) : null}
        </Space>
      }
      open={visible}
      onClose={onClose}
      width={760}
      extra={<Button icon={<ReloadOutlined />} onClick={() => { void fetchTrace(); }} />}
    >
      <Spin spinning={loading}>
        {/* 执行轨迹 */}
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontWeight: 500, fontSize: 15, marginBottom: 8 }}>
            {trace?.trace_kind === "direct" ? "执行轨迹" : "Agent Trace"}
            {tree?.length ? (
              <Space size={6} style={{ marginLeft: 8, fontWeight: 400, fontSize: 12 }}>
                <Tag color="blue">{Math.round(sumMs(tree) / 1000)}s</Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  步骤
                  {(() => {
                    const cnt = (function count(ns: TraceStepNode[]): number {
                      return ns.reduce((a, n) => a + 1 + count(n.children || []), 0);
                    })(tree);
                    return cnt;
                  })()}
                </Text>
              </Space>
            ) : null}
          </div>
          {tree?.length ? (
            <Tree treeData={buildTraceTree(tree, sumMs(tree))} defaultExpandAll showLine />
          ) : (
            <Text type="secondary" italic>
              暂无步骤数据（任务开始后逐步生成）
            </Text>
          )}
        </div>

        {/* LLM 明细 */}
        {trace?.events ? (
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontWeight: 500, fontSize: 15, marginBottom: 8 }}>LLM 构建明细</div>
            <Space size={6} wrap>
              <Tag color="cyan">调用 {trace.events.ok}</Tag>
              {trace.events.error > 0 ? <Tag color="red">失败 {trace.events.error}</Tag> : null}
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
            {Object.keys(trace.events.phases || {}).length > 0 ? (
              <Space size={6} wrap style={{ marginTop: 8 }}>
                {Object.entries(trace.events.phases).map(([phase, n]) => (
                  <Tag key={phase} color="blue">
                    {phase} ×{n}
                  </Tag>
                ))}
              </Space>
            ) : null}
          </div>
        ) : null}

        {/* 构建日志 */}
        <div>
          <div style={{ fontWeight: 500, fontSize: 15, marginBottom: 8 }}>
            构建日志
            {isRunning ? <Spin size="small" style={{ marginLeft: 8 }} /> : null}
          </div>
          {logText ? (
            <pre
              ref={logRef}
              style={{
                maxHeight: 320,
                overflow: "auto",
                background: "#0f1720",
                color: "#d6e2ee",
                fontSize: 12,
                lineHeight: 1.55,
                padding: 12,
                borderRadius: 6,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {logText}
            </pre>
          ) : (
            <Text type="secondary" italic>
              暂无日志内容
            </Text>
          )}
        </div>
      </Spin>
    </Drawer>
  );
}