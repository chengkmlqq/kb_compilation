"use client";

/**
 * SQL 编辑器工作页（对齐 ds datagrid/components/code/index.tsx）：
 *  - CodeMirror 6（@uiw/react-codemirror + @codemirror/lang-sql）语法高亮
 *  - 工具条：limit 下拉（50~1000）· 执行（选中优先）· 清空 · 格式化 · 刷新
 *  - 执行 → console 自动展开 + 输出日志 + 结果 tab 追加
 *
 * kb 后端契约：POST /api/v1/datagrid/execute {dsName, sql} → {success, data:[{success,resultType,columns,rows,rowcount,msg,truncated}]}
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { App, Button, Select, Space } from "antd";
import {
  CaretRightOutlined,
  ClearOutlined,
  CodeOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import dynamic from "next/dynamic";
import type { ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { apiDatagridExecute, DatagridExecuteResult } from "@/lib/api";
import { useDataGridStore, type SchemaTab } from "../store/use-datagrid-store";

const CodeMirror = dynamic(() => import("@uiw/react-codemirror"), { ssr: false });

const LIMIT_OPTIONS = [
  { label: "<=50", value: 50 },
  { label: "<=100", value: 100 },
  { label: "<=200", value: 200 },
  { label: "<=500", value: 500 },
  { label: "<=1000", value: 1000 },
];

function parseSqlTableName(sql: string): string {
  const compact = sql.replace(/\s+/g, " ").trim();
  const m = compact.match(/\bfrom\s+([`"'[\w.]+)/i);
  if (!m?.[1]) return "";
  return m[1].replace(/[`"'[\]]/g, "").split(".").filter(Boolean).pop() || "";
}

const pad2 = (n: number) => String(n).padStart(2, "0");
function fmtTs(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}
function genId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

interface DbCodeProps {
  tab: SchemaTab;
}

export default function DbCode({ tab }: DbCodeProps) {
  const { message } = App.useApp();
  const cmRef = useRef<ReactCodeMirrorRef | null>(null);
  const { addDbConsoleTab, addResultTab, addLog, updateDbToolTab } = useDataGridStore();
  const [sql, setSql] = useState(tab.runSql || "");
  const [limit, setLimit] = useState(tab.limit || 1000);
  const [executing, setExecuting] = useState(false);

  useEffect(() => {
    setSql(tab.runSql || "");
  }, [tab.runSql, tab.id]);

  const sqlExtensions = useMemo(
    () => [import("@codemirror/lang-sql").then((m) => m.sql())] as unknown as ReactCodeMirrorRef["view"],
    [],
  );

  const run = async () => {
    const view = cmRef.current?.view;
    const selection = view?.state.selection.main;
    const selected = selection && selection.empty === false ? view.state.doc.sliceString(selection.from, selection.to) : "";
    const targetSql = selected || sql;
    if (!targetSql.trim()) {
      message.warning("请输入 SQL 语句");
      return;
    }

    const consoleId = tab.id;
    addDbConsoleTab({
      id: consoleId,
      type: "db",
      label: `${tab.dsName}@${tab.dsSchema || "default"}`,
      dsName: tab.dsName,
      dsSchema: tab.dsSchema,
      tabActiveIndex: null,
      runNum: 0,
      tabs: [
        {
          id: `${consoleId}-output`,
          type: "output",
          label: "输出",
          logs: [],
        },
      ],
    });

    const start = new Date();
    addLog(consoleId, { dsType: tab.dsType, code: `${tab.dsType || "sql"} > ${targetSql}`, execTime: fmtTs(start) });
    addLog(consoleId, { dsType: tab.dsType, code: "正在执行查询........", execTime: fmtTs(start) });

    setExecuting(true);
    try {
      const res = await apiDatagridExecute(tab.dsName, targetSql, tab.dsSchema);
      const items = (res.data || []) as DatagridExecuteResult[];
      items.forEach((item, index) => {
        if (!item.success) {
          addLog(consoleId, {
            dsType: tab.dsType,
            code: `执行失败: ${item.msg || ""}`,
            execTime: fmtTs(new Date()),
          });
          message.error(item.msg || "执行失败");
          return;
        }
        if (item.resultType === "select") {
          const cols = item.columns || (item.rows?.[0] ? Object.keys(item.rows[0]) : []);
          const tableName = parseSqlTableName(targetSql);
          addResultTab(consoleId, {
            id: genId(),
            type: "result",
            label: tableName || `结果 ${index + 1}`,
            tableName,
            runSql: targetSql,
            dsSchema: tab.dsSchema,
            dsName: tab.dsName,
            dsType: tab.dsType,
            data: item.rows || [],
            columns: cols,
            filter: { pageNum: 1, pageSize: limit, total: (item.rows || []).length },
          });
          addLog(consoleId, {
            dsType: tab.dsType,
            code: `查询成功，返回 ${(item.rows || []).length} 行${item.truncated ? "（截断）" : ""}`,
            execTime: fmtTs(new Date()),
          });
        } else {
          addLog(consoleId, {
            dsType: tab.dsType,
            code: `执行成功，影响 ${item.rowcount ?? 0} 行`,
            execTime: fmtTs(new Date()),
          });
        }
      });
    } catch (e) {
      addLog(consoleId, {
        dsType: tab.dsType,
        code: `执行异常: ${e instanceof Error ? e.message : String(e)}`,
        execTime: fmtTs(new Date()),
      });
      message.error(e instanceof Error ? e.message : "执行失败");
    } finally {
      setExecuting(false);
    }
  };

  const doFormat = () => {
    // 轻量格式化：统一空白（无 sql-formatter 依赖，对齐 ds 的简单处理）
    const view = cmRef.current?.view;
    const doc = view?.state.doc.toString() || sql;
    const formatted = doc
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .join("\n");
    setSql(formatted);
    updateDbToolTab(tab.id, { runSql: formatted });
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-white">
      {/* 工具条 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[#e8eaed] px-3 py-2">
        <Button
          type="primary"
          size="small"
          icon={<CaretRightOutlined />}
          loading={executing}
          onClick={() => void run()}
        >
          执行
        </Button>
        <Select
          size="small"
          style={{ width: 110 }}
          value={limit}
          options={LIMIT_OPTIONS}
          onChange={(v) => setLimit(v)}
        />
        <Button size="small" icon={<CodeOutlined />} onClick={doFormat}>
          格式化
        </Button>
        <Button size="small" icon={<ClearOutlined />} onClick={() => setSql("")}>
          清空
        </Button>
        <Space style={{ marginLeft: "auto", color: "#999", fontSize: 12 }}>
          选中 SQL 后执行将仅运行选中部分
        </Space>
      </div>
      {/* CodeMirror 编辑器 */}
      <div className="min-h-0 flex-1 overflow-hidden">
        <CodeMirror
          ref={cmRef}
          value={sql}
          height="100%"
          style={{ height: "100%", fontSize: 13 }}
          extensions={[import("@codemirror/lang-sql").then((m) => m.sql())] as never}
          onChange={(v) => {
            setSql(v);
            updateDbToolTab(tab.id, { runSql: v });
          }}
          basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
        />
      </div>
      {/* 底部状态条 */}
      <div className="flex shrink-0 items-center justify-between border-t border-[#e8eaed] bg-[#fafafa] px-3 py-1 text-[11px] text-[#999]">
        <span>{tab.dsType || "sql"}</span>
        <span>{sql.length} 字符 · LIMIT {limit}</span>
      </div>
    </div>
  );
}