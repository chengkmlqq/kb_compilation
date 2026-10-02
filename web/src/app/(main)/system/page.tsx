"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Transfer,
  Tree,
  TreeSelect,
  Upload,
} from "antd";
import type { DataNode } from "antd/es/tree";
import {
  apiCreateMenu,
  apiCreateRole,
  apiDeleteMenu,
  apiDeleteMcpServer,
  apiDeleteRole,
  apiDeleteSkill,
  apiGetRoleMenus,
  apiGetRoleUsers,
  apiGetSkillDetail,
  apiInstallSkill,
  apiListMcpServers,
  apiListMenus,
  apiListMenuIcons,
  apiListOperationLogs,
  apiListRoles,
  apiListSkills,
  apiListTeams,
  apiListUsers,
  apiSaveMcpServers,
  apiSaveRoleMenus,
  apiSaveRoleUsers,
  apiTestMcpServer,
  apiUpdateMenu,
  apiUpdateRole,
  McpServerItem,
  SkillItem,
  SysLogItem,
  SysMenuItem,
  SysRoleItem,
  SysTeamItem,
  SysUserItem,
} from "@/lib/api";

function StateTag({ value }: { value?: string | null }) {
  if (value === "1") return <Tag color="success">启用</Tag>;
  if (value === "0") return <Tag color="default">停用</Tag>;
  return <Tag>{value || "-"}</Tag>;
}

function UsersTab() {
  const [rows, setRows] = useState<SysUserItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListUsers(1, 100, keyword);
      if (res.success) setRows(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, [keyword]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card
      title="用户"
      extra={
        <Input.Search
          placeholder="按账号/姓名搜索"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={() => void load()}
          style={{ width: 220 }}
        />
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无用户" /> }}
        columns={[
          { title: "账号", dataIndex: "user_id" },
          { title: "姓名", dataIndex: "user_name" },
          { title: "邮箱", dataIndex: "email" },
          { title: "手机", dataIndex: "phone" },
          { title: "默认团队", dataIndex: "default_team" },
          { title: "状态", dataIndex: "state", width: 90, render: (v) => <StateTag value={v} /> },
        ]}
      />
    </Card>
  );
}

const ROLE_TYPE_LABEL: Record<string, string> = {
  "plat-mgr": "平台管理",
  "team-role": "团队角色",
};

// 菜单数组 -> antd 树节点（授权勾选 / 菜单管理共用）
function menusToTreeData(items: SysMenuItem[]): DataNode[] {
  const byId = new Map(items.map((m) => [m.menu_id, m]));
  const childrenOf = new Map<string, SysMenuItem[]>();
  for (const m of items) {
    const pid = m.parent_id || "";
    if (!childrenOf.has(pid)) childrenOf.set(pid, []);
    childrenOf.get(pid)!.push(m);
  }
  const toNode = (m: SysMenuItem): DataNode => ({
    key: m.menu_id as string,
    title: `${m.menu_label || m.menu_name}${m.route ? `（${m.route}）` : ""}`,
    children: (childrenOf.get(m.menu_id || "") || []).map(toNode),
  });
  return items.filter((m) => !m.parent_id || m.parent_id === "").map(toNode);
}

function RolesTab() {
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
      const res = await apiListRoles(1, 100);
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

      {/* 新建/编辑角色 */}
      <Modal
        title={editingRole ? `编辑角色: ${editingRole.role_name}` : "新建角色"}
        open={roleModalOpen}
        onCancel={() => setRoleModalOpen(false)}
        onOk={() => void doSaveRole()}
        confirmLoading={savingRole}
        destroyOnClose
      >
        <Form form={roleForm} layout="vertical" initialValues={{ role_type: "team-role", state: "1" }}>
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
      </Modal>

      {/* 分配菜单 */}
      <Modal
        title={`分配菜单权限: ${menuModalRole?.role_name || ""}`}
        open={menuModalOpen}
        onCancel={() => setMenuModalOpen(false)}
        onOk={() => void doSaveRoleMenus()}
        confirmLoading={savingMenus}
        width={480}
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
      </Modal>

      {/* 用户配置 */}
      <Modal
        title={`用户配置: ${userModalRole?.role_name || ""}`}
        open={userModalOpen}
        onCancel={() => setUserModalOpen(false)}
        onOk={() => void doSaveRoleUsers()}
        confirmLoading={savingUsers || userModalLoading}
        width={760}
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
      </Modal>
    </Card>
  );
}

