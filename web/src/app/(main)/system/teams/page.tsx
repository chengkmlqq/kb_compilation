"use client";

/** 团队管理（对齐 ds TeamManagerZj：左侧团队树 + 右键新增/删除/编辑 +
 * 搜索高亮；右侧 Tabs：团队信息 / 团队成员）。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Dropdown,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Tabs,
  Tree,
} from "antd";
import {
  apiCreateTeam,
  apiDeleteTeam,
  apiGetTeamMembers,
  apiListTeams,
  apiListUsers,
  apiSaveTeamMembers,
  apiUpdateTeam,
  SysTeamItem,
  SysTeamMemberItem,
  SysUserItem,
} from "@/lib/api";
import { StateTag } from "../_shared";

type TeamFormValues = {
  teamName: string;
  label: string;
  parentTeamName?: string;
  descr?: string;
  state: string;
};

type TeamNode = SysTeamItem & { key: string; title: React.ReactNode; children?: TeamNode[] };

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
  const { message } = App.useApp();
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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListTeams(1, 500);
      if (res.success) setTeams(res.data?.items || []);
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
            <span>＋</span> 新增
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
          if (ctx?.team) void doDelete(ctx.team);
          setCtx(null);
        },
      },
    ],
  };

  return (
    // 2026-10-07: 统一页面外边距（对齐用户管理页 padding:8）
    <div style={{ padding: 8, height: "100%", overflow: "auto" }}>
      <Card title="团队" style={{ minHeight: 520 }}>
        <div style={{ display: "flex", gap: 16, height: "100%" }}>
          {/* 左侧团队树 */}
          <div style={{ width: 280, flexShrink: 0, borderRight: "1px solid #f0f0f0", paddingRight: 12 }}>
            <Input.Search
              placeholder="输入关键字进行过滤"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              style={{ marginBottom: 8 }}
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
                style={{ maxHeight: 440, overflow: "auto" }}
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
          <div style={{ flex: 1, minWidth: 0 }}>
            {!selectedTeam ? (
              <Empty description="请选择团队" style={{ marginTop: 120 }} />
            ) : (
              <Tabs
                items={[
                  {
                    key: "info",
                    label: "团队信息",
                    children: (
                      <div>
                        <Descriptions
                          bordered
                          size="small"
                          column={1}
                          items={[
                            { key: "name", label: "团队编码", children: selectedTeam.team_name },
                            { key: "label", label: "团队名称", children: selectedTeam.label || "-" },
                            { key: "parent", label: "父团队", children: selectedTeam.parent_team_name || "-" },
                            { key: "descr", label: "描述", children: selectedTeam.descr || "-" },
                            {
                              key: "state",
                              label: "状态",
                              children: <StateTag value={selectedTeam.state} />,
                            },
                            { key: "create", label: "创建时间", children: selectedTeam.create_dt || "-" },
                          ]}
                        />
                        <Space style={{ marginTop: 12 }}>
                          <Button type="primary" onClick={() => openEdit(selectedTeam)}>
                            编辑
                          </Button>
                          <Popconfirm
                            title={`确认删除该团队吗？${selectedTeam.team_name}`}
                            onConfirm={() => void doDelete(selectedTeam)}
                          >
                            <Button danger>删除</Button>
                          </Popconfirm>
                          <Button onClick={() => openCreate(selectedTeam)}>新增子团队</Button>
                        </Space>
                      </div>
                    ),
                  },
                  {
                    key: "members",
                    label: "团队成员",
                    children: (
                      <div>
                        <Space style={{ marginBottom: 12 }}>
                          <Button type="primary" onClick={() => void openMemberModal()}>
                            添加成员
                          </Button>
                        </Space>
                        <Table
                          rowKey="user_id"
                          size="small"
                          loading={membersLoading}
                          dataSource={members}
                          pagination={false}
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
}