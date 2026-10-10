"use client";

/**
 * 数据查询左侧目录树（对齐 ds datagrid/components/db-tree.tsx）：
 *  - 数据源分类（dsLabel）→ 数据源节点（schema - profile）
 *  - 展开数据源 → 懒加载 表/视图/函数/过程/序列 五个文件夹 → 节点
 *  - 双击表节点 → 打开 表结构 tab；双击 schema（SQL 编辑器 tab）→ 打开编辑 tab
 *
 * kb 后端契约（/api/v1/datagrid/*）：
 *  tables/views/functions/procedures/sequences 均返回 { success, rows: [{name,...}] }
 *  columns 返回 { success, rows: [{name,data_type,is_nullable,default_value,comment,...}] }
 *  ddl 返回 { success, ddl }
 */
import React, { useCallback, useMemo, useRef, useState } from "react";
import { App, Input, Tree } from "antd";
import type { DataNode as AntDataNode } from "antd/es/tree";
import {
  DatabaseOutlined,
  FileTextOutlined,
  FunctionOutlined,
  MoreOutlined,
  ReloadOutlined,
  TableOutlined,
} from "@ant-design/icons";
import {
  apiDatagridColumns,
  apiDatagridDdl,
  apiDatagridFunctions,
  apiDatagridProcedures,
  apiDatagridSequences,
  apiDatagridTables,
  apiDatagridViews,
  DatagridDsItem,
} from "@/lib/api";
import { useDataGridStore } from "../store/use-datagrid-store";

const { DirectoryTree } = Tree;

interface DbNodeData {
  type: "category" | "datasource" | "folder" | "table" | "view" | "function" | "procedure" | "sequence";
  dsName?: string;
  schema?: string;
  name?: string;
  [key: string]: unknown;
}

interface DataNode extends AntDataNode {
  data?: DbNodeData;
  children?: DataNode[];
}

interface DbTreeProps {
  data: DatagridDsItem[];
}

function nodeId(kind: string, dsName: string, schema: string, name = ""): string {
  return `${dsName}::${schema}::${kind}::${name}`;
}

/** 表/视图/函数/过程/序列 文件夹节点（懒加载时挂 children） */
const OBJECT_FOLDERS: Array<{ key: string; label: string; icon: React.ReactNode }> = [
  { key: "tables", label: "表", icon: <TableOutlined /> },
  { key: "views", label: "视图", icon: <FileTextOutlined /> },
  { key: "functions", label: "函数", icon: <FunctionOutlined /> },
  { key: "procedures", label: "过程", icon: <MoreOutlined /> },
  { key: "sequences", label: "序列", icon: <MoreOutlined /> },
];

