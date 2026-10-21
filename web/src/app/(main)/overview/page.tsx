"use client";

/**
 * 首页总览（知识构建看板，2026-10-20）
 *
 * 一屏呈现平台知识构建全链路：规模指标卡 + 近 N 天构建趋势 + 解析状态分布 +
 * 任务情况；卡片可点击下钻到对应明细页。
 * 布局对齐「任务监控」页（padding:10 + #EFF4F9 壳 → 内层 #F5F7FA）。
 * 数据口径按调用方可见范围（后端 /api/v1/overview/* 处理）。
 */
import { useCallback, useEffect, useMemo, useState, type ComponentType, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { App, Empty, Segmented, Spin, Tooltip } from "antd";
import {
  BookOutlined,
  CheckCircleOutlined,
  DislikeOutlined,
  FileTextOutlined,
  PartitionOutlined,
  ReloadOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import {
  apiOverviewSummary,
  apiOverviewTrend,
  OverviewSummary,
  OverviewTrendDay,
} from "@/lib/api";

/** 图表懒加载包装：charts 体积大，加载期间占位，避免影响首屏 */
function ChartLazy({
  chart,
  data,
  height,
  config = {},
}: {
  chart: "line" | "pie" | "column";
  data: unknown[];
  height: number;
  /** 图表字段映射配置（xField/yField/seriesField/angleField/colorField 等），
   *  @ant-design/plots v2（G2 架构）缺字段映射时 encode 为空，图表渲染空白 */
  config?: Record<string, unknown>;
}) {
  const [Comp, setComp] = useState<ComponentType<any> | null>(null);
  useEffect(() => {
    let alive = true;
    void import("@ant-design/charts").then((m) => {
      if (!alive) return;
      const map = { line: m.Line, pie: m.Pie, column: m.Column };
      setComp(() => map[chart] as unknown as ComponentType<any>);
    });
    return () => {
      alive = false;
    };
  }, [chart]);
  if (!Comp) {
    return (
      <div style={{ height, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Spin />
      </div>
    );
  }
  return <Comp {...config} data={data} height={height} />;
}

const inFlight = (byState: Record<string, number>) =>
  (byState.PENDING || 0) + (byState.PARSING || 0) + (byState.EMBEDDING || 0);

const fmtDuration = (ms: number) => {
  if (!ms) return "--";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}min`;
};

export default function OverviewPage() {
  const { message } = App.useApp();
  const router = useRouter();
  const [summary, setSummary] = useState<OverviewSummary | null>(null);
  const [trend, setTrend] = useState<OverviewTrendDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [trendLoading, setTrendLoading] = useState(false);
  const [trendDays, setTrendDays] = useState(7);

  const loadSummary = useCallback(async () => {
    try {
      const res = await apiOverviewSummary();
      if (res.success && res.data) setSummary(res.data);
      else message.error(res.message || "加载总览失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  const loadTrend = useCallback(async (days: number) => {
    setTrendLoading(true);
    try {
      const res = await apiOverviewTrend(days);
      if (res.success && res.data) setTrend(res.data.days);
    } finally {
      setTrendLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    void loadTrend(trendDays);
  }, [loadTrend, trendDays]);

  // ── 指标卡（对齐任务监控页 JobStats 视觉：48px 圆角图标底 / 26px 数值）──
  const statCards = useMemo(() => {
    const s = summary;
    return [
      {
        key: "kbs",
        label: "知识库",
        value: s?.kbs.total ?? 0,
        sub: s
          ? `个人 ${s.kbs.by_scope.personal || 0} · 团队 ${s.kbs.by_scope.team || 0} · 系统 ${s.kbs.by_scope.system || 0}`
          : "",
        icon: <BookOutlined style={{ fontSize: 22 }} />,
        color: "#1677ff",
        bg: "#E6F4FF",
        onClick: () => router.push("/kbs"),
      },
      {
        key: "docs",
        label: "文档",
        value: s?.docs.total ?? 0,
        sub: s ? `处理中 ${inFlight(s.docs.by_state)} · 失败 ${s.docs.by_state.FAILED || 0}` : "",
        icon: <FileTextOutlined style={{ fontSize: 22 }} />,
        color: "#389e0d",
        bg: "#F6FFED",
        onClick: () => router.push("/files"),
      },
      {
        key: "chunks",
        label: "知识分块",
        value: s?.chunks.total ?? 0,
        sub: s ? `摘要完成 ${s.docs.summary_ready} · 失败 ${s.docs.summary_failed}` : "",
        icon: <PartitionOutlined style={{ fontSize: 22 }} />,
        color: "#722ed1",
        bg: "#F9F0FF",
        onClick: () => router.push("/chunk-verify"),
      },
      {
        key: "wiki",
        label: "Wiki 页面",
        value: s?.wiki.pages ?? 0,
        sub: s ? `链接 ${s.wiki.links} · 待处理问题 ${s.wiki.issues_open}` : "",
        icon: <ThunderboltOutlined style={{ fontSize: 22 }} />,
        color: "#d48806",
        bg: "#FFFBE6",
        onClick: () => router.push("/kbs"),
      },
      {
        key: "jobs",
        label: "构建任务成功率",
        value: s ? `${s.jobs.success_rate}%` : "0%",
        sub: s
          ? `成功 ${s.jobs.success} · 失败 ${s.jobs.failed} · 均耗时 ${fmtDuration(s.jobs.avg_duration_ms)}`
          : "",
        icon: <CheckCircleOutlined style={{ fontSize: 22 }} />,
        color: "#cf1322",
        bg: "#FFF1F0",
        onClick: () => router.push("/jobs"),
      },
      {
        key: "feedback",
        label: "问答点赞率",
        value: s ? `${(s.feedback.like_rate * 100).toFixed(1)}%` : "0%",
        sub: s ? `反馈 ${s.feedback.total} · 近7天 ${s.feedback.week_count}` : "",
        icon: <DislikeOutlined style={{ fontSize: 22 }} />,
        color: "#13c2c2",
        bg: "#E6FFFB",
        onClick: () => router.push("/feedback-stats"),
      },
    ];
  }, [summary, router]);

  // ── 解析状态分布（环形图数据）──
  const STATE_LABEL: Record<string, string> = {
    PENDING: "待解析",
    PARSING: "解析中",
    EMBEDDING: "向量化中",
    READY: "已就绪",
    FAILED: "失败",
  };
  const statePieData = useMemo(() => {
    const byState = summary?.docs.by_state || {};
    return Object.entries(byState)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => ({ type: STATE_LABEL[k] || k, value: v }));
  }, [summary]);

  const trendLineData = useMemo(
    () =>
      trend.flatMap((d) => [
        { date: d.date, series: "文档", value: d.docs },
        { date: d.date, series: "分块", value: d.chunks },
        { date: d.date, series: "Wiki 页", value: d.wiki_pages },
      ]),
    [trend],
  );

  const sectionStyle: CSSProperties = {
    background: "#fff",
    borderRadius: 8,
    border: "1px solid #E3E9EF",
  };

  return (
    <div
      className="overview-page"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - var(--app-header-h, 48px))",
        minHeight: 0,
        background: "#EFF4F9",
        padding: 10,
        overflow: "auto",
      }}
    >
      <div style={{ background: "#F5F7FA", flex: 1, minHeight: 0, paddingTop: 10 }}>
        {/* 指标卡区 */}
        {loading && !summary ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 64 }}>
            <Spin size="large" />
          </div>
        ) : (
          <>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(6, 1fr)",
                gap: 16,
                marginBottom: 16,
              }}
            >
              {statCards.map((stat) => (
                <div
                  key={stat.key}
                  className="modo-stat-card"
                  onClick={stat.onClick}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: 18,
                    background: "#fff",
                    borderRadius: 8,
                    border: "1px solid #E3E9EF",
                    transition: "box-shadow 0.25s",
                    cursor: "pointer",
                  }}
                >
                  <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                    <span
                      style={{
                        color: "#79879C",
                        fontSize: 13,
                        marginBottom: 8,
                        fontWeight: 500,
                      }}
                    >
                      {stat.label}
                    </span>
                    <span
                      style={{
                        fontSize: 26,
                        fontWeight: 700,
                        lineHeight: "26px",
                        color: stat.color,
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {stat.value}
                    </span>
                    {stat.sub ? (
                      <span
                        style={{
                          marginTop: 6,
                          fontSize: 12,
                          color: "#95a6ba",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={stat.sub}
                      >
                        {stat.sub}
                      </span>
                    ) : null}
                  </div>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      width: 48,
                      height: 48,
                      borderRadius: 12,
                      background: stat.bg,
                      color: stat.color,
                      opacity: 0.75,
                      flexShrink: 0,
                    }}
                  >
                    {stat.icon}
                  </div>
                </div>
              ))}
            </div>

            {/* 趋势区 */}
            <div style={{ ...sectionStyle, padding: 16, marginBottom: 16 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: 12,
                }}
              >
                <span style={{ fontSize: 14, fontWeight: 600, color: "#242E43" }}>
                  构建趋势（按天新增）
                </span>
                <Segmented
                  size="small"
                  value={trendDays}
                  options={[
                    { label: "近 7 天", value: 7 },
                    { label: "近 30 天", value: 30 },
                  ]}
                  onChange={(v) => setTrendDays(v as number)}
                />
              </div>
              {trendLoading && trend.length === 0 ? (
                <div
                  style={{
                    height: 260,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Spin />
                </div>
              ) : trend.length === 0 ? (
                <Empty description="暂无趋势数据" />
              ) : (
                <ChartLazy
                  chart="line"
                  data={trendLineData}
                  height={260}
                  config={{ xField: "date", yField: "value", seriesField: "series" }}
                />
              )}
            </div>

            {/* 分布区：解析状态 + 任务概览 */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 16,
                paddingBottom: 16,
              }}
            >
              <div style={{ ...sectionStyle, padding: 16 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: "#242E43", marginBottom: 12 }}>
                  文档解析状态分布
                </div>
                {statePieData.length === 0 ? (
                  <Empty description="暂无文档" />
                ) : (
                  <ChartLazy
                    chart="pie"
                    data={statePieData}
                    height={260}
                    config={{ angleField: "value", colorField: "type" }}
                  />
                )}
              </div>

              <div style={{ ...sectionStyle, padding: 16 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: "#242E43", marginBottom: 12 }}>
                  任务与质量
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: 12,
                  }}
                >
                  {[
                    {
                      label: "任务总数",
                      value: summary?.jobs.total ?? 0,
                      icon: <SyncOutlined />,
                      color: "#242E43",
                    },
                    {
                      label: "运行中",
                      value: summary?.jobs.running ?? 0,
                      icon: <SyncOutlined spin={!!summary?.jobs.running} />,
                      color: "#1677ff",
                    },
                    {
                      label: "排队中",
                      value: summary?.jobs.queued ?? 0,
                      icon: <WarningOutlined />,
                      color: "#722ed1",
                    },
                    {
                      label: "质量评估次数",
                      value: summary?.quality.runs ?? 0,
                      icon: <CheckCircleOutlined />,
                      color: "#389e0d",
                    },
                  ].map((it) => (
                    <div
                      key={it.label}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        padding: "12px 14px",
                        background: "#F9FBFD",
                        borderRadius: 6,
                      }}
                    >
                      <span style={{ color: it.color, fontSize: 18 }}>{it.icon}</span>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: 12, color: "#79879C" }}>{it.label}</div>
                        <div
                          style={{
                            fontSize: 20,
                            fontWeight: 700,
                            color: it.color,
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {it.value}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <div
                  style={{
                    marginTop: 12,
                    padding: "12px 14px",
                    background: "#F9FBFD",
                    borderRadius: 6,
                    fontSize: 13,
                    color: "#4d5e7d",
                  }}
                >
                  质量评估平均分：
                  <span style={{ fontSize: 20, fontWeight: 700, color: "#1677ff", marginLeft: 8 }}>
                    {summary?.quality.avg_score ?? 0}
                  </span>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
