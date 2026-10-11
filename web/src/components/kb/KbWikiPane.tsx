"use client";

/**
 * 知识库 · Wiki 面板（KbWikiPane）
 *
 * 从 kbs/[id]/page.tsx 抽出，供「动态选项卡」与详情路由复用：
 * 全文检索 + 视图切换（浏览 / 管理）+ WikiBrowseView / WikiManagePanel。
 * 仅依赖 kbId prop（+ 可选 focusSlug / onOpenGraph），自包含。
 *
 * 与拆分前不同：原「图谱」分段已去掉，改为通过页面上的「图谱中查看」入口
 * （onOpenGraph）跳到独立的知识图谱选项卡。
 */
import { useState } from "react";
import { App, Card, Input, List, Segmented, Spin, Typography } from "antd";
import WikiBrowseView from "@/components/WikiBrowseView";
import WikiManagePanel from "@/components/WikiManagePanel";
import { apiSearch, SearchHit } from "@/lib/api";

export interface KbWikiPaneProps {
  kbId: string;
  focusSlug?: string;
  /** 由父级注入：打开「知识图谱」选项卡（不传则 WikiBrowseView 回退到原路由跳转） */
  onOpenGraph?: (slug?: string) => void;
}

export default function KbWikiPane({ kbId, focusSlug, onOpenGraph }: KbWikiPaneProps) {
  const { message } = App.useApp();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [wikiView, setWikiView] = useState<"browse" | "manage">("browse");

  const doSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const res = await apiSearch(kbId, query.trim());
      if (res.success) setHits(res.data?.items || []);
      else message.error(res.message || "检索失败");
    } finally {
      setSearching(false);
    }
  };

  // 浏览视图自身是「左栏 + 内容」的两栏布局，需贴面板边缘（不加内边距）；
  // 检索结果 / 管理视图是常规单栏内容，按系统面板内边距 12/16 排布。
  const isFlush = !searching && hits.length === 0 && wikiView === "browse";

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      {/* 白底直角面板（标题由页签承担，面板内不重复标题） */}
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            padding: 0,
          },
        }}
      >
        {/* 工具条：全文检索 + 浏览/管理切换（对齐系统「面板顶部工具条」样式） */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "12px 16px",
            borderBottom: "1px solid var(--color-gray-2, #eff4f9)",
            flexShrink: 0,
          }}
        >
          <Input.Search
            placeholder="全文检索知识库"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onSearch={doSearch}
            loading={searching}
            style={{ width: 280 }}
          />
          <span style={{ flex: 1 }} />
          <Segmented
            value={wikiView}
            onChange={(v) => setWikiView(v as "browse" | "manage")}
            options={[
              { value: "browse", label: "浏览" },
              { value: "manage", label: "管理" },
            ]}
          />
        </div>
        <div
          style={{
            flex: 1,
            minHeight: 0,
            overflow: isFlush ? "hidden" : "auto",
            padding: isFlush ? 0 : "12px 16px 0",
          }}
        >
          {searching ? (
            <Spin />
          ) : hits.length > 0 ? (
            <List
              dataSource={hits}
              renderItem={(hit) => (
                <List.Item>
                  <List.Item.Meta
                    title={<Typography.Text type="secondary">score {hit.score.toFixed(4)}</Typography.Text>}
                    description={hit.content.slice(0, 200)}
                  />
                </List.Item>
              )}
            />
          ) : wikiView === "manage" ? (
            <WikiManagePanel kbId={kbId} />
          ) : (
            <WikiBrowseView
              kbId={kbId}
              focusSlug={focusSlug}
              onTreeChanged={() => undefined}
              onOpenGraph={onOpenGraph}
            />
          )}
        </div>
      </Card>
    </div>
  );
}
