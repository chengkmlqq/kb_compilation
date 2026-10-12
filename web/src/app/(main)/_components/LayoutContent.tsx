"use client";

import React, { useEffect, useState } from "react";
import { App, Layout } from "antd";
import { AppHeader } from "./AppHeader";
import { AppSider } from "./AppSider";
import { MenuProvider } from "./MenuContext";
import { GlobalWatermark } from "@/components/GlobalWatermark";
import GlobalDropZone from "@/components/GlobalDropZone";
import UploadTaskPanel from "@/components/UploadTaskPanel";
import { apiMe } from "@/lib/api";

const { Content } = Layout;

export function LayoutContent({ children }: { children: React.ReactNode }) {
  const [checked, setChecked] = useState(false);
  // 无框架模式（挂载到其他系统）：iframe 嵌入 或 URL 显式指定（?embed=1 / ?noframe=1 / ?frame=0）
  // 时仅渲染页面本体，跳过 Header/Sider/水印/全局上传浮层；?frame=1 可强制保留框架。
  const [embed, setEmbed] = useState(false);

  // 登录守卫（客户端校验身份）。
  // 方案3（2026-10-11）：供 data_supply 菜单 iframe 挂载，改为「匿名放行」——
  // 未登录不再强制跳 /login，页面正常渲染（与后端 middleware 的
  // “无身份请求放行”策略对齐）。已知取舍：写操作接口仍依赖身份，
  // 匿名用户仅可浏览，与平台后续登录加固方向相反，属挂载场景的显式放宽。
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
        await apiMe();
      } catch {
        /* 匿名也放行 */
      } finally {
        setChecked(true);
      }
    })();
  }, []);

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
            background: "#f5f7fa",
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
