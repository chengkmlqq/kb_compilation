"use client";

/**
 * WeKnora 知识库页 —— 查看 agent-gateway 技能（监督管理制度文档转wiki）构建的
 * wiki 数据：知识库选择 → 统计卡片 → wiki 页面列表 → 详情抽屉（markdown + 双向链接）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  apiWeknoraKbs,
  apiWeknoraPage,
  apiWeknoraPages,
  apiWeknoraStats,
  WeknoraKbItem,
  WeknoraWikiPage,
  WeknoraWikiStats,
} from "@/lib/api";

const { Text } = Typography;

const PAGE_TYPE_LABELS: Record<string, string> = {
  business_ontology: "业务实体",
  rule_ontology: "规则实体",
  original_sentence: "长句原文",
  frequent_keyword: "高频关键词",
  summary: "摘要",
  index: "索引",
};

const PAGE_TYPE_COLORS: Record<string, string> = {
  business_ontology: "blue",
  rule_ontology: "purple",
  original_sentence: "green",
  frequent_keyword: "orange",
  summary: "gold",
  index: "default",
};

function fmtTime(iso?: string): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

export default function WeknoraKbsPage() {
  const { message } = App.useApp();
  const [kbs, setKbs] = useState<WeknoraKbItem[]>([]);
  const [kbId, setKbId] = useState<string>();
  const [stats, setStats] = useState<WeknoraWikiStats | null>(null);
  const [pages, setPages] = useState<WeknoraWikiPage[]>([]);
  const [loadingKbs, setLoadingKbs] = useState(false);
  const [loadingStats, setLoadingStats] = useState(false);
  const [loadingPages, setLoadingPages] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [detail, setDetail] = useState<WeknoraWikiPage | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);

  const loadKbs = useCallback(async () => {
    setLoadingKbs(true);
    try {
      const res = await apiWeknoraKbs();
      if (res.success && res.data) {
        const items = Array.isArray(res.data) ? res.data : [];
        setKbs(items);
        // 默认选中市场监督管理知识库（测试目标）
        const market = items.find((k) => k.name.includes("市场监督"));
        if (market && !kbId) setKbId(market.id);
      }
    } finally {
      setLoadingKbs(false);
    }
  }, [kbId]);

  useEffect(() => {
    void loadKbs();
  }, [loadKbs]);

  const loadStats = useCallback(async () => {
    if (!kbId) return;
    setLoadingStats(true);
    try {
      const res = await apiWeknoraStats(kbId);
      if (res.success && res.data) setStats(res.data);
    } finally {
      setLoadingStats(false);
    }
  }, [kbId]);

  const loadPages = useCallback(async () => {
    if (!kbId) return;
    setLoadingPages(true);
    try {
      const res = await apiWeknoraPages(kbId, page, pageSize);
      if (res.success && res.data) {
        const items = Array.isArray(res.data.pages) ? res.data.pages : [];
        setPages(items);
        setTotal(items.length < pageSize ? (page - 1) * pageSize + items.length : -1);
      }
    } finally {
      setLoadingPages(false);
    }
  }, [kbId, page, pageSize]);

  useEffect(() => {
    if (kbId) {
      void loadStats();
      void loadPages();
    }
  }, [kbId, loadStats, loadPages]);

  const openDetail = async (slug: string) => {
    if (!kbId) return;
    setDetailOpen(true);
    setDetailLoading(true);
    try {
      const res = await apiWeknoraPage(kbId, slug);
      if (res.success && res.data) setDetail(res.data);
      else message.error(res.message || "加载页面失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const typeCounts = useMemo(() => stats?.pages_by_type || {}, [stats]);

  return (
    <Space direction="vertical" size="middle" style={{ display: "flex" }}>
      <Card
        title="WeKnora 知识库（技能构建的 wiki 数据）"
        extra={
          <Select
            style={{ width: 360 }}
            placeholder="选择知识库"
            loading={loadingKbs}
            value={kbId}
            onChange={(v) => {
              setKbId(v);
              setPage(1);
            }}
            showSearch
            optionFilterProp="label"
            options={kbs.map((k) => ({ value: k.id, label: k.name }))}
          />
        }
      >
        {!kbId ? (
          <Empty description="请选择知识库" />
        ) : (
          <Row gutter={16}>
            <Col span={6}>
              <Statistic title="wiki 页面总数" value={stats?.total_pages ?? "-"} loading={loadingStats} />
            </Col>
            <Col span={6}>
              <Statistic title="双向链接数" value={stats?.total_links ?? "-"} loading={loadingStats} />
            </Col>
            <Col span={12}>
              <Space wrap size={[8, 8]} style={{ marginTop: 8 }}>
                {Object.entries(typeCounts).map(([t, n]) => (
                  <Tag key={t} color={PAGE_TYPE_COLORS[t] || "default"}>
                    {PAGE_TYPE_LABELS[t] || t}: {n}
                  </Tag>
                ))}
              </Space>
            </Col>
          </Row>
        )}
      </Card>

      <Card title={`wiki 页面（${total >= 0 ? total : pages.length}）`}>
        <Table<WeknoraWikiPage>
          rowKey="slug"
          size="small"
          loading={loadingPages}
          dataSource={pages}
          pagination={
            total > pageSize
              ? { current: page, pageSize, total, onChange: (p) => setPage(p) }
              : false
          }
          locale={{ emptyText: <Empty description="该库暂无 wiki 页面" /> }}
          columns={[
            {
              title: "页面标题",
              dataIndex: "title",
              render: (v: string, row) => (
                <Typography.Link onClick={() => void openDetail(row.slug)}>{v}</Typography.Link>
              ),
            },
            {
              title: "类型",
              dataIndex: "page_type",
              width: 120,
              render: (v: string) => (
                <Tag color={PAGE_TYPE_COLORS[v] || "default"}>{PAGE_TYPE_LABELS[v] || v}</Tag>
              ),
            },
            {
              title: "目录路径",
              dataIndex: "wiki_path",
              ellipsis: true,
              render: (v?: string) => (v ? <Text style={{ fontSize: 12 }}>{v}</Text> : "-"),
            },
            {
              title: "更新",
              dataIndex: "updated_at",
              width: 160,
              render: (v?: string) => fmtTime(v),
            },
          ]}
        />
      </Card>

      <Drawer
        title={detail?.title || "页面详情"}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={560}
      >
        {detailLoading ? (
          <Spin />
        ) : detail ? (
          <Space direction="vertical" style={{ display: "flex" }} size="middle">
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="类型">
                <Tag color={PAGE_TYPE_COLORS[detail.page_type] || "default"}>
                  {PAGE_TYPE_LABELS[detail.page_type] || detail.page_type}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="路径">{detail.wiki_path || "-"}</Descriptions.Item>
              <Descriptions.Item label="入链（被引用）">
                {(detail.in_links?.length ?? 0) > 0 ? (
                  <Space wrap size={[4, 4]}>
                    {detail.in_links!.map((l) => (
                      <Tag key={l} style={{ cursor: "pointer" }} onClick={() => void openDetail(l)}>
                        {l.length > 24 ? `${l.slice(0, 24)}…` : l}
                      </Tag>
                    ))}
                  </Space>
                ) : (
                  "-"
                )}
              </Descriptions.Item>
              <Descriptions.Item label="出链（引用）">
                {(detail.out_links?.length ?? 0) > 0 ? (
                  <Space wrap size={[4, 4]}>
                    {detail.out_links!.map((l) => (
                      <Tag key={l} style={{ cursor: "pointer" }} onClick={() => void openDetail(l)}>
                        {l.length > 24 ? `${l.slice(0, 24)}…` : l}
                      </Tag>
                    ))}
                  </Space>
                ) : (
                  "-"
                )}
              </Descriptions.Item>
              <Descriptions.Item label="来源文档">
                {detail.source_refs?.join(", ") || "-"}
              </Descriptions.Item>
            </Descriptions>
            <div className="kb-markdown" style={{ borderTop: "1px solid #f0f0f0", paddingTop: 12 }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.content || ""}</ReactMarkdown>
            </div>
          </Space>
        ) : (
          <Empty description="无内容" />
        )}
      </Drawer>
    </Space>
  );
}
