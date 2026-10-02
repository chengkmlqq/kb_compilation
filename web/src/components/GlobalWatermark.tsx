"use client";

import React, { useEffect, useState } from "react";
import { Watermark } from "antd";
import { apiMe } from "@/lib/api";

/**
 * 全局水印（轻量版，基于 antd Watermark）
 *
 * data-synth 的水印走远程注入服务（WATERMARK_API_URL 签名调用），kb 无该
 * 服务；这里用 antd <Watermark> 做等价可见水印（用户名 + 用户 ID），由
 * config SYSTEM_CONFIG.watermark_enabled / env WATERMARK_ENABLED 控制。
 * 未登录时不渲染（LayoutContent 的登录守卫已挡住）。
 */
export function GlobalWatermark({ children }: { children: React.ReactNode }) {
  const [enabled, setEnabled] = useState(true);
  const [text, setText] = useState("");

  useEffect(() => {
    const flag = process.env.NEXT_PUBLIC_WATERMARK_ENABLED;
    if (flag !== undefined) {
      setEnabled(flag === "true" || flag === "1");
    } else {
      // 默认开启（内部平台防截图），可用 NEXT_PUBLIC_WATERMARK_ENABLED=false 关
      setEnabled(true);
    }
    (async () => {
      try {
        const me = await apiMe();
        if (me.success && me.data) {
          setText(`${me.data.userName || ""} ${me.data.userId || ""}`.trim());
        }
      } catch {
        /* ignore */
      }
    })();
  }, []);

  if (!enabled || !text) {
    return <>{children}</>;
  }

  return (
    <Watermark
      content={[text, "知识库平台"]}
      font={{ color: "rgba(36, 46, 67, 0.06)", fontSize: 16 }}
      rotate={-22}
      gap={[120, 120]}
    >
      <div style={{ height: "100%" }}>{children}</div>
    </Watermark>
  );
}