export default function DbTree({ data }: DbTreeProps) {
  const { message } = App.useApp();
  const [treeData, setTreeData] = useState<DataNode[]>(() =>
    (data || []).map((item, index) => ({
      title: item.dsLabel || item.dsName,
      key: `category-${index}`,
      icon: <DatabaseOutlined />,
      isLeaf: false,
      data: { type: "category" as const, dsName: item.dsName },
      children: [
        {
          title: item.dsLabel || item.dsName,
          key: `ds-${item.dsName}`,
          icon: <DatabaseOutlined />,
          isLeaf: false,
          data: { type: "datasource" as const, dsName: item.dsName, schema: item.schema || "" },
          children: undefined, // 懒加载
        },
      ],
    })),
  );
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>(() =>
    (data || []).map((_item, index) => `category-${index}`),
  );
  const [search, setSearch] = useState("");
  const listCache = useRef<Map<string, Array<Record<string, unknown>>>>(new Map());

  const { addDbToolTab } = useDataGridStore();

  const getDsEntry = (dsName: string): DatagridDsItem | undefined =>
    (data || []).find((d) => d.dsName === dsName);

  const loadFolder = useCallback(
    async (dsName: string, kind: string): Promise<Array<Record<string, unknown>>> => {
      const cacheKey = `${dsName}::${kind}`;
      const cached = listCache.current.get(cacheKey);
      if (cached) return cached;
      let rows: Array<Record<string, unknown>> = [];
      try {
        const schema = getDsEntry(dsName)?.schema;
        const res =
          kind === "tables"
            ? await apiDatagridTables(dsName, schema)
            : kind === "views"
              ? await apiDatagridViews(dsName, schema)
              : kind === "functions"
                ? await apiDatagridFunctions(dsName, schema)
                : kind === "procedures"
                  ? await apiDatagridProcedures(dsName, schema)
                  : await apiDatagridSequences(dsName, schema);
        rows = (res.rows || []).map((r) => ({ name: String(r.name || "") }));
        rows = rows.filter((r) => r.name);
      } catch (e) {
        message.error(e instanceof Error ? e.message : `加载 ${kind} 失败`);
      }
      listCache.current.set(cacheKey, rows);
      return rows;
    },
    [message, data],
  );

  const onLoadData = async (node: DataNode): Promise<void> => {
    if (node.children && node.children.length > 0) return;
    const d = node.data;
    if (!d) return;

    if (d.type === "datasource") {
      // 展开数据源 → 挂 5 个文件夹
      const dsName = d.dsName || "";
      const schema = d.schema || "";
      const folders: DataNode[] = OBJECT_FOLDERS.map((f) => ({
        title: f.label,
        key: nodeId(f.key, dsName, schema, ""),
        icon: f.icon,
        isLeaf: false,
        data: { type: "folder" as const, dsName, schema, name: f.key },
      }));
      const patch = (list: DataNode[]): DataNode[] =>
        list.map((n) => {
          if (n.key === node.key) return { ...n, children: folders };
          if (n.children) return { ...n, children: patch(n.children) };
          return n;
        });
      setTreeData((prev) => patch(prev));
      setExpandedKeys((prev) => (prev.includes(node.key) ? prev : [...prev, node.key]));
      return;
    }

    if (d.type === "folder") {
      const dsName = d.dsName || "";
      const schema = d.schema || "";
      const kind = d.name || "";
      const rows = await loadFolder(dsName, kind);
      const childNodes: DataNode[] = rows.map((r) => {
        const name = String(r.name);
        const objType = (kind === "tables" ? "table" : kind === "views" ? "view" : kind) as DbNodeData["type"];
        return {
          title: name,
          key: nodeId(kind, dsName, schema, name),
          icon: kind === "tables" ? <TableOutlined /> : <FileTextOutlined />,
          isLeaf: true,
          data: { type: objType, dsName, schema, name },
        };
      });
      const patch = (list: DataNode[]): DataNode[] =>
        list.map((n) => {
          if (n.key === node.key) return { ...n, children: childNodes };
          if (n.children) return { ...n, children: patch(n.children) };
          return n;
        });
      setTreeData((prev) => patch(prev));
      return;
    }
  };

  const openTableTab = async (dsName: string, schema: string, table: string) => {
    try {
      const [c, ddl] = await Promise.all([
        apiDatagridColumns(dsName, table, schema),
        apiDatagridDdl(dsName, table, schema),
      ]);
      const dsEntry = getDsEntry(dsName);
      addDbToolTab({
        id: nodeId("table", dsName, schema, table),
        type: "table",
        label: table,
        index: Date.now(),
        dsName,
        dsSchema: schema,
        dsType: dsEntry?.dsType || "mysql",
        tableName: table,
        general: (c.rows || []),
        columns: (c.rows || []),
        DDL: ddl.ddl || "",
      });
    } catch (e) {
      message.error(e instanceof Error ? e.message : "加载表结构失败");
    }
  };

  const openSchemaTab = (dsName: string, schema: string) => {
    const dsEntry = getDsEntry(dsName);
    addDbToolTab({
      id: nodeId("schema", dsName, schema, ""),
      type: "schema",
      label: `${dsName} - ${schema || dsEntry?.dsType || "default"}`,
      index: Date.now(),
      dsName,
      dsSchema: schema,
      dsType: dsEntry?.dsType || "mysql",
      runSql: "",
      limit: 1000,
    });
  };

  const onSelect = (_keys: React.Key[], info: { node: DataNode }) => {
    const d = info.node.data;
    if (!d) return;
    if (d.type === "table") void openTableTab(d.dsName || "", d.schema || "", d.name || "");
    if (d.type === "view" || d.type === "function" || d.type === "procedure" || d.type === "sequence") {
      message.info(`${d.type}「${d.name}」暂只支持表结构查看`);
    }
  };

  const onDoubleClick = (node: DataNode) => {
    const d = node.data;
    if (d && d.type === "datasource") {
      openSchemaTab(d.dsName || "", d.schema || "");
    }
  };

  const filteredTree = useMemo(() => {
    const kw = search.trim().toLowerCase();
    if (!kw) return treeData;
    // 搜索表名：过滤出匹配的叶子（保留祖先链）
    const walk = (nodes: DataNode[]): DataNode[] => {
      const out: DataNode[] = [];
      for (const n of nodes) {
        const title = String(n.title || "").toLowerCase();
        const children = n.children ? walk(n.children) : undefined;
        if (title.includes(kw) || (children && children.length > 0)) {
          out.push({ ...n, children });
        }
      }
      return out;
    };
    return walk(treeData);
  }, [treeData, search]);

  const reloadDs = () => {
    listCache.current.clear();
    setTreeData((prev) =>
      prev.map((n) =>
        n.data?.type === "datasource" ? { ...n, children: undefined } : n,
      ),
    );
    message.success("已刷新");
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex shrink-0 items-center gap-2 border-b border-[#e8eaed] px-3 py-2">
        <Input
          size="small"
          placeholder="搜索表/视图"
          allowClear
          prefix={<span style={{ color: "#999" }}>🔍</span>}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-[#888] transition-colors hover:bg-[#f0f0f0] hover:text-[#1677ff]"
          title="刷新"
          onClick={reloadDs}
        >
          <ReloadOutlined style={{ fontSize: 12 }} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        <DirectoryTree
          treeData={filteredTree}
          loadData={onLoadData}
          onSelect={onSelect}
          onDoubleClick={(_e, node) => onDoubleClick(node)}
          defaultExpandAll={false}
          expandedKeys={expandedKeys}
          onExpand={(keys) => setExpandedKeys(keys)}
        />
      </div>
    </div>
  );
}