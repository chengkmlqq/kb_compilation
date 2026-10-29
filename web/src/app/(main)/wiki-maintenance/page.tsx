"use client";

/** 维护中心（Wiki 页面反馈处理）：独立页面。
 *
 * 入口：知识库管理卡片「反馈 N」点击进入（带 ?kb_id= 预选）；侧栏菜单直达。
 * 数据源：/api/v1/kbs/{kb_id}/wiki/feedback（过滤+分页）+ /feedback/stats（统计卡）
 * + 状态流转 + 软删。
 * 布局对齐任务监控页（jobs/page.tsx）：统计卡（modo-stat-card）+ 筛选栏 + 表格 + 钉底分页。
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Button,
  Empty,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  CommentOutlined,
  DeleteOutlined,
  FileDoneOutlined,
  LinkOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SearchOutlined,
  StopOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  apiListKbs,
  apiWikiDeleteFeedback,
  apiWikiFeedbackStats,
  apiWikiListAllFeedback,
  apiWikiUpdateFeedbackStatus,
  KbItem,
  WikiFeedbackItem,
  WikiFeedbackStats,
} from "@/lib/api";
import ModoPagination from "@/components/biz/modo-pagination";
import { PRESET_LABELS, TYPE_OPTIONS } from "@/lib/wikiFeedback";

const PAGE_SIZE = 20;

const STATUS_OPTIONS = [
  { label: "待处理", value: "pending" },
  { label: "已解决", value: "resolved" },
  { label: "已忽略", value: "ignored" },
];

const TYPE_LABEL: Record<string, string> = {
  comment: "评论",
  question: "问题",
  helpful: "有帮助",
  issue: "问题上报",
};

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: "待处理", color: "warning" },
  resolved: { label: "已解决", color: "success" },
  ignored: { label: "已忽略", color: "default" },
};

const EMPTY_STATS: WikiFeedbackStats = {
  total: 0,
  pending: 0,
  resolved: 0,
  ignored: 0,
  comment: 0,
  question: 0,
};

export default function WikiMaintenancePage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlKbId = searchParams.get("kb_id") || "";

  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [kbId, setKbId] = useState<string>(urlKbId);
  const [items, setItems] = useState<WikiFeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [typeFilter, setTypeFilter] = useState<string | undefined>(undefined);
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [keyword, setKeyword] = useState("");
  const [keywordInput, setKeywordInput] = useState("");
  const [stats, setStats] = useState<WikiFeedbackStats>(EMPTY_STATS);
  const [statsLoading, setStatsLoading] = useState(false);

  // 表格可用高度实测（对齐任务监控页）：写死 vh 偏移易失准，这里量容器实际高度传给 scroll.y
  const [tableBodyHeight, setTableBodyHeight] = useState<number>();
  const tableBodyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = tableBodyRef.current;
    if (!el) return;
    const update = () => {
      // scroll.y 的 maxHeight 只约束表体，表头 (.ant-table-thead) 是流内独立块。
      // 直接量容器高度会令表格总高 = 容器高 + 表头高，底部行被分页遮挡——先减表头高。
      const thead = el.querySelector<HTMLElement>(".ant-table-thead");
      const headerH = thead ? thead.getBoundingClientRect().height : 0;
      setTableBodyHeight(Math.max(40, Math.round(el.clientHeight - headerH)));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    const tableEl = el.querySelector<HTMLElement>(".ant-table");
    if (tableEl) ro.observe(tableEl);
    return () => ro.disconnect();
  }, []);

  // 知识库列表（选择器 + 卡片统计已由 /api/v1/kbs feedback_count 提供）
  const loadKbs = useCallback(async () => {
    try {
      const kbRes = await apiListKbs(1, 1000);
      if (kbRes.success) setKbs(kbRes.data?.items || []);
    } catch {
      /* 选择器失败不阻断 */
    }
  }, []);

  const loadStats = useCallback(async () => {
    if (!kbId) {
      setStats(EMPTY_STATS);
      return;
    }
    setStatsLoading(true);
    try {
      const res = await apiWikiFeedbackStats(kbId);
      if (res.success && res.data) setStats(res.data);
    } catch {
      /* 统计卡失败不阻断列表 */
    } finally {
      setStatsLoading(false);
    }
  }, [kbId]);

  const loadList = useCallback(async () => {
    if (!kbId) {
      setItems([]);
      setTotal(0);
      return;
    }
    setLoading(true);
    try {
      const res = await apiWikiListAllFeedback(kbId, {
        page,
        page_size: PAGE_SIZE,
        feedback_type: typeFilter || undefined,
        status: statusFilter || undefined,
        slug: keyword || undefined,
      });
      const data = res?.data;
      setItems(data?.items || []);
      setTotal(data?.total || 0);
    } catch {
      message.error("加载反馈失败");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [kbId, page, typeFilter, statusFilter, keyword]);

  useEffect(() => {
    void loadKbs();
  }, [loadKbs]);

  // 筛选/页码任一变化即重新加载（loadList 依赖全部筛选 state）
  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  // 从知识库卡片进入时同步 URL 的 kb_id
  useEffect(() => {
    setKbId(urlKbId);
  }, [urlKbId]);

  const applyKeyword = () => {
    setKeyword(keywordInput.trim());
    setPage(1);
  };

  const resetFilters = () => {
    setTypeFilter(undefined);
    setStatusFilter(undefined);
    setKeyword("");
    setKeywordInput("");
    setPage(1);
  };

  const setStatus = async (item: WikiFeedbackItem, status: string) => {
    if (!kbId) return;
    try {
      await apiWikiUpdateFeedbackStatus(kbId, item.id, status);
      message.success(status === "resolved" ? "已标记解决" : status === "pending" ? "已重新打开" : "已忽略");
      await Promise.all([loadList(), loadStats()]);
    } catch {
      message.error("状态更新失败（可能无操作权限）");
    }
  };

  const removeItem = async (item: WikiFeedbackItem) => {
    if (!kbId) return;
    try {
      await apiWikiDeleteFeedback(kbId, item.id);
      message.success("已删除");
      await Promise.all([loadList(), loadStats()]);
    } catch {
      message.error("删除失败（可能无操作权限）");
    }
  };

  const openPage = (slug: string) => {
    if (!kbId) return;
    router.push(`/kbs/${kbId}/wiki/${encodeURIComponent(slug)}`);
  };

  const columns: ColumnsType<WikiFeedbackItem> = [
    {
      title: "类型",
      dataIndex: "feedback_type",
      width: 100,
      render: (v: string) => {
        const isQuestion = v === "question" || v === "issue";
        return (
          <Tag color={isQuestion ? "warning" : v === "helpful" ? "success" : "processing"}>
            {TYPE_LABEL[v] || v}
          </Tag>
        );
      },
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (v: string) => {
        const meta = STATUS_META[v] || { label: v, color: "default" };
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "页面",
      dataIndex: "slug",
      width: 280,
      render: (_: unknown, r: WikiFeedbackItem) => (
        <Typography.Link onClick={() => openPage(r.slug)}>
          <Space size={6}>
            <LinkOutlined style={{ opacity: 0.6 }} />
            <span
              style={{ maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {r.page_title || r.slug}
            </span>
            <span style={{ color: "#79879c", fontSize: 12 }}>{r.slug}</span>
          </Space>
        </Typography.Link>
      ),
    },
    {
      title: "内容",
      dataIndex: "content",
      render: (_: unknown, r: WikiFeedbackItem) => (
        <div style={{ display: "flex", alignItems: "flex-start", gap: 8, flexWrap: "wrap" }}>
          {r.preset && <Tag color="geekblue">{PRESET_LABELS[r.preset] || r.preset}</Tag>}
          <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{r.content || "—"}</span>
        </div>
      ),
    },
    {
      title: "上报人",
      dataIndex: "reported_by_name",
      width: 120,
      render: (v: string, r: WikiFeedbackItem) => (
        <span style={{ color: "#4d5e7d" }}>{v || r.user_id || "—"}</span>
      ),
    },
    {
      title: "时间",
      dataIndex: "created_at",
      width: 150,
      render: (v: string | null) => (v ? v.slice(0, 16).replace("T", " ") : "—"),
    },
    {
      title: "操作",
      key: "ops",
      width: 220,
      render: (_: unknown, r: WikiFeedbackItem) => (
        <Space size={2}>
          {r.status === "pending" ? (
            <Button type="text" size="small" icon={<CheckOutlined />} onClick={() => setStatus(r, "resolved")}>
              标记解决
            </Button>
          ) : (
            <Button type="text" size="small" icon={<RollbackOutlined />} onClick={() => setStatus(r, "pending")}>
              重新打开
            </Button>
          )}
          {r.status !== "ignored" && (
            <Button type="text" size="small" icon={<StopOutlined />} onClick={() => setStatus(r, "ignored")}>
              忽略
            </Button>
          )}
          <Popconfirm title="确定删除这条反馈吗？" onConfirm={() => removeItem(r)}>
            <Button type="text" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 统计卡（对齐任务监控页 JobStats：6 列 grid / 48px 圆角图标底 / 26px 数值）
  const statCards: Array<{
    key: string;
    label: string;
    value: number;
    icon: ReactNode;
    color: string;
    bg: string;
  }> = [
    {
      key: "total",
      label: "总数",
      value: stats.total,
      icon: <FileDoneOutlined style={{ fontSize: 22 }} />,
      color: "#242E43",
      bg: "#F0F2F5",
    },
    {
      key: "pending",
      label: "待处理",
      value: stats.pending,
      icon: <ClockCircleOutlined style={{ fontSize: 22 }} />,
      color: "#d48806",
      bg: "#FFFBE6",
    },
    {
      key: "resolved",
      label: "已解决",
      value: stats.resolved,
      icon: <CheckCircleOutlined style={{ fontSize: 22 }} />,
      color: "#389e0d",
      bg: "#F6FFED",
    },
    {
      key: "ignored",
      label: "已忽略",
      value: stats.ignored,
      icon: <StopOutlined style={{ fontSize: 22 }} />,
      color: "#79879C",
      bg: "#F0F2F5",
    },
    {
      key: "comment",
      label: "评论",
      value: stats.comment,
      icon: <CommentOutlined style={{ fontSize: 22 }} />,
      color: "#1677ff",
      bg: "#E6F4FF",
    },
    {
      key: "question",
      label: "问题",
      value: stats.question,
      icon: <QuestionCircleOutlined style={{ fontSize: 22 }} />,
      color: "#722ed1",
      bg: "#F9F0FF",
    },
  ];

  return (
    // 对齐任务监控页：外层锁 视口 − 顶栏，统计卡/筛选栏定高、表格区吃满剩余空间
    <div
      className="wiki-maintenance-page"
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
                  {statsLoading ? "-" : stat.value}
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
          <Space size={12} wrap>
            <Select
              placeholder="选择知识库"
              style={{ width: 300 }}
              value={kbId || undefined}
              showSearch
              optionFilterProp="label"
              allowClear
              onChange={(v) => {
                setKbId(v || "");
                setPage(1);
              }}
              options={kbs.map((k) => ({
                value: k.id,
                label: `${k.name}${(k.feedback_count ?? 0) > 0 ? `（反馈 ${k.feedback_count}）` : ""}`,
              }))}
            />
            <Select
              placeholder="类型"
              style={{ width: 130 }}
              allowClear
              value={typeFilter}
              onChange={(v) => {
                setTypeFilter(v);
                setPage(1);
              }}
              options={TYPE_OPTIONS}
            />
            <Select
              placeholder="状态"
              style={{ width: 130 }}
              allowClear
              value={statusFilter}
              onChange={(v) => {
                setStatusFilter(v);
                setPage(1);
              }}
              options={STATUS_OPTIONS}
            />
            <Input
              placeholder="按页面标题或 slug 搜索…"
              style={{ width: 260 }}
              prefix={<SearchOutlined style={{ color: "#79879c" }} />}
              allowClear
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onPressEnter={applyKeyword}
              onClear={applyKeyword}
            />
          </Space>
          <Space>
            <Button type="primary" icon={<SearchOutlined />} onClick={applyKeyword}>
              查询
            </Button>
            <Button icon={<ReloadOutlined />} onClick={resetFilters}>
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
          <div ref={tableBodyRef} style={{ flex: 1, minHeight: 0, overflow: "hidden", padding: "0 16px" }}>
            <Table<WikiFeedbackItem>
              rowKey="id"
              size="small"
              columns={columns}
              dataSource={items}
              loading={loading}
              pagination={false}
              scroll={{ x: 1200, y: tableBodyHeight }}
              locale={{
                emptyText: (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={kbId ? "暂无维护反馈" : "请先选择知识库"}
                  />
                ),
              }}
            />
          </div>
          <ModoPagination
            current={page}
            pageSize={PAGE_SIZE}
            total={total}
            showTotal={(t) => `共 ${t} 条`}
            onChange={(p) => setPage(p)}
          />
        </div>
      </div>
    </div>
  );
}
