"use client";

import React from "react";
import { App, Button, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  DeleteOutlined,
  EyeOutlined,
  PauseCircleOutlined,
} from "@ant-design/icons";
import { JobItem } from "@/lib/api";

interface JobTableProps {
  loading: boolean;
  data: JobItem[];
  onViewLog: (job: JobItem) => void;
  onStop: (job: JobItem) => void;
  onDelete: (job: JobItem) => void;
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

const TRIGGER_LABEL: Record<string, string> = {
  API: "接口触发",
  CRON: "定时触发",
  HAND: "手动",
  MANUAL: "手动",
  EVENT: "事件",
};

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

function renderStateTag(state: string) {
  const color = STATE_COLOR[state] || "default";
  const label = STATE_LABEL[state] || state || "-";
  if (state === "RUNNING") {
    return (
      <Tag color={color}>
        <span className="modo-running-tag">
          <span className="modo-running-dot" aria-hidden="true" />
          <span>{label}</span>
        </span>
      </Tag>
    );
  }
  return <Tag color={color}>{label}</Tag>;
}

const JobTable: React.FC<JobTableProps> = ({
  loading,
  data,
  onViewLog,
  onStop,
  onDelete,
}) => {
  const { modal } = App.useApp();
  // 2026-10-07: 运行中任务耗时实时累加——本地时钟每秒 tick，RUNNING 行
  // 用 now - start_time 渲染（后端 duration_ms 是快照值，不刷新页面不增长）
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const columns: ColumnsType<JobItem> = [
    {
      title: "作业ID",
      dataIndex: "id",
      key: "id",
      width: 200,
      ellipsis: true,
      render: (text: string) => (
        <Tooltip title={text}>
          <Text style={{ fontFamily: "monospace", fontSize: 12 }}>{text}</Text>
        </Tooltip>
      ),
    },
    {
      title: "任务类",
      dataIndex: "task_class",
      key: "task_class",
      width: 170,
      ellipsis: true,
      render: (v: string) => (
        <Tooltip title={v}>
          <Text>{v?.replace("Kb", "").replace("Task", "") || "-"}</Text>
        </Tooltip>
      ),
    },
    {
      title: "触发类型",
      dataIndex: "trigger_type",
      key: "trigger_type",
      width: 100,
      render: (type: string) => {
        let color = "default";
        let text = type || "-";
        if (type === "HAND" || type === "MANUAL") {
          color = "blue";
          text = "手动";
        } else if (type === "CRON") {
          color = "green";
          text = "定时";
        } else if (type === "API") {
          color = "geekblue";
          text = "接口";
        }
        return <Tag color={color}>{TRIGGER_LABEL[type] || text}</Tag>;
      },
    },
    {
      title: "状态",
      dataIndex: "state",
      key: "state",
      width: 110,
      render: (state: string) => renderStateTag(state),
    },
    {
      title: "开始时间",
      dataIndex: "start_time",
      key: "start_time",
      width: 175,
      render: (val: string | null) => fmtTime(val),
    },
    {
      title: "耗时",
      dataIndex: "duration_ms",
      key: "duration_ms",
      width: 100,
      render: (ms: number | null, record) => {
        // RUNNING：实时累加（now - start_time），其余用后端快照 duration_ms
        if (record.state === "RUNNING" && record.start_time) {
          const st = new Date(record.start_time).getTime();
          const text = Number.isNaN(st) ? fmtDur(ms) : fmtDur(now - st);
          return <span className="modo-running-duration">{text}</span>;
        }
        const text = fmtDur(ms);
        if (record.state === "RUNNING") {
          return <span className="modo-running-duration">{text}</span>;
        }
        return text;
      },
    },
    {
      title: "进度 / 错误",
      dataIndex: "error_message",
      key: "error_message",
      ellipsis: true,
      render: (v: string | null, row) =>
        v ? (
          <Text
            type={row.state === "FAILED" ? "danger" : "secondary"}
            style={{ fontSize: 12 }}
          >
            {v.length > 70 ? `${v.slice(0, 70)}…` : v}
          </Text>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: "操作",
      key: "action",
      width: 150,
      fixed: "right" as const,
      render: (_, record) => (
        <Space size={4}>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => onViewLog(record)}
          >
            日志
          </Button>
          {record.state === "RUNNING" && (
            <Button
              type="link"
              size="small"
              danger
              icon={<PauseCircleOutlined />}
              onClick={() =>
                modal.confirm({
                  title: "确定停止该作业？",
                  content: `作业 ${record.id} 运行中，停止后状态置为「已停止」。`,
                  onOk: () => onStop(record),
                })
              }
            >
              停止
            </Button>
          )}
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() =>
              modal.confirm({
                title: "确定删除该作业？",
                content: `删除后记录不可恢复：${record.id}`,
                onOk: () => onDelete(record),
              })
            }
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Table<JobItem>
      rowKey="id"
      size="middle"
      loading={loading}
      dataSource={data}
      columns={columns}
      scroll={{ x: 1200 }}
      pagination={false}
      rowClassName={(record) =>
        record.state === "RUNNING" ? "modo-running-row" : ""
      }
    />
  );
};

export default JobTable;