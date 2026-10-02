import React from "react";
import { LayoutContent } from "./_components/LayoutContent";

export const dynamic = "force-dynamic";

/** 主布局（对齐 data-synth FrameLayout：Header 顶级导航 + Sider 子菜单 + Content） */
export default function FrameLayout({ children }: { children: React.ReactNode }) {
  return <LayoutContent>{children}</LayoutContent>;
}
