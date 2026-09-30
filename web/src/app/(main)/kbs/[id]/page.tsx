"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  List,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import { InboxOutlined, ReloadOutlined } from "@ant-design/icons";
import { useParams, useRouter } from "next/navigation";
import {
  apiDeleteDocument,
  apiListDocuments,
  apiSearch,
  apiUploadDocument,
  apiWikiPage,
  apiWikiTree,
  DocItem,
  SearchHit,
  WikiTree,
} from "@/lib/api";

const PARSE_STATE_COLOR: Record<string, string> = {
  PENDING: "default",
  PARSING: "processing",
  EMBEDDING: "processing",
  READY: "success",
  FAILED: "error",
};

export default function KbDetailPage() {
  const { id } = useParams<{ id: string }>();
  const kbId = id;
  const { message } = App.useApp();
  const router = useRouter();

  const [docs, setDocs] = useState<DocItem[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [wiki, setWiki] = useState<WikiTree | null>(null);
  const [wikiLoading, setWikiLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);

  const load = useCallback(async () => {
    setDocsLoading(true);
    try {
      const res = await apiListDocuments(kbId, 1, 100);
      if (res.success) setDocs(res.data?.items || []);
    } finally {
      setDocsLoading(false);
    }
  }, [kbId]);

  const loadWiki = useCallback(async () => {
    setWikiLoading(true);
    try {
      const res = await apiWikiTree(kbId);
      if (res.success) setWiki(res.data || null);
    } finally {
      setWikiLoading(false);
    }
  }, [kbId]);

  useEffect(() => {
    void load();
    void loadWiki();
  }, [load, loadWiki]);

  const doSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const res = await apiSearch(kbId, query.trim());
      if (res.success) setHits(res.data?.items || []);
      else message.error(res.message || "检索失败");
    } finally {
      setSearching(false);
    }
  };

  const uploadProps = useMemo(
    () => ({
      beforeUpload: (file: File) => {
        void (async () => {
          const res = await apiUploadDocument(kbId, file);
          if (res.success) {
            message.success(`「${file.name}」已上传，后台解析中`);
            void load();
          } else {
            message.error(res.message || "上传失败");
          }
        })();
        return false; // prevent antd auto-upload; we handle it manually
      },
      showUploadList: false,
      multiple: true,
    }),
    [kbId, load, message],
  );

  const onDeleteDoc = async (doc: DocItem) => {
    const res = await apiDeleteDocument(kbId, doc.id);
    if (res.success) {
      message.success("文档已删除");
      void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const wikiPages = wiki?.pages || [];

  return (
    <Space direction="vertical" size="large" style={{ display: "flex" }}>
      <Card
        title={`知识库文档（${docs.length}）`}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <Upload.Dragger {...uploadProps} style={{ width: 260, padding: "8px 12px" }}>
              点击或拖拽上传文档（md/pdf/docx/xlsx/pptx/epub 等）
            </Upload.Dragger>
          </Space>
        }
      >
        <Table<DocItem>
          rowKey="id"
          size="small"
          loading={docsLoading}
          dataSource={docs}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无文档，拖拽文件到右上角上传" /> }}
          columns={[
            { title: "文件名", dataIndex: "file_name" },
            {
              title: "状态",
              dataIndex: "parse_state",
              render: (v: string) => <Tag color={PARSE_STATE_COLOR[v] || "default"}>{v}</Tag>,
            },
            {
              title: "分块数",
              dataIndex: "chunk_count",
              width: 90,
            },
            {
              title: "操作",
              width: 90,
              render: (_: unknown, doc: DocItem) => (
                <Button type="link" danger size="small" onClick={() => onDeleteDoc(doc)}>
                  删除
                </Button>
              ),
            },
          ]}
        />
      </Card>

      <Card
        title="Wiki 页面"
        extra={
          <Space>
            <Input.Search
              placeholder="全文检索知识库"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onSearch={doSearch}
              loading={searching}
              style={{ width: 280 }}
            />
          </Space>
        }
      >
        {searching ? (
          <Spin />
        ) : hits.length > 0 ? (
          <List
            dataSource={hits}
            renderItem={(hit) => (
              <List.Item>
                <List.Item.Meta
                  title={<Typography.Text type="secondary">score {hit.score.toFixed(4)}</Typography.Text>}
                  description={hit.content.slice(0, 200)}
                />
              </List.Item>
            )}
          />
        ) : (
          <Spin spinning={wikiLoading}>
            {wikiPages.length === 0 ? (
              <Empty description="暂无 wiki 页面，文档解析入库后由后台自动生成" />
            ) : (
              <Table<WikiTree["pages"][number]>
                rowKey="slug"
                size="small"
                dataSource={wikiPages}
                pagination={false}
                columns={[
                  {
                    title: "页面标题",
                    dataIndex: "title",
                    render: (title: string, row) => (
                      <Typography.Link onClick={() => router.push(`/kbs/${kbId}/wiki/${row.slug}`)}>
                        {title}
                      </Typography.Link>
                    ),
                  },
                  { title: "类型", dataIndex: "page_type", width: 110 },
                  {
                    title: "摘要",
                    dataIndex: "summary",
                    ellipsis: true,
                    render: (v: string | null) => v || "-",
                  },
                ]}
              />
            )}
          </Spin>
        )}
      </Card>
    </Space>
  );
}
