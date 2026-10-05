import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // 应用整体挂载在 /wiki 前缀下（不占根路径；nginx 根路径不再代理应用）。
  // 页面路由与静态资源自动带前缀；API 调用走 nginx 直连 api-server（/api 前缀不变）。
  basePath: "/wiki",
  // The Next.js app is a pure frontend: every /api/* call is proxied to the
  // FastAPI backend (api-server) via a route handler (see src/app/api/[...path]/route.ts).
  async rewrites() {
    return [];
  },
};

export default nextConfig;
