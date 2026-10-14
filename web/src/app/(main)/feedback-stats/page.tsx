"use client";

/** 问答反馈统计页（点赞/点踩使用情况采集）：汇总卡片 + 明细分页。
 *
 * 数据源：chat_message.feedback（like/dislike），admin 看全量，
 * 普通用户只统计自己（后端口径）。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Card, Empty, Form, Input, Select, Space, Statistic, Table, Tag } from "antd";
import { apiFeedbackStats, apiFeedbackList, FeedbackStatsData, FeedbackItem } from "@/lib/api";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";

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

  const listPane = (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      {/* 汇总卡片 */}
      <div style={{ display: "flex", gap: 12, flexShrink: 0, marginBottom: 12 }}>
        <Card size="small" style={{ flex: 1 }}>
          <Statistic title="累计反馈" value={stats?.total_feedback ?? 0} />
        </Card>
        <Card size="small" style={{ flex: 1 }}>
          <Statistic title="点赞" value={stats?.like_count ?? 0} valueStyle={{ color: "#1677ff" }} />
        </Card>
        <Card size="small" style={{ flex: 1 }}>
          <Statistic title="点踩" value={stats?.dislike_count ?? 0} valueStyle={{ color: "#ff4d4f" }} />
        </Card>
        <Card size="small" style={{ flex: 1 }}>
          <Statistic
            title="点赞率"
            value={stats ? Number((stats.like_rate * 100).toFixed(1)) : 0}
            suffix="%"
          />
        </Card>
        <Card size="small" style={{ flex: 1 }}>
          <Statistic title="今日反馈" value={stats?.today_count ?? 0} />
        </Card>
        <Card size="small" style={{ flex: 1 }}>
          <Statistic title="近 7 天" value={stats?.week_count ?? 0} />
        </Card>
      </div>

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
          onFinish={doSearch}
          initialValues={{ keyword: "", feedbackType: undefined }}
        >
          <Form.Item name="feedbackType" label="反馈类型">
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
          <Form.Item name="keyword" label="关键字">
            <Input allowClear placeholder="按问题/回答/用户/会话搜索" style={{ width: 240 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button onClick={doReset}>重置</Button>
            </Space>
          </Form.Item>
        </Form>

        <Table<FeedbackItem>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={rows}
          pagination={false}
          scroll={{ x: 1100, y: "calc(100vh - 372px)" }}
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
        <div style={{ flexShrink: 0, marginTop: "auto" }}>
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
      </Card>
    </div>
  );

  return (
    <div
      className="feedback-stats-page"
      style={{
        padding: 8,
        height: "calc(100vh - var(--app-header-h, 45px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey="home"
        items={[{ key: "home", label: "问答反馈", children: listPane }]}
      />
    </div>
  );
}
