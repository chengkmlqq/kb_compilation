"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Empty, Input, Space, Table, Tabs, Tag, Tree } from "antd";
import {
  apiListMenus,
  apiListOperationLogs,
  apiListRoles,
  apiListTeams,
  apiListUsers,
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

export default function SystemPage() {
  return (
    <Tabs
      defaultActiveKey="users"
      items={[
        { key: "users", label: "用户", children: <UsersTab /> },
        { key: "roles", label: "角色", children: <RolesTab /> },
        { key: "teams", label: "团队", children: <TeamsTab /> },
        { key: "menus", label: "菜单", children: <MenusTab /> },
        { key: "logs", label: "操作日志", children: <LogsTab /> },
      ]}
    />
  );
}