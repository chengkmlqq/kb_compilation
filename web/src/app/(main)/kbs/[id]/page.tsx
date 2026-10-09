"use client";

/**
 * 知识库详情路由（薄壳）
 *
 * 保留 /kbs/[id] 深链（chat 引用「跳转原文」、外部链接、?wiki=graph&focus= 定位），
 * 但内容复用与列表页动态选项卡同一套 pane 组件（KbDocsPane / KbWikiPane / KbGraphPane），
 * 避免两份实现分叉。
 *
 * ⚠️ 「图谱」页签有两种语义（2026-10-10 修正）：
 *   - 带 focus（wiki 页内「在图谱中查看」/ ?wiki=graph&focus=slug 深链）
 *     → WikiGraphView：**wiki 页面关联关系图**（wiki_page + wiki_link，不依赖 Neo4j），
 *       ego 邻域模式以当前页为中心展开 —— 对齐 WeKnora 的「在图谱中查看」语义。
 *   - 无 focus（用户直接点「知识图谱」页签）
 *     → KbGraphPane / PopotoGraphView：**Neo4j 知识图谱**（全库图谱查询构建器）。
 */
import { useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { ModoTabs } from "@/components/biz/modo-tabs";
import KbDocsPane from "@/components/kb/KbDocsPane";
import KbWikiPane from "@/components/kb/KbWikiPane";
import KbGraphPane from "@/components/kb/KbGraphPane";
import WikiGraphView from "@/components/WikiGraphView";

export default function KbDetailPage() {
  const { id } = useParams<{ id: string }>();
  const kbId = id;
  const searchParams = useSearchParams();
  // 旧入口（wiki 内「图谱中查看」）带 ?wiki=graph 时直接定位到知识图谱页签
  const [activeTab, setActiveTab] = useState(
    searchParams.get("wiki") === "graph" ? "graph" : "docs",
  );
  const [urlFocus] = useState(searchParams.get("focus") || undefined);
  // 图谱聚焦页：URL 深链（urlFocus）或 wiki 页内点击「在图谱中查看」传入的 slug。
  // 有值 = wiki 关联关系图（ego 邻域）；无值 = Neo4j 全库图谱。
  const [graphFocus, setGraphFocus] = useState<string | undefined>(urlFocus);
  const focusSlug = urlFocus;

  /** Wiki 页内「在图谱中查看」：带上当前页 slug → wiki 关联图 ego 模式 */
  const handleOpenGraph = (slug?: string) => {
    setGraphFocus(slug || undefined);
    setActiveTab("graph");
  };

  return (
    <div
      className="kbs-page"
      style={{
        padding: 8,
        height: "calc(100vh - 45px)",
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
        items={[
          { key: "docs", label: "文档", closable: false, children: <KbDocsPane kbId={kbId} /> },
          {
            key: "wiki",
            label: "Wiki",
            closable: false,
            children: (
              <KbWikiPane
                kbId={kbId}
                focusSlug={focusSlug}
                onOpenGraph={handleOpenGraph}
              />
            ),
          },
          {
            key: "graph",
            label: "知识图谱",
            closable: false,
            children: graphFocus ? (
              <WikiGraphView kbId={kbId} focusSlug={graphFocus} />
            ) : (
              <KbGraphPane kbId={kbId} />
            ),
          },
        ]}
      />
    </div>
  );
}
