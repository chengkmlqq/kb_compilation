"use client";

import React, {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  useMemo,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { apiMyMenus, SysMenuItem } from "@/lib/api";

const STORAGE_KEY = "modo_selected_top_menu";

export interface MenuTreeNode extends SysMenuItem {
  children?: MenuTreeNode[];
}

interface MenuContextType {
  /** 所有顶级菜单（含可选子菜单树）—— Header 顶级导航用 */
  topMenus: MenuTreeNode[];
  /** 当前选中顶级菜单的子菜单 —— Sider 用；单级回退时等于全部可见菜单 */
  siderMenus: SysMenuItem[];
  /** 当前选中的顶级菜单 ID */
  selectedTopMenuId: string | null;
  /** 设置选中的顶级菜单 */
  setSelectedTopMenu: (menuId: string) => void;
  /** 菜单加载状态 */
  loading: boolean;
  /** 是否处于单级回退模式（菜单库无层级 → 全部平铺进 Sider） */
  flatMode: boolean;
}

const MenuContext = createContext<MenuContextType | undefined>(undefined);

/** 内置菜单（后端 my-menus 为空时的回退；三级骨架，对齐 seed 与 ds） */
const FALLBACK_MENUS: SysMenuItem[] = [
  // 三级骨架（对齐 data-synth）：顶级 → 分组 → 页面；分组 route 留空=纯目录
  { menu_id: "grp_knowledge", menu_name: "grp_knowledge", menu_label: "知识管理", route: null, parent_id: "root_kb", menu_icon: "FolderOutlined", sort_num: 1, state: "1" },
  { menu_id: "kbs", menu_name: "kbs", menu_label: "知识库管理", route: "/kbs", parent_id: "grp_knowledge", menu_icon: "AppstoreOutlined", sort_num: 1, state: "1" },
  { menu_id: "wiki", menu_name: "wiki", menu_label: "Wiki 总览", route: "/wiki", parent_id: "grp_knowledge", menu_icon: "BookOutlined", sort_num: 2, state: "1" },
  { menu_id: "files", menu_name: "files", menu_label: "文件管理", route: "/files", parent_id: "grp_knowledge", menu_icon: "FileOutlined", sort_num: 3, state: "1" },
  { menu_id: "grp_ai", menu_name: "grp_ai", menu_label: "智能应用", route: null, parent_id: "root_kb", menu_icon: "FolderOutlined", sort_num: 2, state: "1" },
  { menu_id: "chat", menu_name: "chat", menu_label: "智能问答", route: "/chat", parent_id: "grp_ai", menu_icon: "CommentOutlined", sort_num: 1, state: "1" },
  { menu_id: "agents", menu_name: "agents", menu_label: "智能体配置", route: "/agents", parent_id: "grp_ai", menu_icon: "RobotOutlined", sort_num: 2, state: "1" },
  { menu_id: "websearch", menu_name: "websearch", menu_label: "联网搜索", route: "/websearch", parent_id: "grp_ai", menu_icon: "SearchOutlined", sort_num: 3, state: "1" },
  { menu_id: "grp_data", menu_name: "grp_data", menu_label: "数据与任务", route: null, parent_id: "root_kb", menu_icon: "FolderOutlined", sort_num: 3, state: "1" },
  { menu_id: "datasources", menu_name: "datasources", menu_label: "数据源", route: "/datasources", parent_id: "grp_data", menu_icon: "DatabaseOutlined", sort_num: 1, state: "1" },
  { menu_id: "jobs", menu_name: "jobs", menu_label: "任务监控", route: "/jobs", parent_id: "grp_data", menu_icon: "DashboardOutlined", sort_num: 2, state: "1" },
  { menu_id: "cron", menu_name: "cron", menu_label: "任务管理", route: "/cron", parent_id: "grp_data", menu_icon: "ScheduleOutlined", sort_num: 3, state: "1" },
  { menu_id: "workers", menu_name: "workers", menu_label: "主机监控", route: "/workers", parent_id: "grp_data", menu_icon: "CloudServerOutlined", sort_num: 4, state: "1" },
  { menu_id: "system", menu_name: "system", menu_label: "系统管理", route: "/system", parent_id: "root_kb", menu_icon: "SettingOutlined", sort_num: 4, state: "1" },
  { menu_id: "sys_users", menu_name: "sys_users", menu_label: "用户管理", route: "/system/users", parent_id: "system", menu_icon: "TeamOutlined", sort_num: 1, state: "1" },
  { menu_id: "sys_roles", menu_name: "sys_roles", menu_label: "角色管理", route: "/system/roles", parent_id: "system", menu_icon: "SafetyOutlined", sort_num: 2, state: "1" },
  { menu_id: "sys_teams", menu_name: "sys_teams", menu_label: "团队管理", route: "/system/teams", parent_id: "system", menu_icon: "PartitionOutlined", sort_num: 3, state: "1" },
  { menu_id: "sys_menus", menu_name: "sys_menus", menu_label: "菜单管理", route: "/system/menus", parent_id: "system", menu_icon: "MenuOutlined", sort_num: 4, state: "1" },
  { menu_id: "sys_logs", menu_name: "sys_logs", menu_label: "操作日志", route: "/system/logs", parent_id: "system", menu_icon: "ProfileOutlined", sort_num: 5, state: "1" },
  { menu_id: "notify", menu_name: "notify", menu_label: "通知管理", route: "/notification", parent_id: "system", menu_icon: "BellOutlined", sort_num: 6, state: "1" },
  { menu_id: "models", menu_name: "models", menu_label: "模型配置", route: "/models", parent_id: "system", menu_icon: "CloudServerOutlined", sort_num: 7, state: "1" },
  { menu_id: "mcps", menu_name: "mcps", menu_label: "MCP 管理", route: "/mcps", parent_id: "system", menu_icon: "ApiOutlined", sort_num: 8, state: "1" },
  { menu_id: "skills", menu_name: "skills", menu_label: "技能管理", route: "/skills", parent_id: "system", menu_icon: "ToolOutlined", sort_num: 9, state: "1" },
];

/** 平铺列表 → 树（对齐 data-synth menu-actions buildTree 语义） */
function buildTree(items: SysMenuItem[]): MenuTreeNode[] {
  const sorted = [...items].sort((a, b) => (a.sort_num ?? 0) - (b.sort_num ?? 0));
  const childrenOf = new Map<string | null, MenuTreeNode[]>();
  for (const m of sorted) {
    const key = m.parent_id && m.parent_id !== "" ? m.parent_id : null;
    if (!childrenOf.has(key)) childrenOf.set(key, []);
    childrenOf.get(key)!.push({ ...m });
  }
  const attach = (node: MenuTreeNode): MenuTreeNode => {
    const kids = childrenOf.get(node.menu_id || "") || [];
    node.children = kids.map(attach);
    if (node.children.length === 0) delete node.children;
    return node;
  };
  return (childrenOf.get(null) || []).map(attach);
}

/** 深度：任意菜单是否带子级（决定两级/单级模式） */
function anyHasChildren(items: SysMenuItem[]): boolean {
  const ids = new Set(items.map((m) => m.menu_id));
  return items.some((m) => m.parent_id && m.parent_id !== "" && ids.has(m.parent_id));
}

/**
 * 判断菜单树中是否包含当前路径（用于决定当前应高亮/展开哪个顶级菜单）。
 *
 * 支持带 query 的路由（如 /system?tab=roles）：带 query 的菜单先做完整匹配，
 * 未命中再回退 pathname 精确/前缀匹配（对齐 AppSider.findMenuIdByPath 的两轮策略）。
 */
function checkContainsPath(menu: MenuTreeNode, pathname: string, routeKey?: string): boolean {
  if (menu.route && menu.route.trim() !== "") {
    const route = menu.route;
    if (routeKey && route.includes("?") && routeKey === route) return true;
    // 带 query 的菜单不能用 pathname 命中（/system?tab=roles 与 /system 不同 Tab）
    if (!route.includes("?") && (pathname === route || pathname.startsWith(route + "/"))) {
      return true;
    }
  }
  return (menu.children || []).some((c) => checkContainsPath(c, pathname, routeKey));
}

export function useMenuContext() {
  const ctx = useContext(MenuContext);
  if (!ctx) throw new Error("useMenuContext must be used within a MenuProvider");
  return ctx;
}

export function MenuProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  // 带 query 的当前路由（如 /system?tab=roles），菜单归属判定需要
  const routeKey = useMemo(() => {
    const qs = searchParams?.toString?.() || "";
    return qs ? `${pathname}?${qs}` : pathname;
  }, [pathname, searchParams]);
  const [menus, setMenus] = useState<SysMenuItem[]>(FALLBACK_MENUS);
  const [selectedTopMenuId, setSelectedTopMenuId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 拉取 my-menus；空/失败回退内置菜单
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiMyMenus();
        if (!cancelled && res.success && res.data && res.data.items.length > 0) {
          setMenus(res.data.items);
        }
      } catch (e) {
        console.error("my-menus fetch failed, using fallback", e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 模式判定：有层级 → 两级；否则单级回退
  const flatMode = !anyHasChildren(menus);
  const tree = useMemo(() => buildTree(menus), [menus]);

  const topMenus: MenuTreeNode[] = flatMode ? [] : tree;
  const flatList = useMemo(
    () => [...menus].sort((a, b) => (a.sort_num ?? 0) - (b.sort_num ?? 0)),
    [menus]
  );

  // 初始化选中：路径匹配 / sessionStorage / 第一个
  const initializedRef = useRef(false);
  useEffect(() => {
    if (loading) return;
    if (initializedRef.current) return;
    initializedRef.current = true;

    let matched: string | null = null;
    if (flatMode) {
      // 单级：无需顶级选中（Sider 全量）
      setSelectedTopMenuId(null);
      return;
    }
    const stored = (() => {
      try {
        return sessionStorage.getItem(STORAGE_KEY);
      } catch {
        return null;
      }
    })();
    if (stored && topMenus.some((m) => m.menu_id === stored)) matched = stored;
    if (!matched) {
      const hit = topMenus.find((m) => checkContainsPath(m, pathname, routeKey));
      if (hit) matched = hit.menu_id as string;
    }
    const id = matched || (topMenus[0]?.menu_id as string) || null;
    setSelectedTopMenuId(id);
    if (id) {
      try {
        sessionStorage.setItem(STORAGE_KEY, id);
      } catch {
        /* ignore */
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, flatMode]);

  // 路径变化自动切换顶级菜单（非手动选择时）
  const manualRef = useRef(false);
  useEffect(() => {
    if (loading || flatMode || manualRef.current) return;
    if (!selectedTopMenuId) return;
    const current = topMenus.find((m) => m.menu_id === selectedTopMenuId);
    if (current && checkContainsPath(current, pathname, routeKey)) return;
    const hit = topMenus.find(
      (m) => m.menu_id !== selectedTopMenuId && checkContainsPath(m, pathname, routeKey),
    );
    if (hit) {
      setSelectedTopMenuId(hit.menu_id as string);
      try {
        sessionStorage.setItem(STORAGE_KEY, hit.menu_id as string);
      } catch {
        /* ignore */
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, loading, flatMode]);

  const siderMenus: SysMenuItem[] = flatMode
    ? flatList
    : (topMenus.find((m) => m.menu_id === selectedTopMenuId)?.children as SysMenuItem[]) || [];

  const setSelectedTopMenu = useCallback(
    (menuId: string) => {
      manualRef.current = true;
      setSelectedTopMenuId(menuId);
      try {
        sessionStorage.setItem(STORAGE_KEY, menuId);
      } catch {
        /* ignore */
      }
      // 跳转该顶级菜单的第一个可导航路由（对齐 MenuContext.findFirstRoute）
      const topMenu = topMenus.find((m) => m.menu_id === menuId);
      const walk = (node: MenuTreeNode): string | null => {
        if (node.menu_id === topMenu?.menu_id && node.route && node.route.trim() !== "") return node.route;
        for (const c of node.children || []) {
          const r = walk(c);
          if (r) return r;
        }
        return null;
      };
      if (topMenu) {
        const first = walk(topMenu);
        if (first) router.push(first);
      }
    },
    [topMenus, router]
  );

  return (
    <MenuContext.Provider
      value={{ topMenus, siderMenus, selectedTopMenuId, setSelectedTopMenu, loading, flatMode }}
    >
      {children}
    </MenuContext.Provider>
  );
}