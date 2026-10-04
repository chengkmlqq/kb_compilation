"use client";

/**
 * 系统管理入口（父级页面）。
 *
 * 用户/角色/团队/菜单/操作日志/MCP/技能/模型配置均为独立页面（对齐 ds system/* 独立页面）：
 *   /system/users、/system/roles、/system/teams、/system/menus、/system/logs
 *   /mcps、/skills、/models
 * 本页只做「/system → /system/users」跳转，避免出现带顶部 Tab 的聚合页。
 */
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function SystemPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/system/users", { scroll: false });
  }, [router]);
  return null;
}