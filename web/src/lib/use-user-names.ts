"use client";

/**
 * 用户ID → 用户名（中文）映射 —— 业务页面「创建人」展示用。
 *
 * 背景：kb_tape / kb_tape_template 等表的 create_user 存的是 modo_user.user_id
 * （如 admin / chengkai3），业务页面要展示 user_name（如「系统管理员」）。
 * 挂载时拉一次全量用户（pageSize=1000，端点上限 le=1000）构建映射，页面查表渲染。
 *
 * 用法：
 *   const userNames = useUserNames();
 *   render: (v?: string) => (v ? userNames[v] || v : "-")
 *
 * 拉取失败/未加载完时不阻塞页面：映射为空时调用方回退显示原始 ID。
 */
import { useEffect, useState } from "react";

import { apiListUsers } from "@/lib/api";

export function useUserNames(): Record<string, string> {
  const [names, setNames] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await apiListUsers(1, 1000);
        if (cancelled || !res.success || !res.data) return;
        const map: Record<string, string> = {};
        for (const u of res.data.items ?? []) {
          if (u.user_id && u.user_name) map[u.user_id] = u.user_name;
        }
        setNames(map);
      } catch {
        /* 用户列表拉取失败不阻塞页面（映射为空，渲染时回退显示原始 ID） */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return names;
}