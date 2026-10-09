"use client";

/**
 * 角色管理（2026-10-08 参考用户管理页改造）：
 * 外层 8px padding + Card(标题「角色」/ extra 新建按钮) + inline 筛选表单(角色名称/角色类型/查询/重置)
 * + antd Table 固定视口高度（角色编码/角色名称/角色类型/角色描述/已分配菜单/状态/操作）
 * + ModoPagination 常驻底栏；新建/编辑 ModoDrawer、分配菜单 TreeSelect、用户配置 Transfer。
 *
 * 数据契约（kb 下划线风格，见 web/src/lib/api.ts）：
 * - apiListRoles(page, pageSize) 分页；角色字段 role_id/role_name/role_type/role_descr/state
 * - 角色类型 plat-mgr(平台管理) / team-role(团队角色)
 * - apiGetRoleMenus -> {menuIds}；apiSaveRoleMenus(roleId, menuIds)
 * - apiGetRoleUsers -> {userIds}；apiSaveRoleUsers(roleId, userIds)
 * - apiListMenus() 构建菜单树（复用 _shared.menusToTreeData）
 *
 * 注意：kb 后端 GET /api/v1/system/roles 目前仅支持 page/page_size（list_roles 无
 * keyword/role_type 入参），故筛选条按 data-synth 布局保留，查询仅重置到第 1 页刷新；
 * 待后端支持后在此透传 searchParams 即可。
 * 「已分配菜单」列：列表接口不返回汇总，对当前页逐行并行拉取 apiGetRoleMenus（页面小）。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { App, Button, Card, Form, Input, Select, Space, Spin, Table, Tag, Tooltip, Transfer, TreeSelect } from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  apiCreateRole,
  apiDeleteRole,
  apiGetRoleMenus,
  apiGetRoleUsers,
  apiListMenus,
  apiListRoles,
  apiListUsers,
  apiSaveRoleMenus,
  apiSaveRoleUsers,
  apiUpdateRole,
  SysMenuItem,
  SysRoleItem,
  SysRoleWrite,
} from "@/lib/api";
import { menusToTreeData } from "../_shared";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoModal } from "@/components/biz/modo-modal";
import { ModoInput, ModoTextArea } from "@/components/biz/modo-input";
import { ModoRadio } from "@/components/biz/modo-radio";
import { ModoSelect } from "@/components/biz/modo-select";
import { ModoTabs } from "@/components/biz/modo-tabs";

/** 后端单页条数上限（Query le=100） */
const USERS_PAGE_SIZE = 100;

const ROLE_TYPE_OPTIONS = [
  { label: "平台管理", value: "plat-mgr" },
  { label: "团队角色", value: "team-role" },
];

const ROLE_TYPE_LABEL_MAP: Record<string, string> = {
  "plat-mgr": "平台管理",
  "team-role": "团队角色",
};

