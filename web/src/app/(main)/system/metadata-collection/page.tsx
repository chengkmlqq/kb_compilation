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
 *
 * 布局（2026-10-14）：对齐整体 UI —— 外层定高不滚动 + ModoTabs（首 tab「元数据采集」= 页面标题 + 列表）
 * + Card 内「筛选固定 / 表格吃满剩余高度」；抽屉/弹窗统一 ModoDrawer / ModoModal。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Form,
  Input,
  Radio,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  DownloadOutlined,
  ReloadOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoModal } from "@/components/biz/modo-modal";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import { ModoTable } from "@/components/biz/modo-table";
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

const { Text } = Typography;

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
  const [filterForm] = Form.useForm<{ name?: string; dsType?: string }>();
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

  // 元数据查看器（动态选项卡：表列表 → 字段，对齐 kbs 文档详情模式）
  const [viewerTabs, setViewerTabs] = useState<{ key: string; title: string; ds: MetadataDatasourceItem }[]>([]);
  const [colTabs, setColTabs] = useState<{ key: string; title: string; table: MetadataTableItem; dsId: string }[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeViewerTab = (key: string) => {
    setViewerTabs((prev) => prev.filter((t) => t.key !== key));
    // 关闭元数据查看页签时，级联关闭其下已打开的字段页签
    const dsId = key.replace(/^meta-/, "");
    setColTabs((prev) => prev.filter((t) => t.dsId !== dsId));
    if (activeTab === key) setActiveTab("home");
  };

  const closeColTab = (key: string) => {
    setColTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  const openViewer = (ds: MetadataDatasourceItem) => {
    const key = `meta-${ds.id}`;
    setViewerTabs((prev) => {
      if (prev.some((t) => t.key === key)) return prev;
      return [...prev, { key, title: `元数据 · ${ds.label || ds.name}`, ds }];
    });
    setActiveTab(key);
  };

  const openColumns = (t: MetadataTableItem, dsId: string) => {
    const key = `meta-${dsId}-cols-${t.id}`;
    setColTabs((prev) => {
      if (prev.some((x) => x.key === key)) return prev;
      return [...prev, { key, title: `字段 · ${t.tableName}`, table: t, dsId }];
    });
    setActiveTab(key);
  };

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

  const doReset = () => {
    filterForm.resetFields();
    setFilter({});
  };

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

  // 列表区（首个选项卡内容）：筛选固定 + 表格吃满剩余高度
  const listPane = (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            padding: "12px 0 0",
          },
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 16px 12px", flexShrink: 0 }}>
          <Form
            form={filterForm}
            layout="inline"
            onFinish={(v: { name?: string; dsType?: string }) => setFilter(v)}
            style={{ flex: 1 }}
          >
            <Form.Item name="name" label="数据源">
              <Input placeholder="数据源名称" allowClear style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="dsType" label="类型">
              <Select placeholder="全部类型" allowClear style={{ width: 160 }} options={dsTypeOptions} />
            </Form.Item>
            <Form.Item>
              <Space>
                <Button type="primary" htmlType="submit">
                  查询
                </Button>
                <Button onClick={doReset}>重置</Button>
              </Space>
            </Form.Item>
          </Form>
          <Text type="secondary">共 {filtered.length} 个数据源</Text>
        </div>

        <ModoTable<MetadataDatasourceItem>
          rowKey="id"
          columns={columnsDef}
          dataSource={filtered}
          loading={loading}
        />
      </Card>
    </div>
  );

  return (
    // 2026-10-14: 对齐整体 UI —— 外层定高不滚动，ModoTabs 首 tab「元数据采集」= 页面标题 + 列表。
    <div
      className="metadata-collection-page"
      style={{
        padding: 10,
        height: "calc(100vh - var(--app-header-h, 48px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action !== "remove") return;
          const k = String(key);
          if (k.includes("-cols-")) closeColTab(k);
          else closeViewerTab(k);
        }}
        tabBarExtraContent={
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        }
        items={[
          { key: "home", label: "元数据采集", children: listPane },
          // 元数据查看：点击「查看元数据」打开的动态选项卡（可关闭）
          ...viewerTabs.map((t) => ({
            key: t.key,
            label: t.title,
            closable: true,
            children: <MetadataTablesPane ds={t.ds} onOpenCols={(tb) => openColumns(tb, t.ds.id)} onChanged={delayedRefresh} />,
          })),
          // 字段详情：表列表点「字段」打开的动态选项卡（可关闭）
          ...colTabs.map((t) => ({
            key: t.key,
            label: t.title,
            closable: true,
            children: <MetadataColumnsPane table={t.table} />,
          })),
        ]}
      />

      {/* 采集确认弹窗 */}
      <ModoModal
        title={collectTarget ? `元数据采集 · ${collectTarget.label || collectTarget.name}` : "元数据采集"}
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
      </ModoModal>

      {/* 采集记录抽屉 */}
      <ModoDrawer
        title={`采集记录 · ${runsDs?.label || runsDs?.name || ""}`}
        open={runsOpen}
        onCancel={() => setRunsOpen(false)}
        width={680}
        showFooter={false}
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
      </ModoDrawer>
    </div>
  );
}

