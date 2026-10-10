"use client";
/**
 * 文档详情抽屉（对齐 WeKnora DocContent 抽屉）：元数据 + AI 摘要 + 分块列表预览 + 操作。
 * 打开时拉取 chunks 分页；下载/重新解析/删除/生成摘要 操作回调给父级。
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Descriptions, Drawer, Empty, List, Pagination, Space, Spin, Tag, Typography } from "antd";
import { DeleteOutlined, DownloadOutlined, EyeOutlined, RedoOutlined, RobotOutlined } from "@ant-design/icons";
import { apiGenerateDocSummary, apiGetDocumentChunks, DocChunkItem, DocItem } from "@/lib/api";
import { PARSE_STATE_COLOR, STATE_LABEL, fileTypeIcon, formatSize, formatTime } from "./DocCardView";
import DocPreviewModal from "./DocPreviewModal";
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

export default function DocDetailDrawer({ kbId, doc, hasSummaryModel, onClose, onDownload, onReparse, onDelete, onSummaryUpdated }: Props) {
  const { message } = App.useApp();
  const [chunks, setChunks] = useState<DocChunkItem[]>([]);
  const [chunkTotal, setChunkTotal] = useState(0);
  const [chunkPage, setChunkPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

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

  return (
    <>
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
      width={680}
      rootClassName="kb-doc-drawer"
      open={doc != null}
      onClose={onClose}
      extra={
        doc ? (
          <Space>
            <Button icon={<EyeOutlined />} onClick={() => setPreviewOpen(true)}>
              预览
            </Button>
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
        <Space direction="vertical" size="middle" style={{ display: "flex" }}>
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

          {/* 处理时间线（对齐 WeKnora knowledge-processing-timeline：解析→向量化→Wiki 构建） */}
          <div>
            <div style={{ fontWeight: 500, fontSize: 15, marginBottom: 8 }}>处理时间线</div>
            <ProcessingTimeline
              kbId={kbId}
              docId={doc.id}
              active={doc.parse_state === "PARSING" || doc.parse_state === "EMBEDDING"}
            />
          </div>

          {/* AI 摘要（对齐 WeKnora：知识库配了 LLM 后可生成/重新生成） */}
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
        </Space>
      ) : null}
    </Drawer>

    {/* 文档内容预览（对齐 WeKnora document-preview） */}
    <DocPreviewModal kbId={kbId} doc={previewOpen ? doc : null} onClose={() => setPreviewOpen(false)} />
    </>
  );
}