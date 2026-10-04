"use client";

import React, { useState, useMemo, useEffect } from "react";
import { Layout, Menu, ConfigProvider } from "antd";
import type { MenuProps } from "antd";
import {
  ApiOutlined,
  AppstoreOutlined,
  BookOutlined,
  CloudServerOutlined,
  CommentOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileOutlined,
  FolderOutlined,
  LeftOutlined,
  RightOutlined,
  RobotOutlined,
  ToolOutlined,
  SettingOutlined,
  // 系统管理分组下的子项（用户/角色/团队/菜单/日志）
  UserOutlined,
  SafetyOutlined,
  PartitionOutlined,
  MenuOutlined,
  ProfileOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { useMenuContext, MenuTreeNode } from "./MenuContext";
import { SysMenuItem } from "@/lib/api";

const { Sider } = Layout;

// menu_icon（图标组件名）-> antd 图标；未匹配用默认图标（对齐 data-synth getIconComponent 语义）
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
  ApiOutlined: <ApiOutlined />,
  ToolOutlined: <ToolOutlined />,
};
const DEFAULT_ICON = <AppstoreOutlined />;

/** Sider 局部菜单主题（对齐 data-synth SideMenu menuTheme） */
const menuTheme = {
  components: {
    Menu: {
      subMenuItemBg: "transparent",
      itemHeight: 36,
      itemColor: "#4D5E7D",
      itemSelectedBg: "#EFF4F9",
      itemHoverBg: "#EFF4F9",
      borderRadius: 4,
      borderRadiusLG: 4,
    },
  },
};

function convertToMenuItems(menus: SysMenuItem[], level = 0): MenuProps["items"] {
  return menus.map((menu) => {
    const item: any = {
      key: menu.menu_id,
      icon: ICON_MAP[menu.menu_icon || ""] || DEFAULT_ICON,
      label: menu.menu_label || menu.menu_name || menu.route || "",
      className: level === 0 ? "root-menu-item" : undefined,
    };
    const children = (menu as MenuTreeNode).children;
    if (children && children.length > 0) {
      item.children = convertToMenuItems(children, level + 1);
    }
    return item;
  });
}

/**
 * 按当前 URL 找菜单 id。
 *
 * 支持带 query 的菜单路由（如 /system?tab=roles 指向系统管理聚合页的某个 Tab）：
 *   1. 带 query 的完整匹配（routeKey 精确相等）优先——保证 /system?tab=roles
 *      不会误命中 /system 分组本身；
 *   2. 其次 pathname 精确匹配；
 *   3. 最后 pathname 前缀匹配（/kbs/xxx 命中 /kbs）。
 */
function findMenuIdByPath(menus: SysMenuItem[], pathname: string, routeKey?: string): string | null {
  const key = routeKey ?? pathname;
  // 第一轮：带 query 的完整匹配
  for (const menu of menus) {
    if (menu.route && menu.route.includes("?") && key === menu.route) {
      return menu.menu_id as string;
    }
    const kids = (menu as MenuTreeNode).children;
    if (kids) {
      const hit = findMenuIdByPath(kids, pathname, key);
      if (hit) return hit;
    }
  }
  // 第二轮：pathname 精确 / 前缀
  for (const menu of menus) {
    if (menu.route && (pathname === menu.route || pathname.startsWith(menu.route + "/"))) {
      return menu.menu_id as string;
    }
    const kids = (menu as MenuTreeNode).children;
    if (kids) {
      const hit = findMenuIdByPath(kids, pathname, key);
      if (hit) return hit;
    }
  }
  return null;
}

function findParentKeys(menus: SysMenuItem[], targetId: string, parents: string[] = []): string[] {
  for (const menu of menus) {
    if (menu.menu_id === targetId) return parents;
    const kids = (menu as MenuTreeNode).children;
    if (kids && kids.length > 0) {
      const result = findParentKeys(kids, targetId, [...parents, menu.menu_id as string]);
      if (result.length > 0) return result;
    }
  }
  return [];
}

function findRoute(menus: SysMenuItem[], menuId: string): string | null {
  for (const menu of menus) {
    if (menu.menu_id === menuId) return menu.route || null;
    const kids = (menu as MenuTreeNode).children;
    if (kids) {
      const r = findRoute(kids, menuId);
      if (r) return r;
    }
  }
  return null;
}

export const AppSider: React.FC = () => {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { siderMenus } = useMenuContext();
  const [collapsed, setCollapsed] = useState(false);
  const [openKeys, setOpenKeys] = useState<string[]>([]);

  // 带 query 的当前路由（如 /system?tab=roles），用于菜单高亮
  const routeKey = useMemo(() => {
    const qs = searchParams?.toString?.() || "";
    return qs ? `${pathname}?${qs}` : pathname;
  }, [pathname, searchParams]);

  const menuItems = useMemo(() => convertToMenuItems(siderMenus), [siderMenus]);
  const activeKey = useMemo(
    () => findMenuIdByPath(siderMenus, pathname, routeKey),
    [siderMenus, pathname, routeKey],
  );

  // 自动展开当前页面的父级菜单
  useEffect(() => {
    if (activeKey) {
      const parents = findParentKeys(siderMenus, activeKey);
      setOpenKeys((prev) => Array.from(new Set([...prev, ...parents])));
    }
  }, [siderMenus, activeKey]);

  const handleMenuClick: MenuProps["onClick"] = (e) => {
    const route = findRoute(siderMenus, e.key);
    if (route) router.push(route);
  };

  return (
    <Sider
      collapsible
      collapsed={collapsed}
      onCollapse={setCollapsed}
      theme="light"
      width={200}
      collapsedWidth={45}
      trigger={null}
      className="side-menu-kb"
      style={{
        background: "#F9FBFD",
        borderRight: "1px solid #EFF4F9",
        height: "calc(100vh - 45px)",
        position: "sticky",
        top: 45,
        left: 0,
      }}
    >
      <ConfigProvider theme={menuTheme}>
        <Menu
          mode="inline"
          selectedKeys={activeKey ? [activeKey] : []}
          openKeys={openKeys}
          onOpenChange={setOpenKeys}
          items={menuItems}
          onClick={handleMenuClick}
          style={{
            height: "100%",
            overflowY: "auto",
            overflowX: "hidden",
            borderRight: 0,
            paddingBlockStart: 8,
            paddingBlockEnd: 48,
            background: "transparent",
          }}
        />
      </ConfigProvider>
      {/* 右下角悬浮折叠按钮（对齐 data-synth SideMenu） */}
      <div
        onClick={() => setCollapsed(!collapsed)}
        style={{
          position: "absolute",
          bottom: 12,
          right: 12,
          width: 28,
          height: 28,
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#EFF4F9",
          borderRadius: "50%",
          transition: "box-shadow 0.2s",
          zIndex: 10,
        }}
        onMouseEnter={(e: any) => {
          e.currentTarget.style.boxShadow = "0 2px 8px rgba(0, 0, 0, 0.15)";
        }}
        onMouseLeave={(e: any) => {
          e.currentTarget.style.boxShadow = "none";
        }}
      >
        {collapsed ? <RightOutlined /> : <LeftOutlined />}
      </div>
    </Sider>
  );
};
