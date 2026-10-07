"use client";

/** 文档全流程处理时间线（对齐 WeKnora knowledge-processing-timeline）：
 * 解析 → 向量化 → Wiki 构建 三阶段；wiki 阶段可展开看执行轨迹树（measure 嵌套步骤）。
 * 内嵌于文档详情抽屉，打开时拉取；解析/wiki 运行中每 6s 轮询。
 */
import { useCallback, useEffect, useState } from "react";
import { Collapse, Empty, Space, Spin, Tag, Timeline, Tree, Typography } from "antd";
import {
  apiProcessingTimeline,
  ProcessingStage,
  ProcessingTimelineData,
  TraceStepNode,
} from "@/lib/api";

const { Text } = Typography;

const STATE_TAG: Record<string, { color: string; text: string }> = {
  SUCCESS: { color: "success", text: "完成" },
  FAILED: { color: "error", text: "失败" },
  RUNNING: { color: "processing", text: "进行中" },
  PENDING: { color: "default", text: "等待中" },
  NONE: { color: "default", text: "未开始" },
};

interface TraceTreeNode {
  key: string;
  title: React.ReactNode;
  children?: TraceTreeNode[];
}

function buildTraceTree(nodes: TraceStepNode[], rootTotalMs: number): TraceTreeNode[] {
  return nodes.map((n, i) => {
    const done = n.status === "done" || n.status === "fail";
    const pct = rootTotalMs > 0 ? Math.round(((n.ms || 0) / rootTotalMs) * 100) : 0;
    return {
      key: `${n.step}-${n.start_ts ?? i}`,
      title: (
        <Space size={8} style={{ fontSize: 12 }}>
          <span>{n.step}</span>
          {n.status === "done" ? (
            <Tag color="green">完成</Tag>
          ) : n.status === "fail" ? (
            <Tag color="red">失败</Tag>
          ) : n.status === "running" ? (
            <Tag color="blue">进行中</Tag>
          ) : (
            <Tag>中断</Tag>
          )}
          <Text type="secondary" style={{ fontSize: 12 }}>
            {((n.ms || 0) / 1000).toFixed(1)}s
          </Text>
          {done ? (
            <span style={{ background: "#f0f0f0", borderRadius: 4, height: 8, width: 100 }}>
              <span
                style={{
                  display: "block",
                  width: `${Math.min(100, pct)}%`,
                  height: 8,
                  borderRadius: 4,
                  background: pct > 40 ? "#fa8c16" : "#1677ff",
                }}
              />
            </span>
          ) : null}
        </Space>
      ),
      children: n.children?.length ? buildTraceTree(n.children, rootTotalMs) : undefined,
    };
  });
}

function sumMs(ns: TraceStepNode[]): number {
  return ns.reduce((a, n) => a + (n.ms || 0) + sumMs(n.children || []), 0);
}

export default function ProcessingTimeline({
  kbId,
  docId,
  active,
}: {
  kbId: string;
  docId: string;
  /** 有阶段在运行时开启轮询 */
  active?: boolean;
}) {
  const [data, setData] = useState<ProcessingTimelineData | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!kbId || !docId) return;
    try {
      const res = await apiProcessingTimeline(kbId, docId);
      if (res.success && res.data) setData(res.data);
    } catch {
      /* 瞬时错误忽略（轮询/打开时重试） */
    }
  }, [kbId, docId]);

  useEffect(() => {
    setLoading(true);
    void load().finally(() => setLoading(false));
  }, [load]);

  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => void load(), 6000);
    return () => clearInterval(t);
  }, [active, load]);

  const stages: ProcessingStage[] = data?.stages || [];
  const running = stages.some(
    (s) => s.state === "RUNNING" || s.state === "PENDING",
  );

  if (!loading && stages.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="暂无处理时间线数据"
      />
    );
  }

  return (
    <Spin spinning={loading}>
      <Timeline
        items={stages.map((s) => {
          const st = STATE_TAG[s.state] || STATE_TAG.NONE;
          const dur =
            s.duration_ms != null ? `${Math.round(s.duration_ms / 60000)}min` : "";
          const hasSteps = (s.steps?.length ?? 0) > 0;
          return {
            color:
              s.state === "FAILED"
                ? "red"
                : s.state === "SUCCESS"
                  ? "green"
                  : s.state === "RUNNING"
                    ? "blue"
                    : "gray",
            children: (
              <div>
                <Space size={8} wrap style={{ fontSize: 13 }}>
                  <span style={{ fontWeight: 500 }}>{s.label}</span>
                  <Tag color={st.color}>{st.text}</Tag>
                  {dur ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {dur}
                    </Text>
                  ) : null}
                  {s.detail ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {s.detail}
                    </Text>
                  ) : null}
                  {s.state === "FAILED" && s.error ? (
                    <Text type="danger" style={{ fontSize: 12 }}>
                      {s.error.slice(0, 160)}
                    </Text>
                  ) : null}
                </Space>
                {hasSteps ? (
                  <Collapse
                    ghost
                    size="small"
                    style={{ marginTop: 4 }}
                    items={[
                      {
                        key: "steps",
                        label: (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            执行轨迹（{s.steps?.length} 个顶层步骤 · {Math.round(sumMs(s.steps || []) / 1000)}s）
                          </Text>
                        ),
                        children: (
                          <Tree
                            treeData={buildTraceTree(s.steps || [], sumMs(s.steps || []))}
                            defaultExpandAll
                            showLine
                          />
                        ),
                      },
                    ]}
                  />
                ) : null}
              </div>
            ),
          };
        })}
      />
      {running ? (
        <Text type="secondary" style={{ fontSize: 12 }}>
          处理中，6 秒自动刷新
        </Text>
      ) : null}
    </Spin>
  );
}