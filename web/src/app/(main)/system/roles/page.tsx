"use client";

/** 角色管理（独立页，对齐 ds system/roles）。 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  Popconfirm,
  Radio,
  Space,
  Table,
  Transfer,
  Tree,
} from "antd";
import type { DataNode } from "antd/es/tree";
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
  SysRoleItem,
} from "@/lib/api";
import { menusToTreeData, StateTag } from "../_shared";

const ROLE_TYPE_LABEL: Record<string, string> = {
  "plat-mgr": "平台管理",
  "team-role": "团队角色",
};

export default function SystemRolesPage() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<SysRoleItem[]>([]);
  const [loading, setLoading] = useState(false);

  // 角色新建/编辑
  const [roleModalOpen, setRoleModalOpen] = useState(false);
  const [editingRole, setEditingRole] = useState<SysRoleItem | null>(null);
  const [savingRole, setSavingRole] = useState(false);
  const [roleForm] = Form.useForm();

  // 分配菜单
  const [menuModalOpen, setMenuModalOpen] = useState(false);
  const [menuModalRole, setMenuModalRole] = useState<SysRoleItem | null>(null);
  const [menuTreeData, setMenuTreeData] = useState<DataNode[]>([]);
  const [checkedMenuIds, setCheckedMenuIds] = useState<string[]>([]);
  const [menuModalLoading, setMenuModalLoading] = useState(false);
  const [savingMenus, setSavingMenus] = useState(false);

  // 用户配置
  const [userModalOpen, setUserModalOpen] = useState(false);
  const [userModalRole, setUserModalRole] = useState<SysRoleItem | null>(null);
  const [allUserOptions, setAllUserOptions] = useState<{ key: string; title: string }[]>([]);
  const [targetUserIds, setTargetUserIds] = useState<string[]>([]);
  const [userModalLoading, setUserModalLoading] = useState(false);
  const [savingUsers, setSavingUsers] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListRoles(1, 500);
      if (res.success) setRows(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditingRole(null);
    roleForm.resetFields();
    roleForm.setFieldsValue({ role_type: "team-role", state: "1" });
    setRoleModalOpen(true);
  };

  const openEdit = (r: SysRoleItem) => {
    setEditingRole(r);
    roleForm.setFieldsValue({
      role_id: r.role_id,
      role_name: r.role_name,
      role_type: r.role_type || "team-role",
      role_descr: r.role_descr,
      state: r.state || "1",
    });
    setRoleModalOpen(true);
  };

  const doSaveRole = async () => {
    const values = await roleForm.validateFields();
    setSavingRole(true);
    try {
      const payload = {
        role_id: values.role_id,
        role_name: values.role_name,
        role_type: values.role_type,
        role_descr: values.role_descr,
        state: values.state,
      };
      const res = editingRole
        ? await apiUpdateRole(editingRole.role_id as string, payload)
        : await apiCreateRole(payload);
      if (res.success) {
        message.success(editingRole ? "角色已更新" : "角色已创建");
        setRoleModalOpen(false);
        await load();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSavingRole(false);
    }
  };

  const doDeleteRole = async (roleId: string) => {
    const res = await apiDeleteRole(roleId);
    if (res.success) {
      message.success("角色已删除");
      await load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const openAssignMenu = async (r: SysRoleItem) => {
    setMenuModalRole(r);
    setMenuModalLoading(true);
    setCheckedMenuIds([]);
    setMenuModalOpen(true);
    try {
      const [menusRes, roleMenusRes] = await Promise.all([apiListMenus(), apiGetRoleMenus(r.role_id as string)]);
      if (menusRes.success) setMenuTreeData(menusToTreeData(menusRes.data?.items || []));
      if (roleMenusRes.success) setCheckedMenuIds(roleMenusRes.data?.menuIds || []);
    } finally {
      setMenuModalLoading(false);
    }
  };

  const doSaveRoleMenus = async () => {
    if (!menuModalRole) return;
    setSavingMenus(true);
    try {
      const res = await apiSaveRoleMenus(menuModalRole.role_id as string, checkedMenuIds);
      if (res.success) {
        message.success(res.message || "权限分配成功");
        setMenuModalOpen(false);
      } else {
        message.error(res.message || "权限保存失败");
      }
    } finally {
      setSavingMenus(false);
    }
  };

  const openUserConfig = async (r: SysRoleItem) => {
    setUserModalRole(r);
    setUserModalLoading(true);
    setUserModalOpen(true);
    try {
      const [usersRes, roleUsersRes] = await Promise.all([
        apiListUsers(1, 500),
        apiGetRoleUsers(r.role_id as string),
      ]);
      if (usersRes.success) {
        setAllUserOptions(
          (usersRes.data?.items || []).map((u) => ({
            key: u.user_id || u.id || "",
            title: `${u.user_id}${u.user_name ? `（${u.user_name}）` : ""}`,
          })),
        );
      }
      if (roleUsersRes.success) setTargetUserIds(roleUsersRes.data?.userIds || []);
    } finally {
      setUserModalLoading(false);
    }
  };

  const doSaveRoleUsers = async () => {
    if (!userModalRole) return;
    setSavingUsers(true);
    try {
      const res = await apiSaveRoleUsers(userModalRole.role_id as string, targetUserIds);
      if (res.success) {
        message.success(res.message || "用户配置成功");
        setUserModalOpen(false);
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSavingUsers(false);
    }
  };

  const onMenuCheck = (checked: React.Key[] | { checked: React.Key[]; halfChecked: React.Key[] }) => {
    const keys = Array.isArray(checked) ? checked : checked.checked;
    setCheckedMenuIds(keys.map(String));
  };

  return (
    // 2026-10-07: 统一页面外边距（对齐用户管理页 padding:8）
    <div style={{ padding: 8, height: "100%", overflow: "auto" }}>
      <Card
        title="角色"
        extra={
          <Button type="primary" onClick={openCreate}>
            新建角色
          </Button>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="通过「分配菜单」为角色勾选可访问的页面菜单，勾选后该角色下用户的侧边栏与页面访问即受菜单授权控制。"
        />
        <Table
          rowKey="role_id"
          size="small"
          loading={loading}
          dataSource={rows}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无角色" /> }}
          columns={[
            { title: "角色编码", dataIndex: "role_id", width: 140 },
            { title: "角色名", dataIndex: "role_name", width: 160 },
            {
              title: "类型",
              dataIndex: "role_type",
              width: 110,
              render: (v: string) => ROLE_TYPE_LABEL[v] || v || "-",
            },
            { title: "描述", dataIndex: "role_descr", ellipsis: true },
            { title: "状态", dataIndex: "state", width: 80, render: (v) => <StateTag value={v} /> },
            {
              title: "操作",
              width: 300,
              render: (_: unknown, r: SysRoleItem) => (
                <Space>
                  <Button size="small" onClick={() => openEdit(r)}>
                    编辑
                  </Button>
                  <Popconfirm title={`确定删除角色 ${r.role_name}?`} onConfirm={() => void doDeleteRole(r.role_id as string)}>
                    <Button size="small" danger>
                      删除
                    </Button>
                  </Popconfirm>
                  <Button size="small" onClick={() => void openAssignMenu(r)}>
                    分配菜单
                  </Button>
                  {r.role_type === "plat-mgr" && (
                    <Button size="small" onClick={() => void openUserConfig(r)}>
                      用户配置
                    </Button>
                  )}
                </Space>
              ),
            },
          ]}
        />

        {/* 新建/编辑角色（对齐 ds Drawer 表单） */}
              <Drawer
                title={editingRole ? `编辑角色: ${editingRole.role_name}` : "新建角色"}
                open={roleModalOpen}
                onClose={() => setRoleModalOpen(false)}
                width={480}
                extra={
                  <Space>
                    <Button onClick={() => setRoleModalOpen(false)}>取消</Button>
                    <Button type="primary" loading={savingRole} onClick={() => void doSaveRole()}>
                      保存
                    </Button>
                  </Space>
                }
              >
                <Form form={roleForm} layout="vertical" preserve={false} initialValues={{ role_type: "team-role", state: "1" }}>
                  <Form.Item
                    name="role_id"
                    label="角色编码"
                    rules={[{ required: true, message: "请输入角色编码" }]}
                  >
                    <Input placeholder="如 team-reader" disabled={!!editingRole} />
                  </Form.Item>
                  <Form.Item name="role_name" label="角色名称" rules={[{ required: true, message: "请输入角色名称" }]}>
                    <Input placeholder="角色名称" />
                  </Form.Item>
                  <Form.Item name="role_type" label="角色类型" rules={[{ required: true }]}>
                    <Radio.Group>
                      <Radio value="plat-mgr">平台管理</Radio>
                      <Radio value="team-role">团队角色</Radio>
                    </Radio.Group>
                  </Form.Item>
                  <Form.Item name="role_descr" label="角色描述">
                    <Input.TextArea rows={2} placeholder="角色描述（可选）" maxLength={50} />
                  </Form.Item>
                  <Form.Item name="state" label="状态">
                    <Radio.Group>
                      <Radio value="1">有效</Radio>
                      <Radio value="0">无效</Radio>
                    </Radio.Group>
                  </Form.Item>
                </Form>
              </Drawer>

              {/* 分配菜单 */}
              <Drawer
                title={`分配菜单权限: ${menuModalRole?.role_name || ""}`}
                open={menuModalOpen}
                onClose={() => setMenuModalOpen(false)}
                width={480}
                extra={
                  <Space>
                    <Button onClick={() => setMenuModalOpen(false)}>取消</Button>
                    <Button type="primary" loading={savingMenus} onClick={() => void doSaveRoleMenus()}>
                      保存
                    </Button>
                  </Space>
                }
              >
                <Tree
                  treeData={menuTreeData}
                  checkable
                  checkedKeys={checkedMenuIds}
                  onCheck={onMenuCheck}
                  defaultExpandAll
                  height={420}
                  selectable={false}
                />
              </Drawer>

              {/* 用户配置（对齐 ds 角色用户分配 Drawer + Transfer） */}
              <Drawer
                title={`用户配置: ${userModalRole?.role_name || ""}`}
                open={userModalOpen}
                onClose={() => setUserModalOpen(false)}
                width={760}
                extra={
                  <Space>
                    <Button onClick={() => setUserModalOpen(false)}>取消</Button>
                    <Button
                      type="primary"
                      loading={savingUsers || userModalLoading}
                      onClick={() => void doSaveRoleUsers()}
                    >
                      保存
                    </Button>
                  </Space>
                }
              >
                <Transfer
                  dataSource={allUserOptions}
                  titles={["全部用户", "已选用户"]}
                  targetKeys={targetUserIds}
                  onChange={(keys) => setTargetUserIds(keys as string[])}
                  render={(item) => item.title}
                  showSearch
                  pagination={{ pageSize: 10 }}
                  listStyle={{ width: 320, height: 400 }}
                  disabled={userModalLoading}
                />
              </Drawer>
            </Card>
    </div>
  );
      }