"use client";
/**
 * 文档详情抽屉（对齐 WeKnora DocContent 抽屉）：元数据 + 分块列表预览 + 操作。
 * 打开时拉取 chunks 分页；下载/重新解析/删除 操作回调给父级。
 */
import { useEffect, useState } from "react";
import { App, Button, Descriptions, Drawer, Empty, List, Pagination, Space, Spin, Tag, Typography } from "antd";
import { DeleteOutlined, DownloadOutlined, RedoOutlined } from "@ant-design/icons";
import { apiGetDocumentChunks, DocChunkItem, DocItem } from "@/lib/api";
import { PARSE_STATE_COLOR, fileTypeIcon, formatSize } from "./DocCardView";

interface Props {
  kbId: string;
  doc: DocItem | null;
  onClose: () => void;
  onDownload: (doc: DocItem) => void;
  onReparse: (doc: DocItem) => void;
  onDelete: (doc: DocItem) => void;
}

export default function DocDetailDrawer({ kbId, doc, onClose, onDownload, onReparse, onDelete }: Props) {
  const { message } = App.useApp();
  const [chunks, setChunks] = useState<DocChunkItem[]>([]);
  const [chunkTotal, setChunkTotal] = useState(0);
  const [chunkPage, setChunkPage] = useState(1);
  const [loading, setLoading] = useState(false);

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

  return (
    <Drawer
      title={
        <Space>
          <span style={{ fontSize: 18 }}>{doc ? fileTypeIcon(doc.file_ext) : null}</span>
          {doc?.file_name || "文档详情"}
        </Space>
      }
      width={680}
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
        <Space direction="vertical" size="middle" style={{ display: "flex" }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="文件名">{doc.file_name}</Descriptions.Item>
            <Descriptions.Item label="类型">{doc.file_ext?.toUpperCase() || "—"}</Descriptions.Item>
            <Descriptions.Item label="大小">{formatSize(doc.file_size)}</Descriptions.Item>
            <Descriptions.Item label="分块数">{doc.chunk_count ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={PARSE_STATE_COLOR[doc.parse_state] || "default"}>{doc.parse_state}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="上传时间">{doc.created_at || "—"}</Descriptions.Item>
            {doc.parse_state === "FAILED" && doc.parse_error ? (
              <Descriptions.Item label="解析错误" span={2}>
                <span style={{ color: "#cf1322" }}>{doc.parse_error}</span>
              </Descriptions.Item>
            ) : null}
          </Descriptions>

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
  );
}