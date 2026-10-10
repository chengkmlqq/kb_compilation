"use client";

/**
 * 查询结果网格（对齐 ds datagrid/components/table-viewer.tsx）：
 *  - Handsontable 只读网格（复制表格/查看行数据/选择高亮/列宽拖动）
 *  - 顶部 Pagination（50~1000 页大小可选）
 *
 * Handsontable 使用 non-commercial-and-evaluation license（与 ds 一致）。
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { App, Button, Pagination } from "antd";
import { CopyOutlined, EyeOutlined } from "@ant-design/icons";
import { HotTable, type HotTableRef } from "@handsontable/react-wrapper";
import { registerAllModules } from "handsontable/registry";
import "handsontable/styles/handsontable.min.css";
import "handsontable/styles/ht-theme-main.min.css";
import type { ResultTab } from "../store/use-datagrid-store";

registerAllModules();

function normalizeValue(v: unknown): string | number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "object") return JSON.stringify(v);
  return v as string | number;
}

export default function TableViewer({ tabData }: { tabData: ResultTab }) {
  const { message, modal } = App.useApp();
  const hotRef = useRef<HotTableRef | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [pageNum, setPageNum] = useState(tabData.filter.pageNum || 1);
  const [pageSize, setPageSize] = useState(tabData.filter.pageSize || 50);
  const [hotHeight, setHotHeight] = useState(0);
  const [selection, setSelection] = useState<{ r: number; c: number } | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    let raf = 0;
    const update = () => {
      const h = el.clientHeight;
      if (h <= 0) return;
      setHotHeight(h);
      // Handsontable 从 display:none 切回时表头可能白屏，强制重渲染
      setTimeout(() => hotRef.current?.hotInstance?.render(), 60);
    };
    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };
    schedule();
    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    window.addEventListener("resize", schedule);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", schedule);
      ro.disconnect();
    };
  }, [tabData.id]);

  const columns = useMemo(() => tabData.columns || [], [tabData.columns]);
  const headers = useMemo(() => columns.map((c) => c), [columns]);

  const tableData = useMemo(
    () => tabData.data.map((row, i) => ({ __row_index: i, ...row })),
    [tabData.data],
  ) as Array<Record<string, unknown>>;

  const paged = useMemo(() => {
    const start = (pageNum - 1) * pageSize;
    return tableData.slice(start, start + pageSize);
  }, [tableData, pageNum, pageSize]);

  const hotData = useMemo(
    () =>
      paged.map((row) =>
        columns.length > 0
          ? columns.map((c) => normalizeValue(row[c]))
          : Object.values(row).map(normalizeValue),
      ),
    [paged, columns],
  );

  const copyTable = async () => {
    if (headers.length === 0 || paged.length === 0) {
      message.warning("当前表格无可复制数据");
      return;
    }
    const lines = [headers.join("\t")];
    for (const row of paged) {
      const cells = columns.map((c) => {
        const v = row[c];
        if (v === null || v === undefined) return "";
        if (typeof v === "object") return JSON.stringify(v);
        return String(v);
      });
      lines.push(cells.join("\t"));
    }
    await navigator.clipboard.writeText(lines.join("\n"));
    message.success("已复制当前表格（含表头）");
  };

  const viewRow = () => {
    if (selection === null) {
      message.warning("请先选择一行数据");
      return;
    }
    const row = paged[selection.r];
    if (!row) return;
    modal.info({
      title: "行数据",
      width: 720,
      maskClosable: true,
      content: (
        <pre
          style={{
            maxHeight: "60vh",
            overflow: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
            borderRadius: 4,
            background: "#f5f5f5",
            padding: 12,
            fontSize: 12,
            lineHeight: 1.7,
          }}
        >
          {JSON.stringify(row, null, 2)}
        </pre>
      ),
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-white">
      {/* 工具条：分页 + 复制/查看行 */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-[#e8eaed] bg-white px-3 py-1.5">
        <Pagination
          size="small"
          current={pageNum}
          pageSize={pageSize}
          total={tabData.filter.total || tableData.length}
          onChange={(p, s) => {
            setPageNum(p);
            setPageSize(s);
          }}
          showSizeChanger
          pageSizeOptions={[50, 100, 200, 500, 1000]}
          showQuickJumper={{ goButton: "前往" }}
          showTotal={(t) => `共 ${t} 条`}
        />
        <div style={{ display: "flex", gap: 8 }}>
          <Button
            size="small"
            icon={<CopyOutlined />}
            onClick={() => void copyTable()}
            disabled={columns.length === 0 || paged.length === 0}
          >
            复制
          </Button>
          <Button size="small" icon={<EyeOutlined />} onClick={viewRow} disabled={selection === null}>
            查看行数据
          </Button>
        </div>
      </div>
      {/* Handsontable 网格 */}
      <div ref={wrapRef} className="datagrid-hot-wrap min-h-0 flex-1 overflow-hidden">
        {hotHeight > 0 && (
          <HotTable
            ref={hotRef}
            data={hotData}
            colHeaders={headers}
            rowHeaders
            width="100%"
            height={hotHeight}
            stretchH="all"
            licenseKey="non-commercial-and-evaluation"
            readOnly
            selectionMode="single"
            autoWrapRow={false}
            autoWrapCol={false}
            contextMenu={false}
            manualColumnResize
            copyPaste
            className="ht-theme-main"
            fillHandle={false}
            afterSelectionEnd={(row) => setSelection({ r: row, c: 0 })}
            afterDeselect={() => setSelection(null)}
          />
        )}
      </div>
      <style jsx global>{`
        .datagrid-hot-wrap {
          position: relative;
        }
        .datagrid-hot-wrap .handsontable {
          color: rgba(0, 0, 0, 0.88);
          font-size: 13px;
        }
        .datagrid-hot-wrap .handsontable .htCore td,
        .datagrid-hot-wrap .handsontable .htCore th {
          border-color: #e8eaed;
          color: rgba(0, 0, 0, 0.88);
          background: #fff;
        }
        .datagrid-hot-wrap .handsontable .htCore thead th,
        .datagrid-hot-wrap .handsontable .ht_clone_top thead th,
        .datagrid-hot-wrap .handsontable .ht_clone_inline_start thead th {
          background: #fafafa;
          color: rgba(0, 0, 0, 0.65);
          font-weight: 600;
        }
        .datagrid-hot-wrap .handsontable tbody th {
          background: #fff;
          color: rgba(0, 0, 0, 0.65);
        }
      `}</style>
    </div>
  );
}