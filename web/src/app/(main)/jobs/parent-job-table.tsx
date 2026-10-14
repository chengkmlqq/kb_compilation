"use client";

/**
 * 父任务表格（2026-10-12 任务监控首屏）：
 * 主任务列表（文档处理链头 + 编排执行虚拟父任务），每行可展开看子任务执行过程。
 * 子任务 = modo_job 子任务（文档链）或 编排 step_results 节点（编排）。
 */
import React, { useCallback, useEffect, useState } from "react";
import { App, Button, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ApartmentOutlined, DeleteOutlined, EyeOutlined, PauseCircleOutlined } from "@ant-design/icons";
import { apiListJobChildren, JobItem, ParentJobItem } from "@/lib/api";

interface ParentJobTableProps {
  loading: boolean;
  data: ParentJobItem[];
  /** 表体最大高度（px），由父级实测容器高度传入；不传则不限制高度 */
  scrollY?: number;
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
  queued: "purple",
  running: "processing",
  success: "success",
  failed: "error",
  cancelled: "warning",
};

const STATE_LABEL: Record<string, string> = {
  PENDING: "待执行",
  RUNNING: "运行中",
  SUCCESS: "成功",
  FAILED: "失败",
  STOPPED: "已停止",
  queued: "排队中",
  running: "运行中",
  success: "成功",
  failed: "失败",
  cancelled: "已取消",
};

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
  KbTableIngestTask: "表摄取",
  KbMetadataCollectionTask: "元数据采集",
  OrchestrationRun: "编排执行",
};

function taskClassLabel(v?: string | null): string {
  if (!v) return "-";
  return TASK_CLASS_LABEL[v] ?? v.replace("Kb", "").replace("Task", "");
}

// 后端 datetime 为 UTC 且序列化不带时区标记 → 无时区后缀时按 UTC 处理
function parseIsoUtc(iso: string): Date {
  if (/Z$|[+-]\d{2}:\d{2}$/.test(iso.trim())) return new Date(iso);
  return new Date(iso.endsWith("Z") ? iso : iso + "Z");
}

