"use client";

import { useEffect, useMemo, useState } from "react";
import { App, ConfigProvider, Layout, Menu, Typography } from "antd";
import {
  AppstoreOutlined,
  BookOutlined,
  CommentOutlined,
  DatabaseOutlined,
  LogoutOutlined,
  RobotOutlined,
  SettingOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  FileOutlined,
  FolderOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import { usePathname, useRouter } from "next/navigation";
import { apiMe, apiMyMenus, SysMenuItem } from "@/lib/api";
import { modoTheme } from "@/lib/theme";

const { Header, Sider, Content } = Layout;

// menu_icon（图标组件名）-> antd 图标；未匹配用默认图标
const ICON_MAP: Record<string, React.ReactNode> = {
  AppstoreOutlined: <AppstoreOutlined />,
  BookOutlined: <BookOutlined />,
  CommentOutlined: <CommentOutlined />,
  DatabaseOutlined: <DatabaseOutlined />,
  RobotOutlined: <RobotOutlined />,
  SettingOutlined: <SettingOutlined />,
  CloudServerOutlined: <CloudServerOutlined />,
  DashboardOutlined: <DashboardOutlined />,
  FileOutlined: <FileOutlined />,
  FolderOutlined: <FolderOutlined />,
};
const DEFAULT_ICON = <AppstoreOutlined />;

const DEFAULT_MENUS: SysMenuItem[] = [
  { menu_id: "kbs", menu_name: "kbs", menu_label: "知识库管理", route: "/kbs", menu_icon: "AppstoreOutlined", sort_num: 1, state: "1" },
  { menu_id: "chat", menu_name: "chat", menu_label: "智能问答", route: "/chat", menu_icon: "CommentOutlined", sort_num: 2, state: "1" },
  { menu_id: "agents", menu_name: "agents", menu_label: "智能体配置", route: "/agents", menu_icon: "RobotOutlined", sort_num: 3, state: "1" },
  { menu_id: "datasources", menu_name: "datasources", menu_label: "数据源", route: "/datasources", menu_icon: "DatabaseOutlined", sort_num: 4, state: "1" },
  { menu_id: "wiki", menu_name: "wiki", menu_label: "Wiki 总览", route: "/wiki", menu_icon: "BookOutlined", sort_num: 5, state: "1" },
  { menu_id: "jobs", menu_name: "jobs", menu_label: "任务监控", route: "/jobs", menu_icon: "DashboardOutlined", sort_num: 6, state: "1" },
  { menu_id: "models", menu_name: "models", menu_label: "模型配置", route: "/models", menu_icon: "CloudServerOutlined", sort_num: 7, state: "1" },
  { menu_id: "system", menu_name: "system", menu_label: "系统管理", route: "/system", menu_icon: "SettingOutlined", sort_num: 8, state: "1" },
];

function buildTree(menus: SysMenuItem[], parentId = ""): NonNullable<MenuProps["items"]> {
  return menus
    .filter((m) => (m.parent_id || "") === parentId)
    .sort((a, b) => (a.sort_num ?? 0) - (b.sort_num ?? 0))
    .map((m) => {
      const children = buildTree(menus, m.menu_id || "");
      return {
        key: m.menu_id as string,
        icon: ICON_MAP[m.menu_icon || ""] || DEFAULT_ICON,
        label: m.menu_label || m.menu_name || m.route || "",
        ...(children.length > 0 ? { children } : {}),
      };
    });
}

/** 按路径匹配菜单 id（route 前缀匹配，对齐 AppSider.findMenuIdByPath） */
function findMenuIdByPath(menus: SysMenuItem[], pathname: string): string | null {
  for (const menu of menus) {
    if (menu.route && (pathname === menu.route || pathname.startsWith(menu.route + "/"))) {
      return menu.menu_id as string;
    }
  }
  return null;
}

function findParentKeys(menus: SysMenuItem[], targetId: string, parents: string[] = []): string[] {
  for (const menu of menus) {
    if (menu.menu_id === targetId) return parents;
    const kids = menus.filter((m) => (m.parent_id || "") === (menu.menu_id || ""));
    if (kids.length > 0) {
      const result = findParentKeys(kids, targetId, [...parents, menu.menu_id as string]);
      if (result.length > 0) return result;
    }
  }
  return [];
}

export default function MainLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [userName, setUserName] = useState("");
  const [checked, setChecked] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  // my-menus 返回空（尚未配置菜单/授权）时回退到内置菜单，保证平台可用；
  // 一旦配置了菜单，则严格按角色授权渲染 Sider。
  const [menus, setMenus] = useState<SysMenuItem[]>(DEFAULT_MENUS);

  useEffect(() => {
    (async () => {
      const me = await apiMe();
      if (!me.success || !me.data) {
        router.replace("/login");
        return;
      }
      setUserName(me.data.userName || me.data.userId || "");
      setChecked(true);
      const res = await apiMyMenus();
      if (res.success && res.data && res.data.items.length > 0) {
        setMenus(res.data.items);
      }
    })();
  }, [router]);

  const menuItems = useMemo(() => {
    const roots = menus.filter((m) => !m.parent_id || m.parent_id === "");
    return buildTree(menus).length > 0
      ? buildTree(menus)
      : roots.map((m) => ({
          key: m.menu_id as string,
          icon: ICON_MAP[m.menu_icon || ""] || DEFAULT_ICON,
          label: m.menu_label || m.menu_name || m.route || "",
        }));
  }, [menus]);

  const activeKey = useMemo(() => findMenuIdByPath(menus, pathname) || undefined, [menus, pathname]);

  const [openKeys, setOpenKeys] = useState<string[]>([]);
  useEffect(() => {
    if (activeKey) {
      const parents = findParentKeys(menus, activeKey);
      setOpenKeys((prev) => Array.from(new Set([...prev, ...parents])));
    }
  }, [menus, activeKey]);

  const logout = () => {
    document.cookie = "x-next-identity=; path=/; max-age=0";
    router.replace("/login");
  };

  if (!checked) {
    return null; // identity check in flight
  }

  return (
    <ConfigProvider theme={modoTheme}>
      <App>
        <Layout style={{ minHeight: "100vh" }}>
          <Header
            style={{
              background: "#fff",
              padding: "0 24px",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              borderBottom: "1px solid #EFF4F9",
              height: 56,
              lineHeight: "56px",
            }}
          >
            <Typography.Text strong style={{ fontSize: 16 }}>
              知识库平台
            </Typography.Text>
            <span style={{ display: "flex", alignItems: "center", gap: 16 }}>
              {userName && <Typography.Text>{userName}</Typography.Text>}
              <a onClick={logout} style={{ cursor: "pointer" }}>
                <LogoutOutlined /> 退出
              </a>
            </span>
          </Header>
          <Layout style={{ flex: 1 }} hasSider>
            <Sider
              className="modo-sider"
              theme="light"
              width={220}
              collapsedWidth={56}
              collapsible
              collapsed={collapsed}
              trigger={null}
              style={{ borderRight: "1px solid #EFF4F9", background: "#fff" }}
            >
              <div style={{ display: "flex", justifyContent: "flex-end", padding: "8px 12px" }}>
                {collapsed ? (
                  <MenuUnfoldOutlined onClick={() => setCollapsed(false)} style={{ cursor: "pointer", color: "#79879C" }} />
                ) : (
                  <MenuFoldOutlined onClick={() => setCollapsed(true)} style={{ cursor: "pointer", color: "#79879C" }} />
                )}
              </div>
              <Menu
                mode="inline"
                selectedKeys={activeKey ? [activeKey] : []}
                openKeys={openKeys}
                onOpenChange={setOpenKeys}
                items={menuItems}
                onClick={({ key }) => {
                  const target = menus.find((m) => m.menu_id === key);
                  if (target?.route) router.push(target.route);
                }}
              />
            </Sider>
            <Layout style={{ background: "#F5F7FA" }}>
              <Content style={{ margin: 0, padding: "10px 12px", overflow: "auto" }}>
                {children}
              </Content>
            </Layout>
          </Layout>
        </Layout>
      </App>
    </ConfigProvider>
  );
}