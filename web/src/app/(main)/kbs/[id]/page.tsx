"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Dropdown,
  Empty,
  Input,
  List,
  Modal,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  Pagination,
} from "antd";
import {
  CloudUploadOutlined,
  DownloadOutlined,
  FileTextOutlined,
  InboxOutlined,
  LinkOutlined,
  RedoOutlined,
  ReloadOutlined,
  SettingOutlined,
  ShareAltOutlined,
  SlidersOutlined,
} from "@ant-design/icons";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import WikiGraphView from "@/components/WikiGraphView";
import Neo4jGraphView from "@/components/Neo4jGraphView";
import WikiBrowseView from "@/components/WikiBrowseView";
import WikiManagePanel from "@/components/WikiManagePanel";
import DocCardView, { fileTypeIcon, formatSize } from "@/components/DocCardView";
import DocDetailDrawer from "@/components/DocDetailDrawer";
import UrlImportModal from "@/components/UrlImportModal";
import ChunkingConfigModal from "@/components/ChunkingConfigModal";
import KBConfigModal from "@/components/KBConfigModal";
import {
  apiDeleteDocument,
  apiDownloadDocument,
  apiGetKb,
  apiListDocuments,
  apiReparseDocument,
  apiSearch,
  apiUploadDocument,
  apiUploadDocumentByUrl,
  apiWikiPage,
  DocItem,
  KbItem,
  SearchHit,
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
  const { message, modal } = App.useApp();
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [docs, setDocs] = useState<DocItem[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [docPage, setDocPage] = useState(1);
  const [docPageSize, setDocPageSize] = useState(20);
  const [docTotal, setDocTotal] = useState(0);
  const [docKeyword, setDocKeyword] = useState("");
  const [docStatus, setDocStatus] = useState("");
  const [docType, setDocType] = useState("");
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  const [urlOpen, setUrlOpen] = useState(false);
  const [docView, setDocView] = useState<"card" | "list">(
    () => (localStorage.getItem("kb.docs.viewMode") as "card" | "list") || "card",
  );
  const [detailDoc, setDetailDoc] = useState<DocItem | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [wikiView, setWikiView] = useState<"browse" | "list" | "graph">("browse");
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
      const res = await apiListDocuments(kbId, docPage, docPageSize, {
        keyword: docKeyword.trim() || undefined,
        parseStatus: docStatus || undefined,
        fileType: docType || undefined,
      });
      if (res.success) {
        setDocs(res.data?.items || []);
        setDocTotal(res.data?.total ?? 0);
      }
    } finally {
      setDocsLoading(false);
    }
  }, [kbId, docPage, docPageSize, docKeyword, docStatus, docType]);

  useEffect(() => {
    void load();
  }, [load]);

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

  const onDeleteDoc = async (doc: DocItem) => {
    const res = await apiDeleteDocument(kbId, doc.id);
    if (res.success) {
      message.success("文档已删除");
      if (detailDoc?.id === doc.id) setDetailDoc(null);
      // 删除当前页最后一条且不在第 1 页时回退一页，避免空页
      if (docs.length === 1 && docPage > 1) setDocPage(docPage - 1);
      else void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const onDownloadDoc = async (doc: DocItem) => {
    try {
      await apiDownloadDocument(kbId, doc.id, doc.file_name);
      message.success("开始下载");
    } catch (e) {
      message.error((e as Error).message || "下载失败");
    }
  };

  const onReparseDoc = async (doc: DocItem) => {
    const res = await apiReparseDocument(kbId, doc.id);
    if (res.success) {
      message.success("已重新入队解析");
      setDetailDoc(null);
      void load();
    } else {
      message.error(res.message || "重新解析失败");
    }
  };

  const onViewTrace = (doc: DocItem) => {
    router.push(`/jobs?keyword=DOC_${doc.id}`);
  };

  const selectedList = docs.filter((d) => selectedDocs.has(d.id));

  const onBatchReparse = async () => {
    if (selectedList.length === 0) return;
    for (const d of selectedList) {
      await apiReparseDocument(kbId, d.id).catch(() => undefined);
    }
    message.success(`已重新入队 ${selectedList.length} 个文档解析`);
    setSelectedDocs(new Set());
    void load();
  };

  const onBatchDelete = () => {
    const n = selectedList.length;
    if (n === 0) return;
    modal.confirm({
      title: `删除选中的 ${n} 个文档？`,
      content: "删除后不可恢复（关联分块与向量一并清理）。",
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        for (const d of selectedList) {
          await apiDeleteDocument(kbId, d.id).catch(() => undefined);
        }
        message.success(`已删除 ${n} 个文档`);
        setSelectedDocs(new Set());
        void load();
      },
    });
  };

  const onUploadByUrl = async (url: string, fname: string) => {
    const res = await apiUploadDocumentByUrl(kbId, url.trim(), fname.trim() || undefined);
    if (res.success) {
      message.success("链接已抓取，后台解析中");
      setDocPage(1);
      void load();
    } else {
      message.error(res.message || "URL 导入失败");
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ display: "flex" }}>
      {/* KB 概览条：对齐 WeKnora 详情顶部（类型/归属/向量库/统计/索引开关） */}
      {kb && (
        <Card size="small">
          <Descriptions size="small" column={{ xs: 2, md: 4 }}>
            <Descriptions.Item label="类型">
              {kb.type === "faq" ? "问答对型" : "文档型"}
            </Descriptions.Item>
            <Descriptions.Item label="归属">
              {kb.scope === "system" ? "系统" : kb.scope === "team" ? "团队" : "个人"}
            </Descriptions.Item>
            <Descriptions.Item label="向量库">
              {kb.vector_store_id ? "自定义资源" : "系统默认（pgvector）"}
            </Descriptions.Item>
            <Descriptions.Item label="统计">
              {kb.doc_count ?? 0} 文档 / {kb.page_count ?? 0} Wiki 页
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "docs",
            label: `文档（${docTotal}）`,
            children: (
              <Card
                title={`知识库文档（${docTotal}）`}
                extra={
                  <Space wrap>
                    <Input
                      allowClear
                      placeholder="按文件名筛选"
                      style={{ width: 160 }}
                      value={docKeyword}
                      onChange={(e) => {
                        setDocKeyword(e.target.value);
                        setDocPage(1);
                      }}
                      onPressEnter={() => void load()}
                      suffix={
                        docKeyword ? (
                          <ReloadOutlined onClick={() => { setDocKeyword(""); setDocPage(1); }} />
                        ) : null
                      }
                    />
                    <Select
                      allowClear
                      placeholder="解析状态"
                      style={{ width: 130 }}
                      value={docStatus || undefined}
                      onChange={(v) => {
                        setDocStatus(v || "");
                        setDocPage(1);
                        void load();
                      }}
                      options={["PENDING", "PARSING", "EMBEDDING", "READY", "FAILED"].map((s) => ({
                        label: s,
                        value: s,
                      }))}
                    />
                    <Select
                      allowClear
                      placeholder="文件类型"
                      style={{ width: 120 }}
                      value={docType || undefined}
                      onChange={(v) => {
                        setDocType(v || "");
                        setDocPage(1);
                        void load();
                      }}
                      options={["md", "pdf", "docx", "xlsx", "pptx", "txt", "epub"].map((t) => ({
                        label: t.toUpperCase(),
                        value: t,
                      }))}
                    />
                    <Segmented
                      value={docView}
                      onChange={(v) => {
                        const mode = v as "card" | "list";
                        setDocView(mode);
                        try {
                          localStorage.setItem("kb.docs.viewMode", mode);
                        } catch {
                          /* ignore */
                        }
                      }}
                      options={[
                        { value: "card", label: "卡片" },
                        { value: "list", label: "列表" },
                      ]}
                    />
                    <Button icon={<ReloadOutlined />} onClick={() => void load()}>
                      刷新
                    </Button>
                    <Button icon={<SlidersOutlined />} onClick={() => setChunkingOpen(true)}>
                      切片配置
                    </Button>
                    <Button icon={<SettingOutlined />} onClick={() => setConfigOpen(true)}>
                      知识库配置
                    </Button>
                    <Dropdown
                      menu={{
                        items: [
                          { key: "local", label: "本地上传", icon: <CloudUploadOutlined /> },
                          { key: "url", label: "URL 导入", icon: <LinkOutlined /> },
                        ],
                        onClick: ({ key }) => {
                          if (key === "url") setUrlOpen(true);
                          else if (key === "local") fileInputRef.current?.click();
                        },
                      }}
                    >
                      <Button type="primary" icon={<CloudUploadOutlined />}>
                        上传文档
                      </Button>
                    </Dropdown>
                    {/* 隐藏的文件选择触发器（本地上传） */}
                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      style={{ display: "none" }}
                      onChange={(e) => {
                        const files = e.target.files;
                        if (files) {
                          for (const f of Array.from(files)) {
                            void (async () => {
                              const res = await apiUploadDocument(kbId, f);
                              if (res.success) message.success(`「${f.name}」已上传，后台解析中`);
                              else message.error(res.message || `「${f.name}」上传失败`);
                            })();
                          }
                          setDocPage(1);
                          void load();
                        }
                        e.target.value = "";
                      }}
                    />
                  </Space>
                }
              >
                {selectedList.length > 0 && (
                  <Space
                    style={{
                      display: "flex",
                      marginBottom: 12,
                      padding: "8px 12px",
                      background: "#e6f4ff",
                      borderRadius: 8,
                    }}
                  >
                    <Typography.Text strong>
                      已选 {selectedList.length} 个文档
                    </Typography.Text>
                    <Button size="small" icon={<RedoOutlined />} onClick={() => void onBatchReparse()}>
                      批量重建
                    </Button>
                    <Button size="small" danger icon={<DownloadOutlined />} onClick={onBatchDelete}>
                      批量删除
                    </Button>
                    <Button size="small" type="link" onClick={() => setSelectedDocs(new Set())}>
                      取消选择
                    </Button>
                  </Space>
                )}
                {docView === "card" ? (
                  <>
                    <DocCardView
                      items={docs}
                      onOpen={setDetailDoc}
                      onDownload={onDownloadDoc}
                      onReparse={onReparseDoc}
                      onDelete={onDeleteDoc}
                      onTrace={onViewTrace}
                    />
                    {/* 2026-10-06: 卡片视图补充分页(与列表视图同源 docPage/docPageSize)，否则只能看当前页 */}
                    <div className="mt-3 flex justify-end">
                      <Pagination
                        current={docPage}
                        pageSize={docPageSize}
                        total={docTotal}
                        showSizeChanger
                        showTotal={(t) => `共 ${t} 个文档`}
                        onChange={(p, ps) => {
                          setDocPage(p);
                          setDocPageSize(ps);
                        }}
                      />
                    </div>
                  </>
                ) : (
                <Table<DocItem>
                  rowKey="id"
                  size="small"
                  loading={docsLoading}
                  dataSource={docs}
                  rowSelection={{
                    selectedRowKeys: [...selectedDocs],
                    onChange: (keys) => setSelectedDocs(new Set(keys as string[])),
                  }}
                  onRow={(doc) => ({ onClick: () => setDetailDoc(doc) })}
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
                    {
                      title: "文件名",
                      dataIndex: "file_name",
                      render: (v: string, doc: DocItem) => (
                        <Space>
                          <span style={{ fontSize: 16 }}>{fileTypeIcon(doc.file_ext)}</span>
                          <a onClick={() => setDetailDoc(doc)}>{v}</a>
                        </Space>
                      ),
                    },
                    {
                      title: "状态",
                      dataIndex: "parse_state",
                      render: (v: string, doc: DocItem) => (
                        <Space size={4}>
                          {["PENDING", "PARSING", "EMBEDDING"].includes(v) && <Spin size="small" />}
                          <Tag color={PARSE_STATE_COLOR[v] || "default"}>{v}</Tag>
                          {v === "FAILED" && (
                            <a onClick={() => onViewTrace(doc)}>查看原因</a>
                          )}
                        </Space>
                      ),
                    },
                    {
                      title: "分块数",
                      dataIndex: "chunk_count",
                      width: 90,
                    },
                    {
                      title: "大小",
                      dataIndex: "file_size",
                      width: 110,
                      render: (v: number | null) => formatSize(v),
                    },
                    {
                      title: "操作",
                      width: 150,
                      render: (_: unknown, doc: DocItem) => (
                        <Space size={4}>
                          <Button type="link" size="small" icon={<DownloadOutlined />} onClick={(e) => { e.stopPropagation(); void onDownloadDoc(doc); }}>
                            下载
                          </Button>
                          <Button type="link" size="small" icon={<RedoOutlined />} onClick={(e) => { e.stopPropagation(); void onReparseDoc(doc); }}>
                            重解析
                          </Button>
                          <Button type="link" danger size="small" onClick={(e) => { e.stopPropagation(); void onDeleteDoc(doc); }}>
                            删除
                          </Button>
                        </Space>
                      ),
                    },
                  ]}
                />
                )}
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
                    <Segmented
                      value={wikiView}
                      onChange={(v) => setWikiView(v as "browse" | "list" | "graph")}
                      options={[
                        { value: "browse", label: "浏览" },
                        { value: "list", label: "管理" },
                        { value: "graph", label: "图谱" },
                      ]}
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
                ) : wikiView === "graph" ? (
                  <WikiGraphView kbId={kbId} focusSlug={focusSlug} />
                ) : wikiView === "list" ? (
                  <WikiManagePanel kbId={kbId} />
                ) : (
                  <WikiBrowseView
                    kbId={kbId}
                    focusSlug={focusSlug}
                    onTreeChanged={() => undefined}
                  />
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

      {/* 文档详情抽屉（对齐 WeKnora DocContent：元数据 + 分块预览 + 下载/重解析/删除） */}
      <DocDetailDrawer
        kbId={kbId}
        doc={detailDoc}
        onClose={() => setDetailDoc(null)}
        onDownload={onDownloadDoc}
        onReparse={onReparseDoc}
        onDelete={onDeleteDoc}
      />

      <ChunkingConfigModal
        open={chunkingOpen}
        kbId={kbId}
        onClose={() => setChunkingOpen(false)}
      />

      {/* URL 导入弹窗（对齐 WeKnora 上传下拉的链接导入） */}
      <UrlImportModal
        open={urlOpen}
        onClose={() => setUrlOpen(false)}
        onSubmit={onUploadByUrl}
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
