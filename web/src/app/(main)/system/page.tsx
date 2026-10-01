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
  Modal,
  Popconfirm,
  Space,
  Table,
  Tabs,
  Tag,
  Tree,
  Upload,
} from "antd";
import {
  apiDeleteMcpServer,
  apiDeleteSkill,
  apiGetSkillDetail,
  apiInstallSkill,
  apiListMcpServers,
  apiListMenus,
  apiListOperationLogs,
  apiListRoles,
  apiListSkills,
  apiListTeams,
  apiListUsers,
  apiSaveMcpServers,
  apiTestMcpServer,
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

function RolesTab() {
  const [rows, setRows] = useState<SysRoleItem[]>([]);
  const [loading, setLoading] = useState(false);

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

  return (
    <Card title="角色">
      <Table
        rowKey="role_id"
        size="small"
        loading={loading}
        dataSource={rows}
        pagination={false}
        columns={[
          { title: "角色名", dataIndex: "role_name" },
          { title: "描述", dataIndex: "role_descr" },
          { title: "类型", dataIndex: "role_type" },
          { title: "状态", dataIndex: "state", width: 90, render: (v) => <StateTag value={v} /> },
        ]}
      />
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
  const [items, setItems] = useState<SysMenuItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListMenus();
      if (res.success) setItems(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const treeData = (() => {
    const byId = new Map(items.map((m) => [m.menu_id, m]));
    const childrenOf = new Map<string, SysMenuItem[]>();
    for (const m of items) {
      const pid = m.parent_id || "";
      if (!childrenOf.has(pid)) childrenOf.set(pid, []);
      childrenOf.get(pid)!.push(m);
    }
    const toNode = (m: SysMenuItem): Record<string, unknown> => ({
      key: m.menu_id,
      title: `${m.menu_label || m.menu_name}${m.route ? `（${m.route}）` : ""}`,
      children: (childrenOf.get(m.menu_id || "") || []).map(toNode),
    });
    const roots = items.filter((m) => !m.parent_id || m.parent_id === "");
    return roots.map(toNode);
  })();

  return (
    <Card title="菜单">
      <Tree
        treeData={treeData}
        defaultExpandAll
        blockNode
        showLine
        style={{ maxHeight: 480, overflow: "auto" }}
      />
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