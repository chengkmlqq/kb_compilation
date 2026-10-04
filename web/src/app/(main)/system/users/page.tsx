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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListUsers(1, 100, keyword);
      if (res.success) setRows(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, [keyword]);

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
          onSearch={() => void load()}
          style={{ width: 220 }}
        />
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
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