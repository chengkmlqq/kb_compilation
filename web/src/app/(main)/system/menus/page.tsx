"use client";

/**
 * 菜单管理（左树右表，完全对齐 data-synth system/menus 的布局/交互/样式；
 * 保留 kb 特有的「API 权限」编辑——置于菜单编辑抽屉底部，保存菜单时一并保存）。
 *
 * 数据契约（kb 下划线风格）：
 *   apiListMenus()    -> { items: SysMenuItem[] }（全量，树与表格均由此构建/过滤）
 *   apiCreateMenu / apiUpdateMenu / apiDeleteMenu
 *   apiGetMenuApis / apiSaveMenuApis（菜单级 API 白名单）
 *   apiListMenuIcons() -> string[]（图标名目录）
 * 扩展字段 openType/linkType/fetchMode/route/routeParam/url 放 menu_ext_conf（JSON 字符串）。
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Divider,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Layout,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  TreeSelect,
} from "antd";
import type { DataNode } from "antd/es/tree";
import type { ColumnsType } from "antd/es/table";
import {
  DeleteOutlined,
  EditOutlined,
  FileOutlined,
  FolderOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import * as AntdIcons from "@ant-design/icons";
import { ModoPage } from "@/components/biz/modo-page";
import { ModoButton } from "@/components/biz/modo-button";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoTree } from "@/components/biz/modo-tree";
import { ModoTable } from "@/components/biz/modo-table";
import { PageFilter } from "@/components/biz/page-filter";
import { ModoInput, ModoSearch, ModoTextArea } from "@/components/biz/modo-input";
import { ModoSelect } from "@/components/biz/modo-select";
import { ModoRadio } from "@/components/biz/modo-radio";
import {
  apiCreateMenu,
  apiDeleteMenu,
  apiGetMenuApis,
  apiListMenuIcons,
  apiListMenus,
  apiSaveMenuApis,
  apiUpdateMenu,
  SysApiPerm,
  SysMenuItem,
  SysMenuWrite,
} from "@/lib/api";
import { collectSubtreeIds, menusToTreeData } from "../_shared";

const { Sider, Content } = Layout;

/** SysMenuItem 接口未声明 menu_ext_conf，但后端返回包含该字段，扩展读取用 */
interface MenuItemExt extends SysMenuItem {
  menu_ext_conf?: string | null;
}

const API_METHOD_OPTIONS = ["GET", "POST", "PUT", "DELETE", "PATCH", "*"];

/** @ant-design/icons 动态取组件（图标名目录来自 apiListMenuIcons） */
const ICON_COMPONENTS = AntdIcons as unknown as Record<
  string,
  React.ComponentType<{ style?: React.CSSProperties }>
>;

/** 菜单数组 -> 左侧树节点（递归 parent_id，按 sort_num 排序） */
function buildMenuTree(menus: MenuItemExt[], parentId: string | null = null): DataNode[] {
  return menus
    .filter((m) => (m.parent_id || null) === parentId)
    .sort((a, b) => (a.sort_num ?? 0) - (b.sort_num ?? 0))
    .map((m) => {
      const children = buildMenuTree(menus, m.menu_id || null);
      return {
        key: m.menu_id as string,
        title: m.menu_label || m.menu_name || "",
        icon: m.menu_type === "frame" ? <FolderOutlined /> : <FileOutlined />,
        children: children.length > 0 ? children : undefined,
        isLeaf: children.length === 0,
      };
    });
}

/** 树节点扁平化（搜索展开用：含父级关系） */
function flattenTree(nodes: DataNode[]): { key: string; title: string; parentId: string | null }[] {
  const out: { key: string; title: string; parentId: string | null }[] = [];
  const walk = (list: DataNode[], parentId: string | null) => {
    for (const n of list) {
      out.push({ key: String(n.key), title: String(n.title || ""), parentId });
      if (n.children && n.children.length > 0) walk(n.children, String(n.key));
    }
  };
  walk(nodes, null);
  return out;
}

/** 在树中查找 key 的父级（搜索展开用） */
function getParentKey(key: React.Key, tree: DataNode[]): React.Key | null {
  for (const node of tree) {
    if (node.children) {
      if (node.children.some((item) => item.key === key)) return node.key;
      const found = getParentKey(key, node.children);
      if (found) return found;
    }
  }
  return null;
}

