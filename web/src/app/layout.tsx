import type { Metadata } from "next";
import "./globals.css";
import StyledComponentsRegistry from "@/lib/antd-registry";
import { ThemeModeProvider, themeInitScript } from "@/lib/theme-mode";

export const metadata: Metadata = {
  title: "领域知识构建",
  description: "领域知识构建（文档解析 → 向量化 → 混合检索 → Wiki → 问答）",
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