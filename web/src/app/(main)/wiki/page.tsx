"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Select, Space, Table, Typography } from "antd";
import { useRouter } from "next/navigation";
import { apiListKbs, apiWikiTree, KbItem, WikiTree } from "@/lib/api";

export default function WikiOverviewPage() {
  const router = useRouter();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [kbId, setKbId] = useState<string>();
  const [tree, setTree] = useState<WikiTree | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      const res = await apiListKbs(1, 100);
      if (res.success) {
        const items = res.data?.items || [];
        setKbs(items);
        if (items.length > 0) setKbId((prev) => prev || items[0].id);
      }
    })();
  }, []);

  const loadTree = useCallback(async () => {
    if (!kbId) return;
    setLoading(true);
    try {
      const res = await apiWikiTree(kbId);
      if (res.success) setTree(res.data || null);
    } finally {
      setLoading(false);
    }
  }, [kbId]);

  useEffect(() => {
    void loadTree();
  }, [loadTree]);

  const folderMap = new Map((tree?.folders || []).map((f) => [f.id, f.name]));
  const pages = tree?.pages || [];

  return (
    <Card
      title="Wiki 总览"
      extra={
        <Select
          style={{ width: 240 }}
          placeholder="选择知识库"
          value={kbId}
          onChange={setKbId}
          options={kbs.map((kb) => ({ value: kb.id, label: kb.name }))}
        />
      }
    >
      <Table
        rowKey="slug"
        size="small"
        loading={loading}
        dataSource={pages}
        pagination={false}
        locale={{ emptyText: <Empty description="该知识库暂无 wiki 页面" /> }}
        columns={[
          {
            title: "标题",
            dataIndex: "title",
            render: (title: string, row) => (
              <Typography.Link onClick={() => router.push(`/kbs/${kbId}/wiki/${row.slug}`)}>
                {title}
              </Typography.Link>
            ),
          },
          { title: "类型", dataIndex: "page_type", width: 110 },
          { title: "目录", width: 160, render: (_: unknown, row) => folderMap.get(row.folder_id) || "根目录" },
          {
            title: "摘要",
            dataIndex: "summary",
            ellipsis: true,
            render: (v: string | null) => v || "-",
          },
        ]}
      />
      <Space direction="vertical" size="small" style={{ marginTop: 16, width: "100%" }}>
        <Typography.Text type="secondary">
          共 {pages.length} 个页面 / {tree?.folders.length || 0} 个目录。Wiki 页面由文档入库后的后台任务自动生成。
        </Typography.Text>
      </Space>
    </Card>
  );
}
