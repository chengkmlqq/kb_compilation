"use client";

/**
 * 查询结果网格（对齐 ds datagrid/components/table-viewer.tsx）：
 *  - Handsontable 只读网格（复制表格/选择高亮/列宽拖动）
 *  - 顶部 Pagination（50~1000 页大小可选）
 *
 * Handsontable 使用 non-commercial-and-evaluation license（与 ds 一致）。
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { App, Button, Pagination } from "antd";
import { CopyOutlined } from "@ant-design/icons";
import { HotTable, type HotTableRef } from "@handsontable/react-wrapper";
import { registerAllModules } from "handsontable/registry";
import "handsontable/styles/handsontable.min.css";
import "handsontable/styles/ht-theme-main.min.css";
import type { ResultTab } from "../store/use-datagrid-store";
import { copyText } from "@/lib/clipboard";

registerAllModules();

function normalizeValue(v: unknown): string | number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "object") return JSON.stringify(v);
  return v as string | number;
}

export default function TableViewer({ tabData }: { tabData: ResultTab }) {
  const { message } = App.useApp();
  const hotRef = useRef<HotTableRef | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [pageNum, setPageNum] = useState(tabData.filter.pageNum || 1);
  const [pageSize, setPageSize] = useState(tabData.filter.pageSize || 50);
  const [hotHeight, setHotHeight] = useState(0);

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
    if (await copyText(lines.join("\n"))) {
      message.success("已复制当前表格（含表头）");
    } else {
      message.error("复制失败，请检查浏览器剪贴板权限");
    }
  };

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0, flexDirection: "column", overflow: "hidden", background: "#fff" }}>
      {/* 工具条：分页 + 复制 */}
      <div style={{ display: "flex", flexShrink: 0, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 8, borderBottom: "1px solid #e8eaed", background: "#fff", padding: "6px 12px" }}>
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
        </div>
      </div>
      {/* Handsontable 网格 */}
      <div ref={wrapRef} className="datagrid-hot-wrap" style={{ minHeight: 0, flex: 1, overflow: "hidden" }}>
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