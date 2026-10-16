"use client";

/** 问答反馈统计页（点赞/点踩使用情况采集）：汇总卡片 + 筛选栏 + 明细分页。
 *
 * 布局对齐任务监控页（jobs/page.tsx）：统计卡（modo-stat-card）+ 筛选栏 + 表格 + 钉底分页。
 * 数据源：chat_message.feedback（like/dislike），admin 看全量，普通用户只统计自己（后端口径）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Button, Empty, Form, Input, Select, Space, Table, Tag } from "antd";
import {
  CalendarOutlined,
  DislikeOutlined,
  LikeOutlined,
  LineChartOutlined,
  MessageOutlined,
  ReloadOutlined,
  RiseOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { apiFeedbackStats, apiFeedbackList, FeedbackStatsData, FeedbackItem } from "@/lib/api";
import ModoPagination from "@/components/biz/modo-pagination";

export default function FeedbackStatsPage() {
  const [stats, setStats] = useState<FeedbackStatsData | null>(null);
  const [rows, setRows] = useState<FeedbackItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [feedbackType, setFeedbackType] = useState<string | undefined>(undefined);
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [searchForm] = Form.useForm<{ keyword?: string; feedbackType?: string }>();

  // 表格可用高度实测（对齐任务监控页）：写死 vh 偏移易失准，这里量容器实际高度传给 scroll.y
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

  const loadStats = useCallback(async () => {
    try {
      const res = await apiFeedbackStats();
      if (res.success) setStats(res.data || null);
    } catch {
      /* 统计卡片失败不阻断列表 */
    }
  }, []);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFeedbackList(page, pageSize, feedbackType, keyword);
      if (res.success) {
        setRows(res.data?.items || []);
        setTotal(res.data?.total ?? 0);
      }
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, feedbackType, keyword]);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const doSearch = (v: { keyword?: string; feedbackType?: string }) => {
    setPage(1);
    setFeedbackType(v.feedbackType || undefined);
    setKeyword((v.keyword || "").trim());
  };

  const doReset = () => {
    searchForm.resetFields();
    setPage(1);
    setFeedbackType(undefined);
    setKeyword("");
  };

  // 汇总卡片（对齐任务监控页 JobStats：6 列 grid / 48px 圆角图标底 / 26px 数值）
  const statCards: Array<{
    key: string;
    label: string;
    value: number | string;
    icon: ReactNode;
    color: string;
    bg: string;
  }> = [
    {
      key: "total",
      label: "累计反馈",
      value: stats?.total_feedback ?? 0,
      icon: <MessageOutlined style={{ fontSize: 22 }} />,
      color: "#242E43",
      bg: "#F0F2F5",
    },
    {
      key: "like",
      label: "点赞",
      value: stats?.like_count ?? 0,
      icon: <LikeOutlined style={{ fontSize: 22 }} />,
      color: "#389e0d",
      bg: "#F6FFED",
    },
    {
      key: "dislike",
      label: "点踩",
      value: stats?.dislike_count ?? 0,
      icon: <DislikeOutlined style={{ fontSize: 22 }} />,
      color: "#cf1322",
      bg: "#FFF1F0",
    },
    {
      key: "rate",
      label: "点赞率",
      value: `${(stats ? stats.like_rate * 100 : 0).toFixed(1)}%`,
      icon: <RiseOutlined style={{ fontSize: 22 }} />,
      color: "#1677ff",
      bg: "#E6F4FF",
    },
    {
      key: "today",
      label: "今日反馈",
      value: stats?.today_count ?? 0,
      icon: <CalendarOutlined style={{ fontSize: 22 }} />,
      color: "#722ed1",
      bg: "#F9F0FF",
    },
    {
      key: "week",
      label: "近 7 天",
      value: stats?.week_count ?? 0,
      icon: <LineChartOutlined style={{ fontSize: 22 }} />,
      color: "#d48806",
      bg: "#FFFBE6",
    },
  ];

  return (
    // 对齐任务监控页：外层锁 视口 − 顶栏，统计卡/筛选栏定高、表格区吃满剩余空间
    <div
      className="feedback-stats-page"
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
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(6, 1fr)",
            gap: 16,
            marginBottom: 16,
            flexShrink: 0,
          }}
        >
          {statCards.map((stat) => (
            <div
              key={stat.key}
              className="modo-stat-card"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: 18,
                background: "#fff",
                borderRadius: 8,
                border: "1px solid #E3E9EF",
                transition: "box-shadow 0.25s",
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

        {/* Filter Area */}
        <div
          style={{
            background: "#fff",
            borderRadius: 8,
            border: "1px solid #E3E9EF",
            padding: "14px 16px",
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 16,
            flexWrap: "wrap",
            flexShrink: 0,
          }}
        >
          <Form
            form={searchForm}
            layout="inline"
            onFinish={doSearch}
            initialValues={{ keyword: "", feedbackType: undefined }}
            style={{ gap: 12, rowGap: 8 }}
          >
            <Form.Item name="feedbackType" label="反馈类型" style={{ marginBottom: 0 }}>
              <Select
                allowClear
                placeholder="全部"
                style={{ width: 130 }}
                options={[
                  { label: "点赞", value: "like" },
                  { label: "点踩", value: "dislike" },
                ]}
              />
            </Form.Item>
            <Form.Item name="keyword" label="关键字" style={{ marginBottom: 0 }}>
              <Input allowClear placeholder="按问题/回答/用户/会话搜索" style={{ width: 240 }} />
            </Form.Item>
          </Form>
          <Space>
            <Button type="primary" icon={<SearchOutlined />} onClick={() => searchForm.submit()}>
              查询
            </Button>
            <Button icon={<ReloadOutlined />} onClick={doReset}>
              重置
            </Button>
          </Space>
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
          <div
            ref={tableBodyRef}
            style={{ flex: 1, minHeight: 0, overflow: "hidden", padding: "0 16px" }}
          >
            <Table<FeedbackItem>
              rowKey="id"
              size="middle"
              loading={loading}
              dataSource={rows}
              pagination={false}
              scroll={{ x: 1100, y: tableBodyHeight }}
              locale={{ emptyText: <Empty description="暂无反馈数据（用户对问答点赞/点踩后在此展示）" /> }}
              columns={[
                {
                  title: "反馈",
                  dataIndex: "feedback",
                  width: 90,
                  render: (v: string) =>
                    v === "like" ? <Tag color="blue">点赞</Tag> : <Tag color="red">点踩</Tag>,
                },
                { title: "问题", dataIndex: "question", ellipsis: true, width: 260 },
                {
                  title: "回答摘要",
                  dataIndex: "answer",
                  ellipsis: true,
                  render: (v: string) => v?.slice(0, 160) || "-",
                },
                { title: "用户", dataIndex: "user_id", width: 110, ellipsis: true },
                { title: "会话", dataIndex: "session_title", width: 150, ellipsis: true },
                { title: "反馈时间", dataIndex: "feedback_at", width: 170 },
              ]}
            />
          </div>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
            showTotal={(t) => `共 ${t} 条反馈`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
            }}
          />
        </div>
      </div>
    </div>
  );
}
