"use client";

/** 团队管理（独立页，对齐 ds system/teams）。 */
import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Table } from "antd";
import { apiListTeams, SysTeamItem } from "@/lib/api";
import { StateTag } from "../_shared";

export default function SystemTeamsPage() {
  const [rows, setRows] = useState<SysTeamItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListTeams(page, pageSize);
      if (res.success) {
        setRows(res.data?.items || []);
        setTotal(res.data?.total ?? 0);
      }
    } finally {
      setLoading(false);
    }
  }, [page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card title="团队">
      <Table
        rowKey="team_id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 个团队`,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
        locale={{ emptyText: <Empty description="暂无团队" /> }}
        columns={[
          { title: "团队编码", dataIndex: "team_name" },
          { title: "名称", dataIndex: "label" },
          { title: "描述", dataIndex: "descr" },
          { title: "父团队", dataIndex: "parent_team_name" },
          { title: "状态", dataIndex: "state", width: 90, render: (v) => <StateTag value={v} /> },
        ]}
      />
    </Card>
  );
}