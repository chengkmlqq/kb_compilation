"use client";

import React from "react";
import { App, Button, Space, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  DeleteOutlined,
  EyeOutlined,
  PauseCircleOutlined,
} from "@ant-design/icons";
import ModoTable from "@/components/biz/modo-table";
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
  const d = parseIsoUtc(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // 2026-10-07: 横杠格式 YYYY-MM-DD HH:mm:ss（放弃 toLocaleString 的斜杠输出）
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// 2026-10-07: 任务类编码 -> 中文展示
const TASK_CLASS_LABEL: Record<string, string> = {
  KbDocumentProcessTask: "文档处理",
  KbDocumentEmbedTask: "文档向量化",
  KbSkillDirectBuildTask: "技能直跑构建",
  KbAgentWikiBuildTask: "智能体 Wiki 构建",
  KbSkillWikiBuildTask: "技能 Wiki 构建",
  KbWikiBuildTask: "Wiki 构建",
  KbGraphBuildTask: "图谱构建",
  KbOrphanRecoveryTask: "孤儿任务恢复",
  KbAgentGatewayTask: "智能体网关",
};

function taskClassLabel(v?: string | null): string {
  if (!v) return "-";
  return TASK_CLASS_LABEL[v] ?? v.replace("Kb", "").replace("Task", "");
}

// 2026-10-07: 后端 datetime 为 UTC 且序列化不带时区标记（'2026-10-07T01:25:20'），
// 直接 new Date() 会按浏览器本地时区解析导致偏差 8 小时——无时区后缀时按 UTC 处理。
function parseIsoUtc(iso: string): Date {
  if (/Z$|[+-]\d{2}:\d{2}$/.test(iso.trim())) {
    return new Date(iso);
  }
  return new Date(iso.endsWith("Z") ? iso : iso + "Z");
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
      render: (v: string | null) => (
        <Tooltip title={v || "-"}>
          <Text>{taskClassLabel(v)}</Text>
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
          const st = parseIsoUtc(record.start_time).getTime();
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
    <ModoTable<JobItem>
          rowKey="id"
          size="middle"
          loading={loading}
          dataSource={data}
          columns={columns}
          scroll={{ x: 1200 }}
          rowClassName={(record) =>
            record.state === "RUNNING" ? "modo-running-row" : ""
          }
        />
  );
};

export default JobTable;