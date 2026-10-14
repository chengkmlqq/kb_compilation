"use client";

/**
 * 下部控制台（对齐 ds datagrid/components/db-console.tsx）：
 *  - 每数据源一个 console tab（editable-card 可关）
 *  - console 内：结果子标签（TableViewer）+ 输出日志子标签（42px 图标工具条 + 日志列表）
 *  - 首次执行 SQL 自动展开（store consoleShow）
 */
import React from "react";
import { App, Tabs } from "antd";
import { ClearOutlined, FileExcelOutlined, DownloadOutlined } from "@ant-design/icons";
import { useDataGridStore, type DbConsoleTab, ResultTab, OutputTab } from "../store/use-datagrid-store";
import TableViewer from "./table-viewer";

interface DbConsoleProps {
  exportShow?: boolean;
}

export default function DbConsole({ exportShow = true }: DbConsoleProps) {
  const { message } = App.useApp();
  const {
    dbConsoleTabs,
    dbConsoleActiveTab,
    setDbConsoleActiveTab,
    removeDbConsoleTab,
    removeResultTab,
    clearLogs,
    setDbConsoleInnerActiveTab,
  } = useDataGridStore();

  const handleExport = (dbTab: DbConsoleTab, asSql: boolean) => {
    if (!exportShow) return;
    const activeId = dbTab.tabActiveIndex;
    const resultTab = dbTab.tabs.find((t) => t.id === activeId);
    if (!resultTab || resultTab.type !== "result") {
      message.warning("请选择查询结果页签进行导出");
      return;
    }
    const data = (resultTab as ResultTab).data || [];
    if (data.length === 0) {
      message.warning("暂无数据");
      return;
    }
    const cols = (resultTab as ResultTab).columns || Object.keys(data[0] || {});
    if (asSql) {
      const tableName = (resultTab as ResultTab).tableName || "export_table";
      let sql = "";
      for (const row of data) {
        const values = cols.map((c) => {
          const v = (row as Record<string, unknown>)[c];
          if (v === null || v === undefined) return "NULL";
          if (typeof v === "number") return v;
          if (typeof v === "boolean") return v ? 1 : 0;
          return `'${String(v).replace(/'/g, "''")}'`;
        });
        sql += `INSERT INTO ${tableName} (${cols.join(", ")}) VALUES (${values.join(", ")});\n`;
      }
      const blob = new Blob([sql], { type: "text/plain;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${resultTab.label || "export"}.sql`;
      a.click();
      URL.revokeObjectURL(a.href);
      message.success("SQL 导出成功");
    } else {
      void import("xlsx").then((XLSX) => {
        const ws = XLSX.utils.json_to_sheet(data);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, "Export");
        XLSX.writeFile(wb, `${resultTab.label || "export"}.xlsx`);
        message.success("Excel 导出成功");
      });
    }
  };

  const renderInner = (tab: DbConsoleTab) => {
    const innerItems = tab.tabs.map((t) => {
      if (t.type === "result") {
        return {
          key: t.id,
          label: t.label,
          closable: true,
          // pane 高度链（对齐 data-synth 上游）：Tabs content 不自动撑满，
          // pane 必须显式 height:100% 才让 TableViewer 的 flex 链成立
          style: { height: "100%", minHeight: 0, overflow: "hidden" },
          children: <TableViewer tabData={t} />,
        };
      }
      const output = t as OutputTab;
      return {
        key: t.id,
        label: t.label,
        closable: false,
        style: { height: "100%", minHeight: 0, overflow: "hidden" },
        children: (
          <div style={{ display: "flex", height: "100%", minHeight: 0, width: "100%", background: "#fff" }}>
            <div
              style={{
                display: "flex",
                width: 40,
                flexShrink: 0,
                flexDirection: "column",
                alignItems: "center",
                gap: 12,
                borderRight: "1px solid #e8eaed",
                background: "#fafafa",
                paddingTop: 8,
                paddingBottom: 8,
              }}
            >
              <button className="datagrid-icon-btn" title="导出 Excel" onClick={() => handleExport(tab, false)}>
                <FileExcelOutlined />
              </button>
              <button className="datagrid-icon-btn" title="导出 SQL" onClick={() => handleExport(tab, true)}>
                <DownloadOutlined />
              </button>
              <button className="datagrid-icon-btn" title="清空日志" onClick={() => clearLogs(tab.id)}>
                <ClearOutlined />
              </button>
            </div>
            <div style={{ minHeight: 0, flex: 1, overflow: "auto", padding: 8 }}>
              {output.logs.length === 0 ? (
                <div style={{ color: "#999", fontSize: 12, padding: 8 }}>暂无日志，执行 SQL 后在此查看输出</div>
              ) : (
                output.logs.map((log, i) => (
                  <div key={i} style={{ padding: "2px 0", fontSize: 12, lineHeight: 1.6 }}>
                    <span style={{ color: "#999", marginRight: 8 }}>{log.execTime}</span>
                    <span style={{ color: "#333", fontFamily: "Consolas, Monaco, monospace", wordBreak: "break-all" }}>
                      {log.code}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        ),
      };
    });

    return (
      <Tabs
        size="small"
        type="editable-card"
        hideAdd
        activeKey={tab.tabActiveIndex || undefined}
        onChange={(k) => setDbConsoleInnerActiveTab(tab.id, k)}
        onEdit={(targetKey, action) => {
          if (action === "remove" && typeof targetKey === "string") {
            removeResultTab(tab.id, targetKey);
          }
        }}
        items={innerItems}
        className="datagrid-console-inner-tabs"
        style={{ height: "100%" }}
        // 高度链（对齐 data-synth 上游）：Tabs content 需 flex:1 撑满，否则 pane 高度 0 → Handsontable 不渲染
        styles={{
          header: { flexShrink: 0 },
          body: { height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" },
          content: { flex: 1, minWidth: 0, minHeight: 0, overflow: "hidden" },
        }}
      />
    );
  };

  const outerItems = dbConsoleTabs.map((tab) => ({
    key: tab.id,
    label: tab.label,
    closable: true,
    style: { height: "100%", minHeight: 0, overflow: "hidden" },
    children: renderInner(tab),
  }));

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0, flexDirection: "column", overflow: "hidden", background: "#fff" }}>
      <Tabs
        size="small"
        type="editable-card"
        hideAdd
        activeKey={dbConsoleActiveTab || undefined}
        onChange={setDbConsoleActiveTab}
        onEdit={(targetKey, action) => {
          if (action === "remove" && typeof targetKey === "string") {
            removeDbConsoleTab(targetKey);
          }
        }}
        items={outerItems}
        className="datagrid-console-tabs"
        style={{ height: "100%" }}
        styles={{
          header: { flexShrink: 0 },
          body: { height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" },
          content: { flex: 1, minWidth: 0, minHeight: 0, overflow: "hidden" },
        }}
      />
    </div>
  );
}