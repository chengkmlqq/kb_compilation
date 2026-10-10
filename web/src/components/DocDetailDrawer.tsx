"use client";
/**
 * 文档详情抽屉（像素级对齐 WeKnora DocContent 抽屉，2026-10-10 交互对齐批次）：
 *
 * 交互差异修复（此前 kb 是「抽屉 + 独立预览弹窗」两套容器，WeKnora 是抽屉内三视图）：
 *  - 抽屉 header 右侧 视图切换按钮组：预览（可预览类型时）/ 合并（不可预览时）/ 分块（恒有）
 *    —— 对齐 WeKnora view-mode-buttons（active = base variant + primary theme）
 *  - 预览 = 抽屉内嵌视图（DocPreviewModal embedded 模式），不再开独立 Modal
 *  - 合并视图 = 前端按 seq 拼接全部 chunks 还原全文（对齐 WeKnora mergedContent）
 *  - 默认视图：file 且可预览 → preview；音频 → merged（播放器内嵌）；其他 → merged
 *  - 音频播放器固定显示在内容区顶部（任何视图都可见，对齐 WeKnora audio-player-section）
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Descriptions, Drawer, Empty, List, Pagination, Space, Spin, Tag, Typography } from "antd";
import { DeleteOutlined, DownloadOutlined, RedoOutlined, RobotOutlined } from "@ant-design/icons";
import { apiGenerateDocSummary, apiGetDocumentChunks, DocChunkItem, DocItem } from "@/lib/api";
import { PARSE_STATE_COLOR, STATE_LABEL, fileTypeIcon, formatSize, formatTime } from "./DocCardView";
import DocPreviewModal, { canPreviewExt } from "./DocPreviewModal";
import ProcessingTimeline from "./ProcessingTimeline";

interface Props {
  kbId: string;
  doc: DocItem | null;
  hasSummaryModel: boolean;
  onClose: () => void;
  onDownload: (doc: DocItem) => void;
  onReparse: (doc: DocItem) => void;
  onDelete: (doc: DocItem) => void;
  onSummaryUpdated?: (docId: string, summary: string, status: string) => void;
}

type ViewMode = "preview" | "merged" | "chunks";

const AUDIO_EXTS = new Set(["mp3", "wav", "m4a", "flac", "ogg", "aac", "opus"]);

export default function DocDetailDrawer({ kbId, doc, hasSummaryModel, onClose, onDownload, onReparse, onDelete, onSummaryUpdated }: Props) {
  const { message } = App.useApp();
  const [chunks, setChunks] = useState<DocChunkItem[]>([]);
  const [chunkTotal, setChunkTotal] = useState(0);
  const [chunkPage, setChunkPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("merged");

  const isAudio = doc ? AUDIO_EXTS.has((doc.file_ext || "").toLowerCase()) : false;
  const canPreview = doc ? canPreviewExt(doc.file_ext) : false;

  // 默认视图（对齐 WeKnora watch(details.id)：可预览 file → preview；音频 → merged；其他 → merged）
  useEffect(() => {
    if (!doc) return;
    if (isAudio) setViewMode("merged");
    else if (canPreview) setViewMode("preview");
    else setViewMode("merged");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc?.id]);

  useEffect(() => {
    if (!doc) return;
    setChunks([]);
    setChunkTotal(0);
    setChunkPage(1);
    void loadChunks(doc.id, 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc?.id, doc?.parse_state]);

  const loadChunks = async (docId: string, page: number) => {
    setLoading(true);
    try {
      const res = await apiGetDocumentChunks(kbId, docId);
      if (res.success && res.data) {
        const all = res.data.items || [];
        const paged = all.slice((page - 1) * 10, page * 10);
        setChunks(paged);
        setChunkTotal(res.data.total ?? all.length);
      } else {
        message.error(res.message || "加载分块失败");
      }
    } finally {
      setLoading(false);
    }
  };

  const generateSummary = useCallback(async () => {
    if (!doc) return;
    setSummarizing(true);
    try {
      const res = await apiGenerateDocSummary(kbId, doc.id);
      if (res.success && res.data) {
        message.success("摘要生成完成");
        onSummaryUpdated?.(doc.id, res.data.summary, res.data.summary_status);
      } else {
        message.error(res.message || "摘要生成失败");
      }
    } catch {
      message.error("摘要生成失败");
    } finally {
      setSummarizing(false);
    }
  }, [doc, kbId, message, onSummaryUpdated]);

  const summaryReady = doc?.summary_status === "READY" && doc.summary;
  const summaryFailed = doc?.summary_status === "FAILED";

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
    <Drawer
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={{ fontSize: 18, flexShrink: 0, lineHeight: 1 }}>
            {doc ? fileTypeIcon(doc.file_ext) : null}
          </span>
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              minWidth: 0,
            }}
          >
            {doc?.file_name || "文档详情"}
          </span>
        </div>
      }
      width={760}
      rootClassName="kb-doc-drawer"
      open={doc != null}
      onClose={onClose}
      extra={
        doc ? (
          <Space>
            <Button icon={<DownloadOutlined />} onClick={() => onDownload(doc)}>
              下载
            </Button>
            <Button icon={<RedoOutlined />} onClick={() => onReparse(doc)}>
              重新解析
            </Button>
            <Button danger icon={<DeleteOutlined />} onClick={() => onDelete(doc)}>
              删除
            </Button>
          </Space>
        ) : null
      }
    >
      {doc ? (
        <Space direction="vertical" size="middle" style={{ display: "flex", height: "100%" }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="文件名">{doc.file_name}</Descriptions.Item>
            <Descriptions.Item label="类型">{doc.file_ext?.toUpperCase() || "—"}</Descriptions.Item>
            <Descriptions.Item label="大小">{formatSize(doc.file_size)}</Descriptions.Item>
            <Descriptions.Item label="分块数">{doc.chunk_count ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={PARSE_STATE_COLOR[doc.parse_state] || "default"}>{STATE_LABEL[doc.parse_state] || doc.parse_state}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="上传时间">{formatTime(doc.created_at)}</Descriptions.Item>
            {doc.parse_state === "FAILED" && doc.parse_error ? (
              <Descriptions.Item label="解析错误" span={2}>
                <span style={{ color: "#cf1322" }}>{doc.parse_error}</span>
              </Descriptions.Item>
            ) : null}
          </Descriptions>

          {/* 视图切换按钮组（对齐 WeKnora view-mode-buttons） */}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {canPreview ? viewBtn("preview", "预览") : null}
            {!canPreview ? viewBtn("merged", "合并视图") : null}
            {viewBtn("chunks", "分块")}
            {chunkTotal > 0 ? (
              <span style={{ marginLeft: "auto", fontSize: 12, color: "rgba(0,0,0,.45)" }}>
                共 {chunkTotal} 个分块
              </span>
            ) : null}
          </div>

          {/* 音频播放器（固定显示在内容区顶部，对齐 WeKnora audio-player-section） */}
          {isAudio && (
            <div style={{ padding: "8px 0" }}>
              <DocPreviewModal kbId={kbId} doc={doc} onClose={() => undefined} embedded />
            </div>
          )}

          {/* 预览视图（内嵌，对齐 WeKnora viewMode === 'preview'） */}
          {viewMode === "preview" && !isAudio && (
            <div style={{ flex: 1, minHeight: 300, display: "flex", flexDirection: "column" }}>
              <DocPreviewModal kbId={kbId} doc={doc} onClose={() => undefined} embedded />
            </div>
          )}

          {/* 合并视图（按 seq 拼接全部分块还原全文） */}
          {viewMode === "merged" && !isAudio && (
            <MergedView kbId={kbId} docId={doc.id} total={chunkTotal} />
          )}

          {/* 分块视图 */}
          {viewMode === "chunks" && (
            <div>
              <Space style={{ justifyContent: "space-between", width: "100%" }}>
                <Typography>{chunkTotal > 0 ? `分块预览（${chunkTotal}）` : "分块预览"}</Typography>
              </Space>
              {loading ? (
                <div style={{ textAlign: "center", padding: 24 }}>
                  <Spin />
                </div>
              ) : chunks.length === 0 ? (
                <Empty description={doc.parse_state === "READY" ? "暂无分块" : "解析完成后展示分块"} image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <>
                  <List
                    size="small"
                    dataSource={chunks}
                    renderItem={(c) => (
                      <List.Item>
                        <div style={{ width: "100%" }}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            #{c.seq}
                          </Typography.Text>
                          <div
                            className="kb-markdown"
                            style={{
                              maxHeight: 110,
                              overflowY: "auto",
                              marginTop: 4,
                              fontSize: 13,
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-word",
                            }}
                          >
                            {c.content}
                          </div>
                        </div>
                      </List.Item>
                    )}
                  />
                  <Pagination
                    simple
                    current={chunkPage}
                    pageSize={10}
                    total={chunkTotal}
                    onChange={(p) => {
                      setChunkPage(p);
                      void loadChunks(doc.id, p);
                    }}
                    style={{ marginTop: 8, textAlign: "center" }}
                  />
                </>
              )}
            </div>
          )}

          {/* AI 摘要（保持原有，位于内容底部） */}
          {doc.parse_state === "READY" ? (
            <div
              style={{
                border: "1px solid #f0f0f0",
                borderRadius: 8,
                padding: "12px 16px",
                background: "#fafafa",
              }}
            >
              <Space style={{ justifyContent: "space-between", width: "100%", marginBottom: summaryReady || summaryFailed ? 8 : 0 }}>
                <Space>
                  <RobotOutlined style={{ color: "#1677ff" }} />
                  <Typography.Text strong>AI 摘要</Typography.Text>
                </Space>
                <Button
                  size="small"
                  loading={summarizing}
                  disabled={!hasSummaryModel}
                  title={hasSummaryModel ? "" : "知识库未配置大语言模型（LLM），请在知识库配置中选择"}
                  onClick={() => void generateSummary()}
                >
                  {summaryFailed ? "重试" : summaryReady ? "重新生成" : "生成摘要"}
                </Button>
              </Space>
              {summarizing ? (
                <div style={{ textAlign: "center", padding: 16 }}>
                  <Spin size="small" /> <Typography.Text type="secondary">正在生成摘要…</Typography.Text>
                </div>
              ) : summaryReady ? (
                <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {doc.summary}
                </Typography.Paragraph>
              ) : summaryFailed ? (
                <Typography.Text type="danger" style={{ wordBreak: "break-word" }}>
                  {doc.summary_error || "摘要生成失败，请重试"}
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  {hasSummaryModel ? "解析完成后点击「生成摘要」，用知识库配置的 LLM 自动生成文档摘要。" : "知识库未配置大语言模型（LLM），创建/编辑知识库时选择后可生成摘要。"}
                </Typography.Text>
              )}
            </div>
          ) : null}
        </Space>
      ) : null}
    </Drawer>
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