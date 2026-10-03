"use client";

/**
 * 分块调试抽屉 —— 用真实文本跑一次 chunk-preview，展示选中档位的分块结果
 * 与 Diagnostics（选中档位 / 档位链 / 被拒原因 / profile 指标）。
 *
 * 数据来自契约端点：
 *   POST /api/v1/kbs/chunk-preview  {text, config?}
 *   GET  /api/v1/kbs/{kbId}/documents  （「从文档载入」下拉）
 *   GET  /api/v1/kbs/{kbId}/documents/{docId}/chunks （取文档解析文本）
 *
 * 后端 chunk-preview 未上线时仅提示错误，不崩溃（抽屉照常打开）。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  apiGetDocumentChunks,
  apiListDocuments,
  apiPreviewChunk,
  ChunkingConfig,
  ChunkPreviewData,
  DocItem,
  PreviewChunk,
} from "@/lib/api";

const { Text } = Typography;

const TIER_LABEL: Record<string, string> = {
  heading: "标题优先",
  heuristic: "启发式",
  recursive: "递归分隔符",
  legacy: "基础分隔符",
};

interface ChunkDebugDrawerProps {
  open: boolean;
  kbId: string;
  config?: Partial<ChunkingConfig>;
  onClose: () => void;
}

export default function ChunkDebugDrawer({ open, kbId, config, onClose }: ChunkDebugDrawerProps) {
  const { message } = App.useApp();
  const [text, setText] = useState("");
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [preview, setPreview] = useState<ChunkPreviewData | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    apiListDocuments(kbId, 1, 100)
      .then((res) => {
        if (alive && res.success) setDocs(res.data?.items || []);
      })
      .catch(() => {
        /* 文档列表不可用时静默，仅影响「从文档载入」 */
      });
    return () => {
      alive = false;
    };
  }, [open, kbId]);

  const run = useCallback(async () => {
    if (!text.trim()) {
      message.warning("请先粘贴文本或从文档载入内容");
      return;
    }
    setRunning(true);
    try {
      const res = await apiPreviewChunk(text, config);
      if (res.success && res.data) {
        setPreview(res.data);
      } else {
        setPreview(null);
        message.error(res.message || "分块预览接口不可用（后端未上线？）");
      }
    } catch {
      setPreview(null);
      message.error("分块预览请求失败");
    } finally {
      setRunning(false);
    }
  }, [text, config, message]);

  const loadFromDoc = useCallback(
    async (docId: string) => {
      try {
        const res = await apiGetDocumentChunks(kbId, docId);
        if (res.success && res.data) {
          const joined = (res.data.items || []).map((c) => c.content).join("\n");
          setText(joined);
          message.success(`已载入 ${res.data.items?.length ?? 0} 个文档分块`);
        } else {
          message.error(res.message || "载入文档内容失败");
        }
      } catch {
        message.error("载入文档内容失败");
      }
    },
    [kbId, message],
  );

  const docOptions = useMemo(
    () =>
      docs.map((d) => ({
        value: d.id,
        label: `${d.file_name}（${d.chunk_count ?? 0} 块）`,
      })),
    [docs],
  );

  const diag = preview?.diagnostics;
  const profile = diag?.profile;
  const parents = preview?.parents ?? [];
  const allChunks = preview?.chunks ?? [];

  const chunkColumns = [
    { title: "序号", dataIndex: "seq", width: 60 },
    { title: "字符", dataIndex: "chars", width: 70 },
    { title: "Token", dataIndex: "tokens", width: 70 },
    {
      title: "内容",
      dataIndex: "content",
      render: (v: string, row: PreviewChunk) => (
        <Text
          style={{ fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-all" }}
          ellipsis={{ tooltip: v }}
        >
          {v}
        </Text>
      ),
    },
    ...(allChunks.some((c) => c.is_parent || c.parent_seq !== null)
      ? [
          {
            title: "父子",
            width: 90,
            render: (_: unknown, row: PreviewChunk) => (
              <Tag color={row.is_parent ? "blue" : "default"}>
                {row.is_parent ? "父块" : `子块→${row.parent_seq}`}
              </Tag>
            ),
          },
        ]
      : []),
  ];

  return (
    <Drawer
      title="分块调试"
      placement="right"
      width={720}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      <Space direction="vertical" size="middle" style={{ display: "flex" }}>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            粘贴文本，或从文档载入解析内容，点击「开始分块」查看当前配置的切分结果。
          </Text>
          <Input.TextArea
            rows={6}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="在此粘贴待分块的文本…"
            style={{ marginTop: 8 }}
          />
          <Space style={{ marginTop: 8 }}>
            <Select
              showSearch
              allowClear
              placeholder="从文档载入"
              options={docOptions}
              onChange={(v) => v && loadFromDoc(v)}
              style={{ width: 260 }}
              optionFilterProp="label"
              notFoundContent="暂无文档"
            />
            <Button type="primary" loading={running} onClick={() => void run()}>
              开始分块
            </Button>
          </Space>
        </div>

        {running ? (
          <Spin />
        ) : preview ? (
          <>
            <Descriptions size="small" column={1} bordered title="Diagnostics">
              <Descriptions.Item label="选中档位">
                <Space size={4}>
                  <Tag color="processing">{TIER_LABEL[diag?.selected_tier ?? ""] ?? diag?.selected_tier}</Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {diag?.selected_tier}
                  </Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="档位链">
                <Space size={4} wrap>
                  {(diag?.tier_chain || []).map((t, i) => (
                    <Tag key={t + i}>{TIER_LABEL[t] ?? t}</Tag>
                  ))}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="被拒档位">
                {diag?.rejected && diag.rejected.length > 0 ? (
                  <Space direction="vertical" size={2}>
                    {diag.rejected.map((r, i) => (
                      <Text key={i} style={{ fontSize: 12 }}>
                        <Tag>{TIER_LABEL[r.tier] ?? r.tier}</Tag>
                        <Text type="secondary">{r.reason}</Text>
                      </Text>
                    ))}
                  </Space>
                ) : (
                  <Text type="secondary">无</Text>
                )}
              </Descriptions.Item>
            </Descriptions>

            {profile && (
              <Descriptions size="small" column={3} bordered title="文档画像">
                <Descriptions.Item label="字符">{profile.total_chars}</Descriptions.Item>
                <Descriptions.Item label="行数">{profile.total_lines}</Descriptions.Item>
                <Descriptions.Item label="平均行长">
                  {profile.avg_line_len?.toFixed?.(1) ?? profile.avg_line_len}
                </Descriptions.Item>
                <Descriptions.Item label="标题数">{profile.md_heading_total}</Descriptions.Item>
                <Descriptions.Item label="标题密度">
                  {(profile.heading_density * 100).toFixed(1)}%
                </Descriptions.Item>
                <Descriptions.Item label="主标题层级">{profile.dominant_heading_level}</Descriptions.Item>
                <Descriptions.Item label="表格">
                  <Tag color={profile.has_tables ? "blue" : "default"}>
                    {profile.has_tables ? "有" : "无"}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="代码块">
                  <Tag color={profile.has_code ? "blue" : "default"}>
                    {profile.has_code ? "有" : "无"}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="语言">
                  {(profile.detected_langs || []).join(", ") || "-"}
                </Descriptions.Item>
              </Descriptions>
            )}

            {parents.length > 0 && (
              <div>
                <div style={{ fontWeight: 500, marginBottom: 8 }}>
                  父块（{parents.length}）
                </div>
                <Table<PreviewChunk>
                  rowKey="seq"
                  size="small"
                  dataSource={parents}
                  pagination={false}
                  columns={[
                    { title: "序号", dataIndex: "seq", width: 60 },
                    { title: "字符", dataIndex: "chars", width: 70 },
                    { title: "Token", dataIndex: "tokens", width: 70 },
                    {
                      title: "内容",
                      dataIndex: "content",
                      render: (v: string) => (
                        <Text style={{ fontSize: 12 }} ellipsis={{ tooltip: v }}>
                          {v}
                        </Text>
                      ),
                    },
                  ]}
                  expandable={{
                    expandedRowRender: (row) => (
                      <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>{row.content}</pre>
                    ),
                  }}
                />
              </div>
            )}

            <div>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>
                分块（{preview.chunks.length}）
              </div>
              <Table<PreviewChunk>
                rowKey="seq"
                size="small"
                dataSource={preview.chunks}
                columns={chunkColumns}
                pagination={false}
                locale={{ emptyText: <Empty description="未产生分块" /> }}
                expandable={{
                  expandedRowRender: (row) => (
                    <pre
                      style={{
                        margin: 0,
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-all",
                        fontSize: 12,
                      }}
                    >
                      {row.content}
                    </pre>
                  ),
                }}
              />
            </div>
          </>
        ) : (
          <Empty description="粘贴文本或载入文档后开始分块" />
        )}
      </Space>
    </Drawer>
  );
}
