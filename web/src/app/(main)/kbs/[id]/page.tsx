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
  Tabs,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  InboxOutlined,
  ReloadOutlined,
  SettingOutlined,
  ShareAltOutlined,
  SlidersOutlined,
} from "@ant-design/icons";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import WikiGraphView from "@/components/WikiGraphView";
import Neo4jGraphView from "@/components/Neo4jGraphView";
import WikiManagePanel from "@/components/WikiManagePanel";
import ChunkingConfigModal from "@/components/ChunkingConfigModal";
import KBConfigModal from "@/components/KBConfigModal";
import {
  apiDeleteDocument,
  apiGetKb,
  apiListDocuments,
  apiSearch,
  apiUploadDocument,
  apiWikiPage,
  apiWikiTree,
  DocItem,
  KbItem,
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
  const [docPage, setDocPage] = useState(1);
  const [docPageSize, setDocPageSize] = useState(20);
  const [docTotal, setDocTotal] = useState(0);
  const [wiki, setWiki] = useState<WikiTree | null>(null);
  const [wikiLoading, setWikiLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [wikiView, setWikiView] = useState<"list" | "graph">("list");
  const [focusSlug, setFocusSlug] = useState<string | undefined>(undefined);
  const [activeTab, setActiveTab] = useState("docs");
  const [chunkingOpen, setChunkingOpen] = useState(false);
  const [kb, setKb] = useState<KbItem | null>(null);
  const [configOpen, setConfigOpen] = useState(false);
  const searchParams = useSearchParams();

  // 加载知识库详情（含 WeKnora 对齐配置：索引开关/技能绑定/模型绑定等）
  const loadKb = useCallback(async () => {
    const res = await apiGetKb(kbId).catch(() => null);
    if (res?.success && res.data) setKb(res.data);
  }, [kbId]);

  useEffect(() => {
    void loadKb();
  }, [loadKb]);

  // 从 wiki 页「图谱中查看」跳入时：?wiki=graph&focus=<slug>
  useEffect(() => {
    if (searchParams.get("wiki") === "graph") {
      setActiveTab("wiki");
      setWikiView("graph");
      const f = searchParams.get("focus");
      if (f) setFocusSlug(f);
    }
  }, [searchParams]);

  const load = useCallback(async () => {
    setDocsLoading(true);
    try {
      const res = await apiListDocuments(kbId, docPage, docPageSize);
      if (res.success) {
        setDocs(res.data?.items || []);
        setDocTotal(res.data?.total ?? 0);
      }
    } finally {
      setDocsLoading(false);
    }
  }, [kbId, docPage, docPageSize]);

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

  // Auto-poll while any document is still being parsed (PENDING/PARSING/EMBEDDING).
  const hasInFlight = docs.some((d) =>
    ["PENDING", "PARSING", "EMBEDDING"].includes(d.parse_state),
  );
  useEffect(() => {
    if (!hasInFlight) return;
    const timer = setInterval(() => {
      void load();
    }, 3000);
    return () => clearInterval(timer);
  }, [hasInFlight, load]);

  // Refresh the wiki tree once documents finish parsing (pages appear after READY).
  useEffect(() => {
    if (!hasInFlight) void loadWiki();
  }, [hasInFlight, loadWiki]);

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
            setDocPage(1); // 新文档按创建时间倒序排在第 1 页
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
      // 删除当前页最后一条且不在第 1 页时回退一页，避免空页
      if (docs.length === 1 && docPage > 1) setDocPage(docPage - 1);
      else void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ display: "flex" }}>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "docs",
            label: `文档（${docTotal}）`,
            children: (
              <Card
                title="知识库文档"
                extra={
                  <Space>
                    <Button icon={<ReloadOutlined />} onClick={() => void load()}>
                      刷新
                    </Button>
                    <Button icon={<SlidersOutlined />} onClick={() => setChunkingOpen(true)}>
                      切片配置
                    </Button>
                    <Button icon={<SettingOutlined />} onClick={() => setConfigOpen(true)}>
                      知识库配置
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
                  pagination={{
                    current: docPage,
                    pageSize: docPageSize,
                    total: docTotal,
                    showSizeChanger: true,
                    showTotal: (t) => `共 ${t} 个文档`,
                    onChange: (p, ps) => {
                      setDocPage(p);
                      setDocPageSize(ps);
                    },
                  }}
                  locale={{ emptyText: <Empty description="暂无文档，拖拽文件到右上角上传" /> }}
                  columns={[
                    { title: "文件名", dataIndex: "file_name" },
                    {
                      title: "状态",
                      dataIndex: "parse_state",
                      render: (v: string) => (
                        <Space size={4}>
                          {["PENDING", "PARSING", "EMBEDDING"].includes(v) && <Spin size="small" />}
                          <Tag color={PARSE_STATE_COLOR[v] || "default"}>{v}</Tag>
                        </Space>
                      ),
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
            ),
          },
          {
            key: "wiki",
            label: "Wiki",
            children: (
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
                    <Button
                      icon={<ShareAltOutlined />}
                      type={wikiView === "graph" ? "primary" : "default"}
                      onClick={() => setWikiView(wikiView === "graph" ? "list" : "graph")}
                    >
                      {wikiView === "graph" ? "列表视图" : "图谱视图"}
                    </Button>
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
                ) : wikiView === "graph" ? (
                  <WikiGraphView kbId={kbId} focusSlug={focusSlug} />
                ) : (
                  <WikiManagePanel kbId={kbId} />
                )}
              </Card>
            ),
          },
          {
            key: "graph",
            label: "知识图谱",
            children: <Neo4jGraphView kbId={kbId} />,
          },
        ]}
      />

      <ChunkingConfigModal
        open={chunkingOpen}
        kbId={kbId}
        onClose={() => setChunkingOpen(false)}
      />

      {/* 知识库配置（WeKnora 对齐：索引开关/类型/技能绑定/模型绑定/图谱/FAQ） */}
      <KBConfigModal
        kb={kb}
        open={configOpen}
        onClose={(changed) => {
          setConfigOpen(false);
          if (changed) void loadKb();
        }}
      />
    </Space>
  );
}
