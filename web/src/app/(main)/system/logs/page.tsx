"use client";

/** 操作日志（独立页，对齐 ds system/system-logs）。 */
import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Input, Tag } from "antd";
import { apiListOperationLogs, SysLogItem } from "@/lib/api";
import ModoTable from "@/components/biz/modo-table";
import ModoPagination from "@/components/biz/modo-pagination";

export default function SystemLogsPage() {
  const [rows, setRows] = useState<SysLogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListOperationLogs(page, pageSize, keyword);
      if (res.success) {
        setRows(res.data?.items || []);
        setTotal(res.data?.total ?? 0);
      }
    } finally {
      setLoading(false);
    }
  }, [keyword, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    // 2026-10-07 一屏自适应（对齐 data-synth）：外层不滚动，卡片内表格占满剩余高度，分页常驻底栏
    <div style={{ padding: 8, height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        title="操作日志"
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{ body: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" } }}
        extra={
          <Input.Search
            placeholder="按用户/类型/内容搜索"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onSearch={() => {
              setPage(1);
              void load();
            }}
            style={{ width: 240 }}
          />
        }
      >
        <ModoTable
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={rows}
          locale={{ emptyText: <Empty description="暂无日志" /> }}
          columns={[
            { title: "用户", dataIndex: "user_name" },
            {
              title: "类型",
              dataIndex: "oper_type",
              width: 110,
              render: (v: string) => <Tag color={v === "LOGIN" ? "blue" : "default"}>{v}</Tag>,
            },
            { title: "内容", dataIndex: "oper_content", ellipsis: true },
            { title: "URL", dataIndex: "oper_url", width: 160, ellipsis: true },
            { title: "时间", dataIndex: "oper_time", width: 170 },
          ]}
        />
<ModoPagination
          current={page}
          pageSize={pageSize}
          total={total}
          showSizeChanger
          showTotal={(t) => `共 ${t} 条日志`}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      </Card>
    </div>
  );
}