"use client";

import React, { useState, useMemo } from "react";
import { Layout } from "antd";
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
  TableOutlined,
  SettingOutlined,
  // 系统管理分组下的子项（用户/角色/团队/菜单/日志）
  UserOutlined,
  SafetyOutlined,
  PartitionOutlined,
  MenuOutlined,
  ProfileOutlined,
  TeamOutlined,
  BellOutlined,
  // 2026-10-16 补充：此前漏配的图标名（库内菜单在用但 ICON_MAP 缺，导致静默回落默认 Appstore 图标）
  ApartmentOutlined,
  BlockOutlined,
  BugOutlined,
  CheckCircleOutlined,
  LikeOutlined,
  ScheduleOutlined,
  SearchOutlined,
  BarChartOutlined,
} from "@ant-design/icons";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { useMenuContext, MenuTreeNode } from "./MenuContext";
import { SysMenuItem } from "@/lib/api";
import {
  ModoIconBook1,
  ModoIconCloudAdd,
  ModoIconCloudServer,
  ModoIconDatabase,
  ModoIconHome,
  ModoIconResourceOrchestration,
  ModoIconSemanticSearch,
  ModoIconSystemMgr,
  ModoIconTaskMonitor,
} from "@/components/ModoMenuIcons";

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
  TableOutlined: <TableOutlined />,
  UserOutlined: <UserOutlined />,
  SafetyOutlined: <SafetyOutlined />,
  PartitionOutlined: <PartitionOutlined />,
  MenuOutlined: <MenuOutlined />,
  ProfileOutlined: <ProfileOutlined />,
  TeamOutlined: <TeamOutlined />,
  BellOutlined: <BellOutlined />,
  // 2026-10-16 补充：补齐库内菜单在用但此前漏配的图标名（缺失时 iconOf 会静默回落 DEFAULT_ICON，
  // 表现为「本体Schema/编排组件/调试工具/质量评估/问答反馈/任务管理/联网搜索」全显示成默认 Appstore 图标）
  ApartmentOutlined: <ApartmentOutlined />,
  BlockOutlined: <BlockOutlined />,
  BugOutlined: <BugOutlined />,
  CheckCircleOutlined: <CheckCircleOutlined />,
  LikeOutlined: <LikeOutlined />,
  ScheduleOutlined: <ScheduleOutlined />,
  SearchOutlined: <SearchOutlined />,
  BarChartOutlined: <BarChartOutlined />,
  // 2026-10-16 补充：shandong 数据供给平台菜单实际使用的 modo 彩色图标（ModoMenuIcons.tsx 内联）
  IconBook1Color: <ModoIconBook1 />,
  IconCloudAddColor: <ModoIconCloudAdd />,
  IconCloudServerColor: <ModoIconCloudServer />,
  IconDatabaseColor: <ModoIconDatabase />,
  IconHomeColor: <ModoIconHome />,
  IconResourceOrchestrationColor: <ModoIconResourceOrchestration />,
  IconSemanticSearchColor: <ModoIconSemanticSearch />,
  IconSystemMgrColor: <ModoIconSystemMgr />,
  IconTaskMonitorColor: <ModoIconTaskMonitor />,
};
const DEFAULT_ICON = <AppstoreOutlined />;

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

const iconOf = (menu: SysMenuItem) => ICON_MAP[menu.menu_icon || ""] || DEFAULT_ICON;

export const AppSider: React.FC = () => {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { siderMenus } = useMenuContext();
  const [collapsed, setCollapsed] = useState(false);
  // 用户手动点选的一级分组（默认跟随当前路由所在分组）
  const [groupOverride, setGroupOverride] = useState<string | null>(null);

  // 带 query 的当前路由（如 /system?tab=roles），用于菜单高亮
  const routeKey = useMemo(() => {
    const qs = searchParams?.toString?.() || "";
    return qs ? `${pathname}?${qs}` : pathname;
  }, [pathname, searchParams]);

  // 当前路由命中的菜单（二级页面项）
  const activeKey = useMemo(
    () => findMenuIdByPath(siderMenus, pathname, routeKey),
    [siderMenus, pathname, routeKey],
  );

  // 当前路由所属的一级分组（无命中时默认第一组）
  const activeGroupId = useMemo(() => {
    if (!activeKey) return siderMenus[0]?.menu_id ?? null;
    const isTop = siderMenus.some((m) => m.menu_id === activeKey);
    if (isTop) return activeKey;
    const parents = findParentKeys(siderMenus, activeKey);
    return parents[0] ?? siderMenus[0]?.menu_id ?? null;
  }, [siderMenus, activeKey]);

  const groupId = groupOverride ?? activeGroupId;
  const currentGroup = siderMenus.find((m) => m.menu_id === groupId) as MenuTreeNode | undefined;
  // 对齐 data_supply_base narrow 菜单：第二列子菜单仅在当前一级分组有 children 时渲染
  // （DSB 壳 `.narrow-child-menu` 渲染守卫 = children.length > 0；叶子一级无二级菜单，第二列整列不展示）
  const hasSub = (currentGroup?.children?.length ?? 0) > 0;
  const subItems = hasSub ? (currentGroup!.children ?? []) : [];

  // 一级卡片点击：有子菜单 → 展开第二列；叶子一级（无子菜单）→ 收起第二列（对齐 DSB childMenuHide），有路由则直接跳转
  const handleGroupClick = (menu: MenuTreeNode) => {
    const hasChildren = (menu.children?.length ?? 0) > 0;
    if (hasChildren) {
      setGroupOverride(menu.menu_id as string);
    } else {
      setGroupOverride(null);
      if (menu.route) router.push(menu.route);
    }
  };

  const handleSubClick = (menu: SysMenuItem) => {
    if (menu.route) router.push(menu.route);
  };

  return (
    <Sider
      collapsible
      collapsed={collapsed}
      onCollapse={setCollapsed}
      theme="light"
      width={hasSub ? 240 : 80}
      collapsedWidth={80}
      trigger={null}
      className="side-menu-kb"
      style={{
        background: "transparent",
        height: "calc(100vh - var(--app-header-h, 48px))",
        position: "sticky",
        top: 48,
        left: 0,
      }}
    >
      {/* 左侧导航（对齐 data_supply_base narrow 菜单：第一列 80px 方块卡片(图标上文字下) + 第二列 160px 子菜单） */}
      <div className="kb-nav">
        {/* 第一列：一级菜单方块卡片 */}
        <div className="kb-nav-rail">
          {siderMenus.map((m) => {
            const node = m as MenuTreeNode;
            const sel = m.menu_id === (groupId ?? activeGroupId);
            return (
              <div
                key={m.menu_id}
                className={`kb-nav-card${sel ? " is-active" : ""}`}
                title={m.menu_label || m.menu_name}
                onClick={() => handleGroupClick(node)}
              >
                <span className="kb-nav-card-icon">{iconOf(m)}</span>
                <span className="kb-nav-card-label">{m.menu_label || m.menu_name || ""}</span>
              </div>
            );
          })}
        </div>
        {/* 第二列：当前一级分组下的子菜单（对齐 DSB：无二级菜单时不渲染整列） */}
        {!collapsed && hasSub && (
          <div className="kb-nav-sub">
            {subItems.map((c) => {
              const sel = c.menu_id === activeKey;
              return (
                <div
                  key={c.menu_id}
                  className={`kb-nav-sub-item${sel ? " is-active" : ""}`}
                  title={c.menu_label || c.menu_name}
                  onClick={() => handleSubClick(c)}
                >
                  {c.menu_label || c.menu_name || ""}
                </div>
              );
            })}
          </div>
        )}
      </div>

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