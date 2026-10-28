"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Input, Row, Col, Spin } from "antd";
import { apiListKbs, type KbItem } from "@/lib/api";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import WikiKeywordsPane from "@/components/kb/WikiKeywordsPane";
import { EmptyIllustration } from "@/components/kb/KbsIcons";

/**
 * 高频词管理（2026-10-20，迁移自 WeKnora wiki/keywords/overview）。
 * - 首 tab「高频词」= 知识库卡片列表（卡片统计该库高频词数量）
 * - 点击卡片 → 动态选项卡打开「高频词 · 库名」列表（复用 ModoTabs 模式）
 *
 * 2026-10-21 对齐知识库管理页（kbs/page.tsx）范式：白底定高不滚动 /
 * 顶栏标题图标 + 搜索（按库名/描述过滤）/ 卡片网格布局 / 48px 空态插画 /
 * 服务端分页（30 条/页）——不再一次性拉全部可见库。
 *
 * 2026-10-28 修复样式问题：卡片列表根容器改用 .kb-kbs-page（height:100%
 * flex 列），与知识库管理页完全一致——此前根容器是 .kb-kbs-list-panel
 * （flex:1 依赖 flex 父容器），高度链断裂导致卡片多时溢出页面、无卡片时
 * 空态 top:50% 相对塌缩容器 → 被顶部遮挡；分页改用 ModoPagination 常驻
 * 底栏（旧分页条容器类 kb-kbs-pagination-bar 已于 2026-10-21 随 KB 页
 * 换 ModoPagination 一并从 globals.css 删除，旧实现无样式且 total>30 才出现）。
 */
type KeywordSectionTab = {
  key: string;
  title: string;
  kbId: string;
};

// 部署前缀（next.config basePath），与知识库管理页同一批 DSB 资源
const KBS_ASSET_BASE = "/wiki/kbs";
const headerTitleIcon = `${KBS_ASSET_BASE}/header-title-icon.png`;
const cardTitleIcon = `${KBS_ASSET_BASE}/card-title-icon.png`;

/** 彩色放大镜（对齐 modo-design IconSearchColor：viewBox 12x12，圆圈 opacity .25 + 实心头） */
const SearchColorIcon = ({ size = 14 }: { size?: number }) => (
  <svg
    viewBox="0 0 12 12"
    fill="none"
    stroke="none"
    width={size}
    height={size}
    style={{ verticalAlign: "-2px" }}
    aria-hidden
  >
    <path
      fillRule="evenodd"
      d="M5 8a3 3 0 1 0-3-3 3 3 0 0 0 3 3Zm0 1a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z"
      opacity=".25"
      fill="currentColor"
    />
    <path
      d="M7.64 8.005a4.018 4.018 0 0 0 .365-.364l.35.097a.5.5 0 0 1 .219.129L11 10.293l-.707.707-2.426-2.426a.5.5 0 0 1-.129-.22Z"
      fill="currentColor"
    />
  </svg>
);

