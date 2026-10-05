"use client";

/** 操作日志（独立页，对齐 ds system/system-logs）。 */
import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Input, Table, Tag } from "antd";
import { apiListOperationLogs, SysLogItem } from "@/lib/api";

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
    <Card
      title="操作日志"
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
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条日志`,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
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
    </Card>
  );
}