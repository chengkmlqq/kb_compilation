"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Input, Table, Tag } from "antd";
import { apiListDatasources, DatasourceItem } from "@/lib/api";

export default function DatasourcesPage() {
  const [rows, setRows] = useState<DatasourceItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListDatasources(1, 50, keyword);
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
      title="数据源"
      extra={
        <Input.Search
          placeholder="按名称搜索"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={() => void load()}
          style={{ width: 240 }}
        />
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无数据源" /> }}
        columns={[
          { title: "名称", dataIndex: "dsName" },
          { title: "类型", dataIndex: "dsType", render: (v: string) => <Tag>{v}</Tag> },
          { title: "版本", dataIndex: "dsVersion" },
          { title: "分类", dataIndex: "dsCategory" },
          {
            title: "状态",
            dataIndex: "state",
            width: 90,
            render: (v: string) => <Tag color={v === "1" ? "success" : "default"}>{v}</Tag>,
          },
        ]}
      />
    </Card>
  );
}