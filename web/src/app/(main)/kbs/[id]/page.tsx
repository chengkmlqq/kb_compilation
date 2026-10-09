"use client";

/**
 * 知识库详情路由（薄壳）
 *
 * 保留 /kbs/[id] 深链（chat 引用「跳转原文」、外部链接、?wiki=graph&focus= 定位），
 * 但内容复用与列表页动态选项卡同一套 pane 组件（KbDocsPane / KbWikiPane / KbGraphPane），
 * 避免两份实现分叉。
 */
import { useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { ModoTabs } from "@/components/biz/modo-tabs";
import KbDocsPane from "@/components/kb/KbDocsPane";
import KbWikiPane from "@/components/kb/KbWikiPane";
import KbGraphPane from "@/components/kb/KbGraphPane";

export default function KbDetailPage() {
  const { id } = useParams<{ id: string }>();
  const kbId = id;
  const searchParams = useSearchParams();
  // 旧入口（wiki 内「图谱中查看」）带 ?wiki=graph 时直接定位到知识图谱页签
  const [activeTab, setActiveTab] = useState(
    searchParams.get("wiki") === "graph" ? "graph" : "docs",
  );
  const focusSlug = searchParams.get("focus") || undefined;

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
                onOpenGraph={() => setActiveTab("graph")}
              />
            ),
          },
          { key: "graph", label: "知识图谱", closable: false, children: <KbGraphPane kbId={kbId} /> },
        ]}
      />
    </div>
  );
}
