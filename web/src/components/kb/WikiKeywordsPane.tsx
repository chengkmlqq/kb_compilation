"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Drawer, Input, Modal, Select, Table, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { SearchOutlined, SortAscendingOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import KbsSimplePagination from "@/components/kb/KbsSimplePagination";
import {
  apiListWikiKeywords,
  apiMarkWikiKeyword,
  apiWikiPage,
  type WikiKeywordStat,
} from "@/lib/api";

// 把内容里的 [[slug]] 双链转成 markdown 链接（渲染成可点击的 wiki 页跳转；
// 对齐 wiki 详情页 renderWikiLinks 的同一个实现）。
function renderWikiLinks(content: string, kbId: string): string {
  if (!content) return content;
  const wikiLinkRe = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;
  return content.replace(wikiLinkRe, (_m, slug: string, alias?: string) => {
    // 命名空间形态 slug（WeKnora 风格 longSENTENCE/xxx）→ 库内扁平 slug（longsentence-xxx）
    const clean = (slug || "").trim().replace(/\//g, "-");
    if (!clean) return _m;
    const label = (alias || "").trim() || clean;
    const href = `/kbs/${encodeURIComponent(kbId)}/wiki/${encodeURIComponent(clean)}`;
    return `[${label}](${href})`;
  });
}

/**
 * 高频词管理列表（迁移自 WeKnora WikiKeywords.vue，2026-10-20）。
 * - 表格：排名 / 关键词 / 含义 / 频次（点击开详情抽屉）/ 文档数 / 标注列
 * - 标注：preset 三键（kw_meaningful ✅ / kw_noise ❌ / kw_mis_extracted ⚠️）+ 自定义，
 *   走独立标注通道 POST /wiki/keywords/mark，计数回显在标注列。
 */
interface WikiKeywordsPaneProps {
  kbId: string;
  kbName?: string;
}

const PAGE_SIZE = 20;

// 高频词标注预设（与后端 WIKI_FEEDBACK_PRESETS 的 kw_* 键对齐）
const MARK_OPTIONS = [
  { value: "kw_meaningful", label: "有业务含义", icon: "✅" },
  { value: "kw_noise", label: "无业务含义（噪音）", icon: "❌" },
  { value: "kw_mis_extracted", label: "提取不准确", icon: "⚠️" },
  { value: "custom", label: "自定义", icon: "✏️" },
];

function presetIconOf(preset: string) {
  return MARK_OPTIONS.find((o) => o.value === preset)?.icon ?? "✅";
}

export default function WikiKeywordsPane({ kbId, kbName }: WikiKeywordsPaneProps) {
  const router = useRouter();
  const [items, setItems] = useState<WikiKeywordStat[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<"freq" | "keyword">("freq");
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);

  // 详情抽屉
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerKeyword, setDrawerKeyword] = useState("");
  const [drawerContent, setDrawerContent] = useState("");
  const [drawerLoading, setDrawerLoading] = useState(false);

  // 标注弹窗
  const [markTarget, setMarkTarget] = useState<WikiKeywordStat | null>(null);
  const [markPreset, setMarkPreset] = useState("kw_meaningful");
  const [markContent, setMarkContent] = useState("");
  const [markSubmitting, setMarkSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListWikiKeywords(kbId, {
        search: search || undefined,
        sort: sortBy,
        order,
        page,
        page_size: pageSize,
      });
      setItems(res.data?.items ?? []);
      setTotal(res.data?.total ?? 0);
    } catch {
      message.error("高频词列表加载失败");
    } finally {
      setLoading(false);
    }
  }, [kbId, search, sortBy, order, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const openDrawer = useCallback(
    async (item: WikiKeywordStat) => {
      setDrawerOpen(true);
      setDrawerKeyword(item.keyword);
      setDrawerLoading(true);
      setDrawerContent("");
      try {
        const page = await apiWikiPage(kbId, item.slug);
        setDrawerContent(page.data?.content ?? "");
      } catch {
        setDrawerContent("");
      } finally {
        setDrawerLoading(false);
      }
    },
    [kbId]
  );

  const openMark = (item: WikiKeywordStat) => {
    setMarkTarget(item);
    setMarkPreset("kw_meaningful");
    setMarkContent("");
  };

  const submitMark = async () => {
    if (!markTarget) return;
    if (markPreset === "custom" && !markContent.trim()) {
      message.warning("自定义标注需填写内容");
      return;
    }
    setMarkSubmitting(true);
    try {
      await apiMarkWikiKeyword(kbId, markTarget.slug, markPreset, markContent.trim());
      message.success("标注已提交");
      setMarkTarget(null);
      void load();
    } catch {
      message.error("标注提交失败");
    } finally {
      setMarkSubmitting(false);
    }
  };

  const columns: ColumnsType<WikiKeywordStat> = useMemo(
    () => [
      {
        title: "排名",
        width: 64,
        render: (_v, _r, idx) => <span className="kb-wk-rank">{((page - 1) * PAGE_SIZE) + idx + 1}</span>,
      },
      {
        title: "关键词",
        dataIndex: "keyword",
        width: 200,
        ellipsis: true,
      },
      {
        title: "含义",
        dataIndex: "meaning",
        width: 120,
        render: (v: string) => (v ? v : <span className="kb-wk-empty">—</span>),
      },
      {
        title: "频次",
        dataIndex: "total_freq",
        width: 90,
        render: (v: number, r) => (
          <Button type="link" size="small" className="kb-wk-freq-link" onClick={() => void openDrawer(r)}>
            {v}
          </Button>
        ),
      },
      {
        title: "文档数",
        dataIndex: "doc_count",
        width: 80,
      },
      {
        title: "标注",
        width: 260,
        render: (_v, r) => (
          <div className="kb-wk-mark-cell">
            <Button size="small" type="default" onClick={() => openMark(r)}>
              标注
            </Button>
            {Object.keys(r.feedback_counts ?? {}).length > 0 && (
              <span className="kb-wk-mark-counts">
                {MARK_OPTIONS.filter((o) => o.value !== "custom").map((o) => {
                  const n = r.feedback_counts?.[o.value];
                  if (!n) return null;
                  return (
                    <span key={o.value} className={`kb-wk-mark-count kb-wk-mark-${o.value}`} title={o.label}>
                      {o.icon} {n}
                    </span>
                  );
                })}
              </span>
            )}
          </div>
        ),
      },
    ],
    [page, openDrawer, load]
  );

  return (
    <div className="kb-wk-root">
      <div className="kb-wk-toolbar">
        <Input
          className="kb-wk-search"
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索高频词"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          onPressEnter={() => void load()}
        />
        <Select
          className="kb-wk-sort"
          value={sortBy}
          onChange={(v) => {
            setSortBy(v);
            setPage(1);
          }}
          options={[
            { value: "freq", label: "按频次" },
            { value: "keyword", label: "按关键词" },
          ]}
        />
        <Select
          className="kb-wk-sort"
          value={order}
          onChange={(v) => {
            setOrder(v);
            setPage(1);
          }}
          options={[
            { value: "desc", label: "降序" },
            { value: "asc", label: "升序" },
          ]}
        />
      </div>

      <Table<WikiKeywordStat>
        className="kb-wk-table"
        rowKey="slug"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        scroll={{ x: 1000, y: "calc(100vh - 216px)" }}
        locale={{ emptyText: "暂无高频词，上传文档并启用 Wiki 后将自动生成高频词页面" }}
      />

      {/* 分页条：对齐知识库详情 · 文档列表（KbsSimplePagination + kb-doc-pagination-bar）——
          共 N 个高频词 + 页码输入 + 条/页下拉；仅当总数超过一页时渲染 */}
      <KbsSimplePagination
        containerClassName="kb-doc-pagination-bar"
        current={page}
        pageSize={pageSize}
        total={total}
        showTotal={(t) => `共 ${t} 个高频词`}
        onChange={(p, ps) => {
          setPage(p);
          setPageSize(ps);
        }}
      />

      {/* 详情抽屉：展示该高频词 wiki 页面内容 */}
      <Drawer
        title={drawerKeyword}
        open={drawerOpen}
        width={640}
        onClose={() => setDrawerOpen(false)}
      >
        {drawerLoading ? (
          <div className="kb-wk-drawer-loading">加载中…</div>
        ) : drawerContent ? (
          <div className="kb-markdown kb-wk-drawer-content">
            {/* 对齐 wiki 详情页渲染：remarkGfm 解析表格/任务列表/删除线等 GFM 语法，
                双链 [[slug]] 转可点击 wiki 页跳转（wiki 详情页同款实现） */}
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ href, children }) => (
                  <a
                    href={href}
                    onClick={(e) => {
                      if (href && href.startsWith("/kbs/")) {
                        e.preventDefault();
                        void router.push(href);
                      }
                    }}
                  >
                    {children}
                  </a>
                ),
              }}
            >
              {renderWikiLinks(drawerContent, kbId)}
            </ReactMarkdown>
          </div>
        ) : (
          <div className="kb-wk-empty">暂无页面内容</div>
        )}
      </Drawer>

      {/* 标注弹窗 */}
      <Modal
        title={`标注高频词${markTarget ? `：${markTarget.keyword}` : ""}`}
        open={!!markTarget}
        onOk={() => void submitMark()}
        confirmLoading={markSubmitting}
        onCancel={() => setMarkTarget(null)}
        okText="提交"
        cancelText="取消"
      >
        <div className="kb-wk-mark-form">
          <div className="kb-wk-mark-row">
            <span className="kb-wk-mark-label">标注类型</span>
            <Select
              style={{ flex: 1 }}
              value={markPreset}
              onChange={setMarkPreset}
              options={MARK_OPTIONS}
            />
          </div>
          {markPreset === "custom" && (
            <div className="kb-wk-mark-row">
              <span className="kb-wk-mark-label">内容</span>
              <Input.TextArea
                rows={3}
                value={markContent}
                onChange={(e) => setMarkContent(e.target.value)}
                placeholder="填写该高频词的业务含义说明（必填）"
              />
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}
