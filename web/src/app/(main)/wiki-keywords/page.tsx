"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Empty, Row, Col } from "antd";
import { apiListKbs, type KbItem } from "@/lib/api";
import { ModoTabs } from "@/components/biz/modo-tabs";
import WikiKeywordsPane from "@/components/kb/WikiKeywordsPane";

/**
 * 高频词管理（2026-10-20，迁移自 WeKnora wiki/keywords/overview）。
 * - 首 tab「高频词」= 知识库卡片列表（卡片统计该库高频词数量）
 * - 点击卡片 → 动态选项卡打开「高频词 · 库名」列表（复用 ModoTabs 模式）
 */
type KeywordSectionTab = {
  key: string;
  title: string;
  kbId: string;
};

export default function WikiKeywordsPage() {
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [tabs, setTabs] = useState<KeywordSectionTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const loadKbs = useCallback(async () => {
    setLoading(true);
    try {
      // 一次性拉全部可见知识库（卡片列表不分页，page_size 上限 1000）
      const res = await apiListKbs(1, 1000);
      setKbs(res.data?.items ?? []);
    } catch {
      setKbs([]);
    } finally {
      setLoading(false);
    }
  }, []);

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

  const listPane = useMemo(
    () => (
      <div className="kb-kbs-list-pane">
        {!loading && kbs.length === 0 ? (
          <div className="kb-kbs-empty-state">
            <Empty description="暂无知识库" />
          </div>
        ) : (
          <Row className="kb-kbs-rows" gutter={[16, 16]}>
            {kbs.map((kb) => (
              <Col key={kb.id} span={6}>
                <div className="kb-kbs-card" onClick={() => openKeywords(kb)}>
                  <div className="kb-kbs-card-header">
                    <div className="kb-kbs-card-icon">
                      <span className="kb-wk-card-icon">◈</span>
                    </div>
                    <div className="kb-kbs-card-detail">
                      <div className="kb-kbs-card-title-row">
                        <div className="kb-kbs-card-title-name" title={kb.name}>
                          {kb.name}
                        </div>
                      </div>
                      <div className="kb-kbs-card-meta">{kb.keyword_count ?? 0} 个高频词</div>
                    </div>
                  </div>
                  <div className="kb-kbs-card-description">{kb.description}</div>
                  <div className="kb-kbs-card-footer">
                    <span className="kb-wk-card-footer-text">
                      点击查看该库高频词列表与业务标注
                    </span>
                  </div>
                </div>
              </Col>
            ))}
          </Row>
        )}
      </div>
    ),
    [kbs, loading, openKeywords]
  );

  return (
    <div
      style={{
        padding: 8,
        height: "calc(100vh - var(--app-header-h, 48px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
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
        items={[
          {
            key: "home",
            label: <span className="kb-kbs-tab-title">高频词</span>,
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