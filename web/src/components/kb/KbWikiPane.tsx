"use client";

/**
 * 知识库 · Wiki 面板（KbWikiPane）
 *
 * 从 kbs/[id]/page.tsx 抽出，供「动态选项卡」与详情路由复用：
 * 2026-10-15 起仅保留浏览视图（WikiBrowseView）——顶部全文检索工具条与
 * 浏览/管理切换已移除，管理面板入口同步下线。
 * 仅依赖 kbId prop（+ 可选 focusSlug / onOpenGraph），自包含。
 */
import { Card } from "antd";
import WikiBrowseView from "@/components/WikiBrowseView";

export interface KbWikiPaneProps {
  kbId: string;
  focusSlug?: string;
  /** 由父级注入：打开「知识图谱」选项卡（不传则 WikiBrowseView 回退到原路由跳转） */
  onOpenGraph?: (slug?: string) => void;
  /** 由父级注入：点击「来源文档」打开对应文档详情选项卡（对齐 WeKnora openSourceDoc） */
  onOpenDoc?: (docId: string) => void;
}

export default function KbWikiPane({ kbId, focusSlug, onOpenGraph, onOpenDoc }: KbWikiPaneProps) {
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
        <WikiBrowseView
          kbId={kbId}
          focusSlug={focusSlug}
          onTreeChanged={() => undefined}
          onOpenGraph={onOpenGraph}
          onOpenDoc={onOpenDoc}
        />
      </Card>
    </div>
  );
}
