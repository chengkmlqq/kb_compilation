"use client";

/**
 * 编排运行历史抽屉 —— 列表页共用。
 *
 * 展示某编排的历史执行记录（GET /orchestrations/{tape_id}/runs 分页），
 * 每条可点击「查看日志」回看该次运行的逐步日志（详情由父级拉取并打开 TapeRunLogDrawer）。
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Drawer, Empty, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { apiListTapeRuns, TapeItem, TapeRunSummary } from "@/lib/api";

const { Text } = Typography;

interface TapeRunHistoryDrawerProps {
  open: boolean;
  tape: TapeItem | null;
  onClose: () => void;
  /** 回看某次运行：父级拉详情 → 打开 TapeRunLogDrawer（运行中则继续轮询） */
  onViewRun: (runId: string) => void;
}

const RUN_STATUS_META: Record<string, { color: string; label: string }> = {
  queued: { color: "blue", label: "排队中" },
  running: { color: "blue", label: "执行中" },
  success: { color: "green", label: "成功" },
  failed: { color: "red", label: "失败" },
  cancelled: { color: "orange", label: "已取消" },
};

function formatTime(v?: string): string {
  if (!v) return "-";
  // 后端返回 ISO 时间；保留到秒便于阅读
  const t = new Date(v);
  if (Number.isNaN(t.getTime())) return v;
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")} ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}:${String(t.getSeconds()).padStart(2, "0")}`;
}

export function TapeRunHistoryDrawer({ open, tape, onClose, onViewRun }: TapeRunHistoryDrawerProps) {
  const { message } = App.useApp();
  const [items, setItems] = useState<TapeRunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!tape) return;
    setLoading(true);
    try {
      const res = await apiListTapeRuns(tape.id, page, pageSize);
      if (res.success) {
        setItems(res.data?.items || []);
        setTotal(res.data?.total || 0);
      } else {
        message.error(res.message || "加载运行历史失败");
      }
    } finally {
      setLoading(false);
    }
  }, [tape, page, pageSize, message]);

  // 打开抽屉时回到第一页（重置页码后再拉取）
  useEffect(() => {
    if (open) setPage(1);
  }, [open]);

  useEffect(() => {
    if (open) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, page, pageSize, load]);

  const columns: ColumnsType<TapeRunSummary> = [
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (v: string) => {
        const m = RUN_STATUS_META[v] || { color: "default", label: v };
        return <Tag color={m.color}>{m.label}</Tag>;
      },
    },
    {
      title: "run_id",
      dataIndex: "id",
      width: 220,
      ellipsis: true,
      render: (v: string) => (
        <Tooltip title={v}>
          <Text code style={{ fontSize: 12 }}>
            {v}
          </Text>
        </Tooltip>
      ),
    },
    { title: "开始时间", dataIndex: "created_at", width: 170, render: (v?: string) => formatTime(v) },
    { title: "结束时间", dataIndex: "updated_at", width: 170, render: (v?: string) => formatTime(v) },
    {
      title: "操作",
      key: "act",
      width: 110,
      render: (_, row) => (
        <Button type="link" size="small" style={{ padding: "0 4px" }} onClick={() => onViewRun(row.id)}>
          查看日志
        </Button>
      ),
    },
  ];

  return (
    <Drawer
      title={`运行历史 · ${tape?.tape_name || ""}`}
      placement="right"
      width={760}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={items}
        pagination={false}
        scroll={{ x: "100%", y: "calc(100vh - 320px)" }}
        locale={{ emptyText: <Empty description="暂无运行记录，点击「执行」开始第一次运行" /> }}
      />
      <div style={{ marginTop: 12 }}>
        <ModoPagination
          current={page}
          pageSize={pageSize}
          total={total}
          showTotal={(t) => `共 ${t} 次运行`}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      </div>
    </Drawer>
  );
}

export default TapeRunHistoryDrawer;
