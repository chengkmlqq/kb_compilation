"use client";

/**
 * 工作台多标签（对齐 ds datagrid/components/db-tool.tsx）：
 *  - editable-card 可关闭标签，右键重命名
 *  - schema tab → SQL 编辑器；table tab → 表结构
 *  - 空态：图标 + 引导文案（双击数据源打开 SQL 编辑）
 */
import React, { useState } from "react";
import { Tabs, Dropdown, Input } from "antd";
import { DatabaseOutlined, TableOutlined } from "@ant-design/icons";
import { useDataGridStore, type DbToolTab } from "../store/use-datagrid-store";
import DbCode from "./code";
import TableStructure from "./table-structure";

function TabIcon({ type }: { type: DbToolTab["type"] }) {
  return type === "table" ? <TableOutlined /> : <DatabaseOutlined />;
}

export default function DbTool() {
  const { dbToolTabs, dbToolActiveTab, setDbToolActiveTab, removeDbToolTab, updateDbToolTab } =
    useDataGridStore();
  const [editingTab, setEditingTab] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const renameConfirm = (tabId: string) => {
    if (editValue.trim()) updateDbToolTab(tabId, { label: editValue.trim() });
    setEditingTab(null);
  };

  if (dbToolTabs.length === 0) {
    return (
      <div
        style={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          background: "#fff",
          color: "rgba(0,0,0,.45)",
        }}
      >
        <div
          style={{
            marginBottom: 16,
            width: 64,
            height: 64,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            borderRadius: 12,
            background: "#e6f4ff",
            color: "#1677ff",
          }}
        >
          <DatabaseOutlined style={{ fontSize: 28 }} />
        </div>
        <p style={{ fontSize: 15, fontWeight: 500, color: "rgba(0,0,0,.65)", margin: "0 0 4px" }}>
          从左侧树中选择数据库对象以开始
        </p>
        <p style={{ fontSize: 12, margin: 0 }}>双击数据源，会在这里打开对应工作页签</p>
      </div>
    );
  }

  return (
    <div className="db-tool flex h-full min-h-0 flex-col overflow-hidden bg-white">
      <Tabs
        className="datagrid-tool-tabs h-full"
        type="editable-card"
        activeKey={dbToolActiveTab || undefined}
        onChange={setDbToolActiveTab}
        onEdit={(targetKey, action) => {
          if (action === "remove" && typeof targetKey === "string") removeDbToolTab(targetKey);
        }}
        hideAdd
        style={{ flex: 1, minHeight: 0, height: "100%" }}
        items={dbToolTabs.map((tab) => {
          const isEditing = editingTab === tab.id;
          return {
            key: tab.id,
            label: (
              <Dropdown
                trigger={["contextMenu"]}
                menu={{
                  items: [
                    {
                      key: "rename",
                      label: "重命名",
                      onClick: () => {
                        setEditingTab(tab.id);
                        setEditValue(tab.label);
                      },
                    },
                  ],
                }}
              >
                <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <TabIcon type={tab.type} />
                  {isEditing ? (
                    <Input
                      size="small"
                      value={editValue}
                      autoFocus
                      style={{ width: 150 }}
                      onChange={(e) => setEditValue(e.target.value)}
                      onBlur={() => renameConfirm(tab.id)}
                      onPressEnter={() => renameConfirm(tab.id)}
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <span
                      title={tab.label}
                      style={{
                        fontSize: 13,
                        maxWidth: 160,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        display: "inline-block",
                        verticalAlign: "middle",
                      }}
                    >
                      {tab.label}
                    </span>
                  )}
                </span>
              </Dropdown>
            ),
            closable: true,
            children: tab.type === "table" ? <TableStructure tab={tab} /> : <DbCode tab={tab} />,
            style: { minHeight: 0, overflow: "hidden" },
          };
        })}
      />
    </div>
  );
}