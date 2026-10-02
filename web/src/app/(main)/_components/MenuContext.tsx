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
import { usePathname, useRouter } from "next/navigation";
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

/** 内置菜单（后端 my-menus 为空时的回退；8 项知识库顶级菜单） */
const FALLBACK_MENUS: SysMenuItem[] = [
  { menu_id: "kbs", menu_name: "kbs", menu_label: "知识库管理", route: "/kbs", menu_icon: "AppstoreOutlined", sort_num: 1, state: "1" },
  { menu_id: "chat", menu_name: "chat", menu_label: "智能问答", route: "/chat", menu_icon: "CommentOutlined", sort_num: 2, state: "1" },
  { menu_id: "agents", menu_name: "agents", menu_label: "智能体配置", route: "/agents", menu_icon: "RobotOutlined", sort_num: 3, state: "1" },
  { menu_id: "datasources", menu_name: "datasources", menu_label: "数据源", route: "/datasources", menu_icon: "DatabaseOutlined", sort_num: 4, state: "1" },
  { menu_id: "wiki", menu_name: "wiki", menu_label: "Wiki 总览", route: "/wiki", menu_icon: "BookOutlined", sort_num: 5, state: "1" },
  { menu_id: "jobs", menu_name: "jobs", menu_label: "任务监控", route: "/jobs", menu_icon: "DashboardOutlined", sort_num: 6, state: "1" },
  { menu_id: "models", menu_name: "models", menu_label: "模型配置", route: "/models", menu_icon: "CloudServerOutlined", sort_num: 7, state: "1" },
  { menu_id: "system", menu_name: "system", menu_label: "系统管理", route: "/system", menu_icon: "SettingOutlined", sort_num: 8, state: "1" },
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

/** 根据路径找到所属顶级菜单 ID（对齐 MenuContext.findTopMenuByPath） */
function checkContainsPath(menu: MenuTreeNode, pathname: string): boolean {
  if (menu.route && menu.route.trim() !== "") {
    const route = menu.route;
    if (pathname === route || pathname.startsWith(route + "/")) return true;
  }
  return (menu.children || []).some((c) => checkContainsPath(c, pathname));
}

export function useMenuContext() {
  const ctx = useContext(MenuContext);
  if (!ctx) throw new Error("useMenuContext must be used within a MenuProvider");
  return ctx;
}

export function MenuProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
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
      const hit = topMenus.find((m) => checkContainsPath(m, pathname));
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
    if (current && checkContainsPath(current, pathname)) return;
    const hit = topMenus.find((m) => m.menu_id !== selectedTopMenuId && checkContainsPath(m, pathname));
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