"use client";

/**
 * 数据查询（DataGrid）页 —— 迁移 data-synth 的 datagrid。
 *
 * 布局：左侧数据源选择 + 库表树；右侧 SQL 编辑器（可执行/格式化）+
 * 结果表格 + 表结构面板（字段/DDL/表信息）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Tree,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CaretRightOutlined,
  ClearOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  FileExcelOutlined,
  ReloadOutlined,
  TableOutlined,
} from "@ant-design/icons";
import {
  apiDatagridColumns,
  apiDatagridDdl,
  apiDatagridDatasources,
  apiDatagridExecute,
  apiDatagridExportExcel,
  apiDatagridExportSql,
  apiDatagridTableInfo,
  apiDatagridTables,
  DatagridColumn,
  DatagridDsItem,
  DatagridExecuteResult,
} from "@/lib/api";

const { Text } = Typography;

interface QueryState {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  resultType: string;
  msg: string;
  rowcount: number | null;
  truncated: boolean;
}

export default function DataGridPage() {
  const { message } = App.useApp();

  // ---- 数据源 ----
  const [dsList, setDsList] = useState<DatagridDsItem[]>([]);
  const [dsName, setDsName] = useState<string>("");
  const [dsLoading, setDsLoading] = useState(true);

  // ---- 表树 ----
  const [tables, setTables] = useState<string[]>([]);
  const [treeLoading, setTreeLoading] = useState(false);
  const [tableSearch, setTableSearch] = useState("");

  // ---- SQL ----
  const [sql, setSql] = useState("");
  const [executing, setExecuting] = useState(false);
  const [exporting, setExporting] = useState<"excel" | "sql" | "">("");
  const [query, setQuery] = useState<QueryState | null>(null);
  const [history, setHistory] = useState<string[]>([]);

  // ---- 表结构 ----
  const [activeTable, setActiveTable] = useState<string>("");
  const [columns, setColumns] = useState<DatagridColumn[]>([]);
  const [ddl, setDdl] = useState<string>("");
  const [tableInfo, setTableInfo] = useState<Array<Record<string, unknown>>>([]);
  const [structLoading, setStructLoading] = useState(false);

  const loadDsList = useCallback(async () => {
    setDsLoading(true);
    try {
      const res = await apiDatagridDatasources();
      if (res.success && res.data) {
        setDsList(res.data);
        if (res.data.length > 0 && !dsName) {
          setDsName(res.data[0].dsName);
        }
      } else {
        message.error(res.message || "加载数据源失败");
      }
    } finally {
      setDsLoading(false);
    }
  }, [dsName, message]);

  useEffect(() => {
    void loadDsList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 切换数据源 → 加载表
  const loadTables = useCallback(
    async (name: string) => {
      if (!name) return;
      setTreeLoading(true);
      setActiveTable("");
      try {
        const res = await apiDatagridTables(name);
        if (res.success) {
          const names = ((res.rows || []) as Array<Record<string, unknown>>)
            .map((r) => String(r.name || ""))
            .filter(Boolean);
          setTables(names);
        } else {
          message.error(res.msg || "获取表失败");
          setTables([]);
        }
      } finally {
        setTreeLoading(false);
      }
    },
    [message],
  );

  useEffect(() => {
    if (dsName) void loadTables(dsName);
  }, [dsName, loadTables]);

  // 点击表 → 加载字段/DDL/表信息
  const loadTableStruct = useCallback(
    async (table: string) => {
      if (!dsName || !table) return;
      setActiveTable(table);
      setStructLoading(true);
      try {
        const [c, d, info] = await Promise.all([
          apiDatagridColumns(dsName, table),
          apiDatagridDdl(dsName, table),
          apiDatagridTableInfo(dsName, table),
        ]);
        setColumns((c.rows || []) as DatagridColumn[]);
        setDdl(d.ddl || "");
        setTableInfo((info.rows || []) as Array<Record<string, unknown>>);
      } catch {
        message.error("加载表结构失败");
      } finally {
        setStructLoading(false);
      }
    },
    [dsName, message],
  );

  const executeSql = useCallback(
    async (sqlText: string) => {
      if (!dsName) {
        message.warning("请先选择数据源");
        return;
      }
      if (!sqlText.trim()) {
        message.warning("SQL 不能为空");
        return;
      }
      setExecuting(true);
      try {
        const res = await apiDatagridExecute(dsName, sqlText);
        const item = (res.data || [])[0];
        if (!item) {
          setQuery({ columns: [], rows: [], resultType: "", msg: "无返回", rowcount: 0, truncated: false });
          return;
        }
        setQuery({
          columns: item.columns || [],
          rows: item.rows || [],
          resultType: item.resultType || "execute",
          msg: item.msg || (item.success ? "执行成功" : "执行失败"),
          rowcount: item.rowcount ?? null,
          truncated: !!item.truncated,
        });
        if (!item.success) message.error(item.msg || "执行失败");
        setHistory((prev) => [sqlText, ...prev.filter((h) => h !== sqlText)].slice(0, 20));
      } finally {
        setExecuting(false);
      }
    },
    [dsName, message],
  );

  // ---- 导出（对齐 ds db-console exportExcel / exportSql）----
  const doExportExcel = useCallback(async () => {
    if (!dsName) return message.warning("请先选择数据源");
    if (!query || query.resultType !== "select" || query.rows.length === 0)
      return message.warning("请先执行 SELECT 查询并有结果再导出");
    setExporting("excel");
    try {
      const res = await apiDatagridExportExcel(dsName, sql);
      if (res.success && res.data) {
        const bin = atob(res.data);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const url = URL.createObjectURL(new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
        const a = document.createElement("a");
        a.href = url;
        a.download = res.filename || "export.xlsx";
        a.click();
        URL.revokeObjectURL(url);
        message.success("Excel 导出成功");
      } else {
        message.error(res.msg || "导出失败");
      }
    } finally {
      setExporting("");
    }
  }, [dsName, query, sql, message]);

  const doExportSql = useCallback(async () => {
    if (!dsName) return message.warning("请先选择数据源");
    if (!query || query.resultType !== "select" || query.rows.length === 0)
      return message.warning("请先执行 SELECT 查询并有结果再导出");
    setExporting("sql");
    try {
      const res = await apiDatagridExportSql(dsName, sql);
      if (res.success && res.data) {
        const url = URL.createObjectURL(new Blob([res.data], { type: "text/plain;charset=utf-8" }));
        const a = document.createElement("a");
        a.href = url;
        a.download = res.filename || "export.sql";
        a.click();
        URL.revokeObjectURL(url);
        message.success(`SQL 导出成功（${res.rows ?? 0} 条 INSERT）`);
      } else {
        message.error(res.msg || "导出失败");
      }
    } finally {
      setExporting("");
    }
  }, [dsName, query, sql, message]);

  const filteredTables = useMemo(() => {
    const kw = tableSearch.trim().toLowerCase();
    if (!kw) return tables;
    return tables.filter((t) => t.toLowerCase().includes(kw));
  }, [tables, tableSearch]);

  const treeData = useMemo(
    () => [
      {
        title: "表",
        key: "__tables__",
        selectable: false,
        children: filteredTables.map((t) => ({ title: t, key: `table:${t}`, isLeaf: true })),
      },
    ],
    [filteredTables],
  );

  const resultColumns: ColumnsType<Record<string, unknown>> = useMemo(
    () =>
      (query?.columns || []).map((c) => ({
        title: c,
        dataIndex: c,
        key: c,
        ellipsis: true,
        width: 180,
        render: (v: unknown) => (v === null ? <Text type="secondary">NULL</Text> : String(v)),
      })),
    [query],
  );

  const structColumns: ColumnsType<DatagridColumn> = [
    { title: "字段", dataIndex: "name", width: 160 },
    { title: "类型", dataIndex: "data_type", width: 140, render: (v: string) => <Tag>{v || "-"}</Tag> },
    {
      title: "可空",
      dataIndex: "is_nullable",
      width: 80,
      align: "center" as const,
      render: (v: unknown) =>
        String(v).toLowerCase().startsWith("no") || String(v) === "false" ? "否" : "是",
    },
    { title: "默认值", dataIndex: "default_value", width: 140, ellipsis: true },
    { title: "注释", dataIndex: "comment", ellipsis: true },
  ];

  return (
    <div className="modo-page" style={{ padding: 12, height: "100%", overflow: "hidden" }}>
      <Row gutter={12} style={{ height: "100%" }}>
        {/* 左侧：数据源 + 表树 */}
        <Col span={5} style={{ height: "100%" }}>
          <Card size="small" title="数据源" style={{ height: "100%", overflow: "hidden" }}>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Select
                style={{ width: "100%" }}
                placeholder="选择数据源"
                loading={dsLoading}
                value={dsName || undefined}
                onChange={setDsName}
                options={dsList.map((d) => ({
                  value: d.dsName,
                  label: `${d.dsLabel || d.dsName}（${d.dsType || ""}）`,
                }))}
                showSearch
                optionFilterProp="label"
              />
              <Input.Search
                placeholder="搜索表"
                allowClear
                value={tableSearch}
                onChange={(e) => setTableSearch(e.target.value)}
              />
              <Spin spinning={treeLoading}>
                <div style={{ maxHeight: "calc(100vh - 240px)", overflow: "auto" }}>
                  {filteredTables.length === 0 && !treeLoading ? (
                    <Empty description="暂无表" imageStyle={{ height: 48 }} />
                  ) : (
                    <Tree
                      treeData={treeData}
                      defaultExpandAll
                      showIcon
                      icon={<TableOutlined />}
                      onSelect={(keys, info) => {
                        const k = String(info.node.key);
                        if (k.startsWith("table:")) {
                          void loadTableStruct(k.slice(6));
                        }
                      }}
                    />
                  )}
                </div>
              </Spin>
            </Space>
          </Card>
        </Col>

        {/* 右侧：SQL 编辑器 + 结果 + 表结构 */}
        <Col span={19} style={{ height: "100%" }}>
          <Space direction="vertical" style={{ width: "100%", height: "100%" }} size={8}>
            <Card size="small" styles={{ body: { padding: 12 } }}>
              <Space direction="vertical" style={{ width: "100%" }}>
                <Input.TextArea
                  value={sql}
                  onChange={(e) => setSql(e.target.value)}
                  placeholder="输入 SQL，例如：SELECT * FROM your_table LIMIT 100；支持任意查询/DML"
                  autoSize={{ minRows: 2, maxRows: 8 }}
                  style={{ fontFamily: "Consolas, Monaco, monospace", fontSize: 13 }}
                />
                <Space>
                  <Button
                    type="primary"
                    icon={<CaretRightOutlined />}
                    loading={executing}
                    onClick={() => void executeSql(sql)}
                  >
                    执行
                  </Button>
                  <Button icon={<ClearOutlined />} onClick={() => setSql("")}>
                    清空
                  </Button>
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={() => void loadTables(dsName)}
                    disabled={!dsName}
                  >
                    刷新表
                  </Button>
                  <Button
                    icon={<FileExcelOutlined />}
                    loading={exporting === "excel"}
                    onClick={() => void doExportExcel()}
                    disabled={!dsName}
                  >
                    导出 Excel
                  </Button>
                  <Button
                    icon={<DownloadOutlined />}
                    loading={exporting === "sql"}
                    onClick={() => void doExportSql()}
                    disabled={!dsName}
                  >
                    导出 SQL
                  </Button>
                  {history.length > 0 && (
                    <Select
                      style={{ width: 320 }}
                      placeholder="历史 SQL"
                      value={undefined}
                      onChange={(v) => setSql(String(v))}
                      options={history.map((h) => ({ value: h, label: h.slice(0, 60) }))}
                    />
                  )}
                </Space>
              </Space>
            </Card>

            <Card size="small" style={{ flex: 1, minHeight: 240, overflow: "hidden" }} styles={{ body: { padding: 0, height: "100%" } }}>
              <Tabs
                size="small"
                defaultActiveKey="result"
                items={[
                  {
                    key: "result",
                    label: (
                      <span>
                        查询结果
                        {query && (
                          <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                            {query.resultType === "select"
                              ? `${query.rows.length} 行${query.truncated ? "（截断）" : ""} · ${query.columns.length} 列`
                              : `影响 ${query.rowcount ?? 0} 行`}
                          </Text>
                        )}
                      </span>
                    ),
                    children: (
                      <div style={{ padding: 8 }}>
                        {query ? (
                          query.resultType === "select" ? (
                            <Table<Record<string, unknown>>
                              size="small"
                              columns={resultColumns}
                              dataSource={query.rows.map((r, i) => ({ ...r, __row_key: i }))}
                              rowKey="__row_key"
                              scroll={{ x: "max-content", y: "calc(100vh - 420px)" }}
                              pagination={{ pageSize: 50, showSizeChanger: false }}
                            />
                          ) : (
                            <Empty
                              description={
                                <Text>
                                  执行成功，影响 {query.rowcount ?? 0} 行
                                  {query.msg ? `（${query.msg}）` : ""}
                                </Text>
                              }
                            />
                          )
                        ) : (
                          <Empty description="在左侧选择数据源并输入 SQL 后执行" />
                        )}
                      </div>
                    ),
                  },
                  {
                    key: "struct",
                    label: `表结构${activeTable ? `：${activeTable}` : ""}`,
                    children: (
                      <Spin spinning={structLoading}>
                        {activeTable ? (
                          <Space direction="vertical" size={8} style={{ width: "100%", padding: 8 }}>
                            <Row gutter={8}>
                              {tableInfo.map((info, i) => (
                                <Col span={6} key={i}>
                                  <Card size="small" title={String(Object.keys(info)[0] || "")} styles={{ body: { padding: "6px 12px" } }}>
                                    <Text strong>{String(Object.values(info)[0] ?? "-")}</Text>
                                  </Card>
                                </Col>
                              ))}
                            </Row>
                            {columns.length > 0 && (
                              <Table<DatagridColumn>
                                size="small"
                                columns={structColumns}
                                dataSource={columns.map((c, i) => ({ ...c, __key: i }))}
                                rowKey="__key"
                                scroll={{ x: 700, y: "calc(100vh - 480px)" }}
                                pagination={false}
                              />
                            )}
                            {ddl && (
                              <pre
                                style={{
                                  background: "#0F172A",
                                  color: "#E2E8F0",
                                  padding: 12,
                                  borderRadius: 6,
                                  fontSize: 12,
                                  overflow: "auto",
                                  margin: 0,
                                }}
                              >
                                {ddl}
                              </pre>
                            )}
                          </Space>
                        ) : (
                          <Empty description="点击左侧表查看结构" />
                        )}
                      </Spin>
                    ),
                  },
                ]}
              />
            </Card>
          </Space>
        </Col>
      </Row>
    </div>
  );
}