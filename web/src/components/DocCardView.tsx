"use client";
/**
 * 文档卡片视图（对齐 WeKnora 卡片网格）：文件图标 + 文件名 + 状态/信息 + 更多操作菜单。
 * 点击卡片/文件名 → 打开详情抽屉（onOpen）；处理中/失败状态可点 → 查看轨迹（onTrace）。
 */
import { useState } from "react";
import { Button, Card, Dropdown, Empty, Popover, Space, Spin, Tag, Typography } from "antd";
import {
  DeleteOutlined,
  DownloadOutlined,
  FileExcelOutlined,
  FileImageOutlined,
  FileMarkdownOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileTextOutlined,
  FileUnknownOutlined,
  FileWordOutlined,
  MoreOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import type { DocItem } from "@/lib/api";

export const PARSE_STATE_COLOR: Record<string, string> = {
  PENDING: "default",
  PARSING: "processing",
  EMBEDDING: "processing",
  READY: "success",
  FAILED: "error",
};

export const STATE_LABEL: Record<string, string> = {
  PENDING: "待解析",
  PARSING: "解析中",
  EMBEDDING: "向量化中",
  READY: "已就绪",
  FAILED: "失败",
};

const IN_FLIGHT = new Set(["PENDING", "PARSING", "EMBEDDING"]);

export function fileTypeIcon(ext?: string | null) {
  switch ((ext || "").toLowerCase()) {
    case "pdf":
      return <FilePdfOutlined style={{ color: "#f5222d" }} />;
    case "doc":
    case "docx":
      return <FileWordOutlined style={{ color: "#1677ff" }} />;
    case "xls":
    case "xlsx":
      return <FileExcelOutlined style={{ color: "#52c41a" }} />;
    case "ppt":
    case "pptx":
      return <FilePptOutlined style={{ color: "#fa8c16" }} />;
    case "md":
    case "markdown":
      return <FileMarkdownOutlined style={{ color: "#722ed1" }} />;
    case "png":
    case "jpg":
    case "jpeg":
    case "gif":
    case "webp":
      return <FileImageOutlined style={{ color: "#13c2c2" }} />;
    case "txt":
    case "csv":
    case "html":
    case "epub":
      return <FileTextOutlined style={{ color: "#8c8c8c" }} />;
    default:
      return <FileUnknownOutlined style={{ color: "#8c8c8c" }} />;
  }
}

export function formatSize(bytes?: number | null): string {
  if (bytes == null) return "--";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export function formatTime(iso?: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  const pad = (n: number) => String(n).padStart(2, "0");
  // 2026-10-10: 横杠格式 YYYY-MM-DD HH:mm:ss（对齐任务监控页；放弃 toLocaleString 的斜杠输出）
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

interface Props {
  items: DocItem[];
  onOpen: (doc: DocItem) => void;
  onDownload: (doc: DocItem) => void;
  onReparse: (doc: DocItem) => void;
  onDelete: (doc: DocItem) => void;
  onTrace: (doc: DocItem) => void;
}

export default function DocCardView({ items, onOpen, onDownload, onReparse, onDelete, onTrace }: Props) {
  if (items.length === 0) {
    return <Empty description="暂无文档，拖拽文件到右上角上传" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  const statusOf = (d: DocItem) => {
    if (IN_FLIGHT.has(d.parse_state)) {
      return (
        <Space size={4}>
          <Spin size="small" />
          <a onClick={(e) => { e.stopPropagation(); onTrace(d); }} style={{ color: "inherit" }}>
            {STATE_LABEL[d.parse_state] || d.parse_state}
          </a>
        </Space>
      );
    }
    if (d.parse_state === "FAILED") {
      return (
        <Space size={4}>
          <Tag color="error">失败</Tag>
          <a onClick={(e) => { e.stopPropagation(); onTrace(d); }}>查看原因</a>
        </Space>
      );
    }
    return <Tag color={PARSE_STATE_COLOR[d.parse_state] || "default"}>{STATE_LABEL[d.parse_state] || d.parse_state}</Tag>;
  };

  const menuOf = (d: DocItem) => ({
    items: [
      { key: "download", icon: <DownloadOutlined />, label: "下载原文件", onClick: () => onDownload(d) },
      { key: "reparse", icon: <RedoOutlined />, label: "重新解析", onClick: () => onReparse(d) },
      { key: "delete", icon: <DeleteOutlined />, label: "删除", danger: true, onClick: () => onDelete(d) },
    ],
  });

  const hoverContentOf = (d: DocItem) => (
    <div style={{ maxWidth: 300 }}>
      <Typography.Text strong>{d.file_name}</Typography.Text>
      <div style={{ marginTop: 4, color: "#666", fontSize: 12 }}>
        <div>类型：{d.file_ext?.toUpperCase() || "—"}</div>
        <div>大小：{formatSize(d.file_size)}</div>
        <div>分块：{d.chunk_count != null ? `${d.chunk_count} 个` : "—"}</div>
        <div>状态：{STATE_LABEL[d.parse_state] || d.parse_state}</div>
        <div>上传时间：{formatTime(d.created_at)}</div>
        {d.summary ? <div>摘要：{d.summary.slice(0, 160)}</div> : null}
        {d.parse_state === "FAILED" && d.parse_error ? (
          <div style={{ color: "#cf1322" }}>错误：{d.parse_error.slice(0, 120)}</div>
        ) : null}
      </div>
      <div style={{ marginTop: 8, color: "#999", fontSize: 12 }}>点击卡片查看完整内容</div>
    </div>
  );

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
      {items.map((d) => (
        <Popover key={d.id} content={hoverContentOf(d)} placement="right" overlayStyle={{ pointerEvents: "none" }}>
          <Card
            size="small"
            hoverable
            style={{ cursor: "pointer" }}
            title={
              <Space>
                <span style={{ fontSize: 18 }}>{fileTypeIcon(d.file_ext)}</span>
                <Typography.Text
                  ellipsis={{ tooltip: d.file_name }}
                  style={{ maxWidth: 150, fontWeight: 600 }}
                  onClick={() => onOpen(d)}
                >
                  {d.file_name}
                </Typography.Text>
              </Space>
            }
            extra={
              <Dropdown menu={menuOf(d)} trigger={["click"]} placement="bottomRight">
                <Button size="small" type="text" icon={<MoreOutlined />} onClick={(e) => e.stopPropagation()} />
              </Dropdown>
            }
            onClick={() => onOpen(d)}
          >
            {statusOf(d)}
            {d.summary ? (
              <div
                style={{
                  marginTop: 6,
                  color: "#595959",
                  fontSize: 12,
                  lineHeight: "19px",
                  maxHeight: 38,
                  overflow: "hidden",
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                }}
              >
                {d.summary}
              </div>
            ) : null}
            <div style={{ marginTop: 6, color: "#8c8c8c", fontSize: 12, lineHeight: "20px" }}>
              <div>
                {d.chunk_count != null ? `${d.chunk_count} 个分块` : "—"} · {formatSize(d.file_size)}
              </div>
              <div>{formatTime(d.created_at)}</div>
            </div>
          </Card>
        </Popover>
      ))}
    </div>
  );
}