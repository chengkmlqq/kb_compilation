"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Descriptions,
  Drawer,
  Empty,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { apiGetJob, JobItem } from "@/lib/api";
import { resolveJobMonitorQueueLabel } from "./queue-label";

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

function paramsText(job: JobItem): string {
  try {
    return JSON.stringify(job.params || {}, null, 2);
  } catch {
    return String(job.params || "");
  }
}

const JobLogDrawer: React.FC<JobLogDrawerProps> = ({
  visible,
  jobId,
  onClose,
  onStateChange,
}) => {
  const [loading, setLoading] = useState(false);
  const [job, setJob] = useState<JobItem | null>(null);
  // SSE 实时日志流状态（对齐 data-synth job-log-drawer）
  const [streamConnected, setStreamConnected] = useState(false);
  const [streamFinished, setStreamFinished] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [logContent, setLogContent] = useState("");
  const [logScrollPercent, setLogScrollPercent] = useState(0);
  const eventSourceRef = useRef<EventSource | null>(null);
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

  const fetchDetail = async (silent = false): Promise<JobItem | null> => {
    if (!jobId) return null;
    if (!silent) setLoading(true);
    try {
      const res = await apiGetJob(jobId);
      if (res.success && res.data) {
        setJob(res.data);
        onStateChange?.(res.data);
        return res.data;
      }
      return null;
    } finally {
      if (!silent) setLoading(false);
    }
  };

  // 启动 SSE 实时日志流（数据源：error_message 增量 + log_path 文件）
  const startLogStream = useCallback(
    (targetJobId: string) => {
      closeStream();
      setStreamError(null);
      setStreamFinished(false);

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
            setStreamFinished(true);
            closeStream();
            void fetchDetail(true);
          }
        } catch {
          /* 忽略畸形 payload */
        }
      };

      es.onerror = () => {
        setStreamError("日志流连接中断，可点击刷新按钮兜底获取最新日志");
        closeStream();
      };
    },
    [closeStream, fetchDetail],
  );

  useEffect(() => {
    if (visible && jobId) {
      setJob(null);
      setLogContent("");
      void fetchDetail().then((detail) => {
        if (!detail || !jobId) return;
        // 已有 error_message 作为初始日志（SSE 从其后增量追加）
        setLogContent(detail.error_message || "");
        // 初始 offset：error_message 已有长度（对齐 ds getByteLength 语义）
        const initOffset = (detail.error_message || "").length;
        const es = new EventSource(
          `/api/v1/jobs/${encodeURIComponent(jobId)}/log-stream?offset=${initOffset}`,
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
            if (data.type === "chunk" && payload.content) {
              setLogContent((prev) => `${prev}${payload.content}`);
            } else if (data.type === "warning" || data.type === "error") {
              setStreamError(payload.message || "");
            } else if (data.type === "finish") {
              setStreamFinished(true);
              closeStream();
              void fetchDetail(true);
            }
          } catch {
            /* ignore */
          }
        };
        es.onerror = () => {
          setStreamError("日志流连接中断，可点击刷新按钮兜底获取最新日志");
          closeStream();
        };
      });
      return;
    }
    closeStream();
    setJob(null);
    setStreamError(null);
    setStreamFinished(false);
    setLogContent("");
  }, [visible, jobId, closeStream, fetchDetail]);

  // 日志滚动位置跟踪
  useEffect(() => {
    updateLogScrollPercent(logContainerRef.current);
  }, [logContent, visible, updateLogScrollPercent]);

  const handleRefresh = () => {
    if (!jobId) return;
    void fetchDetail().then(() => {
      setLogContent("");
      startLogStream(jobId);
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
      width={560}
      onClose={onClose}
      open={visible}
      destroyOnClose
    >
      <Spin spinning={loading}>
        {job ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <Descriptions
              title="基本信息"
              column={1}
              bordered
              size="small"
              styles={{ label: { width: 110 } }}
            >
              <Descriptions.Item label="作业ID">
                <Text copyable style={{ fontFamily: "monospace", fontSize: 12 }}>
                  {job.id}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="任务类">
                <Tooltip title={job.task_class}>
                  <Text style={{ wordBreak: "break-all" }}>
                    {job.task_class || "-"}
                  </Text>
                </Tooltip>
              </Descriptions.Item>
              <Descriptions.Item label="队列">
                {resolveJobMonitorQueueLabel(job.queue_name, "")}
              </Descriptions.Item>
              <Descriptions.Item label="触发方式">
                {job.trigger_type === "CRON" ? (
                  <Tag color="green">定时</Tag>
                ) : job.trigger_type === "EVENT" ? (
                  <Tag color="purple">事件</Tag>
                ) : (
                  <Tag color="blue">手动</Tag>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {getStateTag(job.state || null)}
              </Descriptions.Item>
              <Descriptions.Item label="Celery 任务ID">
                <Text style={{ fontFamily: "monospace", fontSize: 12 }}>
                  {job.task_id || "-"}
                </Text>
              </Descriptions.Item>
            </Descriptions>

            <Descriptions
              title="执行统计"
              column={1}
              bordered
              size="small"
              styles={{ label: { width: 110 } }}
            >
              <Descriptions.Item label="开始时间">
                {fmtTime(job.start_time)}
              </Descriptions.Item>
              <Descriptions.Item label="结束时间">
                {fmtTime(job.end_time)}
              </Descriptions.Item>
              <Descriptions.Item label="耗时">
                {fmtDur(job.duration_ms)}
              </Descriptions.Item>
              <Descriptions.Item label="日志路径">
                <Text style={{ fontSize: 12, wordBreak: "break-all" }}>
                  {job.log_path || "-"}
                </Text>
              </Descriptions.Item>
            </Descriptions>

            <div>
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
                <span>实时日志（Span 进度流）</span>
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
                    maxHeight: 320,
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

            <div>
              <div style={{ fontWeight: 500, fontSize: 15, marginBottom: 8 }}>
                任务参数
              </div>
              {job.params && Object.keys(job.params).length > 0 ? (
                <pre
                  style={{
                    background: "#fafafa",
                    border: "1px solid #f0f0f0",
                    borderRadius: 6,
                    padding: 12,
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    maxHeight: 280,
                    overflow: "auto",
                    fontFamily: "monospace",
                  }}
                >
                  {paramsText(job)}
                </pre>
              ) : (
                <Text type="secondary" italic>
                  无参数
                </Text>
              )}
            </div>
          </div>
        ) : (
          !loading && <Empty description="暂无数据" />
        )}
      </Spin>
    </Drawer>
  );
};

export default JobLogDrawer;