export default function WikiKeywordsPage() {
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [tabs, setTabs] = useState<KeywordSectionTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");
  // 卡片列表分页（对齐知识库管理页：服务端分页，页容量默认 30）
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(30);
  const [total, setTotal] = useState(0);

  const loadKbs = useCallback(async () => {
    setLoading(true);
    try {
      // 服务端分页拉取当前页（原实现 page_size=1000 一次性拉全部，接口慢）
      const res = await apiListKbs(page, pageSize);
      setKbs(res.data?.items ?? []);
      setTotal(res.data?.total ?? 0);
    } catch {
      setKbs([]);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize]);

  useEffect(() => {
    void loadKbs();
  }, [loadKbs]);

  const openKeywords = useCallback((kb: KbItem) => {
    const key = `kw-${kb.id}`;
    setTabs((prev) => {
      if (prev.some((t) => t.key === key)) return prev;
      return [...prev, { key, title: `高频词 · ${kb.name}`, kbId: kb.id }];
    });
    setActiveTab(key);
  }, []);

  const closeTab = useCallback((key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    setActiveTab((cur) => (cur === key ? "home" : cur));
  }, []);

  // 顶栏搜索：按库名/描述过滤卡片（对齐知识库管理页交互）
  const visibleKbs = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return kbs;
    return kbs.filter(
      (kb) =>
        kb.name.toLowerCase().includes(q) ||
        (kb.description || "").toLowerCase().includes(q)
    );
  }, [kbs, search]);

  const listPane = useMemo(
    () => (
      // 对齐知识库管理页（kbs/page.tsx）三层结构：
      // kb-kbs-page（height:100% flex 列，定高不滚动）→
      // kb-kbs-list-panel（flex:1 列）→ kb-kbs-content（flex:1 滚动区）+
      // ModoPagination 常驻底栏。根容器必须是 .kb-kbs-page：只有它带
      // height:100%，能撑起卡片区高度链（见 2026-10-28 修复说明）。
      <div className="kb-kbs-page">
        <div className="kb-kbs-list-panel">
          <div className="kb-kbs-content">
            {loading && visibleKbs.length === 0 ? (
              /* 首载/查询加载中（对齐知识库管理页 kbs/page.tsx 惯例：
                 loading 期间不显示空态，居中 Spin 反馈） */
              <div style={{ display: "flex", justifyContent: "center", paddingTop: 96 }}>
                <Spin size="large" tip="正在加载知识库..." />
              </div>
            ) : visibleKbs.length === 0 ? (
              /* 空态（对齐知识库管理页 arco Empty 原生规格：48px 插画 + 「暂无数据」） */
              <div className="kb-kbs-empty">
                <div className="kb-kbs-empty-wrapper">
                  <div className="kb-kbs-empty-image">
                    <EmptyIllustration />
                  </div>
                  <div className="kb-kbs-empty-description">
                    {kbs.length === 0 ? "暂无知识库" : "未找到匹配的知识库"}
                  </div>
                </div>
              </div>
            ) : (
              <Spin spinning={loading}>
                <Row className="kb-kbs-rows" gutter={[16, 16]}>
                  {visibleKbs.map((kb) => (
                    <Col key={kb.id} span={6}>
                      <div className="kb-kbs-card" onClick={() => openKeywords(kb)}>
                        <div className="kb-kbs-card-header">
                          <div className="kb-kbs-card-icon">
                            <img src={cardTitleIcon} alt="" />
                          </div>
                          <div className="kb-kbs-card-detail">
                            <div className="kb-kbs-card-title-row">
                              <div className="kb-kbs-card-title-name" title={kb.name}>
                                {kb.name}
                              </div>
                            </div>
                            <div className="kb-kbs-card-meta">
                              {kb.keyword_count ?? 0} 个高频词
                            </div>
                          </div>
                        </div>
                        <div className="kb-kbs-card-description">{kb.description}</div>
                        <div className="kb-kbs-card-footer">
                          <div className="kb-kbs-card-footer-left" title="点击查看该库高频词列表与业务标注">
                            <span className="kb-wk-card-footer-text">
                              点击查看该库高频词列表与业务标注
                            </span>
                          </div>
                        </div>
                      </div>
                    </Col>
                  ))}
                </Row>
              </Spin>
            )}
          </div>

          {/* 分页底栏（对齐知识库管理页：ModoPagination 常驻底栏，不再 total>30 才出现） */}
          <div style={{ flexShrink: 0, marginTop: "auto" }}>
            <ModoPagination
              current={page}
              pageSize={pageSize}
              total={total}
              showSizeChanger
              showQuickJumper
              showTotal={(t) => `共 ${t} 条`}
              pageSizeOptions={[10, 20, 30, 40, 50]}
              onChange={(p, ps) => {
                setPage(p);
                if (ps && ps !== pageSize) {
                  setPageSize(ps);
                  setPage(1);
                }
              }}
            />
          </div>
        </div>
      </div>
    ),
    [kbs, loading, visibleKbs, openKeywords, total, page, pageSize]
  );

  return (
    // 对齐知识库管理页范式——外层定高不滚动、白底、顶栏 ModoTabs（标题图标 + 搜索）
    <div
      className="kbs-page"
      style={{
        height: "calc(100vh - var(--app-header-h, 48px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
        background: "#fff",
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action !== "remove") return;
          closeTab(String(key));
        }}
        tabBarExtraContent={
          <div className="kb-kbs-tab-actions">
            <Input
              className="kb-kbs-search"
              placeholder="输入并搜索..."
              prefix={<SearchColorIcon size={14} />}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onPressEnter={() => setSearch(search.trim())}
            />
          </div>
        }
        items={[
          {
            key: "home",
            // 页面标题：DSB header-title-icon.png（16px）置于标题左侧（对齐知识库管理页）
            label: (
              <span className="kb-kbs-tab-title">
                <img className="kb-kbs-tab-title-icon" src={headerTitleIcon} alt="" />
                高频词管理
              </span>
            ),
            children: listPane,
          },
          ...tabs.map((t) => ({
            key: t.key,
            label: t.title,
            closable: true,
            children: <WikiKeywordsPane kbId={t.kbId} kbName={t.title.replace("高频词 · ", "")} />,
          })),
        ]}
      />
    </div>
  );
}