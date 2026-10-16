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
import { apiJwtExchange, apiMe } from "@/lib/api";

const { Content } = Layout;

export function LayoutContent({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);
  // 无框架模式（挂载到其他系统）：iframe 嵌入 或 URL 显式指定（?embed=1 / ?noframe=1 / ?frame=0）
  // 时仅渲染页面本体，跳过 Header/Sider/水印/全局上传浮层；?frame=1 可强制保留框架。
  const [embed, setEmbed] = useState(false);

  // 登录守卫（客户端校验身份；失败跳 /login）。
  // URL 带 ?jwt= 时先换签：宿主 JWT → x-next-identity cookie，再走正常校验，
  // 保证挂载场景（iframe/无框架）免二次登录且后续接口带真实身份。
  useEffect(() => {
    // 无框架模式检测（首帧 return null，检测完与身份检查一起出最终形态，无闪动）
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get("frame") === "1") {
        setEmbed(false);
      } else {
        const explicit =
          params.get("embed") === "1" ||
          params.get("noframe") === "1" ||
          params.get("frame") === "0";
        const inIframe = window.self !== window.top;
        setEmbed(explicit || inIframe);
      }
    } catch {
      setEmbed(false);
    }
    (async () => {
      try {
        const jwt = new URLSearchParams(window.location.search).get("jwt");
        if (jwt) {
          try {
            const res = await apiJwtExchange(jwt);
            if (res.success && res.identity_cookie) {
              // 注意：不能 encodeURIComponent —— 后端 decode_identity_cookie
              // 不先做 URL 解码，编码后的 %XX 会导致 base64 解析失败（同 login 页写法）。
              // identity_cookie 在响应顶层（data 是 identity payload，不含它）。
              document.cookie = `x-next-identity=${res.identity_cookie}; path=/; max-age=172800`;
            }
          } catch {
            /* 换签失败：继续走身份校验，未登录会跳 /login */
          }
        }
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

  // 无框架模式：仅渲染页面本体（保留 antd App 上下文供 message/modal 使用），
  // 页面高度里 Header 的 45px 偏移经 --app-header-h 归零。
  if (embed) {
    return (
      <App>
        <div
          style={{
            height: "100vh",
            overflow: "hidden",
            background: "#eff4f9",
            display: "flex",
            flexDirection: "column",
            ["--app-header-h" as string]: "0px",
          }}
        >
          {children}
        </div>
      </App>
    );
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
                  background: "#eff4f9",
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
