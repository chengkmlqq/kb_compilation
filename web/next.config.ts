import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // The Next.js app is a pure frontend: every /api/* call is proxied to the
  // FastAPI backend (api-server) via a route handler (see src/app/api/[...path]/route.ts).
  async rewrites() {
    return [];
  },
};

export default nextConfig;
