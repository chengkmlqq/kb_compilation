"use client";

/**
 * 任务监控页 —— 布局对齐 data-synth job-monitor：
 * 顶部统计卡（JobStats）+ 筛选栏（JobFilter）+ 表格（JobTable）+ 日志抽屉。
 * 数据源 modo_job（list/statistics/queues/stop/delete）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { App, Segmented } from "antd";
import JobStats from "./job-stats";
import JobFilter from "./job-filter";
import JobTable from "./job-table";
import ParentJobTable from "./parent-job-table";
import JobLogDrawer from "./job-log-drawer";
import ModoPagination from "@/components/biz/modo-pagination";
import {
  apiDeleteJob,
  apiJobStatistics,
  apiListJobs,
  apiListParentJobs,
  apiStopJob,
  JobItem,
  JobStatistics,
  ParentJobItem,
} from "@/lib/api";

type JobFilterValues = {
  keyWord?: string;
  queueName?: string;
  state?: string;
};

const EMPTY_STATS: JobStatistics = {
  total: 0,
  running: 0,
  success: 0,
  failed: 0,
  stopped: 0,
  queued: 0,
};

export default function JobsPage() {
  const { message } = App.useApp();
  // 2026-10-12 父子任务：首屏默认「主任务」视图（父任务列表+展开子任务），
  // 可切换「全部任务」退回平铺视图（兼容老习惯）。
  const [view, setView] = useState<"parents" | "all">("parents");
  const [items, setItems] = useState<JobItem[]>([]);
  const [parentItems, setParentItems] = useState<ParentJobItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<JobStatistics>(EMPTY_STATS);
  const [statsLoading, setStatsLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filters, setFilters] = useState<JobFilterValues>({});

  const [detailJobId, setDetailJobId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const latestQueryRef = useRef({ page: 1, pageSize: 20, filters: {} as JobFilterValues });

  // 2026-10-08: 表格可用高度实测——统计卡/筛选栏高度由内容决定，写死 vh 偏移易失准；
  // 这里量「表格容器」的实际高度传给 antd Table 的 scroll.y，窗口缩放/布局变化自动跟随。
  const [tableBodyHeight, setTableBodyHeight] = useState<number>();
  const tableBodyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = tableBodyRef.current;
    if (!el) return;
    const update = () => setTableBodyHeight(el.clientHeight);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const load = useCallback(
    async (p = page, size = pageSize, f = filters, v = view) => {
      setLoading(true);
      try {
        if (v === "parents") {
          const res = await apiListParentJobs({
            page: p,
            page_size: size,
            state: f.state,
            keyword: f.keyWord?.trim() || undefined,
          });
          if (res.success && res.data) {
            setParentItems(res.data.items);
            setTotal(res.data.total);
            latestQueryRef.current = { page: p, pageSize: size, filters: f };
          } else {
            message.error(res.message || "加载主任务失败");
          }
        } else {
          const res = await apiListJobs({
            page: p,
            page_size: size,
            state: f.state,
            keyword: f.keyWord?.trim() || undefined,
            queue_name: f.queueName,
          });
          if (res.success && res.data) {
            setItems(res.data.items);
            setTotal(res.data.total);
            latestQueryRef.current = { page: p, pageSize: size, filters: f };
          } else {
            message.error(res.message || "加载任务失败");
          }
        }
      } finally {
        setLoading(false);
      }
    },
    [page, pageSize, filters, view, message],
  );

  const loadStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const res = await apiJobStatistics();
      if (res.success && res.data) setStats(res.data);
    } finally {
      setStatsLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    const q = latestQueryRef.current;
    void load(q.page, q.pageSize, q.filters);
    void loadStats();
  }, [load, loadStats]);

  useEffect(() => {
    void load();
    void loadStats();
  }, [load, loadStats]);

  // agent-gateway 长任务可能运行 10-25 分钟：有 RUNNING 任务时自动刷新
  const hasRunning =
    (view === "parents"
      ? parentItems.some((i) => i.state === "RUNNING")
      : items.some((i) => i.state === "RUNNING"));
  useEffect(() => {
    if (!hasRunning) return;
    const timer = setInterval(() => refresh(), 8000);
    return () => clearInterval(timer);
  }, [hasRunning, refresh]);

  const handleSearch = (values: JobFilterValues) => {
    setFilters(values);
    setPage(1);
    void load(1, pageSize, values);
  };

  const handleReset = () => {
    const next: JobFilterValues = {};
    setFilters(next);
    setPage(1);
    void load(1, pageSize, next);
  };

  const handleStop = async (job: JobItem) => {
    const res = await apiStopJob(job.id);
    if (res.success) {
      message.success("已停止");
      refresh();
    } else {
      message.error(res.message || "停止失败");
    }
  };

  const handleDelete = async (job: JobItem) => {
    const res = await apiDeleteJob(job.id);
    if (res.success) {
      message.success("已删除");
      refresh();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const handleViewLog = (job: JobItem) => {
    setDetailJobId(job.id);
    setDetailOpen(true);
  };

  return (
    // 2026-10-08: 对齐用户管理页「固定视口高度」——外层锁定 视口 − 顶栏(45px)，
    // 使 flex 链路有界：统计卡/筛选栏定高，表格区吃满剩余空间，任务再多也不溢出屏幕。
    <div
      className="jobs-page"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - var(--app-header-h, 48px))",
        minHeight: 0,
        background: "#EFF4F9",
        padding: 10,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          background: "#F5F7FA",
        }}
      >
        {/* Stats Area */}
        <div style={{ flexShrink: 0 }}>
          <JobStats statistics={stats} loading={statsLoading} />
        </div>

        {/* Filter Area：视图切换（主任务/全部任务）作为 extra 放到筛选卡片右侧 */}
        <div style={{ flexShrink: 0 }}>
          <JobFilter
            onSearch={handleSearch}
            onReset={handleReset}
            extra={
              <Segmented
                size="small"
                value={view}
                options={[
                  { label: "主任务", value: "parents" },
                  { label: "全部任务", value: "all" },
                ]}
                onChange={(v) => {
                  setView(v as "parents" | "all");
                  setPage(1);
                  void load(1, pageSize, filters, v as "parents" | "all");
                }}
              />
            }
          />
        </div>

        {/* Table + Pagination Area */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            overflow: "hidden",
            background: "#fff",
            borderRadius: 8,
            border: "1px solid #E3E9EF",
          }}
        >
          <div ref={tableBodyRef} style={{ flex: 1, minHeight: 0, overflow: "hidden", padding: "0 16px" }}>
            {view === "parents" ? (
              <ParentJobTable
                loading={loading}
                data={parentItems}
                scrollY={tableBodyHeight}
                onViewLog={handleViewLog}
                onStop={handleStop}
                onDelete={handleDelete}
              />
            ) : (
              <JobTable
                loading={loading}
                data={items}
                scrollY={tableBodyHeight}
                onViewLog={handleViewLog}
                onStop={handleStop}
                onDelete={handleDelete}
              />
            )}
          </div>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
            showTotal={(t) => `共 ${t} 条`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
              void load(p, ps, filters);
            }}
          />
        </div>
      </div>

      <JobLogDrawer
        visible={detailOpen}
        jobId={detailJobId}
        onClose={() => setDetailOpen(false)}
        onStateChange={() => loadStats()}
      />
    </div>
  );
}