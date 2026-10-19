"use client";

/** 维护中心（Wiki 页面反馈处理）：独立页面。
 *
 * 入口：知识库管理卡片「反馈 N」点击进入（带 ?kb_id= 预选）；侧栏菜单直达。
 * 数据源：/api/v1/kbs/{kb_id}/wiki/feedback（过滤+分页）+ 状态流转 + 软删。
 * 布局对齐 feedback-stats / jobs：顶部标题 + 知识库选择 + 汇总徽标 + 筛选行 + 表格 + 钉底分页。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
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
  CheckOutlined,
  CommentOutlined,
  DeleteOutlined,
  LinkOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SearchOutlined,
  StopOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  apiListKbs,
  apiWikiDeleteFeedback,
  apiWikiListAllFeedback,
  apiWikiUpdateFeedbackStatus,
  KbItem,
  WikiFeedbackItem,
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
  const [pendingCount, setPendingCount] = useState(0);

  const selectedKb = useMemo(() => kbs.find((k) => k.id === kbId) || null, [kbs, kbId]);

  // 知识库列表（选择器 + 卡片统计已由 /api/v1/kbs feedback_count 提供）
  const loadKbs = useCallback(async () => {
    try {
      const kbRes = await apiListKbs(1, 1000);
      if (kbRes.success) setKbs(kbRes.data?.items || []);
    } catch {
      /* 选择器失败不阻断 */
    }
  }, []);

  const loadList = useCallback(async () => {
    if (!kbId) {
      setItems([]);
      setTotal(0);
      setPendingCount(0);
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
      // 待处理数（当前页统计；与筛选解耦时语义为「当前筛选页内的待处理」）
      setPendingCount((data?.items || []).filter((i) => i.status === "pending").length);
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

  // 从知识库卡片进入时同步 URL 的 kb_id
  useEffect(() => {
    setKbId(urlKbId);
  }, [urlKbId]);

  const applyKeyword = () => {
    setKeyword(keywordInput.trim());
    setPage(1);
  };

  const setStatus = async (item: WikiFeedbackItem, status: string) => {
    if (!kbId) return;
    try {
      await apiWikiUpdateFeedbackStatus(kbId, item.id, status);
      message.success(status === "resolved" ? "已标记解决" : status === "pending" ? "已重新打开" : "已忽略");
      await loadList();
    } catch {
      message.error("状态更新失败（可能无操作权限）");
    }
  };

  const removeItem = async (item: WikiFeedbackItem) => {
    if (!kbId) return;
    try {
      await apiWikiDeleteFeedback(kbId, item.id);
      message.success("已删除");
      await loadList();
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
              className="wm-page-title"
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

  return (
    <div style={{ padding: 10, height: "calc(100vh - var(--app-header-h, 48px))", display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 16, fontWeight: 600, color: "#242e43" }}>
          <CommentOutlined style={{ color: "#3261ce" }} />
          维护中心
          <span style={{ fontSize: 12, fontWeight: 400, color: "#79879c" }}>Wiki 页面反馈汇总与处理</span>
        </div>
        <Space>
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
          <Button icon={<ReloadOutlined />} onClick={() => void loadList()}>
            刷新
          </Button>
        </Space>
      </div>

      {kbId && selectedKb && pendingCount > 0 && (
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <Tag color="blue" style={{ padding: "2px 10px", borderRadius: 999 }}>
            共 {total} 条
          </Tag>
          <Tag color="orange" style={{ padding: "2px 10px", borderRadius: 999 }}>
            待处理 {pendingCount} 条
          </Tag>
        </div>
      )}

      <div style={{ display: "flex", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
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
        <Button type="primary" onClick={applyKeyword}>
          筛选
        </Button>
      </div>

      <div style={{ flex: 1, minHeight: 0, background: "#fff", border: "1px solid #e3e9ef", borderRadius: 6, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <Table<WikiFeedbackItem>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={items}
            loading={loading}
            pagination={false}
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
        {total > 0 && (
          <div style={{ flexShrink: 0, display: "flex", justifyContent: "flex-end", padding: "12px 16px 8px" }}>
            <ModoPagination
              current={page}
              pageSize={PAGE_SIZE}
              total={total}
              onChange={(p) => setPage(p)}
            />
          </div>
        )}
      </div>
    </div>
  );
}