/**
 * API 权限编辑器（kb 独有功能，保留自原页面）：
 * 行 = {path, method}，空列表表示不限制。
 */
function ApiPermEditor({
  value,
  onChange,
}: {
  value?: SysApiPerm[];
  onChange?: (v: SysApiPerm[]) => void;
}) {
  const rows = value || [];
  const update = (i: number, patch: Partial<SysApiPerm>) => {
    onChange?.(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  };
  const remove = (i: number) => {
    onChange?.(rows.filter((_, idx) => idx !== i));
  };
  const add = () => {
    onChange?.([...rows, { path: "", method: "GET" }]);
  };
  return (
    <div>
      <Table
        rowKey={(_, i) => String(i)}
        size="small"
        pagination={false}
        dataSource={rows}
        locale={{ emptyText: "未配置 API 权限（保持现状，不额外限制）" }}
        columns={[
          {
            title: "API 路径",
            dataIndex: "path",
            render: (v: string, _r, i: number) => (
              <Input
                placeholder="/api/v1/kbs/search"
                value={v}
                onChange={(e) => update(i, { path: e.target.value })}
              />
            ),
          },
          {
            title: "方法",
            dataIndex: "method",
            width: 130,
            render: (v: string, _r, i: number) => (
              <Select
                value={v || "GET"}
                options={API_METHOD_OPTIONS.map((m) => ({ label: m, value: m }))}
                onChange={(m) => update(i, { method: m })}
              />
            ),
          },
          {
            title: "",
            width: 56,
            render: (_v, _r, i: number) => (
              <Button size="small" type="link" danger onClick={() => remove(i)}>
                删除
              </Button>
            ),
          },
        ]}
        footer={() => (
          <Button size="small" type="dashed" block onClick={add}>
            + 添加 API
          </Button>
        )}
      />
    </div>
  );
}

export default function SystemMenusPage() {
  const { message, modal } = App.useApp();

  // ---- 数据 ----
  const [items, setItems] = useState<MenuItemExt[]>([]);
  const [loading, setLoading] = useState(false); // 表格加载
  const [treeLoading, setTreeLoading] = useState(false); // 树加载

  // ---- 图标目录 ----
  const [menuIcons, setMenuIcons] = useState<string[]>([]);
  const [menuIconsLoading, setMenuIconsLoading] = useState(false);

  // ---- 左侧菜单树 ----
  const [treeData, setTreeData] = useState<DataNode[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<React.Key[]>([]);
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
  const [autoExpandParent, setAutoExpandParent] = useState(true);
  const [searchValue, setSearchValue] = useState("");
  const [flatData, setFlatData] = useState<{ key: string; title: string; parentId: string | null }[]>([]);

  // ---- 树右键菜单（Dropdown 定位） ----
  const [contextMenu, setContextMenu] = useState<{
    visible: boolean;
    x: number;
    y: number;
    nodeKey: string | null;
  }>({ visible: false, x: 0, y: 0, nodeKey: null });

  // ---- 右侧表格（客户端过滤 + 分页） ----
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [searchForm] = Form.useForm();
  const [searchParams, setSearchParams] = useState<Record<string, unknown>>({});

  // ---- 新建/编辑抽屉 ----
  const [drawerVisible, setDrawerVisible] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"create" | "edit">("create");
  const [currentMenu, setCurrentMenu] = useState<MenuItemExt | null>(null);
  const [menuForm] = Form.useForm();
  const [formKey, setFormKey] = useState(0); // 重置表单用
  const [savingMenu, setSavingMenu] = useState(false);

  // ---- API 权限（kb 独有；编辑抽屉底部） ----
  const [apiPerms, setApiPerms] = useState<SysApiPerm[]>([]);
  const [apiPermsLoading, setApiPermsLoading] = useState(false);

  // 加载菜单全量数据（树 + 表格数据源）
  const load = useCallback(async () => {
    setLoading(true);
    setTreeLoading(true);
    try {
      const res = await apiListMenus();
      if (res.success) {
        const list = (res.data?.items || []) as MenuItemExt[];
        setItems(list);
        const tree = buildMenuTree(list);
        setTreeData(tree);
        // 自动展开第一级
        setExpandedKeys(tree.map((n) => n.key));
        // 扁平化（搜索展开用）
        setFlatData(flattenTree(tree));
      } else {
        message.error(res.message || "获取菜单列表失败");
      }
    } finally {
      setLoading(false);
      setTreeLoading(false);
    }
  }, [message]);

  // 加载图标目录（菜单 icon 下拉选项）
  const loadIcons = useCallback(async () => {
    setMenuIconsLoading(true);
    try {
      const res = await apiListMenuIcons();
      if (res.success && Array.isArray(res.data)) setMenuIcons(res.data);
    } catch {
      /* ignore */
    } finally {
      setMenuIconsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadIcons();
  }, [load, loadIcons]);

  // 表格数据：树选中 parent_id 过滤 + 筛选条件 + 客户端分页
  const filteredItems = useMemo(() => {
    let list = items;
    const parentId = selectedKeys.length > 0 ? String(selectedKeys[0]) : undefined;
    if (parentId) list = list.filter((m) => (m.parent_id || null) === parentId);
    const kwName = typeof searchParams.menu_name === "string" ? searchParams.menu_name.trim().toLowerCase() : "";
    const kwLabel = typeof searchParams.menu_label === "string" ? searchParams.menu_label.trim().toLowerCase() : "";
    if (kwName) list = list.filter((m) => (m.menu_name || "").toLowerCase().includes(kwName));
    if (kwLabel) list = list.filter((m) => (m.menu_label || "").toLowerCase().includes(kwLabel));
    return list;
  }, [items, selectedKeys, searchParams]);

  const pagedItems = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredItems.slice(start, start + pageSize);
  }, [filteredItems, currentPage, pageSize]);

  // 树节点选中 -> 表格 parentId 过滤
  const onSelect = (keys: React.Key[]) => {
    setSelectedKeys(keys);
    setCurrentPage(1);
  };

  const onExpand = (keys: React.Key[]) => {
    setExpandedKeys(keys);
    setAutoExpandParent(false);
  };

  // 树搜索：输入高亮 + 展开匹配节点的父级
  const onSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { value } = e.target;
    if (!value) {
      setExpandedKeys([]);
      setSearchValue("");
      setAutoExpandParent(false);
      return;
    }
    const newExpandedKeys = flatData
      .map((item) => (item.title.indexOf(value) > -1 ? getParentKey(item.key, treeData) : null))
      .filter((k, i, self): k is React.Key => !!k && self.indexOf(k) === i);
    setExpandedKeys(newExpandedKeys);
    setSearchValue(value);
    setAutoExpandParent(true);
  };

  // ---- 筛选 ----
  const handleSearch = (values: Record<string, unknown>) => {
    setSearchParams(values);
    setCurrentPage(1);
  };

  const handleReset = () => {
    setCurrentPage(1);
  };

  // ---- 树右键菜单 ----
  const handleTreeRightClick = ({ event, node }: { event: React.MouseEvent; node: any }) => {
    event.preventDefault();
    event.stopPropagation();
    setContextMenu({ visible: true, x: event.clientX, y: event.clientY, nodeKey: String(node.key) });
  };

  const handleContextMenuClose = () => {
    setContextMenu((prev) => ({ ...prev, visible: false }));
  };

  const handleTreeEditNode = (menuId: string) => {
    const record = items.find((m) => m.menu_id === menuId);
    if (record) openEdit(record);
  };

  // ---- 新建 / 编辑 ----
  const openCreate = (parentId?: string) => {
    setDrawerMode("create");
    setCurrentMenu(null);
    setApiPerms([]);
    setFormKey((p) => p + 1);
    menuForm.resetFields();
    // 树有选中节点时默认挂到该节点下
    const pid = parentId || (selectedKeys.length > 0 ? String(selectedKeys[0]) : undefined);
    menuForm.setFieldsValue({
      state: "1",
      sort_num: 1,
      menu_type: "frame",
      openType: "0",
      linkType: "inner",
      fetchMode: "route",
      ...(pid ? { parent_id: pid } : {}),
    });
    setDrawerVisible(true);
  };

  const openEdit = (m: MenuItemExt) => {
    setDrawerMode("edit");
    setCurrentMenu(m);
    setApiPerms([]);
    setFormKey((p) => p + 1);
    menuForm.resetFields();

    // 解析扩展字段（menu_ext_conf JSON）
    let extConf: Record<string, unknown> = {};
    try {
      if (m.menu_ext_conf) extConf = JSON.parse(m.menu_ext_conf) as Record<string, unknown>;
    } catch (e) {
      console.error("Parse menu_ext_conf error", e);
    }

    menuForm.setFieldsValue({
      menu_name: m.menu_name,
      menu_label: m.menu_label,
      parent_id: m.parent_id || undefined,
      sort_num: m.sort_num ?? 1,
      menu_icon: m.menu_icon,
      route: m.route,
      state: m.state || "1",
      menu_type: m.menu_type || "frame",
      menu_descr: m.menu_descr,
      openType: extConf.openType || "0",
      linkType: extConf.linkType || "inner",
      fetchMode: extConf.fetchMode || "route",
      routeParam: extConf.routeParam,
      url: extConf.url,
    });
    setDrawerVisible(true);

    // 载入该菜单已配置的 API 权限
    setApiPermsLoading(true);
    void (async () => {
      try {
        const res = await apiGetMenuApis(m.menu_id as string);
        if (res.success && res.data) setApiPerms(res.data.apis || []);
        else setApiPerms([]);
      } catch {
        /* ignore */
      } finally {
        setApiPermsLoading(false);
      }
    })();
  };

  // 保存菜单（编辑模式下随菜单一并保存 API 权限）
  const doSaveMenu = async () => {
    let values: Record<string, any>;
    try {
      values = await menuForm.validateFields();
    } catch {
      return; // 校验失败，antd 已高亮错误项
    }
    setSavingMenu(true);
    try {
      const { openType, linkType, fetchMode, route, routeParam, url, ...base } = values;
      const extConf: Record<string, unknown> = {
        openType: openType ?? "0",
        linkType: linkType ?? "inner",
        fetchMode: fetchMode ?? "route",
        route: route ?? null,
        routeParam: routeParam ?? null,
        url: url ?? null,
      };
      const payload: SysMenuWrite = {
        menu_name: base.menu_name,
        menu_label: base.menu_label,
        parent_id: base.parent_id || null,
        sort_num: base.sort_num ?? 1,
        menu_icon: base.menu_icon || null,
        route: route || null,
        state: base.state,
        menu_type: base.menu_type,
        menu_descr: base.menu_descr || null,
        menu_ext_conf: JSON.stringify(extConf),
      };
      const res = currentMenu?.menu_id
        ? await apiUpdateMenu(currentMenu.menu_id, payload)
        : await apiCreateMenu(payload);
      if (res.success) {
        // kb 独有：编辑模式下随菜单一并保存 API 权限
        if (currentMenu?.menu_id) {
          const apiRes = await apiSaveMenuApis(currentMenu.menu_id, apiPerms);
          if (!apiRes.success) {
            message.warning(apiRes.message || "菜单已保存，但 API 权限保存失败");
          }
        }
        message.success(currentMenu?.menu_id ? "菜单已更新" : "菜单已创建");
        setDrawerVisible(false);
        await load();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSavingMenu(false);
    }
  };

  const doDeleteMenu = async (menuId: string) => {
    const res = await apiDeleteMenu(menuId);
    if (res.success) {
      message.success("菜单已删除");
      await load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const handleDeleteConfirm = (menuId: string) => {
    const target = items.find((m) => m.menu_id === menuId);
    const name = target?.menu_label || target?.menu_name || "该菜单";
    modal.confirm({
      title: "确定删除该菜单？",
      content: `菜单「${name}」删除后不可恢复，请确认是否继续。`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => void doDeleteMenu(menuId),
    });
  };

  // 表单父级选择树（编辑时禁用「自身+子孙」，防止成环）
  const treeSelectData = currentMenu?.menu_id
    ? menusToTreeData(items, collectSubtreeIds(items, currentMenu.menu_id))
    : menusToTreeData(items);

  // ---- 表格列 ----
  const columns: ColumnsType<MenuItemExt> = [
    {
      title: "模块编码",
      dataIndex: "menu_name",
      key: "menu_name",
      width: 220,
      render: (text?: string) => (
        <Tooltip title={text} mouseEnterDelay={0.3}>
          <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {text}
          </span>
        </Tooltip>
      ),
    },
    {
      title: "模块中文名",
      dataIndex: "menu_label",
      key: "menu_label",
      width: 220,
      render: (text?: string) => (
        <Tooltip title={text} mouseEnterDelay={0.3}>
          <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {text}
          </span>
        </Tooltip>
      ),
    },
    {
      title: "状态",
      dataIndex: "state",
      key: "state",
      width: 100,
      render: (state?: string) => (
        <Tag color={state === "1" ? "success" : "default"}>{state === "1" ? "已发布" : "未发布"}</Tag>
      ),
    },
    {
      title: "排序",
      dataIndex: "sort_num",
      key: "sort_num",
      width: 100,
    },
    {
      title: "操作",
      key: "action",
      width: 160,
      fixed: "right",
      render: (_, record) => (
        <ModoActionGroup
          actions={[
            { key: "edit", label: "编辑", onClick: () => openEdit(record) },
            { key: "delete", label: "删除", danger: true, onClick: () => handleDeleteConfirm(record.menu_id as string) },
          ]}
        />
      ),
    },
  ];

  return (
    <ModoPage>
      {/* 左树右表布局（对齐 data-synth）：Sider 240px 树区 + Content 表格区 */}
      <Layout style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "row", background: "transparent" }}>
        <Sider
          width={240}
          theme="light"
          style={{
            borderRight: "1px solid #E3E9EF",
            background: "transparent",
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            height: "100%",
            overflow: "hidden",
            flexShrink: 0,
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%" }}>
            {/* 树搜索框 */}
            <div style={{ padding: 16, flexShrink: 0 }}>
              <ModoSearch placeholder="搜索菜单" variant="filled" onChange={onSearchChange} />
            </div>
            {/* 树区（占满剩余高度，超高滚动） */}
            <div style={{ flex: 1, overflowY: "auto", padding: "0 8px 16px", minHeight: 0 }}>
              <ModoTree
                fieldNames={{ key: "key", title: "title" }}
                treeData={treeData}
                selectedKeys={selectedKeys}
                onSelect={onSelect}
                onExpand={onExpand}
                expandedKeys={expandedKeys}
                autoExpandParent={autoExpandParent}
                onRightClick={handleTreeRightClick}
                loading={treeLoading}
                titleRender={(node: any) => {
                  const strTitle = node.title as string;
                  const index = strTitle.indexOf(searchValue);
                  if (index > -1) {
                    const beforeStr = strTitle.substring(0, index);
                    const afterStr = strTitle.slice(index + searchValue.length);
                    return (
                      <span>
                        {beforeStr}
                        <span style={{ color: "#3261CE" }}>{searchValue}</span>
                        {afterStr}
                      </span>
                    );
                  }
                  return <span>{strTitle}</span>;
                }}
              />
            </div>
          </div>
          {/* 树右键菜单（Dropdown 定位固定点） */}
          {contextMenu.visible && (
            <Dropdown
              open
              onOpenChange={(open) => {
                if (!open) handleContextMenuClose();
              }}
              menu={{
                items: [
                  {
                    key: "add",
                    icon: <PlusOutlined />,
                    label: "新增子节点",
                    onClick: () => {
                      handleContextMenuClose();
                      openCreate(contextMenu.nodeKey || undefined);
                    },
                  },
                  {
                    key: "edit",
                    icon: <EditOutlined />,
                    label: "编辑节点",
                    onClick: () => {
                      handleContextMenuClose();
                      if (contextMenu.nodeKey) handleTreeEditNode(contextMenu.nodeKey);
                    },
                  },
                  {
                    key: "delete",
                    icon: <DeleteOutlined />,
                    label: "删除节点",
                    danger: true,
                    onClick: () => {
                      handleContextMenuClose();
                      if (contextMenu.nodeKey) handleDeleteConfirm(contextMenu.nodeKey);
                    },
                  },
                ],
              }}
            >
              <div style={{ position: "fixed", left: contextMenu.x, top: contextMenu.y, width: 1, height: 1 }} />
            </Dropdown>
          )}
        </Sider>

        {/* 右侧：筛选 + 工具栏 + 表格 + 吸底分页 */}
        <Content
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            minHeight: 0,
            background: "#fff",
          }}
        >
          <div style={{ flex: 1, width: "100%", overflow: "hidden", display: "flex", flexDirection: "column" }}>
            <PageFilter
              form={searchForm}
              onSearch={handleSearch}
              onReset={handleReset}
              searchParams={searchParams}
              setSearchParams={setSearchParams}
              labelMap={{ menu_name: "模块编码", menu_label: "模块中文名" }}
            >
              <Form.Item name="menu_name" label="模块编码" style={{ marginBottom: 0 }}>
                <ModoInput placeholder="输入模块编码" allowClear />
              </Form.Item>
              <Form.Item name="menu_label" label="模块中文名" style={{ marginBottom: 0 }}>
                <ModoInput placeholder="输入模块中文名" allowClear />
              </Form.Item>
            </PageFilter>

            <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0, background: "#fff" }}>
              {/* 工具栏 */}
              <div style={{ flexShrink: 0, padding: "10px 16px 0 16px", marginBottom: 10 }}>
                <ModoButton type="primary" icon={<PlusOutlined />} onClick={() => openCreate()}>
                  新建
                </ModoButton>
              </div>
              {/* 表格（占满剩余高度，表体内滚动） */}
              <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                <ModoTable
                  columns={columns}
                  dataSource={pagedItems}
                  rowKey="menu_id"
                  loading={loading}
                  scroll={{ x: "100%" }}
                />
              </div>
              {/* 分页吸底 */}
              <div style={{ flexShrink: 0, background: "#fff" }}>
                <ModoPagination
                  current={currentPage}
                  pageSize={pageSize}
                  total={filteredItems.length}
                  showSizeChanger
                  showQuickJumper
                  showTotal={(t) => `共 ${t} 条`}
                  onChange={(page, size) => {
                    setCurrentPage(page);
                    setPageSize(size);
                  }}
                />
              </div>
            </div>
          </div>
        </Content>
      </Layout>

      {/* 新建/编辑抽屉（宽 500；编辑模式底部含 API 权限） */}
      <ModoDrawer
        title={drawerMode === "create" ? "新建菜单" : "编辑菜单"}
        open={drawerVisible}
        onCancel={() => setDrawerVisible(false)}
        onOk={() => void doSaveMenu()}
        confirmLoading={savingMenu}
        okText="保存"
        width={500}
      >
        <Form
          key={formKey}
          form={menuForm}
          layout="vertical"
          initialValues={{ state: "1", sort_num: 1, openType: "0", linkType: "inner", fetchMode: "route", menu_type: "frame" }}
          validateTrigger={false}
          autoComplete="off"
        >
          <Form.Item name="menu_name" label="模块编码" rules={[{ required: true, message: "请输入模块编码" }]}>
            <ModoInput placeholder="请输入模块编码" />
          </Form.Item>
          <Form.Item name="menu_label" label="模块中文名" rules={[{ required: true, message: "请输入模块中文名" }]}>
            <ModoInput placeholder="请输入模块中文名" />
          </Form.Item>
          <Form.Item name="parent_id" label="父模块">
            <TreeSelect
              treeData={treeSelectData}
              placeholder="请选择父模块（根节点留空）"
              allowClear
              treeDefaultExpandAll
              fieldNames={{ label: "title", value: "key", children: "children" }}
            />
          </Form.Item>
          <Form.Item name="sort_num" label="排序">
            <InputNumber style={{ width: "100%" }} min={1} variant="filled" />
          </Form.Item>
          <Form.Item name="menu_icon" label="图标" extra="图标名来自菜单图标目录">
            <ModoSelect
              placeholder="选择图标"
              allowClear
              showSearch
              loading={menuIconsLoading}
              optionFilterProp="label"
              options={menuIcons.map((name) => ({ label: name, value: name }))}
              optionRender={(option) => {
                const Comp = ICON_COMPONENTS[option.value as string];
                return (
                  <Space size={6}>
                    {Comp ? <Comp style={{ fontSize: 14 }} /> : null}
                    <span>{option.label as React.ReactNode}</span>
                  </Space>
                );
              }}
            />
          </Form.Item>

          <Form.Item name="state" label="状态">
            <ModoRadio.Group optionType="button" buttonStyle="solid">
              <ModoRadio.Button value="1">发布</ModoRadio.Button>
              <ModoRadio.Button value="0">未发布</ModoRadio.Button>
            </ModoRadio.Group>
          </Form.Item>

          <Form.Item name="menu_type" label="菜单类型">
            <ModoRadio.Group optionType="button" buttonStyle="solid">
              <ModoRadio.Button value="frame">侧边菜单</ModoRadio.Button>
              <ModoRadio.Button value="nav">顶栏菜单</ModoRadio.Button>
            </ModoRadio.Group>
          </Form.Item>

          <Form.Item name="openType" label="打开类型">
            <ModoRadio.Group optionType="button" buttonStyle="solid">
              <ModoRadio.Button value="0">内嵌</ModoRadio.Button>
              <ModoRadio.Button value="1">新窗口</ModoRadio.Button>
              <ModoRadio.Button value="2">弹窗</ModoRadio.Button>
            </ModoRadio.Group>
          </Form.Item>

          {/* 条件字段：openType=1 仅 URL；否则 类型(linkType) -> url 时 URL / route 时 路由+参数 */}
          <Form.Item noStyle shouldUpdate={(prev, curr) => prev.openType !== curr.openType}>
            {({ getFieldValue }) => {
              const openType = getFieldValue("openType");
              if (openType === "1") {
                return (
                  <Form.Item name="url" label="URL地址">
                    <ModoInput placeholder="请输入跳转链接地址" />
                  </Form.Item>
                );
              }
              return (
                <>
                  <Form.Item name="linkType" label="类型">
                    <ModoRadio.Group optionType="button" buttonStyle="solid">
                      <ModoRadio.Button value="inner">内部(inner)</ModoRadio.Button>
                      <ModoRadio.Button value="outer">外部(outer)</ModoRadio.Button>
                      <ModoRadio.Button value="url">URL</ModoRadio.Button>
                    </ModoRadio.Group>
                  </Form.Item>

                  <Form.Item noStyle shouldUpdate={(prev, curr) => prev.linkType !== curr.linkType}>
                    {({ getFieldValue }) => {
                      const linkType = getFieldValue("linkType");
                      if (linkType === "url") {
                        return (
                          <Form.Item name="url" label="URL地址" key="url-input">
                            <ModoInput placeholder="请输入外部链接地址" />
                          </Form.Item>
                        );
                      }
                      return (
                        <React.Fragment key="inner-path-config">
                          <Form.Item name="fetchMode" label="获取形式">
                            <ModoRadio.Group optionType="button" buttonStyle="solid">
                              <ModoRadio.Button value="route">路由(route)</ModoRadio.Button>
                            </ModoRadio.Group>
                          </Form.Item>
                          <Form.Item name="route" label="路由">
                            <ModoInput placeholder={linkType === "outer" ? "appName/routeName" : "路由路径"} />
                          </Form.Item>
                          <Form.Item name="routeParam" label="路由参数">
                            <ModoInput placeholder="JSON parameters" />
                          </Form.Item>
                        </React.Fragment>
                      );
                    }}
                  </Form.Item>
                </>
              );
            }}
          </Form.Item>

          <Form.Item name="menu_descr" label="描述">
            <ModoTextArea rows={3} />
          </Form.Item>

          {/* kb 独有：API 权限（编辑模式可配置，随保存一并提交） */}
          <Divider style={{ margin: "8px 0 12px" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ fontWeight: 600, color: "#242E43" }}>API 权限</span>
            {drawerMode === "edit" && (
              <span style={{ fontSize: 12, color: "#79879C" }}>保存菜单时一并保存该菜单的 API 白名单</span>
            )}
          </div>
          {drawerMode === "edit" ? (
            <>
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message="配置后，拥有该菜单的角色访问该页面 API 时仅放行列表内的接口（未配置保持现状不限制）。API 路径为该页面对应 /api/v1 下的接口前缀，方法可选 * 通配。"
              />
              <Spin spinning={apiPermsLoading}>
                <ApiPermEditor value={apiPerms} onChange={setApiPerms} />
              </Spin>
            </>
          ) : (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="菜单创建后可通过「编辑」配置该菜单的 API 权限。"
            />
          )}
        </Form>
      </ModoDrawer>
    </ModoPage>
  );
}