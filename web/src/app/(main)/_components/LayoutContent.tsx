"use client";

import React, { useEffect, useState } from "react";
import { App, Layout } from "antd";
import { useRouter } from "next/navigation";
import { AppHeader } from "./AppHeader";
import { AppSider } from "./AppSider";
import { MenuProvider } from "./MenuContext";
import { GlobalWatermark } from "@/components/GlobalWatermark";
import GlobalDropZone from "@/components/GlobalDropZone";
import UploadTaskPanel from "@/components/UploadTaskPanel";
import { apiMe } from "@/lib/api";

const { Content } = Layout;

export function LayoutContent({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  // 登录守卫（客户端校验身份；失败跳 /login）
  useEffect(() => {
    (async () => {
      try {
        const me = await apiMe();
        if (!me.success || !me.data) {
          router.replace("/login");
          return;
        }
        setChecked(true);
      } catch {
        router.replace("/login");
      }
    })();
  }, [router]);

  if (!checked) {
    return null; // identity check in flight
  }

  return (
    <MenuProvider>
      <App>
        {/* 全局拖放上传遮罩 + 上传任务浮层（仅登录后主区域渲染） */}
        <GlobalDropZone />
        <UploadTaskPanel />
        <Layout style={{ height: "100vh", flexDirection: "column", overflow: "hidden" }}>
          <AppHeader />
          <Layout style={{ flex: 1 }} hasSider>
            <AppSider />
            <Layout style={{ padding: "0" }}>
              <Content
                style={{
                  background: "#f5f7fa",
                  margin: 0,
                  padding: 0,
                  overflow: "hidden",
                }}
              >
                <GlobalWatermark>{children}</GlobalWatermark>
              </Content>
            </Layout>
          </Layout>
        </Layout>
      </App>
    </MenuProvider>
  );
}
