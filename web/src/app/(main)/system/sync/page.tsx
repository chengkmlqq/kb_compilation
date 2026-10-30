"use client";

/**
 * 同步管理 —— 从 data_supply_shandong 同步团队/用户/角色到 kb_compilation。
 *
 * 流程：
 * 1. 数据源管理页配置 shandong 业务库（mysql，指向 data_supply_neo）——已具备；
 * 2. 本页选择该数据源 → 测试连接 → 开始同步；
 * 3. 同步读源库 modo_team/modo_user/modo_team_member/modo_user_role/
 *    modo_user_role_rela（admin 除外）→ upsert 本库，角色保底 normal_user；
 * 4. 页面对每表展示 源行数/新增/更新/跳过/失败 + 最近一次同步结果。
 *
 * 同步完成后 shandong 用户在 kb 换签/鉴权自动通过（用户+团队成员+角色三件套）。
 */
import { useCallback, useEffect, useState } from "react";
import { Alert, App, Button, Card, Empty, Select, Space, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { SyncOutlined } from "@ant-design/icons";
import {
  apiFrameworkSyncHistory,
  apiFrameworkSyncStatus,
  apiListDatasources,
  apiRunFrameworkSync,
  apiTestDatasource,
  DatasourceItem,
  FrameworkSyncHistoryItem,
  FrameworkSyncResult,
  FrameworkSyncTableResult,
} from "@/lib/api";

const TABLE_LABELS: Record<string, string> = {
  modo_team: "团队表",
  modo_user: "用户表",
  modo_team_member: "团队成员表",
  modo_user_role: "角色定义表",
  modo_user_role_rela: "用户角色关系表",
};

const SYNC_TABLE_ORDER = [
  "modo_team",
  "modo_user",
  "modo_team_member",
  "modo_user_role",
  "modo_user_role_rela",
];

function tableRows(tables?: Record<string, FrameworkSyncTableResult>) {
  if (!tables) return [];
  return SYNC_TABLE_ORDER.filter((t) => tables[t]).map((t) => ({
    key: t,
    table: TABLE_LABELS[t] ?? t,
    ...tables[t],
  }));
}

export default function SystemSyncPage() {
  const { message } = App.useApp();

  // 数据源（mysql 类型）
  const [dsList, setDsList] = useState<DatasourceItem[]>([]);
  const [dsId, setDsId] = useState<string>();
  const [dsLoading, setDsLoading] = useState(false);
  const [testing, setTesting] = useState(false);

  // 同步
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState<FrameworkSyncResult | null>(null);

  // 历史
  const [history, setHistory] = useState<FrameworkSyncHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const loadDatasources = useCallback(async () => {
    setDsLoading(true);
    try {
      const res = await apiListDatasources(1, 200, { dsType: "mysql" });
      const items = res.data?.items ?? [];
      setDsList(items);
      if (items.length > 0 && !dsId) setDsId(items[0].id);
    } catch {
      setDsList([]);
    } finally {
      setDsLoading(false);
    }
  }, [dsId]);

  const loadStatus = useCallback(async () => {
    try {
      const res = await apiFrameworkSyncStatus();
      if (res.success && res.data) setResult(res.data);
    } catch {
      /* 首次无记录时忽略 */
    }
  }, []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await apiFrameworkSyncHistory(1, 10);
      setHistory(res.data?.items ?? []);
    } catch {
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDatasources();
    loadStatus();
    loadHistory();
  }, [loadDatasources, loadStatus, loadHistory]);

  const handleTest = async () => {
    if (!dsId) {
      message.warning("请先选择数据源");
      return;
    }
    setTesting(true);
    try {
      const res = await apiTestDatasource({ dsId });
      if (res.data?.success) {
        message.success(res.data?.message || "连接成功");
      } else {
        message.error(res.data?.message || "连接失败");
      }
    } catch (e) {
      message.error("连接失败");
    } finally {
      setTesting(false);
    }
  };

  const handleSync = async () => {
    if (!dsId) {
      message.warning("请先选择数据源");
      return;
    }
    setSyncing(true);
    try {
      const res = await apiRunFrameworkSync(dsId);
      if (res.success) {
        setResult(res.data ?? null);
        message.success("同步完成");
        loadHistory();
      } else {
        message.error(res.data?.error || "同步失败");
        setResult(res.data ?? null);
      }
    } catch (e) {
      message.error("同步失败");
    } finally {
      setSyncing(false);
    }
  };

  const tables = result?.tables ?? result?.responseData?.tables;
  const rows = tableRows(tables);
  const fallback = ((
    result?.fallback ??
    (result?.responseData?.tables as Record<string, unknown> | undefined)?.["_fallback"]
  ) ?? undefined) as { users?: number; normal_user_added?: number } | undefined;

  const tableColumns: ColumnsType<(typeof rows)[number]> = [
    { title: "表", dataIndex: "table", width: 160 },
    { title: "源行数", dataIndex: "source", width: 90, align: "right" },
    { title: "新增", dataIndex: "inserted", width: 80, align: "right" },
    { title: "更新", dataIndex: "updated", width: 80, align: "right" },
    { title: "跳过", dataIndex: "skipped", width: 80, align: "right" },
    {
      title: "状态",
      dataIndex: "failed",
      width: 100,
      render: (v: number, r) =>
        v > 0 ? <Tag color="red">失败 {v}</Tag> : r.error ? <Tag color="orange">异常</Tag> : <Tag color="green">成功</Tag>,
    },
    {
      title: "说明",
      dataIndex: "error",
      render: (v?: string | null) => v || "-",
    },
  ];

  const historyColumns: ColumnsType<FrameworkSyncHistoryItem> = [
    { title: "开始时间", dataIndex: "startTime", width: 190, render: (v?: string | null) => v || "-" },
    {
      title: "状态",
      dataIndex: "syncStatus",
      width: 100,
      render: (v: string) =>
        v === "SUCCESS" ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag>,
    },
    { title: "新增/更新", width: 120, align: "right", render: (_, r) => `${r.successCount ?? 0} 行` },
    {
      title: "失败",
      width: 80,
      align: "right",
      render: (_, r) => (r.failedCount ? <Tag color="red">{r.failedCount}</Tag> : "0"),
    },
    { title: "耗时", dataIndex: "durationMs", width: 100, render: (v?: number | null) => (v != null ? `${v} ms` : "-") },
    { title: "触发人", dataIndex: "createUser", render: (v?: string | null) => v || "-" },
    {
      title: "错误",
      dataIndex: "errorMessage",
      ellipsis: true,
      render: (v?: string | null) => v || "-",
    },
  ];

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 16 }}>
      <Alert
        type="info"
        showIcon
        message="功能介绍"
        description="从 data_supply_shandong 同步团队、用户、角色数据到本平台（admin 用户除外）。同步前先在「数据源管理」配置 shandong 业务库（mysql 类型）。同步完成后，shandong 用户登录后调用本平台时鉴权自动通过。"
      />

      <Card
        title="同步配置"
        extra={
          <Space>
            <Button onClick={handleTest} loading={testing} disabled={!dsId}>
              测试连接
            </Button>
            <Button type="primary" icon={<SyncOutlined />} onClick={handleSync} loading={syncing} disabled={!dsId}>
              开始同步
            </Button>
          </Space>
        }
      >
        <Space size="middle" align="center" style={{ width: "100%" }}>
          <span>同步数据源：</span>
          <Select
            style={{ width: 380 }}
            loading={dsLoading}
            value={dsId}
            onChange={setDsId}
            placeholder="选择数据源（mysql 类型）"
            options={dsList.map((d) => ({
              value: d.id,
              label: `${d.dsLabel || d.dsName}（${d.dsName}）`,
            }))}
            showSearch
            optionFilterProp="label"
          />
          {dsList.length === 0 && (
            <span style={{ color: "#999" }}>
              暂无 mysql 数据源，请先到「数据源管理」配置 shandong 业务库
            </span>
          )}
        </Space>
      </Card>

      <Card
        title="同步结果"
        extra={
          result && (
            <Space>
              <Tag color="blue">{result.syncStatus === "SUCCESS" ? "最近一次：成功" : "最近一次：失败"}</Tag>
              {result.startTime && <span style={{ color: "#999" }}>{result.startTime}</span>}
            </Space>
          )
        }
      >
        {rows.length === 0 ? (
          <Empty description="尚未同步，选择数据源后点击「开始同步」" />
        ) : (
          <>
            <Table rowKey="key" columns={tableColumns} dataSource={rows} pagination={false} size="small" />
            {typeof fallback === "object" && fallback && (
              <div style={{ marginTop: 12, color: "#666" }}>
                角色保底：已处理 {fallback.users ?? 0} 个用户，其中 {fallback.normal_user_added ?? 0} 个补挂 normal_user
                （无授权角色时保证鉴权通过）。
              </div>
            )}
          </>
        )}
      </Card>

      <Card title="同步历史">
        <Table
          rowKey="syncId"
          columns={historyColumns}
          dataSource={history}
          pagination={false}
          size="small"
          loading={historyLoading}
        />
      </Card>
    </div>
  );
}