function fmtTime(iso?: string | null): string {
  if (!iso) return "-";
  const d = parseIsoUtc(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function fmtDur(ms?: number | null): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}min`;
}

function renderStateTag(state?: string | null) {
  const color = STATE_COLOR[state || ""] || "default";
  const label = STATE_LABEL[state || ""] || state || "-";
  if (state === "RUNNING" || state === "running") {
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

/** 子任务说明（跟进文档参数里的 documentId） */
function parentLabel(record: ParentJobItem): string {
  const tc = taskClassLabel(record.task_class);
  if (record.task_class === "OrchestrationRun") {
    const tapeName = ((record.params || {}).tape_name as string) || "";
    return tapeName ? `编排「${tapeName}」` : "编排执行";
  }
  const docId = ((record.params || {}).documentId as string) || ((record.params || {}).document_id as string) || "";
  return docId ? `${tc} · 文档 ${docId.slice(0, 16)}` : tc;
}

/** 子任务进度：已完成/总数 */
function childProgress(record: ParentJobItem): string {
  const states = record.child_states || {};
  const done = (states.SUCCESS || 0) + (states.success || 0) + (states.FAILED || 0) + (states.failed || 0) + (states.STOPPED || 0) + (states.cancelled || 0);
  return `${done}/${record.child_count || 0}`;
}

/** 展开行：子任务列表（文档链子任务 或 编排节点） */
function ChildTable({ parent }: { parent: ParentJobItem }) {
  const { message } = App.useApp();
  const [rows, setRows] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    apiListJobChildren(parent.id)
      .then((res) => {
        if (alive && res.success) setRows(res.data?.items || []);
      })
      .catch(() => alive && message.error("加载子任务失败"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [parent.id, message]);

  // 编排节点：step_results 结构 {step_id, step_inst, step_label, status, duration_ms, error}
  const isOrch = parent.task_class === "OrchestrationRun";
  const stepCols: ColumnsType<Record<string, unknown>> = [
    { title: "节点", dataIndex: "step_label", width: 180, render: (v, r) => v || String(r.step_id || "") },
    { title: "指令", dataIndex: "step_inst", width: 160, render: (v) => <Text code>{v || "-"}</Text> },
    { title: "状态", dataIndex: "status", width: 110, render: (v) => renderStateTag(String(v || "")) },
    { title: "耗时", dataIndex: "duration_ms", width: 100, render: (v) => fmtDur(v as number | null) },
    { title: "信息", dataIndex: "error", ellipsis: true, render: (v, r) => v ? <Text type="danger">{String(v).slice(0, 80)}</Text> : <Text type="secondary">{(r.stdout_tail as string) || "-"}</Text> },
  ];
  const jobCols: ColumnsType<Record<string, unknown>> = [
    { title: "子任务ID", dataIndex: "id", width: 200, render: (v) => <Text style={{ fontFamily: "monospace", fontSize: 12 }}>{String(v)}</Text> },
    { title: "任务类", dataIndex: "task_class", width: 150, render: (v) => taskClassLabel(String(v || "")) },
    { title: "状态", dataIndex: "state", width: 110, render: (v) => renderStateTag(String(v || "")) },
    { title: "开始时间", dataIndex: "start_time", width: 175, render: (v) => fmtTime(String(v || "")) },
    { title: "耗时", dataIndex: "duration_ms", width: 100, render: (v) => fmtDur(v as number | null) },
    { title: "信息", dataIndex: "error_message", ellipsis: true, render: (v) => v ? <Text type="danger">{String(v).slice(0, 80)}</Text> : <Text type="secondary">-</Text> },
  ];

  return (
    // 2026-10-14: 子表宽度对齐所在单元格（x: "100%"），避免展开行内嵌套表格横向溢出。
    <div style={{ padding: "4px 8px", background: "#FAFBFC" }}>
      <Table<Record<string, unknown>>
        rowKey={(r) => String(r.step_id || r.id || "")}
        size="small"
        loading={loading}
        dataSource={rows}
        columns={isOrch ? stepCols : jobCols}
        pagination={false}
        scroll={{ x: "100%" }}
        locale={{ emptyText: "暂无子任务" }}
      />
    </div>
  );
}

const ParentJobTable: React.FC<ParentJobTableProps> = ({
  loading,
  data,
  scrollY,
  onViewLog,
  onStop,
  onDelete,
}) => {
  const { modal } = App.useApp();
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const columns: ColumnsType<ParentJobItem> = [
    {
      title: "主任务",
      key: "name",
      width: 280,
      render: (_, record) => (
        <Space size={6}>
          {record.task_class === "OrchestrationRun" && (
            <ApartmentOutlined style={{ color: "#1677ff" }} />
          )}
          <Tooltip title={record.id}>
            <Text strong style={{ fontSize: 13 }}>{parentLabel(record)}</Text>
          </Tooltip>
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "state",
      key: "state",
      width: 110,
      render: (state) => renderStateTag(state),
    },
    {
      title: "子任务进度",
      key: "progress",
      width: 130,
      render: (_, record) => {
        const total = record.child_count || 0;
        const done = childProgress(record);
        if (total === 0) return <Text type="secondary">—</Text>;
        return <Text>{done}</Text>;
      },
    },
    {
      title: "触发类型",
      dataIndex: "trigger_type",
      key: "trigger_type",
      width: 100,
      render: (type) => (
        <Tag color={type === "CRON" ? "green" : type === "API" ? "geekblue" : "default"}>
          {type === "CRON" ? "定时" : type === "API" ? "接口" : type || "-"}
        </Tag>
      ),
    },
    {
      title: "开始时间",
      dataIndex: "start_time",
      key: "start_time",
      width: 175,
      render: (val) => fmtTime(val),
    },
    {
      title: "信息 / 错误",
      dataIndex: "error_message",
      key: "error_message",
      ellipsis: true,
      render: (v, row) =>
        v ? (
          <Text type={row.state === "FAILED" || row.state === "failed" ? "danger" : "secondary"} style={{ fontSize: 12 }}>
            {String(v).length > 70 ? `${String(v).slice(0, 70)}…` : String(v)}
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
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => onViewLog(record)}>
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
                  title: "确定停止该任务？",
                  content: `主任务 ${record.id} 运行中，停止后子任务不再推进。`,
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
                title: "确定删除该记录？",
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
    <Table<ParentJobItem>
      rowKey="id"
      size="middle"
      loading={loading}
      dataSource={data}
      columns={columns}
      pagination={false}
      scroll={{ x: 1200, y: scrollY }}
      expandable={{
        expandedRowRender: (record) => <ChildTable parent={record} />,
        rowExpandable: (record) => (record.child_count || 0) > 0,
      }}
      rowClassName={(record) =>
        record.state === "RUNNING" || record.state === "running" ? "modo-running-row" : ""
      }
    />
  );
};

export default ParentJobTable;