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
import { useCallback, useEffect, useRef, useState } from "react";
import { App, Button, Space, Table, Tag } from "antd";
import {
  CheckCircleOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import ModoPagination from "@/components/biz/modo-pagination";
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
  const [verifyingIds, setVerifyingIds] = useState<Set<string>>(new Set()); // 单库核对中(防连点)
  const [fixAllLoading, setFixAllLoading] = useState(false); // 修复所有异常中(防连点)
  const [lastCheckedAt, setLastCheckedAt] = useState<string>("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // 对齐任务监控页：表格可用高度实测（统计卡/工具栏高度由内容决定，写死 vh 偏移易失准）
  const [tableBodyHeight, setTableBodyHeight] = useState<number>();
  const tableBodyRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = tableBodyRef.current;
    if (!el) return;
    const update = () => {
      // 2026-10-28: scroll.y 的 maxHeight 只约束表体，表头 (.ant-table-thead) 是流内
      // 独立块。直接量容器高度会令表格总高 = 容器高 + 表头高，底部行被分页遮挡——先减表头高。
      const thead = el.querySelector<HTMLElement>(".ant-table-thead");
      const headerH = thead ? thead.getBoundingClientRect().height : 0;
      setTableBodyHeight(Math.max(40, Math.round(el.clientHeight - headerH)));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    const tableEl = el.querySelector<HTMLElement>(".ant-table");
    if (tableEl) ro.observe(tableEl);
    return () => ro.disconnect();
  }, []);

  const loadAll = useCallback(async (silent = false) => {
    setLoading(true);
    try {
      const res = await apiVerifyAllChunks();
      if (res.success && res.data) {
        setItems(res.data.items || []);
        setPage(1);
        const now = new Date();
        setLastCheckedAt(
          `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
            now.getDate()
          ).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}:${String(
            now.getMinutes()
          ).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`
        );
        const { total, ok, unhealthy } = res.data;
        // silent：进入页面自动核对时不弹提示（仅手动点「全部核对」才提示）
        if (!silent) message.success(`核对完成：共 ${total} 个知识库，健康 ${ok} 个，异常 ${unhealthy} 个`);
      } else if (!silent) {
        message.error(res.message || "核对失败");
      }
    } catch (e) {
      if (!silent) message.error(`核对失败: ${String(e).slice(0, 80)}`);
    } finally {
      setLoading(false);
    }
  }, [message]);

  // 进入页面自动触发一次全库核对（不弹提示）；核对会真实扫描 MySQL 切片 ↔ 向量库，
  // 用 ref 守卫确保每次进入只触发一次（含 StrictMode 双调用）。
  const autoCheckedRef = useRef(false);
  useEffect(() => {
    if (autoCheckedRef.current) return;
    autoCheckedRef.current = true;
    void loadAll(true);
  }, [loadAll]);

  // 单库核对（点击行内按钮，只刷新该行）
  const handleVerifyOne = async (kbId: string) => {
    setVerifyingIds((prev) => new Set(prev).add(kbId));
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
    } finally {
      setVerifyingIds((prev) => {
        const next = new Set(prev);
        next.delete(kbId);
        return next;
      });
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
    if (fixAllLoading) return; // 防连点
    setFixAllLoading(true);
    try {
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
    } finally {
      setFixAllLoading(false);
    }
  };

  const healthyCount = items.filter((it) => it.ok).length;
  const unhealthyCount = items.length - healthyCount;
  const missingCount = items.reduce((acc, it) => acc + (it.missing_in_vector || 0), 0);
  const orphanCount = items.reduce((acc, it) => acc + (it.orphan_vectors || 0), 0);

  const statCards = [
    { key: "total", label: "知识库总数", value: items.length, icon: <DatabaseOutlined />, color: "#3261CE", bg: "#EAF0FF", valueColor: "#242E43" },
    { key: "healthy", label: "健康", value: healthyCount, icon: <CheckCircleOutlined />, color: "#2FA84F", bg: "#E8F7EC", valueColor: "#2FA84F" },
    { key: "unhealthy", label: "不一致", value: unhealthyCount, icon: <WarningOutlined />, color: "#FA8C16", bg: "#FFF3E6", valueColor: unhealthyCount ? "#FA8C16" : "#2FA84F" },
    { key: "missing", label: "缺失切片", value: missingCount, icon: <FileSearchOutlined />, color: "#FA8C16", bg: "#FFF3E6", valueColor: missingCount ? "#FA8C16" : "#2FA84F" },
    { key: "orphan", label: "孤儿向量", value: orphanCount, icon: <ExclamationCircleOutlined />, color: "#FA8C16", bg: "#FFF3E6", valueColor: orphanCount ? "#FA8C16" : "#2FA84F" },
  ];

  const pagedItems = items.slice((page - 1) * pageSize, page * pageSize);

  const columns: ColumnsType<ChunkVerifyReport> = [
    {
      title: "知识库",
      dataIndex: "kb_name",
      key: "kb_name",
      width: 260,
      ellipsis: true,
      render: (_, row) => (
        <span style={{ fontWeight: 500 }}>{row.kb_name || "(未命名)"}</span>
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
          <Button
            type="link"
            size="small"
            loading={verifyingIds.has(row.kb_id)}
            disabled={verifyingIds.has(row.kb_id) || fixingIds.has(row.kb_id)}
            onClick={() => void handleVerifyOne(row.kb_id)}
          >
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
    // 对齐任务监控页：外层锁定 视口 − 顶栏(45px)，flex 链路有界，
    // 统计区/工具栏定高，表格区吃满剩余空间，行数再多也不溢出屏幕。
    <div
      className="chunk-verify-page"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - var(--app-header-h, 48px))",
        minHeight: 0,
        background: "#EFF4F9",
        padding: 10,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          background: "#F5F7FA",
        }}
      >
        {/* 统计区 */}
        <div style={{ flexShrink: 0, marginBottom: 16 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16 }}>
            {statCards.map((s) => (
              <div
                key={s.key}
                className="modo-stat-card"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: 18,
                  background: "#fff",
                  borderRadius: 8,
                  border: "1px solid #E3E9EF",
                  transition: "box-shadow 0.25s",
                }}
              >
                <div>
                  <div style={{ color: "#79879C", fontSize: 13, marginBottom: 8, fontWeight: 500 }}>{s.label}</div>
                  <div
                    style={{
                      fontSize: 26,
                      fontWeight: 700,
                      lineHeight: "26px",
                      color: s.valueColor,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {s.value}
                  </div>
                </div>
                <div
                  style={{
                    width: 48,
                    height: 48,
                    borderRadius: 12,
                    background: s.bg,
                    color: s.color,
                    opacity: 0.75,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  <span style={{ fontSize: 22 }}>{s.icon}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 工具栏 */}
        <div
          style={{
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 16,
            flexWrap: "wrap",
            background: "#fff",
            borderRadius: 8,
            border: "1px solid #E3E9EF",
            padding: "14px 16px",
            marginBottom: 16,
          }}
        >
          <span style={{ color: "#79879C", fontSize: 13 }}>
            {lastCheckedAt ? `上次核对：${lastCheckedAt}` : "尚未核对"}
          </span>
          <Space>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void loadAll()}>
              全部核对
            </Button>
            <Button type="primary" danger loading={fixAllLoading} disabled={fixAllLoading} onClick={() => void handleFixAll()}>
              修复所有异常
            </Button>
          </Space>
        </div>

        {/* 表格 + 分页 */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            overflow: "hidden",
            background: "#fff",
            borderRadius: 8,
            border: "1px solid #E3E9EF",
          }}
        >
          <div ref={tableBodyRef} style={{ flex: 1, minHeight: 0, overflow: "hidden", padding: "0 16px" }}>
            <Table<ChunkVerifyReport>
              rowKey="kb_id"
              loading={loading}
              columns={columns}
              dataSource={pagedItems}
              size="middle"
              pagination={false}
              scroll={{ x: 960, y: tableBodyHeight }}
            />
          </div>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={items.length}
            showTotal={(t) => `共 ${t} 条`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
            }}
          />
        </div>
      </div>
    </div>
  );
}
