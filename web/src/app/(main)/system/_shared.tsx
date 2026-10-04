"use client";

/**
 * 系统管理各独立页面共享的 UI 工具（对齐 ds system/* 独立页面结构）。
 */
import { Tag } from "antd";
import type { DataNode } from "antd/es/tree";
import { SysMenuItem } from "@/lib/api";

export function StateTag({ value }: { value?: string | null }) {
  if (value === "1") return <Tag color="success">启用</Tag>;
  if (value === "0") return <Tag color="default">停用</Tag>;
  return <Tag>{value || "-"}</Tag>;
}

// 菜单数组 -> antd 树节点（授权勾选 / 菜单管理共用）
// disabledIds：编辑时传入「自身 + 全部子孙」，禁止把节点挂到自己的后代下（否则树成环、菜单消失）
export function menusToTreeData(items: SysMenuItem[], disabledIds?: Set<string>): DataNode[] {
  const childrenOf = new Map<string, SysMenuItem[]>();
  for (const m of items) {
    const pid = m.parent_id || "";
    if (!childrenOf.has(pid)) childrenOf.set(pid, []);
    childrenOf.get(pid)!.push(m);
  }
  const toNode = (m: SysMenuItem): DataNode => ({
    key: m.menu_id as string,
    title: `${m.menu_label || m.menu_name}${m.route ? `（${m.route}）` : ""}`,
    disabled: disabledIds?.has(m.menu_id as string) || undefined,
    children: (childrenOf.get(m.menu_id || "") || []).map(toNode),
  });
  return items.filter((m) => !m.parent_id || m.parent_id === "").map(toNode);
}

/** 收集 rootId 自身及其所有后代 id（防成环用） */
export function collectSubtreeIds(items: SysMenuItem[], rootId: string): Set<string> {
  const childrenOf = new Map<string, SysMenuItem[]>();
  for (const m of items) {
    const pid = m.parent_id || "";
    if (!childrenOf.has(pid)) childrenOf.set(pid, []);
    childrenOf.get(pid)!.push(m);
  }
  const out = new Set<string>([rootId]);
  const walk = (id: string) => {
    for (const c of childrenOf.get(id) || []) {
      const cid = c.menu_id as string;
      if (out.has(cid)) continue;
      out.add(cid);
      walk(cid);
    }
  };
  walk(rootId);
  return out;
}