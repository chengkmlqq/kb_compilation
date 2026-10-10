"use client";

/**
 * 元数据采集（迁移 data-synth system/metadata-collection，2026-10-10）。
 *
 * 功能（对齐 ds 页面）：
 *  - 可采集数据源列表（名称/类型/表数量/采集状态 Tag/最近采集时间）+ 筛选（名称/类型/刷新）
 *  - 立即采集 / 重新采集（确认弹窗，full=全量重采 / incremental+targets=定向）
 *  - 采集记录抽屉（GET /runs，状态/耗时/错误）
 *  - 元数据查看器（表列表分页 → 字段抽屉）
 *  - 导入模板下载 + xlsx 定向导入
 *  - 级联删除数据源元数据
 *
 * 后端契约见 api/routers/metadata.py（/api/v1/metadata/*）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  SyncOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { ModoPage } from "@/components/biz/modo-page";
import { ModoTable } from "@/components/biz/modo-table";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import {
  apiDeleteMetadataByDatasource,
  apiDownloadMetadataTemplate,
  apiGetMetadataConfig,
  apiImportMetadataCollection,
  apiListMetadataColumns,
  apiListMetadataDatasources,
  apiListMetadataRuns,
  apiListMetadataTables,
  apiSubmitMetadataCollection,
  MetadataColumnItem,
  MetadataCollectionRun,
  MetadataDatasourceItem,
  MetadataTableItem,
} from "@/lib/api";

type StatusType = "UNCOLLECTED" | "COLLECTED" | "COLLECTING" | "FAILED";

const STATUS_META: Record<StatusType, { color: string; label: string }> = {
  UNCOLLECTED: { color: "default", label: "未采集" },
  COLLECTED: { color: "success", label: "已采集" },
  COLLECTING: { color: "processing", label: "采集中" },
  FAILED: { color: "error", label: "采集失败" },
};

export default function MetadataCollectionPage() {
  const { message, modal } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<MetadataDatasourceItem[]>([]);
  const [filter, setFilter] = useState<{ name?: string; dsType?: string }>({});
  const [dsTypeOptions, setDsTypeOptions] = useState<{ label: string; value: string }[]>([]);
  const [fullCollectionEnabled, setFullCollectionEnabled] = useState(true);

  // 采集确认弹窗
  const [collectTarget, setCollectTarget] = useState<MetadataDatasourceItem | null>(null);
  const [collectMode, setCollectMode] = useState<"full" | "incremental">("full");
  const [collecting, setCollecting] = useState(false);

  // 采集记录抽屉
  const [runsOpen, setRunsOpen] = useState(false);
  const [runsDs, setRunsDs] = useState<MetadataDatasourceItem | null>(null);
  const [runs, setRuns] = useState<MetadataCollectionRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsPage, setRunsPage] = useState(1);
  const [runsTotal, setRunsTotal] = useState(0);

  // 元数据查看器（表列表 → 字段）
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerDs, setViewerDs] = useState<MetadataDatasourceItem | null>(null);
  const [tables, setTables] = useState<MetadataTableItem[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [tablesPage, setTablesPage] = useState(1);
  const [tablesTotal, setTablesTotal] = useState(0);
  const [tableKeyword, setTableKeyword] = useState("");
  const [columnsOpen, setColumnsOpen] = useState(false);
  const [columnsFor, setColumnsFor] = useState<MetadataTableItem | null>(null);
  const [columns, setColumns] = useState<MetadataColumnItem[]>([]);
  const [columnsLoading, setColumnsLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListMetadataDatasources();
      if (res.success && res.data) {
        setItems(res.data.items || []);
        const types = new Set((res.data.items || []).map((i) => i.dsTypeGroup).filter(Boolean));
        setDsTypeOptions(Array.from(types).map((t) => ({ label: t, value: t })));
      } else {
        message.error(res.message || "加载数据源失败");
      }
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void apiGetMetadataConfig().then((res) => {
      if (res.success && res.data) setFullCollectionEnabled(res.data.fullCollectionEnabled);
    });
  }, []);

  // 短延时二次刷新（采集任务入队后状态异步变化）
  const delayedRefresh = () => {
    void load();
    setTimeout(() => void load(), 2500);
  };

  const filtered = useMemo(() => {
    const q = (filter.name || "").trim().toLowerCase();
    const t = filter.dsType || "";
    return items.filter(
      (i) =>
        (!q || i.name.toLowerCase().includes(q) || (i.label || "").toLowerCase().includes(q)) &&
        (!t || i.dsTypeGroup === t),
    );
  }, [items, filter]);

  const onCollect = async () => {
    if (!collectTarget) return;
    setCollecting(true);
    try {
      const res = await apiSubmitMetadataCollection(collectTarget.id, collectMode);
      if (res.success) {
        message.success("采集任务已提交，后台执行中");
        setCollectTarget(null);
        delayedRefresh();
      } else {
        message.error(res.message || "提交失败");
      }
    } finally {
      setCollecting(false);
    }
  };

  const openRuns = async (ds: MetadataDatasourceItem) => {
    setRunsDs(ds);
    setRunsOpen(true);
    setRunsPage(1);
    setRunsLoading(true);
    try {
      const res = await apiListMetadataRuns(ds.id, 1, 20);
      if (res.success && res.data) {
        setRuns(res.data.items || []);
        setRunsTotal(res.data.total || 0);
      }
    } finally {
      setRunsLoading(false);
    }
  };

  const loadRunsPage = async (p: number, dsId: string) => {
    setRunsLoading(true);
    try {
      const res = await apiListMetadataRuns(dsId, p, 20);
      if (res.success && res.data) {
        setRuns(res.data.items || []);
        setRunsTotal(res.data.total || 0);
        setRunsPage(p);
      }
    } finally {
      setRunsLoading(false);
    }
  };

  const openViewer = async (ds: MetadataDatasourceItem) => {
    setViewerDs(ds);
    setViewerOpen(true);
    setTablesPage(1);
    setTableKeyword("");
    await loadTables(1, "", ds.id);
  };

  const loadTables = async (p: number, kw: string, dsId: string) => {
    setTablesLoading(true);
    try {
      const res = await apiListMetadataTables({ datasourceId: dsId, keyword: kw, page: p, pageSize: 20 });
      if (res.success && res.data) {
        setTables(res.data.items || []);
        setTablesTotal(res.data.total || 0);
        setTablesPage(p);
      }
    } finally {
      setTablesLoading(false);
    }
  };

  const openColumns = async (t: MetadataTableItem) => {
    setColumnsFor(t);
    setColumnsOpen(true);
    setColumnsLoading(true);
    try {
      const res = await apiListMetadataColumns(t.id);
      if (res.success && res.data) setColumns(res.data.items || []);
    } finally {
      setColumnsLoading(false);
    }
  };

  const onDelete = (ds: MetadataDatasourceItem) => {
    modal.confirm({
      title: `删除数据源「${ds.label || ds.name}」的全部元数据？`,
      content: "将删除该数据源的所有表与字段元数据（不影响数据源本身）。",
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiDeleteMetadataByDatasource(ds.id);
        if (res.success) {
          message.success("已删除");
          void load();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  const onDownloadTemplate = async () => {
    if (!viewerDs) return;
    try {
      const { blob, filename } = await apiDownloadMetadataTemplate(viewerDs.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      message.success("模板已下载");
    } catch (e) {
      message.error(e instanceof Error ? e.message : "下载失败");
    }
  };

  const onImportFile = async (file: File) => {
    if (!viewerDs) return;
    try {
      const res = await apiImportMetadataCollection(viewerDs.id, file);
      if (res.success) {
        message.success(res.message || `已提交 ${res.data?.targetCount ?? 0} 张表定向采集`);
        delayedRefresh();
      } else {
        message.error(res.message || "导入失败");
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : "导入失败");
    }
  };

  const columnsDef: ColumnsType<MetadataDatasourceItem> = [
    {
      title: "数据源名称",
      dataIndex: "label",
      key: "label",
      render: (_v, r) => (
        <Space size={6}>
          <span style={{ fontWeight: 500 }}>{r.label || r.name}</span>
          <Tag>{r.dsType}</Tag>
        </Space>
      ),
    },
    { title: "数据源类型", dataIndex: "dsTypeGroup", key: "dsTypeGroup", width: 140 },
    { title: "表数量", dataIndex: "tableCount", key: "tableCount", width: 90 },
    {
      title: "采集状态",
      dataIndex: "collectionStatus",
      key: "collectionStatus",
      width: 110,
      render: (v: StatusType, r) => (
        <Tooltip title={r.lastError || undefined}>
          <Tag color={STATUS_META[v]?.color || "default"}>
            {v === "COLLECTING" ? <Spin size="small" style={{ marginRight: 4 }} /> : null}
            {STATUS_META[v]?.label || v}
          </Tag>
        </Tooltip>
      ),
    },
    { title: "最近采集时间", dataIndex: "lastCollectionTime", key: "lastCollectionTime", width: 170 },
    {
      title: "操作",
      key: "actions",
      width: 260,
      render: (_v, r) => (
        <ModoActionGroup
          actions={[
            {
              key: "collect",
              label: r.collectionStatus === "COLLECTED" ? "重新采集" : "立即采集",
              onClick: () => {
                setCollectTarget(r);
                setCollectMode("full");
              },
            },
            { key: "viewer", label: "查看元数据", onClick: () => void openViewer(r) },
            { key: "runs", label: "采集记录", onClick: () => void openRuns(r) },
            { key: "delete", label: "删除", danger: true, onClick: () => onDelete(r) },
          ]}
        />
      ),
    },
  ];

  return (
    <ModoPage>
      <div className="kb-flex-col" style={{ height: "100%", gap: 8 }}>
        <Form
          layout="inline"
          onFinish={(v: { name?: string; dsType?: string }) => setFilter(v)}
          style={{ gap: 8, marginBottom: 8 }}
        >
          <Form.Item name="name" noStyle>
            <Input placeholder="数据源名称" allowClear style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="dsType" noStyle>
            <Select
              placeholder="类型"
              allowClear
              style={{ width: 150 }}
              options={dsTypeOptions}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<FileSearchOutlined />}>
            查询
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        </Form>

        <Card
          size="small"
          title={`可采集数据源 (${filtered.length})`}
          style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
          styles={{ body: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" } }}
        >
          <ModoTable<MetadataDatasourceItem>
            rowKey="id"
            columns={columnsDef}
            dataSource={filtered}
            loading={loading}
          />
        </Card>
      </div>

      {/* 采集确认弹窗 */}
      <Modal
        title={collectTarget ? `元数据采集 · ${collectTarget.label || collectTarget.name}` : ""}
        open={!!collectTarget}
        onCancel={() => setCollectTarget(null)}
        onOk={onCollect}
        okText="开始采集"
        confirmLoading={collecting}
        width={520}
      >
        {collectTarget && (
          <Space direction="vertical" style={{ display: "flex" }} size={12}>
            <div>
              数据源类型：<Tag>{collectTarget.dsType}</Tag>
              当前表数量：<b>{collectTarget.tableCount}</b>
              {collectTarget.lastCollectionTime ? (
                <div style={{ color: "rgba(0,0,0,.65)", fontSize: 12, marginTop: 4 }}>
                  最近采集：{collectTarget.lastCollectionTime}
                </div>
              ) : null}
            </div>
            <Form layout="vertical">
              <Form.Item label="采集模式" style={{ marginBottom: 0 }}>
                <Radio.Group
                  value={collectMode}
                  onChange={(e) => setCollectMode(e.target.value)}
                >
                  <Radio value="full">全量采集（清空历史后重新采集全部表结构）</Radio>
                  <Radio value="incremental">增量采集（保留历史，仅采集新增表）</Radio>
                </Radio.Group>
              </Form.Item>
            </Form>
            <div style={{ color: "rgba(0,0,0,.45)", fontSize: 12 }}>
              采集在后台异步执行（约 1-5 分钟，视数据库大小）；提交后页面自动刷新状态。
              {!fullCollectionEnabled ? <div style={{ color: "#faad14" }}>⚠️ 全量采集开关当前为关闭状态。</div> : null}
            </div>
          </Space>
        )}
      </Modal>

      {/* 采集记录抽屉 */}
      <Drawer
        title={`采集记录 · ${runsDs?.label || runsDs?.name || ""}`}
        open={runsOpen}
        onClose={() => setRunsOpen(false)}
        width={680}
      >
        <Table<MetadataCollectionRun>
          rowKey="job_id"
          size="small"
          loading={runsLoading}
          pagination={false}
          dataSource={runs}
          columns={[
            { title: "任务 ID", dataIndex: "job_id", key: "job_id", width: 200, ellipsis: true },
            {
              title: "状态",
              dataIndex: "state",
              key: "state",
              width: 100,
              render: (v: string) => {
                const color = v === "SUCCESS" ? "success" : v === "FAILED" ? "error" : "processing";
                return <Tag color={color}>{v}</Tag>;
              },
            },
            { title: "模式", dataIndex: "collection_mode", key: "collection_mode", width: 90 },
            { title: "耗时(ms)", dataIndex: "duration_ms", key: "duration_ms", width: 100 },
            { title: "提交时间", dataIndex: "create_time", key: "create_time", width: 170 },
            {
              title: "摘要/错误",
              key: "detail",
              render: (_v, r) =>
                r.state === "FAILED" ? (
                  <span style={{ color: "#cf1322" }}>{r.error_message}</span>
                ) : (
                  <span>
                    {r.summary
                      ? `表 ${(r.summary as Record<string, unknown>).tableCount ?? "-"} · 字段 ${(r.summary as Record<string, unknown>).columnCount ?? "-"}`
                      : "—"}
                  </span>
                ),
            },
          ]}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
          <ModoPagination
            current={runsPage}
            pageSize={20}
            total={runsTotal}
            onChange={(p) => runsDs && void loadRunsPage(p, runsDs.id)}
          />
        </div>
      </Drawer>

      {/* 元数据查看器 */}
      <Drawer
        title={`元数据查看 · ${viewerDs?.label || viewerDs?.name || ""}`}
        open={viewerOpen}
        onClose={() => setViewerOpen(false)}
        width={900}
      >
        <Space style={{ marginBottom: 12 }} wrap>
          <Input
            placeholder="按表名/注释搜索"
            allowClear
            style={{ width: 220 }}
            value={tableKeyword}
            onChange={(e) => setTableKeyword(e.target.value)}
            onPressEnter={() => viewerDs && void loadTables(1, tableKeyword, viewerDs.id)}
          />
          <Button onClick={() => viewerDs && void loadTables(1, tableKeyword, viewerDs.id)}>
            搜索
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => void onDownloadTemplate()}>
            下载导入模板
          </Button>
          <label
            style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}
          >
            <input
              type="file"
              accept=".xlsx"
              style={{ display: "none" }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onImportFile(f);
                e.target.value = "";
              }}
            />
            <Button icon={<UploadOutlined />}>导入定向采集</Button>
          </label>
        </Space>
        <Table<MetadataTableItem>
          rowKey="id"
          size="small"
          loading={tablesLoading}
          pagination={false}
          dataSource={tables}
          columns={[
            { title: "表名", dataIndex: "tableName", key: "tableName", ellipsis: true },
            {
              title: "类型",
              dataIndex: "tableType",
              key: "tableType",
              width: 80,
              render: (v: string) => <Tag color={v === "VIEW" ? "purple" : "blue"}>{v || "TABLE"}</Tag>,
            },
            { title: "注释", dataIndex: "tableComment", key: "tableComment", ellipsis: true },
            { title: "行数", dataIndex: "rowCount", key: "rowCount", width: 90 },
            { title: "字段数", dataIndex: "columnCount", key: "columnCount", width: 80 },
            { title: "采集时间", dataIndex: "collectionTime", key: "collectionTime", width: 170 },
            {
              title: "操作",
              key: "actions",
              width: 100,
              render: (_v, r) => (
                <Button size="small" type="link" icon={<EyeOutlined />} onClick={() => void openColumns(r)}>
                  字段
                </Button>
              ),
            },
          ]}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
          <ModoPagination
            current={tablesPage}
            pageSize={20}
            total={tablesTotal}
            onChange={(p) => viewerDs && void loadTables(p, tableKeyword, viewerDs.id)}
          />
        </div>
      </Drawer>

      {/* 字段详情抽屉 */}
      <Drawer
        title={`字段 · ${columnsFor?.tableName || ""}`}
        open={columnsOpen}
        onClose={() => setColumnsOpen(false)}
        width={820}
      >
        <Table<MetadataColumnItem>
          rowKey="id"
          size="small"
          loading={columnsLoading}
          pagination={false}
          dataSource={columns}
          columns={[
            {
              title: "列名",
              dataIndex: "columnName",
              key: "columnName",
              render: (v: string, r) => (
                <Space size={4}>
                  <b>{v}</b>
                  {r.isPrimaryKey ? <Tag color="red">PK</Tag> : null}
                  {r.isUnique ? <Tag color="orange">UQ</Tag> : null}
                </Space>
              ),
            },
            { title: "类型", dataIndex: "columnType", key: "columnType", width: 140 },
            {
              title: "长度",
              dataIndex: "columnLength",
              key: "columnLength",
              width: 70,
              render: (v) => v ?? "—",
            },
            { title: "可空", dataIndex: "isNullable", key: "isNullable", width: 70 },
            { title: "默认值", dataIndex: "columnDefault", key: "columnDefault", ellipsis: true },
            { title: "注释", dataIndex: "columnComment", key: "columnComment", ellipsis: true },
          ]}
        />
      </Drawer>
    </ModoPage>
  );
}