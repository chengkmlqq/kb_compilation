"use client";

/**
 * 切片核对页 —— 知识库整体健康检查 + 修复（独立页面，替代 KB 详情里的入口）。
 *
 * 功能：
 * - 「全部核对」：一次调用 POST /api/v1/kbs/chunks/verify-all，批量核对可见知识库
 *   的 MySQL 切片 ↔ 向量库一致性，返回逐库报告（文档切片/向量切片/缺失/孤儿/健康）。
 * - 逐库「核对」：单库同步核对（POST /kbs/{id}/chunks/verify）。
 * - 逐库「修复」：投递 KbChunkVerifyTask worker 异步任务（清孤儿向量 + 重嵌入缺失切片），
 *   修复结果在任务监控页查看。
 * - 全部修复：对当前不健康的库逐个投递修复任务。
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Col, Row, Space, Statistic, Table, Tag } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  apiFixChunks,
  apiVerifyAllChunks,
  apiVerifyChunks,
  ChunkVerifyReport,
} from "@/lib/api";

export default function ChunkVerifyPage() {
  const { message } = App.useApp();

  const [items, setItems] = useState<ChunkVerifyReport[]>([]);
  const [loading, setLoading] = useState(false); // 全部核对/加载中
  const [fixingIds, setFixingIds] = useState<Set<string>>(new Set()); // 修复中(防连点)
  const [lastCheckedAt, setLastCheckedAt] = useState<string>("");

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiVerifyAllChunks();
      if (res.success && res.data) {
        setItems(res.data.items || []);
        const now = new Date();
        setLastCheckedAt(
          `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
            now.getDate()
          ).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}:${String(
            now.getMinutes()
          ).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`
        );
        const { total, ok, unhealthy } = res.data;
        message.success(`核对完成：共 ${total} 个知识库，健康 ${ok} 个，异常 ${unhealthy} 个`);
      } else {
        message.error(res.message || "核对失败");
      }
    } catch (e) {
      message.error(`核对失败: ${String(e).slice(0, 80)}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // 单库核对（点击行内按钮，只刷新该行）
  const handleVerifyOne = async (kbId: string) => {
    try {
      const res = await apiVerifyChunks(kbId);
      if (res.success && res.data) {
        setItems((prev) => prev.map((it) => (it.kb_id === kbId ? { ...it, ...res.data } : it)));
        message.success(res.data.ok ? "该知识库切片一致" : "该知识库存在不一致");
      } else {
        message.error(res.message || "核对失败");
      }
    } catch (e) {
      message.error(`核对失败: ${String(e).slice(0, 80)}`);
    }
  };

  // 单库修复（异步 worker 任务）
  const handleFixOne = async (kbId: string) => {
    setFixingIds((prev) => new Set(prev).add(kbId));
    try {
      const res = await apiFixChunks(kbId);
      if (res.success) {
        message.success("修复任务已投递，请在「任务监控」查看进度");
      } else {
        message.error(res.message || "投递失败");
      }
    } catch (e) {
      message.error(`投递失败: ${String(e).slice(0, 80)}`);
    } finally {
      setFixingIds((prev) => {
        const next = new Set(prev);
        next.delete(kbId);
        return next;
      });
    }
  };

  // 修复所有异常库
  const handleFixAll = async () => {
    const unhealthy = items.filter((it) => !it.ok);
    if (unhealthy.length === 0) {
      message.info("没有需要修复的知识库");
      return;
    }
    let okCount = 0;
    for (const it of unhealthy) {
      try {
        const res = await apiFixChunks(it.kb_id);
        if (res.success) okCount += 1;
      } catch {
        // 单库投递失败不阻断
      }
    }
    message.success(`已投递 ${okCount}/${unhealthy.length} 个知识库的修复任务`);
  };

  const healthyCount = items.filter((it) => it.ok).length;
  const unhealthyCount = items.length - healthyCount;
  const missingCount = items.reduce((acc, it) => acc + (it.missing_in_vector || 0), 0);
  const orphanCount = items.reduce((acc, it) => acc + (it.orphan_vectors || 0), 0);

  const columns: ColumnsType<ChunkVerifyReport> = [
    {
      title: "知识库",
      dataIndex: "kb_name",
      key: "kb_name",
      width: 260,
      ellipsis: true,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <span style={{ fontWeight: 500 }}>{row.kb_name || "(未命名)"}</span>
          <span style={{ color: "#8c8c8c", fontSize: 12 }}>{row.kb_id}</span>
        </Space>
      ),
    },
    {
      title: "文档切片",
      dataIndex: "doc_chunks",
      key: "doc_chunks",
      width: 100,
      align: "center",
    },
    {
      title: "向量切片",
      dataIndex: "vector_chunks",
      key: "vector_chunks",
      width: 100,
      align: "center",
    },
    {
      title: "缺失（未向量化）",
      dataIndex: "missing_in_vector",
      key: "missing_in_vector",
      width: 130,
      align: "center",
      render: (v: number) =>
        v > 0 ? <span style={{ color: "#faad14" }}>{v}</span> : <span style={{ color: "#52c41a" }}>{v}</span>,
    },
    {
      title: "孤儿向量",
      dataIndex: "orphan_vectors",
      key: "orphan_vectors",
      width: 110,
      align: "center",
      render: (v: number) =>
        v > 0 ? <span style={{ color: "#faad14" }}>{v}</span> : <span style={{ color: "#52c41a" }}>{v}</span>,
    },
    {
      title: "状态",
      dataIndex: "ok",
      key: "ok",
      width: 110,
      align: "center",
      render: (ok: boolean, row) => {
        if (row.error) return <Tag color="red">异常</Tag>;
        return ok ? <Tag color="green">健康</Tag> : <Tag color="orange">不一致</Tag>;
      },
    },
    {
      title: "操作",
      key: "action",
      width: 150,
      align: "center",
      render: (_, row) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => void handleVerifyOne(row.kb_id)}>
            核对
          </Button>
          <Button
            type="link"
            size="small"
            danger={!row.ok}
            loading={fixingIds.has(row.kb_id)}
            disabled={fixingIds.has(row.kb_id)}
            onClick={() => void handleFixOne(row.kb_id)}
          >
            修复
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: 16, background: "#F5F7FA" }}>
      {/* 顶部统计 + 操作 */}
      <Row gutter={12} style={{ flexShrink: 0, marginBottom: 12 }}>
        <Col span={5}>
          <Card size="small">
            <Statistic title="知识库总数" value={items.length} />
          </Card>
        </Col>
        <Col span={5}>
          <Card size="small">
            <Statistic title="健康" value={healthyCount} valueStyle={{ color: "#52c41a" }} />
          </Card>
        </Col>
        <Col span={5}>
          <Card size="small">
            <Statistic title="不一致" value={unhealthyCount} valueStyle={{ color: "#faad14" }} />
          </Card>
        </Col>
        <Col span={5}>
          <Card size="small">
            <Statistic title="缺失切片" value={missingCount} valueStyle={{ color: missingCount ? "#faad14" : "#52c41a" }} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="孤儿向量" value={orphanCount} valueStyle={{ color: orphanCount ? "#faad14" : "#52c41a" }} />
          </Card>
        </Col>
      </Row>

      {/* 工具栏 */}
      <div
        style={{
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "#fff",
          borderRadius: 8,
          border: "1px solid #E3E9EF",
          padding: "10px 16px",
          marginBottom: 12,
        }}
      >
        <span style={{ color: "#8c8c8c", fontSize: 13 }}>
          {lastCheckedAt ? `上次核对：${lastCheckedAt}` : "尚未核对"}
        </span>
        <Space>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void loadAll()}>
            全部核对
          </Button>
          <Button type="primary" danger loading={loading} onClick={() => void handleFixAll()}>
            修复所有异常
          </Button>
        </Space>
      </div>

      {/* 表格 */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          background: "#fff",
          borderRadius: 8,
          border: "1px solid #E3E9EF",
        }}
      >
        <Table<ChunkVerifyReport>
          rowKey="kb_id"
          loading={loading}
          columns={columns}
          dataSource={items}
          size="middle"
          pagination={items.length > 10 ? { pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 个知识库` } : false}
          scroll={{ y: "calc(100vh - 340px)" }}
        />
      </div>
    </div>
  );
}
