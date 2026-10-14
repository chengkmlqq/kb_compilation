"use client";
/**
 * 文档详情面板（DocDetailPanel）
 *
 * 由原「文档详情抽屉」DocDetailDrawer 改造而来：不再用 Drawer，改为承载在
 * 动态选项卡页签内的内容面板（2026-10-14）。
 *
 *  - 头部：文件图标 + 文件名 + 下载 / 重新解析
 *  - 内容顺序：基本信息（Descriptions）→ AI 摘要 → 视图切换 → 预览/合并/分块
 *  - 三视图：预览（可预览类型 / 表源实时样例）、合并（按 seq 拼接全文）、分块（卡片网格，支持关键字检索+高亮）
 *  - 默认视图：音频 → 合并；表源 / 可预览 → 预览；其他 → 合并
 *  - 音频播放器固定显示在内容区顶部（任何视图都可见）
 */
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { App, Button, Card, Descriptions, Empty, Input, List, Pagination, Space, Spin, Table, Tag, Typography } from "antd";
import { DownloadOutlined, RedoOutlined, RobotOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  apiDownloadDocument,
  apiGetDocumentChunks,
  apiGetTableSample,
  apiReparseDocument,
  DocChunkItem,
  DocItem,
} from "@/lib/api";
import { PARSE_STATE_COLOR, STATE_LABEL, fileTypeIcon, formatSize, formatTime } from "./DocCardView";
import DocPreviewModal, { canPreviewExt } from "./DocPreviewModal";

interface Props {
  kbId: string;
  doc: DocItem;
  /** 重新解析成功后回调（父级刷新文档列表） */
  onDocReparsed?: (doc: DocItem) => void;
}

type ViewMode = "preview" | "merged" | "chunks";

const AUDIO_EXTS = new Set(["mp3", "wav", "m4a", "flac", "ogg", "aac", "opus"]);

/** 关键字高亮：把命中的片段包成 <mark>（大小写不敏感匹配，保留原文大小写） */
function highlight(text: string, keyword: string): ReactNode {
  const kw = keyword.trim();
  if (!kw) return text;
  const src = text || "";
  const lower = src.toLowerCase();
  const target = kw.toLowerCase();
  const parts: ReactNode[] = [];
  let cursor = 0;
  let hit = lower.indexOf(target);
  let n = 0;
  while (hit !== -1) {
    if (hit > cursor) parts.push(src.slice(cursor, hit));
    parts.push(
      <mark key={n++} style={{ background: "#fff1b8", color: "inherit", padding: 0 }}>
        {src.slice(hit, hit + kw.length)}
      </mark>,
    );
    cursor = hit + kw.length;
    hit = lower.indexOf(target, cursor);
  }
  parts.push(src.slice(cursor));
  return parts;
}

