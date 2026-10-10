"use client";

/** 团队管理（对齐 ds TeamManagerZj：左侧团队树 + 右键新增/删除/编辑 +
 * 搜索高亮；右侧 Tabs：团队信息 / 团队成员）。 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Dropdown,
  Empty,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Tabs,
  Tree,
} from "antd";
import {
  apiCreateTeam,
  apiDeleteTeam,
  apiGetTeamMembers,
  apiListDatasources,
  apiListTeamDsAuth,
  apiListTeams,
  apiListUsers,
  apiSaveTeamDsAuth,
  apiSaveTeamMembers,
  apiUpdateTeam,
  DatasourceItem,
  SysTeamItem,
  SysTeamMemberItem,
  SysUserItem,
  TeamDsMapItem,
} from "@/lib/api";
import { StateTag } from "../_shared";
import ModoTable from "@/components/biz/modo-table";
import { ModoTabs } from "@/components/biz/modo-tabs";

/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type TeamTab = { key: string; title: string; children: ReactNode };

type TeamFormValues = {
  teamName: string;
  label: string;
  parentTeamName?: string;
  descr?: string;
  state: string;
};

type TeamNode = SysTeamItem & { key: string; title: React.ReactNode; children?: TeamNode[] };

type DsAuthRow = { key: string; dsName: string; label: string };

function buildTree(teams: SysTeamItem[]): TeamNode[] {
  const byName = new Map<string, TeamNode>();
  teams.forEach((t) => {
    byName.set(t.team_name as string, { ...t, key: t.team_name as string, title: t.label || t.team_name });
  });
  const roots: TeamNode[] = [];
  byName.forEach((node) => {
    const parent = node.parent_team_name ? byName.get(node.parent_team_name) : undefined;
    if (parent) {
      parent.children = parent.children || [];
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  });
  return roots;
}

function highlightTitle(label: string, kw: string): React.ReactNode {
  if (!kw) return label;
  const idx = label.toLowerCase().indexOf(kw.toLowerCase());
  if (idx === -1) return label;
  return (
    <>
      {label.slice(0, idx)}
      <span style={{ color: "#1677ff" }}>{label.slice(idx, idx + kw.length)}</span>
      {label.slice(idx + kw.length)}
    </>
  );
}

export default function SystemTeamsPage() {
  const { message, modal } = App.useApp();
  const [teams, setTeams] = useState<SysTeamItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);

  // 右键菜单
  const [ctx, setCtx] = useState<{ x: number; y: number; team: SysTeamItem } | null>(null);

  // 新增 / 编辑 Modal
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<SysTeamItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<TeamFormValues>();

  // 成员
  const [members, setMembers] = useState<SysTeamMemberItem[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [memberModalOpen, setMemberModalOpen] = useState(false);
  const [allUsers, setAllUsers] = useState<SysUserItem[]>([]);
  const [targetUsers, setTargetUsers] = useState<string[]>([]);
  const [savingMembers, setSavingMembers] = useState(false);

  // 数据源授权（作用于左侧当前选中团队）
  const [authItems, setAuthItems] = useState<TeamDsMapItem[]>([]);
  const [authRows, setAuthRows] = useState<DatasourceItem[]>([]);
  const [authLoading, setAuthLoading] = useState(false);
  const [savingAuth, setSavingAuth] = useState(false);

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「团队管理」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<TeamTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListTeams(1, 500);
      if (res.success) {
        const items = res.data?.items || [];
        setTeams(items);
        // 进入页面/刷新后兜底选中：保留仍存在的当前选中，否则默认选第一个团队（避免右侧空白）
        setSelectedKey((prev) =>
          prev && items.some((t) => t.team_name === prev) ? prev : items[0]?.team_name ?? null,
        );
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedTeam = useMemo(
    () => teams.find((t) => t.team_name === selectedKey) || null,
    [teams, selectedKey],
  );

  const treeData = useMemo(() => {
    const kw = keyword.trim();
    const roots = buildTree(teams);
    const decorate = (nodes: TeamNode[]): TeamNode[] =>
      nodes.map((n) => {
        const kids = n.children ? decorate(n.children) : undefined;
        const label = String(n.label || n.team_name || "");
        return { ...n, title: highlightTitle(label, kw), children: kids };
      });
    return decorate(roots);
  }, [teams, keyword]);

  const teamOptions = useMemo(
    () => teams.map((t) => ({ label: `${t.label || ""}（${t.team_name}）`, value: t.team_name as string })),
    [teams],
  );

  const loadMembers = useCallback(async (teamName: string) => {
    setMembersLoading(true);
    try {
      const res = await apiGetTeamMembers(teamName);
      if (res.success) setMembers(res.data?.items || []);
      else setMembers([]);
    } finally {
      setMembersLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedKey) void loadMembers(selectedKey);
  }, [selectedKey, loadMembers]);

  const loadAuth = useCallback(async (teamName: string) => {
    setAuthLoading(true);
    try {
      const [maps, list] = await Promise.all([
        apiListTeamDsAuth(teamName),
        apiListDatasources(1, 200),
      ]);
      setAuthItems(maps.success ? maps.data || [] : []);
      setAuthRows(list.success ? list.data?.items || [] : []);
    } finally {
      setAuthLoading(false);
    }
  }, []);

  useEffect(() => {
    // 切换团队先清空，避免把上一团队的勾选误存到新团队
    setAuthItems([]);
    setAuthRows([]);
    if (selectedKey) void loadAuth(selectedKey);
  }, [selectedKey, loadAuth]);

  const doSaveAuth = async () => {
    if (!selectedKey) return;
    setSavingAuth(true);
    try {
      const res = await apiSaveTeamDsAuth(selectedKey, authItems);
      if (res.success) {
        message.success("数据源授权已保存");
        await loadAuth(selectedKey);
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSavingAuth(false);
    }
  };

  const openCreate = (parent?: SysTeamItem | null) => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      parentTeamName: parent?.team_name || undefined,
      state: "1",
    });
    setFormOpen(true);
  };

  const openEdit = (t: SysTeamItem) => {
    setEditing(t);
    form.resetFields();
    form.setFieldsValue({
      teamName: t.team_name,
      label: t.label || "",
      parentTeamName: t.parent_team_name || undefined,
      descr: t.descr || "",
      state: t.state || "1",
    });
    setFormOpen(true);
  };

  const doSave = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      const res = editing
        ? await apiUpdateTeam(editing.team_name as string, {
            label: v.label,
            descr: v.descr,
            parentTeamName: v.parentTeamName || "",
            state: v.state,
          })
        : await apiCreateTeam({
            teamName: v.teamName.trim(),
            label: v.label,
            descr: v.descr,
            parentTeamName: v.parentTeamName || "",
            state: v.state,
          });
      if (res.success) {
        message.success(editing ? "团队已更新" : "团队已创建");
        setFormOpen(false);
        await load();
        if (!editing && v.teamName) setSelectedKey(v.teamName.trim());
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const doDelete = async (t: SysTeamItem) => {
    const res = await apiDeleteTeam(t.team_name as string);
    if (res.success) {
      message.success("团队已删除");
      if (selectedKey === t.team_name) {
        setSelectedKey(null);
        setMembers([]);
      }
      await load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  // 删除确认（对齐用户管理页：统一用 modal.confirm）
  const confirmDelete = (t: SysTeamItem) => {
    modal.confirm({
      title: `确认删除团队 ${t.team_name}？`,
      content: t.label ? `团队「${t.label}」删除后不可恢复。` : undefined,
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: () => doDelete(t),
    });
  };

  const openMemberModal = async () => {
    setTargetUsers(members.map((m) => m.user_id as string));
    setMemberModalOpen(true);
    const res = await apiListUsers(1, 500);
    if (res.success) setAllUsers(res.data?.items || []);
  };

  const doSaveMembers = async () => {
    if (!selectedKey) return;
    setSavingMembers(true);
    try {
      const res = await apiSaveTeamMembers(selectedKey, targetUsers);
      if (res.success) {
        message.success("团队成员已保存");
        setMemberModalOpen(false);
        await loadMembers(selectedKey);
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSavingMembers(false);
    }
  };

  const ctxMenu = {
    items: [
      {
        key: "add",
        label: (
          <Space size={6}>
            <span>＋</span> 新增子团队
          </Space>
        ),
        onClick: () => {
          openCreate(ctx?.team || null);
          setCtx(null);
        },
      },
      {
        key: "edit",
        label: (
          <Space size={6}>
            <span>✎</span> 编辑
          </Space>
        ),
        onClick: () => {
          if (ctx?.team) openEdit(ctx.team);
          setCtx(null);
        },
      },
      {
        key: "del",
        danger: true,
        label: (
          <Space size={6}>
            <span>🗑</span> 删除
          </Space>
        ),
        onClick: () => {
          const team = ctx?.team;
          setCtx(null);
          if (team) confirmDelete(team);
        },
      },
    ],
  };

  // 列表区（首个选项卡内容）：左侧团队树 + 右侧团队信息/成员
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
        <div style={{ display: "flex", gap: 16, flex: 1, minHeight: 0 }}>
          {/* 左侧团队树 */}
          <div style={{ width: 280, flexShrink: 0, borderRight: "1px solid #f0f0f0", paddingRight: 12, display: "flex", flexDirection: "column", minHeight: 0 }}>
          <Input.Search
            placeholder="输入关键字进行过滤"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            style={{ marginBottom: 8, flexShrink: 0 }}
          />
          {teams.length === 0 && !loading ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <span>
                  暂无数据，请先{" "}
                  <Button type="link" style={{ padding: 0 }} onClick={() => openCreate(null)}>
                    创建团队
                  </Button>
                </span>
              }
            />
          ) : (
            <Tree
              treeData={treeData}
              showLine
              blockNode
              defaultExpandAll
              expandedKeys={expandedKeys}
              onExpand={(keys) => setExpandedKeys(keys)}
              selectedKeys={selectedKey ? [selectedKey] : []}
              onSelect={(keys) => setSelectedKey((keys[0] as string) || null)}
              onRightClick={({ event, node }) => {
                const team = teams.find((t) => t.team_name === node.key);
                if (team) setCtx({ x: event.clientX, y: event.clientY, team });
              }}
              style={{ flex: 1, minHeight: 0, overflow: "auto" }}
                          />
                        )}
                        <Dropdown
            open={!!ctx}
            onOpenChange={(open) => !open && setCtx(null)}
            menu={ctxMenu}
            trigger={[]}
          >
            <span
              style={{
                position: "fixed",
                left: ctx?.x ?? -999,
                top: ctx?.y ?? -999,
                width: 0,
                height: 0,
              }}
            />
          </Dropdown>
        </div>

        {/* 右侧内容 */}
                <div style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}>
                  {!selectedTeam ? (
                    <Empty description="请选择团队" style={{ marginTop: 120 }} />
                  ) : (
                    <Tabs
                      style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
                      styles={{
                        header: { flexShrink: 0 },
                        body: { height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" },
                        // 注意：content 样式会透传到每个 TabPane 的 inline style，
                        // 不能设 display/flexDirection（会盖过 .ant-tabs-content-hidden 的 display:none 导致所有 tab 同时显示）。
                        content: { flex: 1, minWidth: 0, minHeight: 0, overflow: "hidden" },
                      }}
                      items={[
                {
                  key: "members",
                  label: "团队成员",
                  children: (
                    <div style={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column" }}>
                      <Space style={{ marginBottom: 12, flexShrink: 0 }}>
                        <Button type="primary" onClick={() => void openMemberModal()}>
                          添加成员
                        </Button>
                      </Space>
                      <ModoTable
                        rowKey="user_id"
                        size="small"
                        loading={membersLoading}
                        dataSource={members}
                        locale={{ emptyText: <Empty description="暂无成员" /> }}
                        columns={[
                          { title: "用户ID", dataIndex: "user_id" },
                          { title: "用户名", dataIndex: "user_name", render: (v: string | null | undefined) => v || "-" },
                          { title: "手机号", dataIndex: "phone", render: (v: string | null) => v || "-" },
                          { title: "邮箱", dataIndex: "email", render: (v: string | null) => v || "-" },
                          {
                            title: "状态",
                            dataIndex: "state",
                            width: 90,
                            render: (v: string | null) => <StateTag value={v} />,
                          },
                        ]}
                      />
                    </div>
                  ),
                },
                {
                  key: "dsAuth",
                  label: "数据源授权",
                  children: (
                    <div style={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column" }}>
                      <Space style={{ marginBottom: 12, flexShrink: 0 }}>
                        <Button type="primary" loading={savingAuth} onClick={() => void doSaveAuth()}>
                          保存授权
                        </Button>
                        <Button onClick={() => selectedKey && void loadAuth(selectedKey)}>刷新</Button>
                      </Space>
                      <ModoTable<DsAuthRow>
                        rowKey="dsName"
                        size="small"
                        loading={authLoading}
                        dataSource={authRows.map((r) => ({
                          key: String(r.dsName),
                          dsName: String(r.dsName),
                          label: String(r.dsLabel || r.dsName),
                        }))}
                        locale={{ emptyText: <Empty description="暂无数据源" /> }}
                        columns={[
                          { title: "数据源英文名", dataIndex: "dsName", width: 220 },
                          { title: "数据源中文名", dataIndex: "label" },
                          {
                            title: "授权",
                            width: 120,
                            render: (_: unknown, row: DsAuthRow) => (
                              <Checkbox
                                checked={authItems.some((i) => i.dsName === row.dsName)}
                                onChange={(e) => {
                                  setAuthItems((prev) =>
                                    e.target.checked
                                      ? [
                                          ...prev.filter((i) => i.dsName !== row.dsName),
                                          {
                                            id: `new-${row.dsName}`,
                                            dsName: row.dsName,
                                            schemaName: "",
                                            teamName: selectedKey || "",
                                            isProd: "0",
                                          },
                                        ]
                                      : prev.filter((i) => i.dsName !== row.dsName),
                                  );
                                }}
                              />
                            ),
                          },
                          {
                            title: "生产",
                            width: 90,
                            render: (_: unknown, row: DsAuthRow) => (
                              <Checkbox
                                checked={authItems.some(
                                  (i) => i.dsName === row.dsName && i.isProd === "1",
                                )}
                                onChange={(e) => {
                                  setAuthItems((prev) =>
                                    prev.map((i) =>
                                      i.dsName === row.dsName
                                        ? { ...i, isProd: e.target.checked ? "1" : "0" }
                                        : i,
                                    ),
                                  );
                                }}
                              />
                            ),
                          },
                        ]}
                      />
                    </div>
                  ),
                },
              ]}
            />
          )}
        </div>
      </div>

      {/* 新增 / 编辑团队（对齐 ds 团队编辑 Modal） */}
      <Modal
        title={editing ? `编辑团队: ${editing.team_name}` : "新增团队"}
        open={formOpen}
        onCancel={() => setFormOpen(false)}
        onOk={() => void doSave()}
        confirmLoading={saving}
        okText="保存"
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item
            name="teamName"
            label="团队编码"
            rules={[{ required: true, message: "请输入团队编码" }]}
          >
            <Input placeholder="如 team_a" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="label" label="团队名称" rules={[{ required: true, message: "请输入团队名称" }]}>
            <Input placeholder="团队名称" />
          </Form.Item>
          <Form.Item name="parentTeamName" label="父团队">
            <Select allowClear showSearch placeholder="请选择父团队（根节点留空）" options={teamOptions} />
          </Form.Item>
          <Form.Item name="descr" label="描述">
            <Input.TextArea rows={3} placeholder="描述（可选）" />
          </Form.Item>
          <Form.Item name="state" label="状态" rules={[{ required: true }]}>
            <Radio.Group>
              <Radio value="1">启用</Radio>
              <Radio value="0">停用</Radio>
            </Radio.Group>
          </Form.Item>
        </Form>
      </Modal>

      {/* 添加成员（多选用户） */}
      <Modal
        title={`添加成员: ${selectedTeam?.team_name || ""}`}
        open={memberModalOpen}
        onCancel={() => setMemberModalOpen(false)}
        onOk={() => void doSaveMembers()}
        confirmLoading={savingMembers}
        okText="保存"
        width={520}
        destroyOnClose
      >
        <Select
          mode="multiple"
          allowClear
          showSearch
          style={{ width: "100%" }}
          placeholder="请选择成员（保存后覆盖当前成员列表）"
          value={targetUsers}
          onChange={(v) => setTargetUsers(v as string[])}
          options={allUsers
            .filter((u) => u.state === "1")
            .map((u) => ({
              label: `${u.user_id}${u.user_name ? `（${u.user_name}）` : ""}`,
              value: u.user_id as string,
            }))}
          optionFilterProp="label"
        />
      </Modal>
      </Card>
    </div>
  );

  return (
    // 2026-10-08: 对齐用户管理页范式——外层固定视口高度不滚动，卡片内左右分栏占满剩余高度
    // 2026-10-09: 顶部改为「动态选项卡」（复用用户管理页 ModoTabs editable-card 模式）——
    // 首 tab「团队管理」= 页面标题 + 列表（不可关闭），后续详情/编辑表单可作为可关闭 tab 打开。
    <div
      className="teams-page"
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
          <Button type="primary" onClick={() => openCreate(null)}>
            新增团队
          </Button>
        }
        items={[
          { key: "home", label: "团队管理", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />
    </div>
  );
}