"use client";

/** 团队管理（独立页，对齐 ds system/teams）。 */
import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Table } from "antd";
import { apiListTeams, SysTeamItem } from "@/lib/api";
import { StateTag } from "../_shared";

export default function SystemTeamsPage() {
  const [rows, setRows] = useState<SysTeamItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListTeams(1, 100);
      if (res.success) setRows(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, []);

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
        pagination={false}
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