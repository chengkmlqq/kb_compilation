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
  const [wiki, setWiki] = useState<WikiTree | null>(null);
  const [wikiLoading, setWikiLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [wikiView, setWikiView] = useState<"list" | "graph">("list");
  const [focusSlug, setFocusSlug] = useState<string | undefined>(undefined);
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
      setWikiView("graph");
      const f = searchParams.get("focus");
      if (f) setFocusSlug(f);
    }
  }, [searchParams]);

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

  return (
    <Space direction="vertical" size="large" style={{ display: "flex" }}>
      <Card
        title={`知识库文档（${docs.length}）`}
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
          pagination={false}
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

      {/* Neo4j 实体/关系知识图谱（与上方 wiki 链接图不同源：Neo4j vs pg） */}
      <Neo4jGraphView kbId={kbId} />

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