function TeamsTab() {
  const [rows, setRows] = useState<SysTeamItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListTeams(1, 100);
      if (res.success) setRows(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card title="团队">
      <Table
        rowKey="team_id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        columns={[
          { title: "团队编码", dataIndex: "team_name" },
          { title: "名称", dataIndex: "label" },
          { title: "描述", dataIndex: "descr" },
          { title: "父团队", dataIndex: "parent_team_name" },
          { title: "状态", dataIndex: "state", width: 90, render: (v) => <StateTag value={v} /> },
        ]}
      />
    </Card>
  );
}

function MenusTab() {
  const { message } = App.useApp();
  const [items, setItems] = useState<SysMenuItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [menuIcons, setMenuIcons] = useState<string[]>([]);
  const [menuIconsLoading, setMenuIconsLoading] = useState(false);

  // 菜单新建/编辑
  const [menuModalOpen, setMenuModalOpen] = useState(false);
  const [editingMenu, setEditingMenu] = useState<SysMenuItem | null>(null);
  const [menuForm] = Form.useForm();
  const [savingMenu, setSavingMenu] = useState(false);
  const [selectedMenuId, setSelectedMenuId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListMenus();
      if (res.success) setItems(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, []);

  // 图标目录（对齐 data-synth icon-actions；菜单 icon 下拉选择用）
  const loadIcons = useCallback(async () => {
    setMenuIconsLoading(true);
    try {
      const res = await apiListMenuIcons();
      if (res.success && res.data) setMenuIcons(res.data);
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

  const treeData = menusToTreeData(items);

  const openCreate = (parentId?: string) => {
    setEditingMenu(null);
    menuForm.resetFields();
    menuForm.setFieldsValue({ state: "1", sort_num: 1, menu_type: "frame", parent_id: parentId || undefined });
    setMenuModalOpen(true);
  };

  const openEdit = (m: SysMenuItem) => {
    setEditingMenu(m);
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
    });
    setMenuModalOpen(true);
  };

  const doSaveMenu = async () => {
    const values = await menuForm.validateFields();
    setSavingMenu(true);
    try {
      const payload = {
        menu_name: values.menu_name,
        menu_label: values.menu_label,
        parent_id: values.parent_id || null,
        sort_num: values.sort_num,
        menu_icon: values.menu_icon || null,
        route: values.route || null,
        state: values.state,
        menu_type: values.menu_type,
        menu_descr: values.menu_descr,
      };
      const res = editingMenu
        ? await apiUpdateMenu(editingMenu.menu_id as string, payload)
        : await apiCreateMenu(payload);
      if (res.success) {
        message.success(editingMenu ? "菜单已更新" : "菜单已创建");
        setMenuModalOpen(false);
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

  return (
    <Card
      title="菜单"
      extra={
        <Space>
          <Button
            disabled={!selectedMenuId}
            onClick={() => openCreate(selectedMenuId || undefined)}
          >
            新增子菜单
          </Button>
          <Button type="primary" onClick={() => openCreate()}>
            新增根菜单
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="菜单 route 填写前端页面路径（如 /kbs、/chat），勾选到角色后即成为该角色的可访问页面。已发布（state=1）且被角色引用的菜单才会出现在侧边栏与权限校验中。"
      />
      <Tree
        treeData={treeData}
        defaultExpandAll
        blockNode
        showLine
        selectedKeys={selectedMenuId ? [selectedMenuId] : []}
        onSelect={(keys) => setSelectedMenuId((keys[0] as string) || null)}
        style={{ maxHeight: 380, overflow: "auto" }}
        titleRender={(node) => {
          const m = items.find((x) => x.menu_id === node.key);
          return (
            <Space size={8}>
              <span>{node.title as React.ReactNode}</span>
              {m && (
                <Space size={4}>
                  <Button size="small" type="link" onClick={() => openEdit(m)}>
                    编辑
                  </Button>
                  <Popconfirm title={`删除菜单 ${m.menu_label || m.menu_name}?`} onConfirm={() => void doDeleteMenu(m.menu_id as string)}>
                    <Button size="small" type="link" danger>
                      删除
                    </Button>
                  </Popconfirm>
                  <Button size="small" type="link" onClick={() => openCreate(m.menu_id)}>
                    新增子
                  </Button>
                </Space>
              )}
            </Space>
          );
        }}
      />

      <Modal
        title={editingMenu ? `编辑菜单: ${editingMenu.menu_label || editingMenu.menu_name}` : "新建菜单"}
        open={menuModalOpen}
        onCancel={() => setMenuModalOpen(false)}
        onOk={() => void doSaveMenu()}
        confirmLoading={savingMenu}
        destroyOnClose
      >
        <Form form={menuForm} layout="vertical" initialValues={{ state: "1", menu_type: "frame", sort_num: 1 }}>
          <Form.Item name="menu_name" label="模块编码" rules={[{ required: true, message: "请输入模块编码" }]}>
            <Input placeholder="如 kbs" />
          </Form.Item>
          <Form.Item name="menu_label" label="模块中文名" rules={[{ required: true, message: "请输入模块中文名" }]}>
            <Input placeholder="如 知识库管理" />
          </Form.Item>
          <Form.Item name="route" label="页面路由" extra="前端页面路径，如 /kbs、/chat、/system；留空表示仅作分组父节点">
            <Input placeholder="/kbs" />
          </Form.Item>
          <Form.Item name="parent_id" label="父模块">
            <TreeSelect
              treeData={menusToTreeData(items)}
              placeholder="请选择父模块（根节点留空）"
              allowClear
              treeDefaultExpandAll
              fieldNames={{ label: "title", value: "key", children: "children" }}
            />
          </Form.Item>
          <Form.Item name="sort_num" label="排序">
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="menu_icon" label="图标" extra="留空用默认图标">
            <Select
              allowClear
              showSearch
              placeholder="选择图标"
              loading={menuIconsLoading}
              options={menuIcons.map((name) => ({
                label: name,
                value: name,
              }))}
            />
          </Form.Item>
          <Form.Item name="menu_type" label="菜单类型">
            <Radio.Group>
              <Radio value="frame">侧边菜单</Radio>
              <Radio value="nav">顶栏菜单</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item name="state" label="状态">
            <Radio.Group>
              <Radio value="1">发布</Radio>
              <Radio value="0">未发布</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item name="menu_descr" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}

function LogsTab() {
  const [rows, setRows] = useState<SysLogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListOperationLogs(1, 100, keyword);
      if (res.success) setRows(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, [keyword]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card
      title="操作日志"
      extra={
        <Input.Search
          placeholder="按用户/类型/内容搜索"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={() => void load()}
          style={{ width: 240 }}
        />
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无日志" /> }}
        columns={[
          { title: "用户", dataIndex: "user_name" },
          {
            title: "类型",
            dataIndex: "oper_type",
            width: 110,
            render: (v: string) => <Tag color={v === "LOGIN" ? "blue" : "default"}>{v}</Tag>,
          },
          { title: "内容", dataIndex: "oper_content", ellipsis: true },
          { title: "URL", dataIndex: "oper_url", width: 160, ellipsis: true },
          { title: "时间", dataIndex: "oper_time", width: 170 },
        ]}
      />
    </Card>
  );
}

function McpServersTab() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<McpServerItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<McpServerItem | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testInfo, setTestInfo] = useState<string>("");
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListMcpServers();
      if (res.success && res.data) setRows(res.data.data || []);
      else message.error(res.message || "加载 MCP 配置失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const openEdit = (item: McpServerItem | null) => {
    setEditing(item);
    setTestInfo("");
    if (item) {
      form.setFieldsValue({
        name: item.name,
        type: item.type || "streamable_http",
        url: item.url || "",
        api_key: (item.headers && Object.entries(item.headers).find(([k]) => k.toLowerCase().includes("key"))?.[1]) || "",
      });
    } else {
      form.resetFields();
    }
    setModalOpen(true);
  };

  const doSave = async () => {
    const values = await form.validateFields();
    const item: McpServerItem = {
      name: values.name,
      type: values.type || "streamable_http",
      url: values.url,
    };
    if (values.api_key) {
      item.headers = { "X-API-Key": values.api_key };
    }
    // 若编辑已有 server，替换列表中的同名校；否则追加
    const next = editing ? rows.map((r) => (r.name === editing.name ? item : r)) : [...rows, item];
    const res = await apiSaveMcpServers(next);
    if (res.success) {
      message.success("MCP 配置已保存并热生效");
      setModalOpen(false);
      await load();
    } else {
      message.error(res.message || "保存失败");
    }
  };

  const doTest = async () => {
    const values = await form.validateFields();
    setTesting(true);
    setTestInfo("");
    try {
      const item: McpServerItem = { name: values.name, type: values.type, url: values.url };
      if (values.api_key) item.headers = { "X-API-Key": values.api_key };
      const res = await apiTestMcpServer(item);
      if (res.success && res.data?.ok) {
        setTestInfo(`连通成功，${res.data.tool_count ?? "?"} 个工具`);
        message.success("连通正常");
      } else {
        setTestInfo(`失败: ${res.data?.error || res.message}`);
        message.warning("连通失败");
      }
    } finally {
      setTesting(false);
    }
  };

  const doDelete = async (name: string) => {
    const res = await apiDeleteMcpServer(name);
    if (res.success) {
      message.success(`已删除 ${name}`);
      await load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  return (
    <Card
      title="MCP 服务器"
      extra={
        <Button type="primary" onClick={() => openEdit(null)}>
          新增服务器
        </Button>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="配置保存在 agent-gateway（热生效，无需重启）。密钥字段回传掩码表示保持不变。"
      />
      <Table
        rowKey="name"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无 MCP 服务器" /> }}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "类型", dataIndex: "type", width: 150 },
          { title: "URL", dataIndex: "url", ellipsis: true },
          {
            title: "操作",
            width: 180,
            render: (_: unknown, r: McpServerItem) => (
              <Space>
                <Button size="small" onClick={() => openEdit(r)}>
                  编辑
                </Button>
                <Popconfirm title={`删除 ${r.name}?`} onConfirm={() => void doDelete(r.name)}>
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={editing ? `编辑 MCP 服务器: ${editing.name}` : "新增 MCP 服务器"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => void doSave()}
        footer={
          <Space>
            <Button loading={testing} onClick={() => void doTest()}>
              测试连接
            </Button>
            <Button onClick={() => setModalOpen(false)}>取消</Button>
            <Button type="primary" onClick={() => void doSave()}>
              保存
            </Button>
          </Space>
        }
      >
        {testInfo && (
          <Alert type={testInfo.startsWith("连通成功") ? "success" : "warning"} message={testInfo} style={{ marginBottom: 12 }} />
        )}
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如 mcp-gateway" />
          </Form.Item>
          <Form.Item name="type" label="类型" initialValue="streamable_http">
            <Input placeholder="streamable_http" />
          </Form.Item>
          <Form.Item name="url" label="URL" rules={[{ required: true }]}>
            <Input placeholder="http://host:8000/mcp/" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key" extra="留空保持不变（编辑时）">
            <Input.Password placeholder="X-API-Key（如有）" autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}

function SkillsTab() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<{ name: string; content?: string } | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListSkills();
      if (res.success && res.data) setRows(res.data.data || []);
      else message.error(res.message || "加载技能失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const showDetail = async (name: string) => {
    const res = await apiGetSkillDetail(name);
    if (res.success && res.data) {
      setDetail({ name, content: res.data.content || "（无 SKILL.md 内容）" });
      setDetailOpen(true);
    } else {
      message.error(res.message || "加载详情失败");
    }
  };

  const doDelete = async (name: string) => {
    const res = await apiDeleteSkill(name);
    if (res.success) {
      message.success(`已删除 ${name}`);
      await load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  return (
    <Card
      title="技能"
      extra={
        <Upload
          accept=".zip"
          showUploadList={false}
          beforeUpload={async (file) => {
            const res = await apiInstallSkill(file as File);
            if (res.success) {
              message.success(`技能已安装: ${res.data?.name ?? "?"}`);
              await load();
            } else {
              message.error(res.message || "安装失败");
            }
            return false;
          }}
        >
          <Button type="primary">安装技能 (ZIP)</Button>
        </Upload>
      }
    >
      <Table
        rowKey="name"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无技能" /> }}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "描述", dataIndex: "description", ellipsis: true },
          {
            title: "脚本数",
            dataIndex: "scripts",
            width: 90,
            render: (v: string[] | undefined) => (Array.isArray(v) ? v.length : "-"),
          },
          {
            title: "操作",
            width: 160,
            render: (_: unknown, r: SkillItem) => (
              <Space>
                <Button size="small" onClick={() => void showDetail(r.name)}>
                  详情
                </Button>
                <Popconfirm title={`删除 ${r.name}?`} onConfirm={() => void doDelete(r.name)}>
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={detail?.name ?? "技能详情"}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={<Button onClick={() => setDetailOpen(false)}>关闭</Button>}
        width={720}
      >
        <pre
          style={{
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 480,
            overflow: "auto",
            background: "#fafafa",
            padding: 12,
            borderRadius: 6,
            fontSize: 12,
          }}
        >
          {detail?.content}
        </pre>
      </Modal>
    </Card>
  );
}

export default function SystemPage() {
  return (
    <Tabs
      defaultActiveKey="users"
      items={[
        { key: "users", label: "用户", children: <UsersTab /> },
        { key: "roles", label: "角色", children: <RolesTab /> },
        { key: "teams", label: "团队", children: <TeamsTab /> },
        { key: "menus", label: "菜单", children: <MenusTab /> },
        { key: "mcps", label: "MCP 管理", children: <McpServersTab /> },
        { key: "skills", label: "技能管理", children: <SkillsTab /> },
        { key: "logs", label: "操作日志", children: <LogsTab /> },
      ]}
    />
  );
}