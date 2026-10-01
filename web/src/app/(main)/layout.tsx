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
} from "@ant-design/icons";
import { usePathname, useRouter } from "next/navigation";
import { apiMe } from "@/lib/api";

const { Header, Sider, Content } = Layout;

const MENU_ITEMS = [
  { key: "/kbs", icon: <AppstoreOutlined />, label: "知识库管理" },
  { key: "/chat", icon: <CommentOutlined />, label: "智能问答" },
  { key: "/agents", icon: <RobotOutlined />, label: "智能体配置" },
  { key: "/datasources", icon: <DatabaseOutlined />, label: "数据源" },
  { key: "/wiki", icon: <BookOutlined />, label: "Wiki 总览" },
  { key: "/jobs", icon: <DashboardOutlined />, label: "任务监控" },
  { key: "/models", icon: <CloudServerOutlined />, label: "模型配置" },
  { key: "/system", icon: <SettingOutlined />, label: "系统管理" },
];

export default function MainLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [userName, setUserName] = useState("");
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    (async () => {
      const me = await apiMe();
      if (!me.success || !me.data) {
        router.replace("/login");
        return;
      }
      setUserName(me.data.userName || me.data.userId || "");
      setChecked(true);
    })();
  }, [router]);

  const selectedKey =
    MENU_ITEMS.find((m) => pathname.startsWith(m.key))?.key || "/kbs";

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
              items={MENU_ITEMS}
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
