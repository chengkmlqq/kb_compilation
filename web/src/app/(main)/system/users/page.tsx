"use client";

/** 用户管理（独立页，对齐 ds system/users）。 */
import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Input, Table } from "antd";
import { apiListUsers, SysUserItem } from "@/lib/api";
import { StateTag } from "../_shared";

export default function SystemUsersPage() {
  const [rows, setRows] = useState<SysUserItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListUsers(page, pageSize, keyword);
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
      title="用户"
      extra={
        <Input.Search
          placeholder="按账号/姓名搜索"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={() => {
            setPage(1);
            void load();
          }}
          style={{ width: 220 }}
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
          showTotal: (t) => `共 ${t} 个用户`,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
        locale={{ emptyText: <Empty description="暂无用户" /> }}
        columns={[
          { title: "账号", dataIndex: "user_id" },
          { title: "姓名", dataIndex: "user_name" },
          { title: "邮箱", dataIndex: "email" },
          { title: "手机", dataIndex: "phone" },
          { title: "默认团队", dataIndex: "default_team" },
          { title: "状态", dataIndex: "state", width: 90, render: (v) => <StateTag value={v} /> },
        ]}
      />
    </Card>
  );
}