/** 元数据查看（动态选项卡内容）：表列表 + 搜索 + 模板下载/定向导入 + 分页 */
function MetadataTablesPane({
  ds,
  onOpenCols,
  onChanged,
}: {
  ds: MetadataDatasourceItem;
  onOpenCols: (t: MetadataTableItem) => void;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [tables, setTables] = useState<MetadataTableItem[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [tablesPage, setTablesPage] = useState(1);
  const [tablesTotal, setTablesTotal] = useState(0);
  const [tableKeyword, setTableKeyword] = useState("");

  const loadTables = async (p: number, kw: string) => {
    setTablesLoading(true);
    try {
      const res = await apiListMetadataTables({ datasourceId: ds.id, keyword: kw, page: p, pageSize: 20 });
      if (res.success && res.data) {
        setTables(res.data.items || []);
        setTablesTotal(res.data.total || 0);
        setTablesPage(p);
      }
    } finally {
      setTablesLoading(false);
    }
  };

  useEffect(() => {
    void loadTables(1, "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ds.id]);

  const onDownloadTemplate = async () => {
    try {
      const { blob, filename } = await apiDownloadMetadataTemplate(ds.id);
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
    try {
      const res = await apiImportMetadataCollection(ds.id, file);
      if (res.success) {
        message.success(res.message || `已提交 ${res.data?.targetCount ?? 0} 张表定向采集`);
        onChanged();
      } else {
        message.error(res.message || "导入失败");
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : "导入失败");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <Space style={{ marginBottom: 12 }} wrap>
        <Input
          placeholder="按表名/注释搜索"
          allowClear
          style={{ width: 220 }}
          value={tableKeyword}
          onChange={(e) => setTableKeyword(e.target.value)}
          onPressEnter={() => void loadTables(1, tableKeyword)}
        />
        <Button onClick={() => void loadTables(1, tableKeyword)}>搜索</Button>
        <Button icon={<DownloadOutlined />} onClick={() => void onDownloadTemplate()}>
          下载导入模板
        </Button>
        <label style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
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
              <ModoActionGroup
                maxCount={1}
                actions={[{ key: "columns", label: "字段", onClick: () => onOpenCols(r) }]}
              />
            ),
          },
        ]}
      />
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
        <ModoPagination
          current={tablesPage}
          pageSize={20}
          total={tablesTotal}
          onChange={(p) => void loadTables(p, tableKeyword)}
        />
      </div>
    </div>
  );
}

/** 字段详情（动态选项卡内容）：某表字段列表 */
function MetadataColumnsPane({ table }: { table: MetadataTableItem }) {
  const [columns, setColumns] = useState<MetadataColumnItem[]>([]);
  const [columnsLoading, setColumnsLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setColumnsLoading(true);
    apiListMetadataColumns(table.id)
      .then((res) => {
        if (alive && res.success && res.data) setColumns(res.data.items || []);
      })
      .finally(() => {
        if (alive) setColumnsLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [table.id]);

  return (
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
  );
}