export default function DocDetailPanel({ kbId, doc, onDocReparsed }: Props) {
  const { message } = App.useApp();
  const [allChunks, setAllChunks] = useState<DocChunkItem[]>([]);
  const [chunkKeyword, setChunkKeyword] = useState(""); // 已提交的检索词
  const [chunkInput, setChunkInput] = useState(""); // 检索框当前输入
  const [chunkPage, setChunkPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("merged");

  /** 切片总数（接口一次返回全量，分页与检索均在前端完成） */
  const chunkTotal = allChunks.length;

  const isAudio = AUDIO_EXTS.has((doc.file_ext || "").toLowerCase());
  const canPreview = canPreviewExt(doc.file_ext);
  // 表源（2026-10-10 表→wiki）：source_type === "table" → 预览=实时样例表格
  const isTable = (doc.source_type || "file") === "table";
  const [sampleData, setSampleData] = useState<{ columns: string[]; rows: Record<string, unknown>[] } | null>(null);
  const [sampleLoading, setSampleLoading] = useState(false);

  // 默认视图：音频 → 合并；表源 / 可预览 → 预览；其他 → 合并
  useEffect(() => {
    if (isAudio) setViewMode("merged");
    else if (isTable || canPreview) setViewMode("preview");
    else setViewMode("merged");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.id]);

  useEffect(() => {
    setAllChunks([]);
    setChunkKeyword("");
    setChunkInput("");
    setChunkPage(1);
    void loadChunks(doc.id);
    // 表源：拉实时样例数据（预览视图用，实时连库不落库）
    if ((doc.source_type || "file") === "table") {
      setSampleData(null);
      setSampleLoading(true);
      void apiGetTableSample(kbId, doc.id, 50)
        .then((res) => {
          if (res.success && res.data) {
            setSampleData({ columns: res.data.columns, rows: res.data.rows });
          }
        })
        .catch(() => setSampleData(null))
        .finally(() => setSampleLoading(false));
    } else {
      setSampleData(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.id, doc.parse_state]);

  const loadChunks = async (docId: string) => {
    setLoading(true);
    try {
      const res = await apiGetDocumentChunks(kbId, docId);
      if (res.success && res.data) {
        setAllChunks(res.data.items || []);
      } else {
        message.error(res.message || "加载分块失败");
      }
    } finally {
      setLoading(false);
    }
  };

  /** 关键字命中的切片：跨「全部分块」过滤（接口一次返回全量），非仅当前页 */
  const filteredChunks = useMemo(() => {
    const kw = chunkKeyword.trim().toLowerCase();
    if (!kw) return allChunks;
    return allChunks.filter((c) => (c.content || "").toLowerCase().includes(kw));
  }, [allChunks, chunkKeyword]);

  /** 命中结果的前端分页（每页 10 条） */
  const pageChunks = useMemo(
    () => filteredChunks.slice((chunkPage - 1) * 10, chunkPage * 10),
    [filteredChunks, chunkPage],
  );

  /** 提交检索：切回第 1 页 */
  const submitChunkSearch = (value: string) => {
    setChunkKeyword(value.trim());
    setChunkPage(1);
  };

  const onDownload = async () => {
    try {
      await apiDownloadDocument(kbId, doc.id, doc.file_name);
      message.success("开始下载");
    } catch (e) {
      message.error((e as Error).message || "下载失败");
    }
  };

  const onReparse = async () => {
    const res = await apiReparseDocument(kbId, doc.id);
    if (res.success) {
      message.success("已重新入队解析");
      onDocReparsed?.(doc);
    } else {
      message.error(res.message || "重新解析失败");
    }
  };

  const summaryReady = doc.summary_status === "READY" && doc.summary;
  const summaryFailed = doc.summary_status === "FAILED";

  // 视图切换按钮（对齐 WeKnora view-mode-buttons：active = base+primary，非 active = outline+default）
  const viewBtn = (mode: ViewMode, label: string) => (
    <Button
      size="small"
      type={viewMode === mode ? "primary" : "default"}
      onClick={() => setViewMode(mode)}
      style={{
        padding: "0 12px",
        fontSize: 12,
        height: 24,
        borderRadius: 4,
      }}
    >
      {label}
    </Button>
  );

  return (
    <div style={{ height: "100%", overflow: "auto", padding: 16, boxSizing: "border-box" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: "100%" }}>
        {/* 头部：文件图标 + 文件名 + 操作 */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          <span style={{ fontSize: 18, flexShrink: 0, lineHeight: 1 }}>{fileTypeIcon(doc.file_ext)}</span>
          <Typography.Text
            strong
            style={{ flex: 1, minWidth: 0, fontSize: 15, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            title={doc.file_name}
          >
            {doc.file_name}
          </Typography.Text>
          <Space size={4} style={{ flexShrink: 0 }}>
            <Button size="small" icon={<DownloadOutlined />} onClick={() => void onDownload()}>
              下载
            </Button>
            <Button size="small" icon={<RedoOutlined />} onClick={() => void onReparse()}>
              重新解析
            </Button>
          </Space>
        </div>

        {/* 基本信息 */}
        <Descriptions size="small" column={2} bordered>
          {isTable ? (
            <>
              <Descriptions.Item label="表名">{doc.file_name}</Descriptions.Item>
              <Descriptions.Item label="数据源">{doc.ds_source_name || "—"}</Descriptions.Item>
              <Descriptions.Item label="库 / Schema">{doc.ds_table_schema || "—"}</Descriptions.Item>
              <Descriptions.Item label="数据量">{doc.row_count != null ? `${doc.row_count.toLocaleString()} 行` : "—"}</Descriptions.Item>
              <Descriptions.Item label="分块数">{doc.chunk_count ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={PARSE_STATE_COLOR[doc.parse_state] || "default"}>{STATE_LABEL[doc.parse_state] || doc.parse_state}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="绑定时间">{formatTime(doc.created_at)}</Descriptions.Item>
            </>
          ) : (
            <>
              <Descriptions.Item label="文件名">{doc.file_name}</Descriptions.Item>
              <Descriptions.Item label="类型">{doc.file_ext?.toUpperCase() || "—"}</Descriptions.Item>
              <Descriptions.Item label="大小">{formatSize(doc.file_size)}</Descriptions.Item>
              <Descriptions.Item label="分块数">{doc.chunk_count ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={PARSE_STATE_COLOR[doc.parse_state] || "default"}>{STATE_LABEL[doc.parse_state] || doc.parse_state}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="上传时间">{formatTime(doc.created_at)}</Descriptions.Item>
            </>
          )}
          {doc.parse_state === "FAILED" && doc.parse_error ? (
            <Descriptions.Item label="解析错误" span={2}>
              <span style={{ color: "#cf1322" }}>{doc.parse_error}</span>
            </Descriptions.Item>
          ) : null}
        </Descriptions>

        {/* AI 摘要（展示；摘要改走 wiki 构建任务生成，此处仅展示） */}
        {doc.parse_state === "READY" ? (
          <div
            style={{
              border: "1px solid #f0f0f0",
              borderRadius: 8,
              padding: "12px 16px",
              background: "#fafafa",
              flexShrink: 0,
            }}
          >
            <Space style={{ marginBottom: summaryReady || summaryFailed ? 8 : 0 }}>
              <RobotOutlined style={{ color: "#1677ff" }} />
              <Typography.Text strong>AI 摘要</Typography.Text>
            </Space>
            {summaryReady ? (
              /* 摘要为 LLM 生成的 Markdown（标题/列表/表格/链接），按 md 渲染；
                 复用全站 kb-markdown 样式（与文档预览的 markdown 分支同款） */
              <div className="kb-markdown" style={{ wordBreak: "break-word" }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.summary || ""}</ReactMarkdown>
              </div>
            ) : summaryFailed ? (
              <Typography.Text type="danger" style={{ wordBreak: "break-word" }}>
                {doc.summary_error || "摘要生成失败"}
              </Typography.Text>
            ) : (
              <Typography.Text type="secondary">暂无摘要</Typography.Text>
            )}
          </div>
        ) : null}

        {/* 视图切换按钮组 */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {isTable ? (
            <>
              {viewBtn("preview", "预览")}
              {viewBtn("merged", "合并视图")}
            </>
          ) : (
            <>
              {canPreview ? viewBtn("preview", "预览") : null}
              {!canPreview ? viewBtn("merged", "合并视图") : null}
            </>
          )}
          {viewBtn("chunks", "分块")}
          {chunkTotal > 0 ? (
            <span style={{ marginLeft: "auto", fontSize: 12, color: "rgba(0,0,0,.45)" }}>
              共 {chunkTotal} 个分块
            </span>
          ) : null}
        </div>

        {/* 音频播放器（固定显示在内容区顶部，任何视图都可见） */}
        {isAudio && (
          <div style={{ padding: "8px 0" }}>
            <DocPreviewModal kbId={kbId} doc={doc} onClose={() => undefined} embedded />
          </div>
        )}

        {/* 预览视图（内嵌；表源 = 实时样例表格） */}
        {viewMode === "preview" && !isAudio && (
          <div style={{ flex: 1, minHeight: 300, display: "flex", flexDirection: "column" }}>
            {isTable ? (
              <TableSampleView loading={sampleLoading} data={sampleData} />
            ) : (
              <DocPreviewModal kbId={kbId} doc={doc} onClose={() => undefined} embedded />
            )}
          </div>
        )}

        {/* 合并视图（按 seq 拼接全部分块还原全文） */}
        {viewMode === "merged" && !isAudio && <MergedView kbId={kbId} docId={doc.id} total={chunkTotal} />}

        {/* 分块视图 */}
        {viewMode === "chunks" && (
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
              <Typography>
                {chunkTotal > 0 ? `分块预览（${chunkTotal}）` : "分块预览"}
                {chunkKeyword ? ` · 命中 ${filteredChunks.length}` : ""}
              </Typography>
              <Input.Search
                allowClear
                size="small"
                placeholder="按关键字检索切片"
                style={{ width: 260, marginLeft: "auto" }}
                value={chunkInput}
                onChange={(e) => {
                  setChunkInput(e.target.value);
                  // 清空（含 allowClear 的 ×）即恢复全量
                  if (!e.target.value) submitChunkSearch("");
                }}
                onSearch={submitChunkSearch}
              />
            </div>
            {loading ? (
              <div style={{ textAlign: "center", padding: 24 }}>
                <Spin />
              </div>
            ) : pageChunks.length === 0 ? (
              <Empty
                description={
                  chunkKeyword
                    ? `未命中「${chunkKeyword}」`
                    : doc.parse_state === "READY"
                      ? "暂无分块"
                      : "解析完成后展示分块"
                }
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            ) : (
              <>
                <List
                  grid={{ gutter: 12, xs: 1, sm: 1, md: 2, lg: 2, xl: 3, xxl: 3 }}
                  dataSource={pageChunks}
                  renderItem={(c) => (
                    <List.Item style={{ marginBottom: 12 }}>
                      <Card
                        size="small"
                        style={{ height: "100%", borderColor: "#eff4f9" }}
                        styles={{ body: { padding: "8px 12px" } }}
                        title={<span style={{ fontSize: 12, fontWeight: 500, color: "#8a96a8" }}>#{c.seq}</span>}
                      >
                        <div
                          className="kb-markdown"
                          style={{
                            // 固定高度（而非 maxHeight）：网格内每张切片卡片等高，避免高低不齐
                            height: 140,
                            overflowY: "auto",
                            fontSize: 13,
                            lineHeight: 1.7,
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word",
                          }}
                        >
                          {highlight(c.content || "", chunkKeyword)}
                        </div>
                      </Card>
                    </List.Item>
                  )}
                />
                <Pagination
                  simple
                  current={chunkPage}
                  pageSize={10}
                  total={filteredChunks.length}
                  onChange={(p) => setChunkPage(p)}
                  style={{ marginTop: 12, textAlign: "center" }}
                />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** 合并视图：拉取全部分块并按 seq 拼接（对齐 WeKnora mergedContent） */
function MergedView({ kbId, docId, total }: { kbId: string; docId: string; total: number }) {
  const { message } = App.useApp();
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (total <= 0) return;
    setLoading(true);
    (async () => {
      try {
        const res = await apiGetDocumentChunks(kbId, docId);
        if (res.success && res.data) {
          const all = [...(res.data.items || [])].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
          setContent(all.map((c) => c.content || "").join("\n\n"));
        } else {
          message.error(res.message || "加载全文失败");
        }
      } catch (e) {
        message.error(e instanceof Error ? e.message : "加载全文失败");
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, total]);

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 24 }}>
        <Spin /> <Typography.Text type="secondary">正在合并全文…</Typography.Text>
      </div>
    );
  }
  if (!content) return <Empty description="暂无内容" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  return (
    <div
      className="kb-markdown"
      style={{ maxHeight: 420, overflowY: "auto", fontSize: 13, lineHeight: 1.7, whiteSpace: "pre-wrap", wordBreak: "break-word" }}
    >
      {content}
    </div>
  );
}

/** 表样例数据视图（实时连库拉取，不落库） */
function TableSampleView({
  loading,
  data,
}: {
  loading: boolean;
  data: { columns: string[]; rows: Record<string, unknown>[] } | null;
}) {
  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 24 }}>
        <Spin /> <Typography.Text type="secondary">正在读取样例数据…</Typography.Text>
      </div>
    );
  }
  if (!data || data.rows.length === 0) {
    return <Empty description="暂无可展示的样例数据（表可能为空）" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }
  const columns = data.columns.map((c) => ({
    title: c,
    dataIndex: c,
    key: c,
    ellipsis: true,
    render: (v: unknown) => {
      if (v === null || v === undefined) return <span style={{ color: "#bfbfbf" }}>NULL</span>;
      if (typeof v === "object") return JSON.stringify(v);
      return String(v);
    },
  }));
  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12, marginBottom: 6 }}>
        样例数据（前 {data.rows.length} 行，实时从数据源读取）
      </Typography.Text>
      <Table
        size="small"
        columns={columns}
        dataSource={data.rows.map((r, i) => ({ ...r, __key: i }))}
        rowKey="__key"
        scroll={{ x: "max-content" }}
        pagination={false}
      />
    </div>
  );
}
