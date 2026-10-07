"use client";

/**
 * 任务监控页 —— 布局对齐 data-synth job-monitor：
 * 顶部统计卡（JobStats）+ 筛选栏（JobFilter）+ 表格（JobTable）+ 日志抽屉。
 * 数据源 modo_job（list/statistics/queues/stop/delete）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { App } from "antd";
import JobStats from "./job-stats";
import JobFilter from "./job-filter";
import JobTable from "./job-table";
import JobLogDrawer from "./job-log-drawer";
import ModoPagination from "@/components/biz/modo-pagination";
import {
  apiDeleteJob,
  apiJobStatistics,
  apiListJobs,
  apiStopJob,
  JobItem,
  JobStatistics,
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
  const [items, setItems] = useState<JobItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<JobStatistics>(EMPTY_STATS);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filters, setFilters] = useState<JobFilterValues>({});

  const [detailJobId, setDetailJobId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const latestQueryRef = useRef({ page: 1, pageSize: 20, filters: {} as JobFilterValues });

  const load = useCallback(
    async (p = page, size = pageSize, f = filters) => {
      setLoading(true);
      try {
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
      } finally {
        setLoading(false);
      }
    },
    [page, pageSize, filters, message],
  );

  const loadStats = useCallback(async () => {
    const res = await apiJobStatistics();
    if (res.success && res.data) setStats(res.data);
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
  const hasRunning = items.some((i) => i.state === "RUNNING");
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
    <div
      className="modo-page"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        background: "#F5F7FA",
        padding: 8,
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
          <JobStats statistics={stats} />
        </div>

        {/* Filter Area */}
        <div style={{ flexShrink: 0 }}>
          <JobFilter onSearch={handleSearch} onReset={handleReset} />
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
          <JobTable
            loading={loading}
            data={items}
            onViewLog={handleViewLog}
            onStop={handleStop}
            onDelete={handleDelete}
          />
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