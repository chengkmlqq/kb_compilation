"use client";

/** 顶部 KPI 概览卡片（4 个核心指标，含拆分小字）。
 *
 * 总请求数 / 总 Token 消耗（输入·输出·缓存拆分）/ 业务产出（文档·知识条目）/
 * 平均耗时（首字 TTFT · 总耗时）。纯展示组件，无任何金额口径。
 */
import { Card, Statistic } from "antd";

export interface UsageSummary {
  totalRequests: number;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  cacheHitRate: number;
  processedDocs: number;
  generatedKnowledge: number;
  avgLatency: { ttft: number | null; total: number | null };
  successCount: number;
  failedCount: number;
  errorRate: number;
}

/** Token 大数格式化：1234567890 -> 1.23B */
export function formatTokens(n: number | null | undefined): string {
  const v = Number(n || 0);
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(Math.round(v));
}

function Sub({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 12, color: "#8c8c8c", marginTop: 2, lineHeight: "18px" }}>{children}</div>
  );
}

const PIE_COLORS = ["#1677ff", "#722ed1", "#13c2c2", "#fa8c16", "#52c41a", "#eb2f96", "#fadb14", "#2f54eb"];

export default function KpiCards({ summary }: { summary: UsageSummary | null }) {
  const s = summary;
  const errorRate = s && s.totalRequests ? (s.errorRate * 100).toFixed(1) : "0.0";
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12 }}>
      <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
        <Statistic title="总请求数" value={s?.totalRequests ?? 0} />
        <Sub>
          成功 <b style={{ color: "#52c41a" }}>{s?.successCount ?? 0}</b> · 失败{" "}
          <b style={{ color: "#ff4d4f" }}>{s?.failedCount ?? 0}</b>（错误率 {errorRate}%）
        </Sub>
      </Card>
      <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
        <Statistic title="总 Token 消耗" value={formatTokens(s?.totalTokens)} />
        <Sub>
          输入 {formatTokens(s?.inputTokens)} · 输出 {formatTokens(s?.outputTokens)} · 缓存命中{" "}
          {formatTokens(s?.cacheReadTokens)}（命中率 {s ? Math.round(s.cacheHitRate * 100) : 0}%）
        </Sub>
      </Card>
      <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
        <Statistic title="业务产出" value={s?.generatedKnowledge ?? 0} />
        <Sub>
          已处理文档 <b>{s?.processedDocs ?? 0}</b> 份 · 生成知识条目{" "}
          <b>{s?.generatedKnowledge ?? 0}</b> 条
        </Sub>
      </Card>
      <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
        <Statistic
          title="平均耗时"
          value={s?.avgLatency?.total != null ? `${(s.avgLatency.total / 1000).toFixed(2)}s` : "-"}
        />
        <Sub>
          首字耗时（TTFT）{" "}
          {s?.avgLatency?.ttft != null ? `${s.avgLatency.ttft.toFixed(2)}s` : "-"} · 端到端{" "}
          {s?.avgLatency?.total != null ? `${(s.avgLatency.total / 1000).toFixed(2)}s` : "-"}
        </Sub>
      </Card>
    </div>
  );
}

export { PIE_COLORS };
