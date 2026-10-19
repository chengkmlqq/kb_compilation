"use client";

/**
 * 父任务表格（2026-10-12 任务监控首屏）：
 * 主任务列表（文档处理链头 + 编排执行虚拟父任务），每行可展开看子任务执行过程。
 * 子任务 = modo_job 子任务（文档链）或 编排 run 概要一行（编排）。
 * （2026-10-20：编排内部 step_results 节点不再作为子任务呈现——节点归编排详情/日志视图）
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
  KbOrchestrationRunTask: "编排执行",
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

/** 单行耗时：运行中 → 按本地时钟实时累加；已结束 → 后端 duration_ms，
 *  缺失时（如编排 run 的 duration_ms 恒为 null）用「结束时间 - 开始时间」兜底。 */
function rowDurationMs(record: ParentJobItem, now: number): number | null {
  const start = record.start_time ? parseIsoUtc(record.start_time).getTime() : NaN;
  const running = record.state === "RUNNING" || record.state === "running";
  // 负值钳 0：后台标签页定时器被节流或浏览器/服务器时钟偏斜时会短暂为负
  if (running && !Number.isNaN(start)) return Math.max(0, now - start);
  if (record.duration_ms != null) return record.duration_ms;
  const end = record.end_time ? parseIsoUtc(record.end_time).getTime() : NaN;
  if (!Number.isNaN(start) && !Number.isNaN(end)) return Math.max(0, end - start);
  return null;
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
  // 2026-10-15：优先显示文档文件名（后端已按 documentId 解析并附 doc_name）；
  // 文档已删/查不到时回退原 document_id 前 16 位展示
  const docName = (record as { doc_name?: string }).doc_name || "";
  if (docName) return `${tc} · 文档 ${docName}`;
  const docId = ((record.params || {}).documentId as string) || ((record.params || {}).document_id as string) || "";
  return docId ? `${tc} · 文档 ${docId.slice(0, 16)}` : tc;
}

/** 子任务进度：已完成/总数 */
function childProgress(record: ParentJobItem): string {
  const states = record.child_states || {};
  const done = (states.SUCCESS || 0) + (states.success || 0) + (states.FAILED || 0) + (states.failed || 0) + (states.STOPPED || 0) + (states.cancelled || 0);
  return `${done}/${record.child_count || 0}`;
}

/** 2026-10-15 主任务展示状态合成：链头 job 是「投递即成功」语义（解析完成即
 *  SUCCESS，不等编排子任务），后端 child_states 已按编排 run 真实状态聚合。
 *  自身终态但存在非终态子任务（RUNNING/PENDING）时主任务展示为「运行中」，
 *  全部子任务终态后才显示自身状态。
 */
function effectiveState(record: ParentJobItem): string {
  const own = String(record.state || "");
  if (own === "RUNNING" || own === "running") return own;
  const states = record.child_states || {};
  const pending =
    (states.RUNNING || 0) +
    (states.PENDING || 0) +
    (states.running || 0) +
    (states.queued || 0);
  return pending > 0 ? "RUNNING" : own;
}

/** 展开行：子任务列表（文档链子任务；编排 run 虚拟父任务 → 编排自身概要一行）。

 * 2026-10-20：编排内部 step_results 节点不再作为子任务呈现——节点是编排内部
 * 执行步骤，归编排详情/日志视图（tape-run-log-drawer / 日志抽屉），任务监控
 * 子任务粒度只到「编排 run」这一级。 */

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

  // 2026-10-20：编排 run（OrchestrationRun 虚拟父任务）的 children 由后端返回
  // 编排自身一行概要（不再返回 step_results 节点）——统一用 jobCols 渲染，
  // 子任务粒度只到「编排 run」这一级。
  const jobCols: ColumnsType<Record<string, unknown>> = [
    { title: "子任务ID", dataIndex: "id", ellipsis: true, render: (v) => <Tooltip title={String(v)}><Text style={{ fontFamily: "monospace", fontSize: 12 }}>{String(v).slice(0, 12)}</Text></Tooltip> },
    { title: "任务类", dataIndex: "task_class", ellipsis: true, render: (v) => taskClassLabel(String(v || "")) },
    { title: "状态", dataIndex: "state", width: 110, render: (v) => renderStateTag(String(v || "")) },
    { title: "开始时间", dataIndex: "start_time", width: 150, render: (v) => fmtTime(String(v || "")) },
    { title: "耗时", dataIndex: "duration_ms", width: 90, render: (v) => fmtDur(v as number | null) },
    {
      title: "信息 / 错误",
      dataIndex: "error_message",
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
  ];

  return (
    // 2026-10-14: 子表宽度对齐所在单元格（x: "100%"），避免展开行内嵌套表格横向溢出。
    <div style={{ padding: "4px 8px", background: "#FAFBFC" }}>
      <Table<Record<string, unknown>>
        rowKey={(r) => String(r.id || "")}
        size="small"
        loading={loading}
        dataSource={rows}
        columns={jobCols}
        pagination={false}
        // 2026-10-15: tableLayout fixed——子表宽度锁 100% 展开区，列宽固定、多余空间由
        // 省略号列吸收；此前子表按内容最小宽度撑开，窄视口下溢出展开区域。
        tableLayout="fixed"
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
      render: (state, record) => renderStateTag(effectiveState(record as ParentJobItem)),
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
      title: "结束时间",
      dataIndex: "end_time",
      key: "end_time",
      width: 175,
      render: (val) => fmtTime(val),
    },
    {
      title: "耗时",
      key: "duration_ms",
      width: 100,
      render: (_, record) => {
        const text = fmtDur(rowDurationMs(record, now));
        // 运行中（含子任务未完成合成态）：套实时样式（与「全部任务」表一致）
        return effectiveState(record as ParentJobItem) === "RUNNING" ? (
          <span className="modo-running-duration">{text}</span>
        ) : (
          text
        );
      },
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
      scroll={{ x: 1220, y: scrollY }}
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