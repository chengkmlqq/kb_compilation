"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button, Card, Progress, Space, Tag, Tooltip, Typography } from "antd";
import {
  ClearOutlined,
  CloudUploadOutlined,
  CloseOutlined,
  DownOutlined,
  ReloadOutlined,
  UpOutlined,
} from "@ant-design/icons";
import { formatBytes } from "@/lib/drop-files";
import { onUploadTask, type UploadTaskDetail, type UploadTaskStatus } from "@/lib/upload-bus";

const MAX_TASKS = 50;

const STATUS_META: Record<UploadTaskStatus, { color: string; label: string }> = {
  pending: { color: "default", label: "等待中" },
  uploading: { color: "processing", label: "上传中" },
  success: { color: "success", label: "成功" },
  error: { color: "error", label: "失败" },
};

/**
 * 上传任务浮层（固定右下角）：任何页面经 CustomEvent('uploadTask') 上报的上传任务
 * 都会汇总展示——文件名、大小、进度条、状态、重试按钮、全部清除。
 */
export default function UploadTaskPanel() {
  const [tasks, setTasks] = useState<UploadTaskDetail[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const idIndex = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    return onUploadTask((detail) => {
      setTasks((prev) => {
        let next: UploadTaskDetail[];
        const idx = idIndex.current.get(detail.id);
        if (idx !== undefined && idx >= 0 && idx < prev.length) {
          next = prev.map((t, i) => (i === idx ? { ...t, ...detail } : t));
        } else {
          next = [...prev, detail];
          idIndex.current.set(detail.id, next.length - 1);
        }
        // 上限裁剪：超出时丢弃最早的非进行中任务
        if (next.length > MAX_TASKS) {
          const dropIdx = next.findIndex((t) => t.status !== "uploading" && t.status !== "pending");
          if (dropIdx >= 0) {
            idIndex.current.delete(next[dropIdx].id);
            next = next.filter((_, i) => i !== dropIdx);
            idIndex.current.clear();
            next.forEach((t, i) => idIndex.current.set(t.id, i));
          }
        }
        return next;
      });
    });
  }, []);

  const removeTask = useCallback((id: string) => {
    setTasks((prev) => {
      const next = prev.filter((t) => t.id !== id);
      idIndex.current.clear();
      next.forEach((t, i) => idIndex.current.set(t.id, i));
      return next;
    });
  }, []);

  const clearAll = useCallback(() => {
    idIndex.current.clear();
    setTasks([]);
  }, []);

  if (tasks.length === 0) return null;

  const activeCount = tasks.filter((t) => t.status === "uploading" || t.status === "pending").length;

  return (
    <div style={{ position: "fixed", right: 16, bottom: 16, width: 400, maxWidth: "calc(100vw - 32px)", zIndex: 1000 }}>
      <Card
        size="small"
        title={
          <Space size={8}>
            <CloudUploadOutlined />
            上传任务
            {activeCount > 0 && <Tag color="processing">{activeCount} 进行中</Tag>}
          </Space>
        }
        extra={
          <Space size={4}>
            <Button
              size="small"
              type="text"
              icon={<ClearOutlined />}
              onClick={clearAll}
              title="全部清除"
            >
              全部清除
            </Button>
            <Button
              size="small"
              type="text"
              icon={collapsed ? <UpOutlined /> : <DownOutlined />}
              onClick={() => setCollapsed((c) => !c)}
              title={collapsed ? "展开" : "收起"}
            />
          </Space>
        }
        styles={{ body: { padding: collapsed ? 0 : "4px 12px 8px", maxHeight: 320, overflow: "auto" } }}
      >
        {!collapsed &&
          tasks.map((t) => {
            const meta = STATUS_META[t.status];
            return (
              <div
                key={t.id}
                style={{ padding: "6px 0 4px", borderBottom: "1px solid rgba(0, 0, 0, 0.06)" }}
              >
                <Space size={8} style={{ width: "100%" }} align="center">
                  <Tooltip title={t.name}>
                    <Typography.Text ellipsis style={{ flex: 1, minWidth: 0 }}>
                      {t.name}
                    </Typography.Text>
                  </Tooltip>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {formatBytes(t.size)}
                  </Typography.Text>
                  <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>
                    {meta.label}
                  </Tag>
                  {t.status === "error" && t.retry && (
                    <Button
                      size="small"
                      type="text"
                      icon={<ReloadOutlined />}
                      title="重试"
                      onClick={() => t.retry?.()}
                    />
                  )}
                  <Button
                    size="small"
                    type="text"
                    icon={<CloseOutlined />}
                    title="移除"
                    onClick={() => removeTask(t.id)}
                  />
                </Space>
                <Progress
                  percent={t.progress}
                  size="small"
                  status={
                    t.status === "uploading" ? "active" : t.status === "error" ? "exception" : "success"
                  }
                  style={{ margin: "2px 0 0" }}
                />
                {t.status === "error" && t.error && (
                  <Tooltip title={t.error}>
                    <Typography.Text
                      type="danger"
                      style={{ fontSize: 12, display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    >
                      {t.error}
                    </Typography.Text>
                  </Tooltip>
                )}
              </div>
            );
          })}
      </Card>
    </div>
  );
}
