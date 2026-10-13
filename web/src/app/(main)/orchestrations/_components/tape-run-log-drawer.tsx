"use client";

/**
 * 编排执行日志抽屉 —— 列表页与设计器共用。
 *
 * 轮询由父级负责（execute 返回 run_id 后轮询 runs/{run_id} 拿逐步日志），
 * 本组件只负责展示：状态 / run_id / 逐步日志 / 错误 / 最终变量。
 */
import { Button, Drawer, Space, Tag, Typography } from "antd";
import type { TapeRunDetail } from "@/lib/api";

const { Text } = Typography;

interface TapeRunLogDrawerProps {
  open: boolean;
  run: TapeRunDetail | null;
  /** 父级轮询中（展示「轮询中…」提示） */
  polling?: boolean;
  onClose: () => void;
}

const RUN_STATUS_META: Record<string, { color: string; label: string }> = {
  queued: { color: "blue", label: "排队中" },
  running: { color: "blue", label: "执行中" },
  success: { color: "green", label: "成功" },
  failed: { color: "red", label: "失败" },
  cancelled: { color: "orange", label: "已取消" },
};

const STEP_STATUS_META: Record<string, { color: string; label: string }> = {
  success: { color: "green", label: "成功" },
  failed: { color: "red", label: "失败" },
  skipped: { color: "orange", label: "跳过" },
  running: { color: "blue", label: "执行中" },
};

export function TapeRunLogDrawer({ open, run, polling, onClose }: TapeRunLogDrawerProps) {
  const runMeta = (run && RUN_STATUS_META[run.status]) || { color: "blue", label: "执行中" };
  return (
    <Drawer
      title={`执行日志 · ${run?.tape_name || ""}`}
      placement="right"
      width={680}
      open={open}
      onClose={onClose}
      footer={
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Button onClick={onClose}>关闭</Button>
        </div>
      }
      destroyOnClose
    >
      {run && (
        <div>
          <Space style={{ marginBottom: 10 }}>
            <Tag color={runMeta.color}>{runMeta.label}</Tag>
            <Text type="secondary">run_id: {run.id}</Text>
            {polling && <Text type="secondary">（轮询中…）</Text>}
          </Space>
          {run.error && (
            <pre style={{ margin: "0 0 8px", fontSize: 12, color: "#cf1322", whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 120, overflow: "auto" }}>
              {run.error}
            </pre>
          )}
          <div style={{ maxHeight: 360, overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 8, padding: 8 }}>
            {(run.step_results || []).map((s, i) => {
              const m = STEP_STATUS_META[s.status] || { color: "red", label: s.status };
              return (
                <div key={s.step_id || i} style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "4px 0", borderBottom: "1px dashed #f5f5f5" }}>
                  <Tag color={m.color} style={{ width: 62, textAlign: "center", margin: 0, flexShrink: 0 }}>
                    {m.label}
                  </Tag>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Text strong style={{ fontSize: 13 }}>
                      [{s.step_inst}] {s.step_label}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                      {s.duration_ms ?? 0}ms
                    </Text>
                    {s.error ? (
                      <pre style={{ margin: "4px 0 0", fontSize: 12, color: "#cf1322", whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 120, overflow: "auto" }}>
                        {s.error}
                      </pre>
                    ) : (
                      s.body !== null &&
                      s.body !== undefined && (
                        <pre style={{ margin: "4px 0 0", fontSize: 12, color: "#555", whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 160, overflow: "auto" }}>
                          {typeof s.body === "string" ? s.body : JSON.stringify(s.body, null, 2)}
                        </pre>
                      )
                    )}
                  </div>
                </div>
              );
            })}
            {(run.step_results || []).length === 0 && (
              <Text type="secondary">尚无步骤日志{run.status === "queued" ? "（排队中）" : "（执行中）"}…</Text>
            )}
          </div>
          {run.bindings && Object.keys(run.bindings).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <Text strong style={{ fontSize: 12 }}>
                最终变量
              </Text>
              <pre style={{ margin: "4px 0 0", fontSize: 12, background: "#fafafa", padding: 8, borderRadius: 6, maxHeight: 140, overflow: "auto" }}>
                {JSON.stringify(run.bindings, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}

export default TapeRunLogDrawer;
