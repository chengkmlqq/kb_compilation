import type { Metadata } from "next";
import "./globals.css";
import StyledComponentsRegistry from "@/lib/antd-registry";
import { ThemeModeProvider, themeInitScript } from "@/lib/theme-mode";

export const metadata: Metadata = {
  title: "领域知识构建",
  description: "领域知识构建（文档解析 → 向量化 → 混合检索 → Wiki → 问答）",
  // 浏览器标签 logo。路径必须带 basePath 前缀（next.config.ts basePath=/wiki）：
  // Next 的 metadata icons 不会自动补前缀（实测渲染出 /favicon.ico → 404，
  // 只有 /wiki/favicon.ico 可访问）。图标取自 data_supply_base
  // （data-supply-front/public/static/favicon.ico），与 web/public/favicon.ico 同源。
  icons: {
    icon: "/wiki/favicon.ico",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        {/* 深色模式首帧防闪烁（2026-10-12 对齐 WeKnora html[theme-mode]） */}
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <ThemeModeProvider>
          <StyledComponentsRegistry>{children}</StyledComponentsRegistry>
        </ThemeModeProvider>
      </body>
    </html>
  );
}