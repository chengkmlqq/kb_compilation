"use client";

import { useEffect, useState } from "react";
import { App, ConfigProvider, Layout, Menu, Typography, theme } from "antd";
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
} from "@ant-design/icons";
import { usePathname, useRouter } from "next/navigation";
import { apiMe, apiMyMenus, SysMenuItem } from "@/lib/api";

const { Header, Sider, Content } = Layout;

const DEFAULT_MENU_ITEMS = [
  { key: "/kbs", icon: <AppstoreOutlined />, label: "知识库管理" },
  { key: "/chat", icon: <CommentOutlined />, label: "智能问答" },
  { key: "/agents", icon: <RobotOutlined />, label: "智能体配置" },
  { key: "/datasources", icon: <DatabaseOutlined />, label: "数据源" },
  { key: "/wiki", icon: <BookOutlined />, label: "Wiki 总览" },
  { key: "/jobs", icon: <DashboardOutlined />, label: "任务监控" },
  { key: "/weknora", icon: <AppstoreOutlined />, label: "WeKnora 库" },
  { key: "/models", icon: <CloudServerOutlined />, label: "模型配置" },
  { key: "/system", icon: <SettingOutlined />, label: "系统管理" },
];

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

type MenuItem = { key: string; icon: React.ReactNode; label: string };

function menusToItems(menus: SysMenuItem[]): MenuItem[] {
  return menus
    .filter((m) => m.route)
    .sort((a, b) => (a.sort_num ?? 0) - (b.sort_num ?? 0))
    .map((m) => ({
      key: m.route as string,
      icon: ICON_MAP[m.menu_icon || ""] || DEFAULT_ICON,
      label: m.menu_label || m.menu_name || m.route || "",
    }));
}

export default function MainLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [userName, setUserName] = useState("");
  const [checked, setChecked] = useState(false);
  // my-menus 返回空（尚未配置菜单/授权）时回退到内置菜单，保证平台可用；
  // 一旦配置了菜单，则严格按角色授权渲染 Sider。
  const [menuItems, setMenuItems] = useState<MenuItem[]>(DEFAULT_MENU_ITEMS);

  useEffect(() => {
    (async () => {
      const me = await apiMe();
      if (!me.success || !me.data) {
        router.replace("/login");
        return;
      }
      setUserName(me.data.userName || me.data.userId || "");
      setChecked(true);
      const menus = await apiMyMenus();
      if (menus.success && menus.data && menus.data.items.length > 0) {
        setMenuItems(menusToItems(menus.data.items));
      }
    })();
  }, [router]);

  const selectedKey = menuItems.find((m) => pathname.startsWith(m.key))?.key || "/kbs";

  const logout = () => {
    document.cookie = "x-next-identity=; path=/; max-age=0";
    router.replace("/login");
  };

  if (!checked) {
    return null; // identity check in flight
  }

  return (
    <ConfigProvider theme={{ algorithm: theme.defaultAlgorithm }}>
      <App>
        <Layout style={{ minHeight: "100vh" }}>
          <Sider theme="dark" width={220}>
            <div style={{ padding: 16, color: "#fff", fontSize: 16, fontWeight: 600 }}>
              知识库平台
            </div>
            <Menu
              theme="dark"
              mode="inline"
              selectedKeys={[selectedKey]}
              items={menuItems}
              onClick={({ key }) => router.push(key)}
            />
          </Sider>
          <Layout>
            <Header
              style={{
                background: "#fff",
                padding: "0 24px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <Typography.Text type="secondary">知识库 wiki 平台</Typography.Text>
              <span style={{ display: "flex", alignItems: "center", gap: 16 }}>
                {userName && <Typography.Text>{userName}</Typography.Text>}
                <a onClick={logout} style={{ cursor: "pointer" }}>
                  <LogoutOutlined /> 退出
                </a>
              </span>
            </Header>
            <Content style={{ margin: 24 }}>{children}</Content>
          </Layout>
        </Layout>
      </App>
    </ConfigProvider>
  );
}