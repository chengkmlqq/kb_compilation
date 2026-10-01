"use client";

/**
 * 任务监控页 —— modo_job 列表 + 详情抽屉。
 *
 * 展示所有后台任务（文档处理 / wiki 构建 / agent-gateway 技能执行等）：
 * 状态、耗时、触发方式、参数、错误/进度日志。agent-gateway 长任务在轮询
 * 期间把进度写入 error_message（[poll N] gateway status=…），此处可见实时进度。
 */
import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { apiGetJob, apiListJobs, JobItem } from "@/lib/api";

const { Text } = Typography;

const STATE_COLOR: Record<string, string> = {
  PENDING: "default",
  RUNNING: "processing",
  SUCCESS: "success",
  FAILED: "error",
};

const TRIGGER_LABEL: Record<string, string> = {
  API: "接口触发",
  CRON: "定时触发",
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

export default function JobsPage() {
  const { message } = App.useApp();
  const [items, setItems] = useState<JobItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [taskClass, setTaskClass] = useState<string>();
  const [state, setState] = useState<string>();
  const [keyword, setKeyword] = useState("");
  const [detail, setDetail] = useState<JobItem | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListJobs({
        page,
        page_size: pageSize,
        task_class: taskClass,
        state,
        keyword: keyword.trim() || undefined,
      });
      if (res.success && res.data) {
        setItems(res.data.items);
        setTotal(res.data.total);
      } else {
        message.error(res.message || "加载任务失败");
      }
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, taskClass, state, keyword, message]);

  useEffect(() => {
    void load();
  }, [load]);

  // agent-gateway 长任务可能运行 10-25 分钟：有 RUNNING 任务时自动刷新
  const hasRunning = items.some((i) => i.state === "RUNNING");
  useEffect(() => {
    if (!hasRunning) return;
    const timer = setInterval(() => void load(), 8000);
    return () => clearInterval(timer);
  }, [hasRunning, load]);

  const openDetail = async (job: JobItem) => {
    setDetailOpen(true);
    setDetailLoading(true);
    try {
      const res = await apiGetJob(job.id);
      if (res.success && res.data) setDetail(res.data);
      else message.error(res.message || "加载详情失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const paramsText = (job: JobItem): string => {
    try {
      return JSON.stringify(job.params || {}, null, 2);
    } catch {
      return String(job.params || "");
    }
  };

  return (
    <Card
      title="任务监控"
      extra={
        <Space>
          <Input.Search
            placeholder="搜索任务 ID / 文档 ID"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onSearch={() => {
              setPage(1);
              void load();
            }}
            allowClear
            style={{ width: 220 }}
          />
          <Select
            placeholder="任务类型"
            allowClear
            style={{ width: 200 }}
            value={taskClass}
            onChange={(v) => {
              setTaskClass(v);
              setPage(1);
            }}
            options={[
              { value: "KbDocumentProcessTask", label: "文档处理" },
              { value: "KbDocumentEmbedTask", label: "向量化" },
              { value: "KbWikiBuildTask", label: "Wiki 构建" },
              { value: "KbGraphBuildTask", label: "图谱构建" },
              { value: "KbAgentGatewayTask", label: "Agent 技能" },
            ]}
          />
          <Select
            placeholder="状态"
            allowClear
            style={{ width: 120 }}
            value={state}
            onChange={(v) => {
              setState(v);
              setPage(1);
            }}
            options={[
              { value: "PENDING", label: "待执行" },
              { value: "RUNNING", label: "执行中" },
              { value: "SUCCESS", label: "成功" },
              { value: "FAILED", label: "失败" },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        </Space>
      }
    >
      <Table<JobItem>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
        locale={{ emptyText: <Empty description="暂无任务" /> }}
        columns={[
          {
            title: "任务 ID",
            dataIndex: "id",
            width: 200,
            ellipsis: true,
            render: (v: string, row) => (
              <Typography.Link onClick={() => void openDetail(row)} style={{ fontFamily: "monospace" }}>
                {v}
              </Typography.Link>
            ),
          },
          {
            title: "类型",
            dataIndex: "task_class",
            width: 170,
            render: (v: string) => (
              <Tag>{v?.replace("Kb", "").replace("Task", "") || "-"}</Tag>
            ),
          },
          {
            title: "状态",
            dataIndex: "state",
            width: 100,
            render: (v: string) => <Tag color={STATE_COLOR[v] || "default"}>{v || "-"}</Tag>,
          },
          {
            title: "触发",
            dataIndex: "trigger_type",
            width: 90,
            render: (v: string) => TRIGGER_LABEL[v] || v || "-",
          },
          {
            title: "耗时",
            dataIndex: "duration_ms",
            width: 90,
            render: (v: number | null) => fmtDur(v),
          },
          {
            title: "开始时间",
            dataIndex: "start_time",
            width: 170,
            render: (v: string | null) => fmtTime(v),
          },
          {
            title: "进度 / 错误",
            dataIndex: "error_message",
            ellipsis: true,
            render: (v: string | null, row) =>
              v ? (
                <Text type={row.state === "FAILED" ? "danger" : "secondary"} style={{ fontSize: 12 }}>
                  {v.length > 80 ? `${v.slice(0, 80)}…` : v}
                </Text>
              ) : (
                <Text type="secondary">-</Text>
              ),
          },
        ]}
      />

      <Drawer
        title={detail?.id || "任务详情"}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={520}
      >
        {detailLoading ? (
          <Space style={{ width: "100%", justifyContent: "center", padding: 40 }}>
            <Button type="link" loading />
          </Space>
        ) : detail ? (
          <Space direction="vertical" style={{ display: "flex" }} size="middle">
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="状态">
                <Tag color={STATE_COLOR[detail.state || ""] || "default"}>{detail.state || "-"}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="任务类型">{detail.task_class || "-"}</Descriptions.Item>
              <Descriptions.Item label="触发方式">
                {TRIGGER_LABEL[detail.trigger_type || ""] || detail.trigger_type || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="任务 ID（Celery）">
                <Text style={{ fontFamily: "monospace", fontSize: 12 }}>{detail.task_id || "-"}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="耗时">{fmtDur(detail.duration_ms)}</Descriptions.Item>
              <Descriptions.Item label="开始时间">{fmtTime(detail.start_time)}</Descriptions.Item>
              <Descriptions.Item label="结束时间">{fmtTime(detail.end_time)}</Descriptions.Item>
            </Descriptions>

            {detail.error_message && (
              <div>
                <Text strong>进度 / 错误</Text>
                <pre
                  style={{
                    background: "#fafafa",
                    border: "1px solid #f0f0f0",
                    borderRadius: 6,
                    padding: 10,
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    color: detail.state === "FAILED" ? "#cf1322" : "#666",
                  }}
                >
                  {detail.error_message}
                </pre>
              </div>
            )}

            <div>
              <Text strong>任务参数</Text>
              <pre
                style={{
                  background: "#fafafa",
                  border: "1px solid #f0f0f0",
                  borderRadius: 6,
                  padding: 10,
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                }}
              >
                {paramsText(detail)}
              </pre>
            </div>
          </Space>
        ) : (
          <Empty description="无详情" />
        )}
      </Drawer>
    </Card>
  );
}
