"use client";

import React, { useEffect, useState } from "react";
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

const { Paragraph, Text } = Typography;

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

  const fetchDetail = async (silent = false) => {
    if (!jobId) return;
    if (!silent) setLoading(true);
    try {
      const res = await apiGetJob(jobId);
      if (res.success && res.data) {
        setJob(res.data);
        onStateChange?.(res.data);
      }
    } finally {
      if (!silent) setLoading(false);
    }
  };

  useEffect(() => {
    if (visible && jobId) {
      setJob(null);
      void fetchDetail();
    }
  }, [visible, jobId]);

  // RUNNING 任务每 5s 轮询一次详情（刷新进度/错误）
  useEffect(() => {
    if (!visible || job?.state !== "RUNNING") return;
    const timer = setInterval(() => void fetchDetail(true), 5000);
    return () => clearInterval(timer);
  }, [visible, job?.state]);

  return (
    <Drawer
      title={
        <Space size={8}>
          <span>作业详情与日志</span>
          {job && getStateTag(job.state || null)}
          <Button
            type="text"
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => void fetchDetail()}
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
              <div style={{ fontWeight: 500, fontSize: 15, marginBottom: 8 }}>
                进度 / 异常信息
              </div>
              {job.error_message ? (
                <div
                  style={{
                    background:
                      job.state === "FAILED" ? "#fff1f0" : "#fffbe6",
                    padding: 12,
                    borderRadius: 6,
                    border:
                      job.state === "FAILED"
                        ? "1px solid #ffccc7"
                        : "1px solid #ffe58f",
                  }}
                >
                  <Paragraph
                    style={{
                      marginBottom: 0,
                      color: job.state === "FAILED" ? "#cf1322" : "#666",
                      fontFamily: "monospace",
                      fontSize: 12,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-all",
                    }}
                  >
                    {job.error_message}
                  </Paragraph>
                </div>
              ) : (
                <Text type="secondary" italic>
                  无异常信息
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