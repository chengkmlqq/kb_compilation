"use client";

/**
 * Worker 监控页 —— 迁移自 data-synth 主机监控（Flower）。
 *
 * 数据经后端 /api/v1/workers 代理 Flower HTTP API：
 * worker 概览（状态/队列/运行中/保留中/已调度/注册任务/心跳），点击
 * 「查看任务」打开任务快照抽屉（active/reserved/scheduled/recent 四 Tab），
 * 点击注册任务数打开定时配置抽屉（modo_cron_task 关联）。5s 自动轮询。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  App,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { TabsProps } from "antd";
import {
  apiFlowerWorkerTasks,
  apiFlowerWorkers,
  apiWorkerRegisteredTasks,
  FlowerWorkerOverview,
  FlowerWorkerTaskItem,
  FlowerWorkerTaskOverview,
  FlowerWorkersOverview,
  WorkerRegisteredTaskConfigOverview,
} from "@/lib/api";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";

const { Text } = Typography;

const POLL_INTERVAL_MS = 5000;

const WORKER_STATUS_MAP: Record<string, { label: string; color: string }> = {
  ONLINE: { label: "在线", color: "success" },
  OFFLINE: { label: "离线", color: "default" },
};

const TASK_STATE_COLOR: Record<string, string> = {
  SUCCESS: "success",
  FAILURE: "error",
  FAILED: "error",
  STARTED: "processing",
  RUNNING: "processing",
  ACTIVE: "processing",
  RETRY: "warning",
  RECEIVED: "purple",
  PENDING: "purple",
  REVOKED: "warning",
};

const EMPTY_OVERVIEW: FlowerWorkersOverview = {
  workers: [],
  summary: { total: 0, online: 0, offline: 0 },
  collectedAt: "",
};

function formatTime(value?: string | null) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", { hour12: false });
}

function formatRuntime(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value) || value < 0) return "-";
  return `${value.toFixed(3)}s`;
}

function renderCellText(value: unknown) {
  const text = String(value ?? "").trim();
  if (!text) return "-";
  return (
    <Tooltip placement="topLeft" title={text}>
      <span
        style={{
          display: "inline-block",
          maxWidth: "100%",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {text}
      </span>
    </Tooltip>
  );
}

function renderStateTag(state: string) {
  const normalized = String(state || "").trim().toUpperCase();
  return (
    <Tag color={TASK_STATE_COLOR[normalized] || "default"}>
      {normalized || "-"}
    </Tag>
  );
}

/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type WorkerTab = { key: string; title: string; children: ReactNode };

export default function WorkersPage() {
  const { message } = App.useApp();

  const [searchForm] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [overview, setOverview] = useState<FlowerWorkersOverview>(EMPTY_OVERVIEW);
  const [filter, setFilter] = useState<{ workerKeyword?: string; status?: string }>({});
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const inFlightRef = useRef(false);

  const [taskDrawerOpen, setTaskDrawerOpen] = useState(false);
  const [taskWorkerName, setTaskWorkerName] = useState("");
  const [taskOverview, setTaskOverview] = useState<FlowerWorkerTaskOverview | null>(null);
  const [taskLoading, setTaskLoading] = useState(false);
  const taskInFlightRef = useRef(false);

  const [registeredDrawerOpen, setRegisteredDrawerOpen] = useState(false);
  const [registeredWorkerName, setRegisteredWorkerName] = useState("");
  const [registeredOverview, setRegisteredOverview] =
    useState<WorkerRegisteredTaskConfigOverview | null>(null);
  const [registeredLoading, setRegisteredLoading] = useState(false);

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「主机监控」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<WorkerTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  const fetchOverview = useCallback(
    async (options?: { forceRefresh?: boolean; silent?: boolean }) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      setLoading(true);
      try {
        const res = await apiFlowerWorkers(Boolean(options?.forceRefresh));
        if (res.success && res.data) {
          setOverview(res.data);
        } else if (!options?.silent) {
          message.error(res.message || "获取 Worker 监控数据失败");
        }
      } catch (e) {
        if (!options?.silent) {
          message.error(e instanceof Error ? e.message : "获取 Worker 监控数据异常");
        }
      } finally {
        setLoading(false);
        inFlightRef.current = false;
      }
    },
    [message],
  );

  const fetchWorkerTasks = useCallback(
    async (workerName: string, options?: { forceRefresh?: boolean; silent?: boolean }) => {
      if (!workerName || taskInFlightRef.current) return;
      taskInFlightRef.current = true;
      setTaskLoading(true);
      try {
        const res = await apiFlowerWorkerTasks(workerName, 100);
        if (res.success && res.data) {
          setTaskOverview(res.data);
        } else if (!options?.silent) {
          message.error(res.message || "获取 Worker 任务失败");
        }
      } catch (e) {
        if (!options?.silent) {
          message.error(e instanceof Error ? e.message : "获取 Worker 任务异常");
        }
      } finally {
        setTaskLoading(false);
        taskInFlightRef.current = false;
      }
    },
    [message],
  );

  useEffect(() => {
    void fetchOverview({ forceRefresh: true });
    const timer = setInterval(
      () => void fetchOverview({ forceRefresh: false, silent: true }),
      POLL_INTERVAL_MS,
    );
    return () => clearInterval(timer);
  }, [fetchOverview]);

  // 任务抽屉打开时轮询
  useEffect(() => {
    if (!taskDrawerOpen || !taskWorkerName) return;
    const timer = setInterval(
      () => void fetchWorkerTasks(taskWorkerName, { forceRefresh: false, silent: true }),
      POLL_INTERVAL_MS,
    );
    return () => clearInterval(timer);
  }, [taskDrawerOpen, taskWorkerName, fetchWorkerTasks]);

  const filteredWorkers = useMemo(() => {
    const kw = String(filter.workerKeyword || "").trim().toLowerCase();
    return overview.workers.filter((w) => {
      if (kw && !w.workerName.toLowerCase().includes(kw)) return false;
      if (filter.status && w.status !== filter.status) return false;
      return true;
    });
  }, [overview.workers, filter]);

  const pagedWorkers = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredWorkers.slice(start, start + pageSize);
  }, [filteredWorkers, currentPage, pageSize]);

  const handleSearch = (values: { workerKeyword?: string; status?: string }) => {
    setFilter({ workerKeyword: values.workerKeyword, status: values.status });
    setCurrentPage(1);
  };

  const handleReset = () => {
    searchForm.resetFields();
    setFilter({});
    setCurrentPage(1);
  };

  const openTaskDrawer = useCallback(
    (workerName: string) => {
      setTaskWorkerName(workerName);
      setTaskOverview(null);
      setTaskDrawerOpen(true);
      void fetchWorkerTasks(workerName, { forceRefresh: true });
    },
    [fetchWorkerTasks],
  );

  const openRegisteredDrawer = useCallback(
    async (worker: FlowerWorkerOverview) => {
      setRegisteredWorkerName(worker.workerName);
      setRegisteredOverview(null);
      setRegisteredDrawerOpen(true);
      setRegisteredLoading(true);
      try {
        const res = await apiWorkerRegisteredTasks(worker.workerName);
        if (res.success && res.data) {
          setRegisteredOverview(res.data);
        } else {
          message.error(res.message || "获取注册任务配置失败");
        }
      } catch (e) {
        message.error(e instanceof Error ? e.message : "获取注册任务配置异常");
      } finally {
        setRegisteredLoading(false);
      }
    },
    [message],
  );

  const columns: ColumnsType<FlowerWorkerOverview> = useMemo(
    () => [
      {
        title: "Worker",
        dataIndex: "workerName",
        key: "workerName",
        width: 280,
        ellipsis: true,
        render: (v: string) => renderCellText(v),
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        width: 90,
        render: (v: FlowerWorkerOverview["status"]) => {
          const meta = WORKER_STATUS_MAP[v] ?? { label: v, color: "default" };
          return <Tag color={meta.color}>{meta.label}</Tag>;
        },
      },
      {
        title: "队列",
        dataIndex: "activeQueueNames",
        key: "activeQueueNames",
        width: 200,
        ellipsis: true,
        render: (v: string[]) =>
          Array.isArray(v) && v.length ? v.join(", ") : "-",
      },
      { title: "运行中", dataIndex: "activeTaskCount", key: "active", width: 80 },
      { title: "保留中", dataIndex: "reservedTaskCount", key: "reserved", width: 80 },
      { title: "已调度", dataIndex: "scheduledTaskCount", key: "scheduled", width: 80 },
      {
        title: "已注册任务数",
        dataIndex: "registeredTaskCount",
        key: "registered",
        width: 120,
        render: (count: number, record) =>
          count > 0 ? (
            <Button
              type="link"
              size="small"
              style={{ padding: 0 }}
              onClick={() => void openRegisteredDrawer(record)}
            >
              {count}
            </Button>
          ) : (
            0
          ),
      },
      {
        title: "已处理任务数",
        dataIndex: "processedTaskCount",
        key: "processed",
        width: 120,
      },
      { title: "并发", dataIndex: "concurrency", key: "concurrency", width: 70 },
      { title: "Prefetch", dataIndex: "prefetchCount", key: "prefetch", width: 80 },
      {
        title: "最近心跳",
        dataIndex: "lastHeartbeatAt",
        key: "heartbeat",
        width: 165,
        render: (v: string | null) => formatTime(v),
      },
      {
        title: "操作",
        key: "action",
        width: 110,
        fixed: "right" as const,
        render: (_, record) => (
          <Button
            type="link"
            size="small"
            onClick={() => openTaskDrawer(record.workerName)}
          >
            查看任务
          </Button>
        ),
      },
    ],
    [openRegisteredDrawer, openTaskDrawer],
  );

  const taskColumns: ColumnsType<FlowerWorkerTaskItem> = useMemo(
    () => [
      {
        title: "任务ID",
        dataIndex: "taskId",
        key: "taskId",
        width: 280,
        ellipsis: true,
        render: (v: string) => renderCellText(v),
      },
      {
        title: "任务名",
        dataIndex: "taskName",
        key: "taskName",
        width: 220,
        ellipsis: true,
        render: (v: string) => renderCellText(v),
      },
      {
        title: "状态",
        dataIndex: "state",
        key: "state",
        width: 110,
        render: (v: string) => renderStateTag(v),
      },
      {
        title: "队列",
        dataIndex: "queueName",
        key: "queueName",
        width: 120,
        render: (v: string | null) => v || "-",
      },
      {
        title: "开始",
        dataIndex: "startedAt",
        key: "startedAt",
        width: 165,
        render: (v: string | null) => formatTime(v),
      },
      {
        title: "耗时",
        dataIndex: "runtimeSeconds",
        key: "runtime",
        width: 100,
        render: (v: number | null) => formatRuntime(v),
      },
    ],
    [],
  );

  const taskTabItems: TabsProps["items"] = useMemo(() => {
    const s = taskOverview?.summary;
    const mkTable = (data: FlowerWorkerTaskItem[]) => (
      <Table<FlowerWorkerTaskItem>
        rowKey={(r) => r.taskId}
        size="small"
        dataSource={data}
        columns={taskColumns}
        scroll={{ x: 1100, y: "calc(100vh - 308px)" }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        locale={{ emptyText: "暂无任务" }}
      />
    );
    return [
      { key: "active", label: `运行中 (${s?.active ?? 0})`, children: mkTable(taskOverview?.activeTasks ?? []) },
      { key: "reserved", label: `保留中 (${s?.reserved ?? 0})`, children: mkTable(taskOverview?.reservedTasks ?? []) },
      { key: "scheduled", label: `已调度 (${s?.scheduled ?? 0})`, children: mkTable(taskOverview?.scheduledTasks ?? []) },
      { key: "recent", label: `最近任务 (${s?.recent ?? 0})`, children: mkTable(taskOverview?.recentTasks ?? []) },
    ];
  }, [taskOverview, taskColumns]);

  // 列表区（首个选项卡内容）：摘要 + 筛选 + 表格 + 钉底分页
  const listPane = (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            padding: "12px 16px 0",
          },
        }}
      >
        <Form
          form={searchForm}
          layout="inline"
          style={{ marginBottom: 12, flexShrink: 0 }}
          onFinish={handleSearch}
        >
          <Form.Item name="workerKeyword" label="Worker 名称">
            <Input allowClear placeholder="请输入 Worker 名称" style={{ width: 220 }} />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select
              allowClear
              placeholder="请选择状态"
              style={{ width: 120 }}
              options={[
                { label: "在线", value: "ONLINE" },
                { label: "离线", value: "OFFLINE" },
              ]}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button onClick={handleReset}>重置</Button>
            </Space>
          </Form.Item>
        </Form>

        <Table<FlowerWorkerOverview>
          rowKey="workerName"
          size="small"
          loading={loading}
          dataSource={pagedWorkers}
          columns={columns}
          pagination={false}
          scroll={{ x: 1500, y: "calc(100vh - 252px)" }}
        />

        <div style={{ borderTop: "1px solid #E3E9EF", flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={currentPage}
            pageSize={pageSize}
            total={filteredWorkers.length}
            showTotal={(t) => `共 ${t} 个 Worker`}
            onChange={(p, ps) => {
              setCurrentPage(p);
              setPageSize(ps);
            }}
          />
        </div>
      </Card>
    </div>
  );

  return (
    <div
      className="workers-page"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 45px)",
        minHeight: 0,
        background: "#F5F7FA",
        padding: 8,
        overflow: "hidden",
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action === "remove") closeTab(String(key));
        }}
        tabBarExtraContent={
          <Space size={16} wrap>
            <span>
              总数: <strong>{overview.summary.total}</strong>
            </span>
            <span style={{ color: "#389e0d" }}>
              在线: <strong>{overview.summary.online}</strong>
            </span>
            <span style={{ color: "#79879C" }}>
              离线: <strong>{overview.summary.offline}</strong>
            </span>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void fetchOverview({ forceRefresh: true })}
            >
              刷新
            </Button>
          </Space>
        }
        items={[
          { key: "home", label: "主机监控", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />

      {/* 任务快照抽屉 */}
      <Drawer
        title={`Worker 任务查看 - ${taskWorkerName || "-"}`}
        placement="right"
        width={1100}
        open={taskDrawerOpen}
        onClose={() => setTaskDrawerOpen(false)}
        destroyOnClose
      >
        <div style={{ marginBottom: 12, color: "#79879C", fontSize: 13 }}>
          采集时间：{formatTime(taskOverview?.collectedAt)}
          <Button
            type="link"
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => void fetchWorkerTasks(taskWorkerName, { forceRefresh: true })}
          >
            刷新
          </Button>
        </div>
        <Tabs items={taskTabItems} />
      </Drawer>

      {/* 注册任务配置抽屉 */}
      <Drawer
        title={`注册任务 - ${registeredWorkerName || "-"}`}
        placement="right"
        width={680}
        open={registeredDrawerOpen}
        onClose={() => setRegisteredDrawerOpen(false)}
        destroyOnClose
      >
        <Table
          rowKey="taskClass"
          size="small"
          loading={registeredLoading}
          dataSource={registeredOverview?.tasks ?? []}
          pagination={false}
          locale={{ emptyText: "暂无注册任务" }}
          columns={[
            {
              title: "任务名",
              dataIndex: "taskName",
              key: "taskName",
              width: 200,
              render: (v: string) => renderCellText(v),
            },
            {
              title: "任务类",
              dataIndex: "taskClass",
              key: "taskClass",
              width: 220,
              ellipsis: true,
              render: (v: string) => renderCellText(v),
            },
            {
              title: "定时配置",
              dataIndex: "cronConfigs",
              key: "cronConfigs",
              render: (cfgs: Array<{ label?: string; cronExpression?: string; state?: string }>) =>
                Array.isArray(cfgs) && cfgs.length ? (
                  <Space direction="vertical" size={2}>
                    {cfgs.map((cfg, i) => (
                      <Tag key={i} color={cfg.state === "1" ? "green" : "default"}>
                        {cfg.label || cfg.cronExpression || "-"}
                        {cfg.cronExpression ? ` (${cfg.cronExpression})` : ""}
                      </Tag>
                    ))}
                  </Space>
                ) : (
                  <Text type="secondary">-</Text>
                ),
            },
          ]}
        />
        {registeredOverview?.summary ? (
          <div style={{ marginTop: 12, color: "#79879C", fontSize: 13 }}>
            共 {registeredOverview.summary.taskCount} 个任务、
            {registeredOverview.summary.configuredTaskCount} 个已配置定时、
            {registeredOverview.summary.cronConfigCount} 条 cron 规则
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}