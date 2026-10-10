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
          children: <TableViewer tabData={t} />,
        };
      }
      const output = t as OutputTab;
      return {
        key: t.id,
        label: t.label,
        closable: false,
        children: (
          <div className="flex h-full min-h-0 w-full bg-white">
            <div
              className="flex w-[40px] shrink-0 flex-col items-center gap-3 border-r border-[#e8eaed] bg-[#fafafa] py-2"
            >
              <button
                className="flex h-7 w-7 items-center justify-center text-[#888] transition-colors hover:text-[#1677ff]"
                title="导出 Excel"
                onClick={() => handleExport(tab, false)}
              >
                <FileExcelOutlined />
              </button>
              <button
                className="flex h-7 w-7 items-center justify-center text-[#888] transition-colors hover:text-[#1677ff]"
                title="导出 SQL"
                onClick={() => handleExport(tab, true)}
              >
                <DownloadOutlined />
              </button>
              <button
                className="flex h-7 w-7 items-center justify-center text-[#888] transition-colors hover:text-[#1677ff]"
                title="清空日志"
                onClick={() => clearLogs(tab.id)}
              >
                <ClearOutlined />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-2">
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
        className="datagrid-console-inner-tabs h-full"
        style={{ height: "100%" }}
      />
    );
  };

  const outerItems = dbConsoleTabs.map((tab) => ({
    key: tab.id,
    label: tab.label,
    closable: true,
    children: renderInner(tab),
  }));

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-white">
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
        className="datagrid-console-tabs h-full"
        style={{ height: "100%" }}
      />
    </div>
  );
}