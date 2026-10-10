"use client";

/**
 * 表结构 tab（对齐 ds datagrid/components/table-structure.tsx）：
 *  - 表信息概览（行数/大小/引擎等 table-info 键值卡）
 *  - 字段表格（名称/类型/可空/默认/注释）+ DDL 代码块
 *
 * kb 后端契约：
 *  columns → { success, rows: [{name, data_type, is_nullable, default_value, comment}] }
 *  ddl     → { success, ddl }
 *  table-info → { success, rows: [{key: value}] }（kb datagrid.get_table_info）
 */
import React from "react";
import { Col, Row, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DatagridColumn } from "@/lib/api";
import type { TableTab } from "../store/use-datagrid-store";

const { Text } = Typography;

interface TableStructureProps {
  tab: TableTab;
}

export default function TableStructure({ tab }: TableStructureProps) {
  const columns = (tab.columns || []) as unknown as DatagridColumn[];
  const infoRows = (tab.general || []) as Array<Record<string, unknown>>;

  const structColumns: ColumnsType<DatagridColumn> = [
    { title: "字段", dataIndex: "name", width: 160 },
    { title: "类型", dataIndex: "data_type", width: 140, render: (v: string) => <Tag>{v || "-"}</Tag> },
    {
      title: "可空",
      dataIndex: "is_nullable",
      width: 80,
      align: "center",
      render: (v: unknown) =>
        String(v).toLowerCase().startsWith("no") || String(v) === "false" ? "否" : "是",
    },
    { title: "默认值", dataIndex: "default_value", width: 140, ellipsis: true },
    { title: "注释", dataIndex: "comment", ellipsis: true },
  ];

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-hidden bg-white p-2">
      {infoRows.length > 0 && (
        <Row gutter={8} style={{ flexShrink: 0 }}>
          {infoRows.map((info, i) => (
            <Col span={6} key={i}>
              <div
                style={{
                  border: "1px solid #e8eaed",
                  borderRadius: 6,
                  padding: "6px 12px",
                  background: "#fafafa",
                }}
              >
                <div style={{ fontSize: 11, color: "#999" }}>{String(Object.keys(info)[0] || "")}</div>
                <Text strong>{String(Object.values(info)[0] ?? "-")}</Text>
              </div>
            </Col>
          ))}
        </Row>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        <Table<DatagridColumn>
          size="small"
          rowKey="__key"
          pagination={false}
          columns={structColumns}
          dataSource={columns.map((c, i) => ({ ...c, __key: i }))}
        />
      </div>
      {tab.DDL ? (
        <pre
          style={{
            flexShrink: 0,
            maxHeight: 160,
            overflow: "auto",
            margin: 0,
            background: "#0F172A",
            color: "#E2E8F0",
            padding: 12,
            borderRadius: 6,
            fontSize: 12,
            fontFamily: "Consolas, Monaco, monospace",
          }}
        >
          {tab.DDL}
        </pre>
      ) : null}
    </div>
  );
}