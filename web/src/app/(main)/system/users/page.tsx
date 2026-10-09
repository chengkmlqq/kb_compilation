"use client";

/** 用户管理（对齐 ds UserManagerNeo：筛选表单 + 表格 + Drawer 编辑 +
 * 角色配置弹窗 + 重置密码；用户ID/用户名/手机/邮箱/状态/操作列）。 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
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
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoTabs } from "@/components/biz/modo-tabs";

type FormValues = {
  userId: string;
  userName: string;
  pwd?: string;
  email?: string;
  phone?: string;
  defaultTeam?: string;
  state: string;
};

/** 动态选项卡条目（复用数据源页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type UserTab = { key: string; title: string; children: ReactNode };

export default function SystemUsersPage() {
  const { message, modal } = App.useApp();
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
  const [searchForm] = Form.useForm<{ userId: string; userName: string }>();

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

  // ---- 动态选项卡（复用数据源页 ModoTabs 模式）----
  // 首个 tab「用户管理」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<UserTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  const teamOptions = useMemo(
    () => teams.map((t) => ({ label: `${t.label || ""}（${t.team_name}）`, value: t.team_name as string })),
    [teams],
  );
  const roleOptions = useMemo(
    () => roles.map((r) => ({ label: `${r.role_name}（${r.role_id}）`, value: r.role_id as string })),
    [roles],
  );
  // role_id → role_name（表格「角色」列展示用；复用已加载的 roles，无额外请求）
  const roleNameMap = useMemo(() => {
    const m: Record<string, string> = {};
    roles.forEach((r) => {
      if (r.role_id) m[r.role_id] = r.role_name || r.role_id;
    });
    return m;
  }, [roles]);

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
      {/* 筛选表单（对齐 ds FilterForm：用户ID + 用户名） */}
      <Form
        form={searchForm}
        layout="inline"
        style={{ marginBottom: 12, flexShrink: 0 }}
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
                searchForm.resetFields();
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
        pagination={false}
        scroll={{ x: "100%", y: "calc(100vh - 252px)" }}
        locale={{ emptyText: <Empty description="暂无用户" /> }}
        columns={[
          { title: "用户编码", dataIndex: "user_id", ellipsis: true },
          {
            title: "用户名",
            dataIndex: "user_name",
            ellipsis: true,
            render: (v: string | null | undefined) => v || "-",
          },
          {
            title: "角色",
            dataIndex: "role_ids",
            width: 200,
            render: (ids: string[] | undefined) =>
              ids && ids.length ? (
                <Space size={4} wrap>
                  {ids.map((id) => (
                    <Tag key={id} color="green">
                      {roleNameMap[id] || id}
                    </Tag>
                  ))}
                </Space>
              ) : (
                "-"
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
            width: 140,
            render: (_: unknown, u: SysUserItem) => (
              <ModoActionGroup
                maxCount={2}
                actions={[
                  { key: "edit", label: "编辑", onClick: () => openEdit(u) },
                  {
                    key: "delete",
                    label: "删除",
                    danger: true,
                    onClick: () =>
                      modal.confirm({
                        title: `确定删除用户 ${u.user_id}?`,
                        onOk: () => doDelete(u),
                      }),
                  },
                  { key: "role", label: "角色配置", onClick: () => void openRoleConfig(u) },
                  { key: "pwd", label: "重置密码", onClick: () => openResetPwd(u) },
                ]}
              />
            ),
          },
        ]}
      />
      <div style={{ flexShrink: 0, marginTop: "auto" }}>
        <ModoPagination
          current={page}
          pageSize={pageSize}
          total={total}
          showTotal={(t) => `共 ${t} 个用户`}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      </div>
      </Card>
    </div>
  );

  return (
    // 2026-10-07: 参考任务管理页，最外层容器加 8px padding（统一页面边距）
    // 2026-10-09: 顶部改为「动态选项卡」（复用数据源页 ModoTabs editable-card 模式）——首 tab「用户管理」
    // = 页面标题 + 列表（不可关闭），后续内容较多的详情/编辑表单可作为可关闭 tab 打开；本轮先预留能力。
    // 表格高度偏移 264 → 252（tab 导航 44px 取代卡片头 56px）。
    <div
      className="users-page"
      style={{
        padding: 8,
        height: "calc(100vh - 45px)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
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
          <Button type="primary" onClick={openCreate}>
            新增用户
          </Button>
        }
        items={[
          { key: "home", label: "用户管理", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
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
    </div>
  );
}