// 单元格文本截断（对齐 data-synth 的 block truncate）
const truncateStyle: React.CSSProperties = {
  display: "block",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type RoleTab = { key: string; title: string; children: ReactNode };

export default function SystemRolesPage() {
  const { message, modal } = App.useApp();
  const [loading, setLoading] = useState(false);

  // Table State
  const [data, setData] = useState<SysRoleItem[]>([]);
  const [total, setTotal] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  // Filter State（同 data-synth：查询参数回显筛选标签；后端暂不支持过滤，仅驱动刷新）
  const [searchForm] = Form.useForm();
  const [searchParams, setSearchParams] = useState<any>({});

  // Drawer State（新建/编辑）
  const [drawerVisible, setDrawerVisible] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"create" | "edit">("create");
  const [currentRole, setCurrentRole] = useState<SysRoleItem | null>(null);
  const [roleForm] = Form.useForm();
  const [formKey, setFormKey] = useState(0);
  const [savingRole, setSavingRole] = useState(false);

  // 菜单树（权限分配 + 已分配菜单列标签）
  const [menuItems, setMenuItems] = useState<SysMenuItem[]>([]);

  // Menu Modal State（分配菜单）
  const [menuModalVisible, setMenuModalVisible] = useState(false);
  const [currentRoleNodes, setCurrentRoleNodes] = useState<SysRoleItem | null>(null);
  const [checkedKeys, setCheckedKeys] = useState<React.Key[]>([]);
  const [treeLoading, setTreeLoading] = useState(false);
  const [savingMenus, setSavingMenus] = useState(false);
  const menuRequestSeqRef = useRef(0);

  // 已分配菜单列（当前页逐行拉取）
  const [roleMenusMap, setRoleMenusMap] = useState<Record<string, string[]>>({});
  const roleMenusSeqRef = useRef(0);

  // User Configuration Modal State（用户配置）
  const [userModalVisible, setUserModalVisible] = useState(false);
  const [currentRoleForUsers, setCurrentRoleForUsers] = useState<SysRoleItem | null>(null);
  const [allUsers, setAllUsers] = useState<{ key: string; title: string; description?: string }[]>([]);
  const [targetUserKeys, setTargetUserKeys] = useState<string[]>([]);
  const [userLoading, setUserLoading] = useState(false);
  const [saveLoading, setSaveLoading] = useState(false);
  const userConfigRequestSeqRef = useRef(0);

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「角色管理」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<RoleTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  // 菜单树数据（TreeSelect 用）+ 菜单 id -> 名称 映射（已分配菜单列用）
  const menuTreeData = useMemo(() => menusToTreeData(menuItems), [menuItems]);
  const menuLabelMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const m of menuItems) {
      const id = m.menu_id;
      if (id) map[id] = m.menu_label || m.menu_name || id;
    }
    return map;
  }, [menuItems]);

  // 拉取角色列表（分页由 ModoPagination 驱动）
  const fetchRoles = useCallback(async () => {
    setLoading(true);
    try {
      // 注：kb /roles 仅支持 page/page_size；searchParams 参与依赖以便查询后触发刷新
      const res = await apiListRoles(currentPage, pageSize);
      if (res.success) {
        setData(res.data?.items || []);
        setTotal(res.data?.total ?? 0);
      } else {
        message.error(res.message || "获取角色列表失败");
      }
    } finally {
      setLoading(false);
    }
  }, [currentPage, pageSize, searchParams, message]);

  // 菜单树数据（仅拉取一次）
  const loadMenus = useCallback(async () => {
    try {
      const res = await apiListMenus();
      if (res.success) setMenuItems(res.data?.items || []);
    } catch {
      /* ignore */
    }
  }, []);

  // 当前页每行已分配菜单（并行拉取，seq 防串页覆盖）
  const refreshMenuMap = useCallback(async (rows: SysRoleItem[]) => {
    const seq = ++roleMenusSeqRef.current;
    const roleIds = rows.map((r) => r.role_id).filter(Boolean) as string[];
    if (roleIds.length === 0) {
      setRoleMenusMap({});
      return;
    }
    const settled = await Promise.allSettled(roleIds.map((id) => apiGetRoleMenus(id)));
    if (roleMenusSeqRef.current !== seq) return;
    const next: Record<string, string[]> = {};
    settled.forEach((s, i) => {
      next[roleIds[i]] = s.status === "fulfilled" && s.value.success ? s.value.data?.menuIds || [] : [];
    });
    setRoleMenusMap(next);
  }, []);

  useEffect(() => {
    void fetchRoles();
  }, [fetchRoles]);

  useEffect(() => {
    void loadMenus();
  }, [loadMenus]);

  useEffect(() => {
    void refreshMenuMap(data);
  }, [data, refreshMenuMap]);

  // 抽屉打开后回填表单（ModoDrawer 默认 destroyOnClose，需挂载后再 setFieldsValue）
  useEffect(() => {
    if (!drawerVisible) return;
    if (drawerMode === "edit" && currentRole) {
      roleForm.setFieldsValue({
        roleId: currentRole.role_id,
        roleName: currentRole.role_name,
        roleType: currentRole.role_type || "team-role",
        roleDescr: currentRole.role_descr || "",
        state: currentRole.state || "1",
      });
    } else if (drawerMode === "create") {
      roleForm.resetFields();
    }
  }, [drawerVisible, drawerMode, currentRole, roleForm]);

  // ---- 筛选 ----
  const handleSearch = (values: any) => {
    setSearchParams(values);
    setCurrentPage(1);
  };

  const handleReset = () => {
    setSearchParams({});
    setCurrentPage(1);
  };

  // ---- 新建 / 编辑 ----
  const handleCreate = () => {
    setDrawerMode("create");
    setCurrentRole(null);
    setFormKey((prev) => prev + 1);
    roleForm.resetFields();
    setDrawerVisible(true);
  };

  const handleEdit = (record: SysRoleItem) => {
    setDrawerMode("edit");
    setCurrentRole(record);
    setFormKey((prev) => prev + 1);
    roleForm.resetFields();
    setDrawerVisible(true);
  };

  const handleSave = async () => {
    try {
      const values = await roleForm.validateFields();
      setSavingRole(true);
      const payload: SysRoleWrite = {
        role_id: values.roleId?.trim(),
        role_name: values.roleName,
        role_type: values.roleType,
        role_descr: values.roleDescr || undefined,
        state: values.state || "1",
      };
      const res =
        drawerMode === "edit" && currentRole?.role_id
          ? await apiUpdateRole(currentRole.role_id, payload)
          : await apiCreateRole(payload);
      if (res.success) {
        message.success(drawerMode === "create" ? "角色已创建" : "角色已更新");
        setDrawerVisible(false);
        await fetchRoles();
      } else {
        message.error(res.message || "保存失败");
      }
    } catch (error) {
      // 表单校验失败
      if (error && typeof error === "object" && "errorFields" in error) return;
      console.error("Save role failed:", error);
      message.error(error instanceof Error ? error.message : "保存失败，请稍后重试");
    } finally {
      setSavingRole(false);
    }
  };

  // ---- 删除（modal.confirm） ----
  const handleDelete = (record: SysRoleItem) => {
    modal.confirm({
      title: "确定删除该角色？",
      content: `角色：${record.role_name}（${record.role_id}）`,
      onOk: async () => {
        const res = await apiDeleteRole(record.role_id as string);
        if (res.success) {
          message.success("角色已删除");
          // 当前页删空则回退一页
          if (data.length <= 1 && currentPage > 1) {
            setCurrentPage(currentPage - 1);
          } else {
            await fetchRoles();
          }
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  // ---- 分配菜单 ----
  const fetchRoleMenus = async (roleId: string, requestSeq: number) => {
    setTreeLoading(true);
    try {
      const result = await apiGetRoleMenus(roleId);
      if (menuRequestSeqRef.current !== requestSeq) return false;
      if (result.success) {
        setCheckedKeys((result.data?.menuIds || []).map(String) as React.Key[]);
        return true;
      }
      setCheckedKeys([]);
      message.error(result.message || "获取角色菜单权限失败");
      return false;
    } catch (error) {
      if (menuRequestSeqRef.current === requestSeq) {
        setCheckedKeys([]);
        message.error(error instanceof Error ? error.message : "获取角色菜单权限失败");
      }
      return false;
    } finally {
      if (menuRequestSeqRef.current === requestSeq) setTreeLoading(false);
    }
  };

  const handleAssignMenu = async (record: SysRoleItem) => {
    const requestSeq = menuRequestSeqRef.current + 1;
    menuRequestSeqRef.current = requestSeq;

    setCurrentRoleNodes(record);
    setCheckedKeys([]);
    setMenuModalVisible(true);

    if (menuItems.length === 0) void loadMenus();
    const loaded = await fetchRoleMenus(record.role_id as string, requestSeq);
    if (!loaded && menuRequestSeqRef.current === requestSeq) {
      setMenuModalVisible(false);
      setCurrentRoleNodes(null);
    }
  };

  const handleSaveMenuPerm = async () => {
    if (!currentRoleNodes) return;
    setSavingMenus(true);
    try {
      const res = await apiSaveRoleMenus(currentRoleNodes.role_id as string, checkedKeys.map(String));
      if (res.success) {
        message.success(res.message || "权限分配成功");
        setMenuModalVisible(false);
        setCurrentRoleNodes(null);
        setCheckedKeys([]);
        // 刷新当前页「已分配菜单」列
        void refreshMenuMap(data);
      } else {
        message.error(res.message || "权限保存失败");
      }
    } catch (error) {
      console.error(error);
    } finally {
      setSavingMenus(false);
    }
  };

  // ---- 用户配置 ----
  const handleCloseUserModal = () => {
    userConfigRequestSeqRef.current += 1;
    setUserModalVisible(false);
    setUserLoading(false);
    setCurrentRoleForUsers(null);
    setAllUsers([]);
    setTargetUserKeys([]);
  };

  const handleUserConfig = async (record: SysRoleItem) => {
    if (record.role_type !== "plat-mgr") {
      message.warning("仅平台管理角色支持用户配置");
      return;
    }

    const requestSeq = userConfigRequestSeqRef.current + 1;
    userConfigRequestSeqRef.current = requestSeq;

    setCurrentRoleForUsers(record);
    setAllUsers([]);
    setTargetUserKeys([]);
    setUserLoading(true);
    setUserModalVisible(true);

    try {
      // 分页拉取全部用户（后端单页上限 100），Transfer 内再按 pageSize 10 客户端分页
      const allUserItems: { key: string; title: string; description?: string }[] = [];
      let page = 1;
      for (;;) {
        if (userConfigRequestSeqRef.current !== requestSeq) return;
        const usersRes = await apiListUsers(page, USERS_PAGE_SIZE);
        if (userConfigRequestSeqRef.current !== requestSeq) return;
        if (!usersRes.success) {
          message.error(usersRes.message || "获取用户列表失败");
          handleCloseUserModal();
          return;
        }
        const users = usersRes.data?.items || [];
        const totalUsers = usersRes.data?.total ?? 0;
        allUserItems.push(
          ...users.map((u) => ({
            key: (u.user_id || u.id || "") as string,
            title: u.user_name || (u.user_id as string) || "",
            description: (u.user_id as string) || undefined,
          })),
        );
        if (users.length < USERS_PAGE_SIZE || allUserItems.length >= totalUsers) break;
        page += 1;
      }

      if (userConfigRequestSeqRef.current !== requestSeq) return;
      setAllUsers(allUserItems);

      // 回显该角色已配置的用户
      const roleUsersRes = await apiGetRoleUsers(record.role_id as string);
      if (userConfigRequestSeqRef.current !== requestSeq) return;
      if (roleUsersRes.success) {
        setTargetUserKeys(roleUsersRes.data?.userIds || []);
        return;
      }
      message.error(roleUsersRes.message || "获取用户配置数据失败");
      handleCloseUserModal();
    } catch (error) {
      console.error(error);
      if (userConfigRequestSeqRef.current === requestSeq) {
        message.error(error instanceof Error ? error.message : "获取用户配置数据失败");
        handleCloseUserModal();
      }
    } finally {
      if (userConfigRequestSeqRef.current === requestSeq) {
        setUserLoading(false);
      }
    }
  };

  const handleSaveUsers = async () => {
    if (!currentRoleForUsers) return;
    if (userLoading) {
      message.warning("用户配置数据加载中，请稍后再试");
      return;
    }
    setSaveLoading(true);
    try {
      const res = await apiSaveRoleUsers(currentRoleForUsers.role_id as string, targetUserKeys);
      if (res.success) {
        message.success(res.message || "用户配置成功");
        handleCloseUserModal();
      } else {
        message.error(res.message || "保存失败");
      }
    } catch (error) {
      console.error(error);
      message.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaveLoading(false);
    }
  };

  // ---- 表格列 ----
  const columns: ColumnsType<SysRoleItem> = [
    {
      title: "角色编码",
      dataIndex: "role_id",
      key: "role_id",
      width: 150,
      render: (text?: string) => (
        <Tooltip title={text} mouseEnterDelay={0.3}>
          <span style={truncateStyle}>{text}</span>
        </Tooltip>
      ),
    },
    {
      title: "角色名称",
      dataIndex: "role_name",
      key: "role_name",
      width: 200,
      render: (text?: string) => (
        <Tooltip title={text} mouseEnterDelay={0.3}>
          <span style={truncateStyle}>{text}</span>
        </Tooltip>
      ),
    },
    {
      title: "角色类型",
      dataIndex: "role_type",
      key: "role_type",
      width: 140,
      render: (value?: string) =>
        ROLE_TYPE_LABEL_MAP[value || ""] || (
          <span style={{ color: "#9aa4b2" }}>{value || "未设置"}</span>
        ),
    },
    {
      title: "角色描述",
      dataIndex: "role_descr",
      key: "role_descr",
      width: 300,
      render: (text?: string) => (
        <Tooltip title={text} mouseEnterDelay={0.3}>
          <span style={{ ...truncateStyle, color: text ? undefined : "#9aa4b2" }}>
            {text || "-"}
          </span>
        </Tooltip>
      ),
    },
    {
      title: "已分配菜单",
      key: "menus",
      width: 250,
      render: (_, record) => {
        const labels = (roleMenusMap[record.role_id || ""] || []).map((id) => menuLabelMap[id] || id);
        const text = labels.length > 0 ? labels.join("、") : "";
        return (
          <Tooltip title={text || ""} mouseEnterDelay={0.3}>
            <span style={{ ...truncateStyle, color: text ? undefined : "#9aa4b2" }}>
              {text || "未分配"}
            </span>
          </Tooltip>
        );
      },
    },
    {
      title: "状态",
      dataIndex: "state",
      key: "state",
      width: 100,
      render: (state?: string) => (
        <Tag color={state === "1" ? "success" : "default"}>{state === "1" ? "有效" : "无效"}</Tag>
      ),
    },
    {
      title: "操作",
      key: "action",
      width: 160,
      fixed: "right",
      render: (_, record) => (
        <ModoActionGroup
          maxCount={2}
          actions={[
            { key: "edit", label: "编辑", onClick: () => handleEdit(record) },
            {
              key: "delete",
              label: "删除",
              danger: true,
              onClick: () => handleDelete(record),
            },
            { key: "assign", label: "分配菜单", onClick: () => void handleAssignMenu(record) },
            ...(record.role_type === "plat-mgr"
              ? [{ key: "users", label: "用户配置", onClick: () => void handleUserConfig(record) }]
              : []),
          ]}
        />
      ),
    },
  ];

  // 列表区（首个选项卡内容）：筛选 + 表格 + 钉底分页
  const listPane = (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            padding: "12px 16px 0",
          },
        }}
      >
        {/* 筛选表单（对齐用户管理页 FilterForm：角色名称 + 角色类型 + 查询/重置） */}
        <Form
          form={searchForm}
          layout="inline"
          colon={false}
          style={{ marginBottom: 12, flexShrink: 0 }}
          onFinish={handleSearch}
        >
          <Form.Item name="roleName" label="角色名称">
            <Input allowClear placeholder="请输入角色名称" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="roleType" label="角色类型">
            <Select allowClear placeholder="请选择角色类型" style={{ width: 180 }} options={ROLE_TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button
                onClick={() => {
                  searchForm.resetFields();
                  handleReset();
                }}
              >
                重置
              </Button>
            </Space>
          </Form.Item>
        </Form>

        <Table
          columns={columns}
          dataSource={data}
          rowKey="role_id"
          loading={loading}
          pagination={false}
          scroll={{ x: 1400, y: "calc(100vh - 252px)" }}
          size="small"
        />
        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={currentPage}
            pageSize={pageSize}
            total={total}
            showSizeChanger
            showQuickJumper
            showTotal={(t: number) => `共 ${t} 条`}
            onChange={(page: number, size: number) => {
              setCurrentPage(page);
              setPageSize(size);
            }}
          />
        </div>
      </Card>
    </div>
  );

  return (
    // 2026-10-08: 参考用户管理页改造 —— 外层 8px padding、inline 筛选表单、分页常驻底栏
    // 2026-10-09: 顶部改为「动态选项卡」（复用用户管理页 ModoTabs editable-card 模式）——首 tab「角色管理」
    // = 页面标题 + 列表（不可关闭），后续内容较多的详情/编辑表单可作为可关闭 tab 打开；本轮先预留能力。
    // 表格高度偏移 264 → 252（tab 导航 44px 取代卡片头 56px）。
    <div
      className="roles-page"
      style={{ padding: 8, height: "calc(100vh - 45px)", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action === "remove") closeTab(String(key));
        }}
        tabBarExtraContent={
          <Button type="primary" onClick={handleCreate}>
            新建角色
          </Button>
        }
        items={[
          { key: "home", label: "角色管理", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />

      {/* 新建 / 编辑角色 Drawer */}
      <ModoDrawer
        title={drawerMode === "create" ? "新建角色" : "编辑角色"}
        open={drawerVisible}
        onCancel={() => setDrawerVisible(false)}
        onOk={() => void handleSave()}
        confirmLoading={savingRole}
        width={480}
      >
        <Form
          key={formKey}
          form={roleForm}
          layout="vertical"
          initialValues={{ state: "1", roleType: "team-role" }}
          validateTrigger={false}
          autoComplete="off"
        >
          <Form.Item
            name="roleId"
            label="角色编码"
            rules={[{ required: true, message: "请输入角色编码" }]}
          >
            <ModoInput placeholder="请输入角色编码" disabled={drawerMode === "edit"} />
          </Form.Item>
          <Form.Item
            name="roleName"
            label="角色名称"
            rules={[{ required: true, message: "请输入角色名称" }]}
          >
            <ModoInput placeholder="请输入角色名称" />
          </Form.Item>
          <Form.Item
            name="roleType"
            label="角色类型"
            rules={[{ required: true, message: "请选择角色类型" }]}
          >
            <ModoSelect placeholder="请选择角色类型" allowClear={false} options={ROLE_TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="roleDescr" label="角色描述">
            <ModoTextArea rows={2} placeholder="角色描述" variant="filled" maxLength={50} showCount />
          </Form.Item>
          <Form.Item name="state" label="状态">
            <ModoRadio.Group>
              <ModoRadio value="1">有效</ModoRadio>
              <ModoRadio value="0">无效</ModoRadio>
            </ModoRadio.Group>
          </Form.Item>
        </Form>
      </ModoDrawer>

      {/* 分配菜单权限 Modal（TreeSelect 勾选，回显角色已有菜单） */}
      <ModoModal
        title="分配菜单权限"
        open={menuModalVisible}
        onCancel={() => {
          menuRequestSeqRef.current += 1;
          setTreeLoading(false);
          setMenuModalVisible(false);
          setCurrentRoleNodes(null);
          setCheckedKeys([]);
        }}
        onOk={() => void handleSaveMenuPerm()}
        confirmLoading={savingMenus || treeLoading}
        okButtonProps={{ disabled: treeLoading }}
        width={520}
      >
        <div style={{ padding: "4px 0" }}>
          <div style={{ marginBottom: 8, fontSize: 13, color: "#79879C" }}>
            当前角色：{currentRoleNodes?.role_name || "-"}
          </div>
          <TreeSelect
            style={{ width: "100%" }}
            styles={{ popup: { root: { maxHeight: 400, overflow: "auto" } } }}
            treeData={menuTreeData}
            fieldNames={{ label: "title", value: "key", children: "children" }}
            placeholder={treeLoading ? "正在加载菜单权限..." : "请选择菜单权限"}
            treeCheckable
            showCheckedStrategy={TreeSelect.SHOW_ALL}
            maxTagCount={3}
            value={checkedKeys as string[]}
            onChange={(values: any) => setCheckedKeys(values as React.Key[])}
            treeDefaultExpandAll
            allowClear
            multiple
            disabled={treeLoading}
            loading={treeLoading}
          />
        </div>
      </ModoModal>

      {/* 用户配置 Modal（Transfer：全部用户 -> 已选用户，回显 getRoleUsers） */}
      <ModoModal
        title="用户配置"
        open={userModalVisible}
        onCancel={handleCloseUserModal}
        onOk={() => void handleSaveUsers()}
        confirmLoading={saveLoading || userLoading}
        width={800}
      >
        <Form layout="horizontal" labelCol={{ span: 3 }} wrapperCol={{ span: 21 }}>
          <Form.Item label="角色名">
            <ModoInput value={currentRoleForUsers?.role_name} disabled />
          </Form.Item>
          <Form.Item label="用户">
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                minHeight: 400,
                alignItems: "center",
              }}
            >
              {userLoading ? (
                <Spin tip="正在加载角色用户配置..." />
              ) : (
                <Transfer
                  dataSource={allUsers}
                  titles={["全部用户", "已选用户"]}
                  targetKeys={targetUserKeys}
                  onChange={(keys) => setTargetUserKeys(keys as string[])}
                  render={(item) => item.title}
                  showSearch
                  disabled={userLoading || saveLoading}
                  pagination={{ pageSize: 10 }}
                  styles={{ section: { width: 320, height: 400 } }}
                  locale={{ searchPlaceholder: "输入用户名进行过滤" }}
                />
              )}
            </div>
          </Form.Item>
        </Form>
      </ModoModal>
    </div>
  );
}