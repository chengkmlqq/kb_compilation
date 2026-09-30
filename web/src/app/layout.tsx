import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "知识库平台",
  description: "知识库 wiki 平台（文档解析 → 向量化 → 混合检索 → Wiki → 问答）",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
