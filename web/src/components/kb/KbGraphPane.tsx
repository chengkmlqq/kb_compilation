"use client";

/**
 * 知识库 · 知识图谱面板（KbGraphPane）
 *
 * 从 kbs/[id]/page.tsx 抽出，供「动态选项卡」与详情路由复用：
 * Popoto.js 可视化查询构建器（自包含，仅依赖 kbId）。
 *
 * 布局对齐系统列表页内容面板：白底直角卡片 + 内边距 12/16；
 * 标题由页签承担，面板内不再重复标题（避免与页签「知识图谱 · xxx」重复）。
 */
import { Card } from "antd";
import PopotoGraphView from "@/components/PopotoGraphView";

export interface KbGraphPaneProps {
  kbId: string;
}

export default function KbGraphPane({ kbId }: KbGraphPaneProps) {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{ body: { flex: 1, minHeight: 0, overflow: "auto", padding: "12px 16px 0" } }}
      >
        <PopotoGraphView kbId={kbId} />
      </Card>
    </div>
  );
}
