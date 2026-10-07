"use client";

/** 用户管理（对齐 ds UserManagerNeo：筛选表单 + 表格 + Drawer 编辑 +
 * 角色配置弹窗 + 重置密码；用户ID/用户名/手机/邮箱/状态/操作列）。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Drawer,
  Dropdown,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import {
  apiCreateUser,
  apiDeleteUser,
  apiGetUserRoles,
  apiListRoles,
  apiListTeams,
  apiListUsers,
  apiResetUserPwd,
  apiSaveUserRoles,
  apiUpdateUser,
  SysRoleItem,
  SysTeamItem,
  SysUserItem,
} from "@/lib/api";
import { StateTag } from "../_shared";

type FormValues = {
  userId: string;
  userName: string;
  pwd?: string;
  email?: string;
  phone?: string;
  defaultTeam?: string;
  state: string;
};

export default function SystemUsersPage() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<SysUserItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<{ userId: string; userName: string }>({ userId: "", userName: "" });
  const [applied, setApplied] = useState<{ userId: string; userName: string }>({ userId: "", userName: "" });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);

  // 团队（默认团队下拉）
  const [teams, setTeams] = useState<SysTeamItem[]>([]);
  // 角色（角色配置多选）
  const [roles, setRoles] = useState<SysRoleItem[]>([]);

  // 新增 / 编辑 Drawer
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<SysUserItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<FormValues>();

  // 角色配置弹窗
  const [roleModalOpen, setRoleModalOpen] = useState(false);
  const [roleTarget, setRoleTarget] = useState<SysUserItem | null>(null);
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [savingRoles, setSavingRoles] = useState(false);

  // 重置密码弹窗
  const [pwdModalOpen, setPwdModalOpen] = useState(false);
  const [pwdTarget, setPwdTarget] = useState<SysUserItem | null>(null);
  const [savingPwd, setSavingPwd] = useState(false);
  const [pwdForm] = Form.useForm<{ pwd: string; pwd2: string }>();

  const teamOptions = useMemo(
    () => teams.map((t) => ({ label: `${t.label || ""}（${t.team_name}）`, value: t.team_name as string })),
    [teams],
  );
  const roleOptions = useMemo(
    () => roles.map((r) => ({ label: `${r.role_name}（${r.role_id}）`, value: r.role_id as string })),
    [roles],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const kw = [applied.userId, applied.userName].filter(Boolean).join(" ");
      const res = await apiListUsers(page, pageSize, kw);
      if (res.success) {
        setRows(res.data?.items || []);
        setTotal(res.data?.total ?? 0);
      }
    } finally {
      setLoading(false);
    }
  }, [applied, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void apiListTeams(1, 500).then((res) => {
      if (res.success) setTeams(res.data?.items || []);
    });
    void apiListRoles(1, 500).then((res) => {
      if (res.success) setRoles(res.data?.items || []);
    });
  }, []);

  const doSearch = () => {
    setPage(1);
    setApplied(filter);
  };

  const doReset = () => {
    const empty = { userId: "", userName: "" };
    setFilter(empty);
    setApplied(empty);
    setPage(1);
  };

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ state: "1" });
    setDrawerOpen(true);
  };

  const openEdit = (u: SysUserItem) => {
    setEditing(u);
    form.resetFields();
    form.setFieldsValue({
      userId: u.user_id,
      userName: u.user_name || "",
      email: u.email || "",
      phone: u.phone || "",
      defaultTeam: u.default_team || undefined,
      state: u.state || "1",
    });
    setDrawerOpen(true);
  };

  const doSave = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      const res = editing
        ? await apiUpdateUser(editing.user_id as string, {
            userName: v.userName,
            email: v.email,
            phone: v.phone,
            defaultTeam: v.defaultTeam || "",
            state: v.state,
          })
        : await apiCreateUser({
            userId: v.userId.trim(),
            userName: v.userName,
            pwd: v.pwd,
            email: v.email,
            phone: v.phone,
            defaultTeam: v.defaultTeam || "",
            state: v.state,
          });
      if (res.success) {
        message.success(editing ? "用户已更新" : "用户已创建");
        setDrawerOpen(false);
        await load();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async (u: SysUserItem) => {
    const res = await apiDeleteUser(u.user_id as string);
    if (res.success) {
      message.success("用户已删除");
      await load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const openRoleConfig = async (u: SysUserItem) => {
    setRoleTarget(u);
    setRoleIds([]);
    setRoleModalOpen(true);
    const res = await apiGetUserRoles(u.user_id as string);
    if (res.success && res.data) setRoleIds(res.data.roleIds || []);
  };

  const doSaveRoles = async () => {
    if (!roleTarget) return;
    setSavingRoles(true);
    try {
      const res = await apiSaveUserRoles(roleTarget.user_id as string, roleIds);
      if (res.success) {
        message.success("角色配置成功");
        setRoleModalOpen(false);
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSavingRoles(false);
    }
  };

  const openResetPwd = (u: SysUserItem) => {
    setPwdTarget(u);
    pwdForm.resetFields();
    setPwdModalOpen(true);
  };

  const doResetPwd = async () => {
    if (!pwdTarget) return;
    const v = await pwdForm.validateFields();
    setSavingPwd(true);
    try {
      const res = await apiResetUserPwd(pwdTarget.user_id as string, v.pwd);
      if (res.success) {
        message.success("密码已重置");
        setPwdModalOpen(false);
      } else {
        message.error(res.message || "重置失败");
      }
    } finally {
      setSavingPwd(false);
    }
  };

  return (
    // 2026-10-07: 参考任务管理页，最外层容器加 8px padding（统一页面边距）
    <div style={{ padding: 8, height: "100%", overflow: "auto" }}>
      <Card
        title="用户"
        extra={
          <Button type="primary" onClick={openCreate}>
            新增用户
          </Button>
        }
      >
      {/* 筛选表单（对齐 ds FilterForm：用户ID + 用户名） */}
      <Form
        layout="inline"
        style={{ marginBottom: 12 }}
        onFinish={doSearch}
        initialValues={{ userId: "", userName: "" }}
      >
        <Form.Item name="userId" label="用户ID">
          <Input allowClear placeholder="请输入用户ID" style={{ width: 180 }} />
        </Form.Item>
        <Form.Item name="userName" label="用户名">
          <Input allowClear placeholder="请输入用户名" style={{ width: 180 }} />
        </Form.Item>
        <Form.Item>
          <Space>
            <Button type="primary" htmlType="submit">
              查询
            </Button>
            <Button
              onClick={() => {
                form.resetFields();
                doReset();
              }}
            >
              重置
            </Button>
          </Space>
        </Form.Item>
      </Form>

      <Table
        rowKey="user_id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 个用户`,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
        locale={{ emptyText: <Empty description="暂无用户" /> }}
        columns={[
          { title: "用户ID", dataIndex: "user_id", ellipsis: true },
          {
            title: "用户名",
            dataIndex: "user_name",
            ellipsis: true,
            render: (v: string | null | undefined) => (
              <Space size={6}>
                <Tag color="blue">用户</Tag>
                <span>{v || "-"}</span>
              </Space>
            ),
          },
          { title: "手机号", dataIndex: "phone", ellipsis: true, render: (v: string | null) => v || "-" },
          { title: "邮箱", dataIndex: "email", ellipsis: true, render: (v: string | null) => v || "-" },
          {
            title: "默认团队",
            dataIndex: "default_team",
            ellipsis: true,
            render: (v: string | null) => v || "-",
          },
          {
            title: "状态",
            dataIndex: "state",
            width: 90,
            render: (v: string | null | undefined) => <StateTag value={v} />,
          },
          {
            title: "操作",
            width: 190,
            render: (_: unknown, u: SysUserItem) => (
              <Space>
                <Button size="small" type="link" onClick={() => openEdit(u)}>
                  编辑
                </Button>
                <Popconfirm title={`确定删除用户 ${u.user_id}?`} onConfirm={() => void doDelete(u)}>
                  <Button size="small" type="link" danger>
                    删除
                  </Button>
                </Popconfirm>
                <Dropdown
                  menu={{
                    items: [
                      { key: "role", label: "角色配置", onClick: () => void openRoleConfig(u) },
                      { key: "pwd", label: "重置密码", onClick: () => openResetPwd(u) },
                    ],
                  }}
                >
                  <Button size="small" type="link">
                    更多
                  </Button>
                </Dropdown>
              </Space>
            ),
          },
        ]}
      />

      {/* 新增 / 编辑用户（对齐 ds Drawer 表单） */}
      <Drawer
        title={editing ? `编辑用户: ${editing.user_id}` : "新增用户"}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={480}
        extra={
          <Space>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            <Button type="primary" loading={saving} onClick={() => void doSave()}>
              保存
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item
            name="userId"
            label="用户ID"
            rules={[{ required: true, message: "请输入用户ID" }]}
          >
            <Input placeholder="请输入用户ID" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="userName" label="用户名">
            <Input placeholder="请输入用户名" />
          </Form.Item>
          <Form.Item
            name="pwd"
            label="密码"
            rules={editing ? [] : [{ required: true, message: "请输入密码" }]}
            extra={editing ? "留空表示不修改密码" : undefined}
          >
            <Input.Password placeholder="请输入密码" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="phone" label="手机号">
            <Input placeholder="请输入手机号" />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input placeholder="请输入邮箱" />
          </Form.Item>
          <Form.Item name="defaultTeam" label="默认团队">
            <Select allowClear showSearch placeholder="请选择默认团队" options={teamOptions} />
          </Form.Item>
          <Form.Item name="state" label="状态" rules={[{ required: true }]}>
            <Select
              options={[
                { label: "启用", value: "1" },
                { label: "停用", value: "0" },
              ]}
            />
          </Form.Item>
        </Form>
      </Drawer>

      {/* 角色配置（对齐 ds 角色配置弹窗） */}
      <Modal
        title={`角色配置: ${roleTarget?.user_id || ""}`}
        open={roleModalOpen}
        onCancel={() => setRoleModalOpen(false)}
        onOk={() => void doSaveRoles()}
        confirmLoading={savingRoles}
        okText="保存"
        width={480}
      >
        <Select
          mode="multiple"
          allowClear
          showSearch
          style={{ width: "100%" }}
          placeholder="请选择角色"
          value={roleIds}
          onChange={(v) => setRoleIds(v as string[])}
          options={roleOptions}
        />
      </Modal>

      {/* 重置密码 */}
      <Modal
        title={`重置密码: ${pwdTarget?.user_id || ""}`}
        open={pwdModalOpen}
        onCancel={() => setPwdModalOpen(false)}
        onOk={() => void doResetPwd()}
        confirmLoading={savingPwd}
        okText="确定"
        destroyOnClose
      >
        <Form form={pwdForm} layout="vertical" preserve={false}>
          <Form.Item name="pwd" label="新密码" rules={[{ required: true, message: "请输入新密码" }]}>
            <Input.Password placeholder="请输入新密码" autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="pwd2"
            label="确认新密码"
            dependencies={["pwd"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("pwd") === value) return Promise.resolve();
                  return Promise.reject(new Error("两次输入的密码不一致"));
                },
              }),
            ]}
          >
            <Input.Password placeholder="请再次输入新密码" autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
    </div>
  );
}